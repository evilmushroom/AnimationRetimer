# -*- coding: utf-8 -*-
bl_info = {
    "name": "Animation Retimer (Multi-Object)",
    "blender": (3, 6, 0), # Or your target Blender version
    "category": "Animation",
    "description": "Scale animation between markers for multiple selected objects, preserving key types.",
    "version": (1, 1, 0), # Added version
    "author": "Your Name (Modified by AI)", # Optional: Add author info
    "location": "Dope Sheet > UI Panel > Retime Tools", # Optional: Add location
    "warning": "", # Optional: Add warnings if any
    "doc_url": "", # Optional: Link to documentation
    "tracker_url": "", # Optional: Link to bug tracker
}

import bpy
from collections import defaultdict
import copy  # Import deep copy module
import time # For performance timing (optional)

# --- Data Storage ---
# Store original marker positions (scene-wide)
retimer_data = {
    "original_markers": {},
    "original_start": 0,
    "original_end": 0,
    "original_segments": [],
    # Store data per object
    "objects_processed": [], # Keep track of which objects we started retiming
    "objects_temp_keyframe_data": {}, # {obj_name: {fcurve_key: [(x, y, interp, hl, hr, type), ...], ...}} # Added type
}

# --- Utility Functions ---
def get_ordered_markers(scene):
    """Return markers sorted by frame number"""
    # Ensure markers exist before trying to access them
    if not scene or not scene.timeline_markers:
        return []
    return sorted(
        [m for m in scene.timeline_markers if m.name.startswith("RT_")],
        key=lambda m: m.frame
    )

def find_segment(frame, segments):
    """Find which segment a frame belongs to. Returns index, 'before', 'after', or None."""
    if not segments: # Handle case with no segments defined
        return None

    for i, (start, end) in enumerate(segments):
        # Allow frame to be exactly on the start or end marker
        # Use a small tolerance for floating point comparisons? Maybe not needed for frame numbers.
        if start <= frame <= end:
            # Special case: if frame is exactly on the end marker of a segment,
            # and it's NOT the end marker of the *last* segment, associate it with the *next* segment's start.
            # This helps handle scaling at the marker points more intuitively.
            # Let's reconsider: Associate with the segment it falls within.
            # If frame == end, it belongs to segment i. If frame == start of next, it belongs to next.
             if frame == end and i < len(segments) - 1 and frame == segments[i+1][0]:
                  # If exactly on the boundary between two segments, associate with the *first* one (i)
                  return i
             elif start <= frame <= end:
                  return i


    # Check if frame is before the first marker or after the last marker
    if frame < segments[0][0]:
        return 'before'
    elif frame > segments[-1][1]:
        return 'after'

    # Should theoretically not be reached if segments cover the range, but acts as fallback
    return None


# --- Operators ---

class ANIMATION_RETIMER_OT_AddMarker(bpy.types.Operator):
    """Add a new retiming marker at the current frame"""
    bl_idname = "animation_retimer.add_marker"
    bl_label = "Add Retime Marker"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not context.scene:
             self.report({'ERROR'}, "No active scene found.")
             return {'CANCELLED'}

        frame = context.scene.frame_current
        marker_name = f"RT_{frame}"
        # Avoid duplicate names if marker already exists at this frame
        i = 1
        base_name = marker_name
        while marker_name in context.scene.timeline_markers:
             marker_name = f"{base_name}_{i}"
             i += 1

        marker = context.scene.timeline_markers.new(name=marker_name, frame=frame)
        # Store original position immediately IF retiming is not active
        if not context.window_manager.retimer_active:
             if "original_markers" not in retimer_data:
                 retimer_data["original_markers"] = {}
             retimer_data["original_markers"][marker.name] = marker.frame
        self.report({'INFO'}, f"Added marker: {marker.name}")
        return {'FINISHED'}


class ANIMATION_RETIMER_OT_RetimeMarker(bpy.types.Operator):
    """Start or stop the retiming process"""
    bl_idname = "animation_retimer.retime_marker"
    bl_label = "Toggle Retime"
    bl_options = {'REGISTER', 'UNDO'} # UNDO applies to starting/stopping

    _timer = None
    _last_marker_positions = {}

    def store_initial_keyframe_data_for_object(self, obj):
        """Store complete initial keyframe data for a single object, including type"""
        if not obj or not obj.animation_data or not obj.animation_data.action:
            # print(f"Skipping {obj.name}: No animation data or action.") # Less verbose
            return False # Indicate failure

        action = obj.animation_data.action
        obj_name = obj.name
        if "objects_temp_keyframe_data" not in retimer_data:
            retimer_data["objects_temp_keyframe_data"] = {}
        retimer_data["objects_temp_keyframe_data"][obj_name] = {} # Ensure object entry exists

        # print(f"Storing initial keyframes for: {obj_name}") # Less verbose
        count = 0
        for fcurve in action.fcurves:
            key = (fcurve.data_path, fcurve.array_index) # Unique key: (data_path, array_index)
            # Store x, y, interpolation, handles (deep copied), and type
            keyframe_data = [
                (kp.co.x, kp.co.y, kp.interpolation, copy.deepcopy(kp.handle_left), copy.deepcopy(kp.handle_right), kp.type)
                for kp in fcurve.keyframe_points
            ]
            retimer_data["objects_temp_keyframe_data"][obj_name][key] = keyframe_data
            count += len(keyframe_data)

        # print(f"Stored {count} keyframes across {len(action.fcurves)} F-Curves for {obj_name}.") # Less verbose
        return True # Indicate success

    def restore_from_original_for_object(self, obj):
        """Restore keyframes for a single object from stored data, including type. Used by Cancel."""
        obj_name = obj.name
        if not obj or not obj.animation_data or not obj.animation_data.action:
            # print(f"Cannot restore for {obj_name}: Missing object or animation data.") # Less verbose
            return
        if "objects_temp_keyframe_data" not in retimer_data or \
           obj_name not in retimer_data["objects_temp_keyframe_data"]:
             # print(f"Warning: No stored keyframe data found for {obj_name}. Skipping restore.") # Less verbose
             return

        action = obj.animation_data.action
        stored_fcurve_data = retimer_data["objects_temp_keyframe_data"][obj_name]
        # print(f"Restoring keyframes for: {obj_name}") # Less verbose

        for fcurve in action.fcurves:
            key = (fcurve.data_path, fcurve.array_index)
            if key not in stored_fcurve_data:
                continue

            # --- Clear existing keyframes ---
            try:
                # Faster way to clear? No direct 'clear' method. Removing one by one is standard.
                while len(fcurve.keyframe_points) > 0:
                    fcurve.keyframe_points.remove(fcurve.keyframe_points[0], fast=True)
            except ReferenceError:
                print(f"Warning: Could not clear keyframes for {key} in {obj_name} (possible internal Blender issue). Skipping F-Curve.")
                continue

            # --- Add back original keyframes ---
            original_keys = stored_fcurve_data[key]
            if not original_keys:
                 continue

            # Data should be sorted by x already, but sorting doesn't hurt
            sorted_keys = sorted(original_keys, key=lambda k: k[0])
            for x, y, interp, hl, hr, k_type in sorted_keys: # Unpack type
                try:
                    kp = fcurve.keyframe_points.insert(x, y, options={'NEEDED', 'FAST'})
                    kp.interpolation = interp
                    # Use deepcopy *again* when restoring handles
                    kp.handle_left = copy.deepcopy(hl)
                    kp.handle_right = copy.deepcopy(hr)
                    kp.type = k_type # Restore the keyframe type
                except Exception as e:
                    print(f"Error inserting keyframe at {x} for {key} in {obj_name}: {e}")

            fcurve.update()

    def process_retiming(self, context):
        """Apply retiming logic to all tracked objects based on marker changes"""
        #perf_start_time = time.time() # Optional: for performance measurement
        wm = context.window_manager
        scene = context.scene
        if not scene: return # Scene closed?

        # --- 1. Check if Markers Changed ---
        markers = get_ordered_markers(scene)
        if len(markers) < 2: return

        current_positions = {m.name: m.frame for m in markers}
        if not hasattr(self, '_last_marker_positions'): self._last_marker_positions = {}
        if current_positions == self._last_marker_positions: return # No change

        self._last_marker_positions = current_positions.copy()

        # --- 2. Recalculate Segment Mappings ---
        original_marker_positions = retimer_data.get("original_markers", {})
        if not original_marker_positions: return # Should not happen if started correctly

        valid_current_markers = {name: marker for name, marker in zip(current_positions.keys(), markers) if name in original_marker_positions}
        if len(valid_current_markers) < 2: return

        sorted_marker_names = sorted(valid_current_markers.keys(), key=lambda name: original_marker_positions[name])

        original_segments = []
        current_segments = []
        for i in range(len(sorted_marker_names) - 1):
            name1, name2 = sorted_marker_names[i], sorted_marker_names[i+1]
            if name1 not in current_positions or name2 not in current_positions: continue

            orig_start = original_marker_positions[name1]
            orig_end = original_marker_positions[name2]
            curr_start = current_positions[name1]
            curr_end = current_positions[name2]

            original_segments.append((orig_start, orig_end))
            current_segments.append((curr_start, curr_end))

        # --- 3. Process Each Object ---
        objects_to_retime = retimer_data.get("objects_processed", [])
        if not objects_to_retime: return

        snap_frames = wm.retimer_snap_frames # Cache property lookup

        for obj_name in objects_to_retime:
            obj = bpy.data.objects.get(obj_name)
            if not obj or not obj.animation_data or not obj.animation_data.action: continue
            if "objects_temp_keyframe_data" not in retimer_data or \
               obj_name not in retimer_data["objects_temp_keyframe_data"]: continue

            action = obj.animation_data.action
            stored_fcurve_data = retimer_data["objects_temp_keyframe_data"][obj_name]

            # --- OPTIMIZATION: Remove restore_from_original call here ---
            # self.restore_from_original_for_object(obj) # REMOVED!

            for fcurve in action.fcurves:
                key = (fcurve.data_path, fcurve.array_index)
                if key not in stored_fcurve_data: continue

                original_keys_for_fcurve = stored_fcurve_data[key]
                if not original_keys_for_fcurve:
                    # If original was empty, ensure current is also empty
                    if len(fcurve.keyframe_points) > 0:
                         try:
                              while len(fcurve.keyframe_points) > 0:
                                   fcurve.keyframe_points.remove(fcurve.keyframe_points[0], fast=True)
                              fcurve.update()
                         except ReferenceError: pass # Ignore if clearing fails
                    continue # Skip to next fcurve

                # --- Calculate New X Positions ---
                calculated_new_keys = [] # List of (new_x, y, interp, hl, hr, type)
                for orig_x, orig_y, interp, hl, hr, k_type in original_keys_for_fcurve: # Unpack type
                    segment_idx = find_segment(orig_x, original_segments)
                    new_x = orig_x # Default

                    if original_segments and current_segments: # Check lists aren't empty
                        if segment_idx == 'before':
                            offset = current_segments[0][0] - original_segments[0][0]
                            new_x = orig_x + offset
                        elif segment_idx == 'after':
                            offset = current_segments[-1][1] - original_segments[-1][1]
                            new_x = orig_x + offset
                        elif isinstance(segment_idx, int):
                            if 0 <= segment_idx < len(original_segments): # Bounds check
                                orig_start, orig_end = original_segments[segment_idx]
                                curr_start, curr_end = current_segments[segment_idx]
                                orig_length = orig_end - orig_start

                                if abs(orig_length) < 0.0001:
                                    new_x = curr_start # Snap keys in zero-length segments to start
                                else:
                                    position_in_segment = (orig_x - orig_start) / float(orig_length)
                                    curr_length = curr_end - curr_start
                                    new_x = curr_start + (position_in_segment * curr_length)

                    # Append calculated key including type
                    calculated_new_keys.append((new_x, orig_y, interp, copy.deepcopy(hl), copy.deepcopy(hr), k_type))


                # --- Apply Snapping and Merging (Handles Type) ---
                final_keys_to_insert = {} # Use dict {frame: (x, y, interp, hl, hr, type)}

                if snap_frames:
                    temp_grouped_keys = defaultdict(list)
                    # Group by rounded frame, store full calculated data
                    for calc_x, y, interp, hl, hr, k_type in calculated_new_keys:
                        rounded_x = round(calc_x)
                        temp_grouped_keys[rounded_x].append((calc_x, y, interp, hl, hr, k_type))

                    for frame, keys_at_frame in temp_grouped_keys.items():
                        # Merge strategy: Keep the one whose calculated X was closest to the target frame
                        keys_at_frame.sort(key=lambda k: abs(k[0] - frame))
                        best_key_data = keys_at_frame[0]
                        # Store using the rounded frame, but keep full data from the best key
                        final_keys_to_insert[frame] = (frame, best_key_data[1], best_key_data[2], best_key_data[3], best_key_data[4], best_key_data[5]) # Include type
                else:
                     # No snapping, use calculated keys directly. Dict handles exact float duplicates.
                     for calc_x, y, interp, hl, hr, k_type in calculated_new_keys:
                          final_keys_to_insert[calc_x] = (calc_x, y, interp, hl, hr, k_type) # Include type


                # --- 3c. Apply Updated Keyframes for this F-Curve ---
                # Clear existing keyframes first
                try:
                    while len(fcurve.keyframe_points) > 0:
                         fcurve.keyframe_points.remove(fcurve.keyframe_points[0], fast=True)
                except ReferenceError:
                     print(f"Warning: Could not clear keyframes for {key} in {obj_name} (during apply phase). Skipping update.")
                     continue # Skip inserting if clearing failed


                # Insert the final set of keys, sorted by frame
                if not final_keys_to_insert:
                     # Ensure fcurve is updated even if empty now
                     fcurve.update()
                     continue

                final_sorted_keys = sorted(final_keys_to_insert.values(), key=lambda k: k[0])

                # --- Batch insert? No direct batch insert with all properties ---
                # --- Inserting one by one ---
                for x, y, interp, hl, hr, k_type in final_sorted_keys: # Unpack type
                    try:
                        kp = fcurve.keyframe_points.insert(x, y, options={'NEEDED', 'FAST'})
                        kp.interpolation = interp
                        kp.handle_left = hl # Already deep copied
                        kp.handle_right = hr # Already deep copied
                        kp.type = k_type # Restore type
                    except Exception as e:
                        print(f"Error inserting final keyframe at {x} for {key} in {obj_name}: {e}")

                fcurve.update() # Update fcurve after all points are inserted

        # --- End of Object Loop ---

        # Optional: Force UI redraw if needed, though fcurve.update() often suffices
        # context.view_layer.update()
        # for area in context.screen.areas:
        #      if area.type == 'DOPESHEET_EDITOR':
        #           area.tag_redraw()

        #perf_end_time = time.time()
        #print(f"Process Retiming took: {perf_end_time - perf_start_time:.4f} seconds") # Optional performance print


    def modal(self, context, event):
        wm = context.window_manager
        op_context = 'INVOKE_DEFAULT'

        if not wm.retimer_active:
            self.cancel_modal(context)
            # print("Retiming stopped externally.") # Less verbose
            return {'CANCELLED'}

        if event.type == 'ESC' and event.value == 'PRESS':
            # print("ESC pressed, cancelling retiming.") # Less verbose
            bpy.ops.animation_retimer.cancel_retiming(op_context)
            return {'CANCELLED'}

        if (event.type == 'RET' or event.type == 'NUMPAD_ENTER') and event.value == 'PRESS':
            # print("Enter pressed, applying retiming.") # Less verbose
            bpy.ops.animation_retimer.apply_retiming(op_context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            try:
                 self.process_retiming(context)
            except Exception as e:
                 print(f"Error during process_retiming: {e}")
                 import traceback
                 traceback.print_exc()
                 # Stop the timer and cancel on error to prevent spamming
                 self.cancel_modal(context)
                 wm.retimer_active = False # Ensure state is reset
                 context.area.tag_redraw() # Update UI
                 self.report({'ERROR'}, "Error during update, retiming cancelled.")
                 return {'CANCELLED'}

        return {'PASS_THROUGH'}


    def execute(self, context):
        wm = context.window_manager
        scene = context.scene
        op_context = 'INVOKE_DEFAULT'

        if not wm.retimer_active:
            # --- STARTING RETIMING ---
            # print("Starting retiming...") # Less verbose
            if not scene:
                 self.report({'ERROR'}, "No active scene.")
                 return {'CANCELLED'}
            markers = get_ordered_markers(scene)
            if len(markers) < 2:
                self.report({'ERROR'}, "Add at least 2 'RT_' prefixed markers first!")
                return {'CANCELLED'}

            selected_objects = [o for o in context.selected_objects if o.animation_data and o.animation_data.action]
            if not selected_objects:
                 self.report({'ERROR'}, "No selected objects with animation data found!")
                 return {'CANCELLED'}

            # Store original marker positions
            retimer_data["original_markers"] = {m.name: m.frame for m in markers}
            retimer_data["original_start"] = markers[0].frame
            retimer_data["original_end"] = markers[-1].frame
            retimer_data["original_segments"] = [
                (markers[i].frame, markers[i+1].frame)
                for i in range(len(markers)-1)
            ]

            # Store initial keyframes for selected objects
            retimer_data["objects_temp_keyframe_data"] = {} # Clear/initialize
            retimer_data["objects_processed"] = [] # Reset processed list
            success_count = 0
            for obj in selected_objects:
                if self.store_initial_keyframe_data_for_object(obj):
                    retimer_data["objects_processed"].append(obj.name)
                    success_count += 1

            if success_count == 0:
                 self.report({'ERROR'}, "Could not store keyframe data for any selected object.")
                 retimer_data.clear() # Clear all data if failed
                 return {'CANCELLED'}

            # Store current marker positions to detect changes
            self._last_marker_positions = {m.name: m.frame for m in markers}

            # Start modal timer (Increased interval for performance)
            timer_interval = 0.1 # Seconds (10 updates per second)
            self._timer = wm.event_timer_add(timer_interval, window=context.window)
            wm.modal_handler_add(self)
            wm.retimer_active = True
            print(f"Retiming active for objects: {retimer_data['objects_processed']} (Update interval: {timer_interval}s)")
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        else:
            # --- STOPPING RETIMING (via toggle button) ---
            # print("Toggle Retime called while active - Applying changes.") # Less verbose
            bpy.ops.animation_retimer.apply_retiming(op_context)
            return {'FINISHED'}

    def cancel_modal(self, context):
        """Only cleans up the modal timer"""
        wm = context.window_manager
        if self._timer:
            try:
                wm.event_timer_remove(self._timer)
                # print("Modal timer removed.") # Less verbose
            except ValueError: # Timer might already be removed
                 # print("Modal timer already removed.") # Less verbose
                 pass
            self._timer = None


class ANIMATION_RETIMER_OT_ApplyRetiming(bpy.types.Operator):
    """Apply the current retiming changes permanently"""
    bl_idname = "animation_retimer.apply_retiming"
    bl_label = "Apply Retiming"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.window_manager.retimer_active

    def execute(self, context):
        wm = context.window_manager
        # print("Applying retiming changes.") # Less verbose
        wm.retimer_active = False # Stop modal loop FIRST

        # Clear temporary data
        retimer_data.pop("objects_temp_keyframe_data", None)
        retimer_data.pop("objects_processed", None)
        retimer_data.pop("original_markers", None)
        retimer_data.pop("original_segments", None)

        context.area.tag_redraw()
        self.report({'INFO'}, "Retiming applied.")
        return {'FINISHED'}

class ANIMATION_RETIMER_OT_CancelRetiming(bpy.types.Operator):
    """Discard all retiming changes since 'Start Retiming' was pressed"""
    bl_idname = "animation_retimer.cancel_retiming"
    bl_label = "Discard Changes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        wm = context.window_manager
        scene = context.scene

        objects_to_restore = retimer_data.get("objects_processed", [])
        original_marker_positions = retimer_data.get("original_markers", {})
        stored_keyframe_data_exists = "objects_temp_keyframe_data" in retimer_data and \
                                     retimer_data["objects_temp_keyframe_data"]

        if not objects_to_restore and not original_marker_positions and not stored_keyframe_data_exists:
             self.report({'WARNING'}, "No retiming data found to cancel/discard.")
             if wm.retimer_active: wm.retimer_active = False # Ensure inactive
             context.area.tag_redraw()
             return {'CANCELLED'}

        # print("Cancelling retiming and restoring original state...") # Less verbose
        was_active = wm.retimer_active
        wm.retimer_active = False # Stop modal loop FIRST

        # --- Restore Keyframes (using the dedicated restore function now) ---
        if stored_keyframe_data_exists:
            # print(f"Objects to restore keyframes for: {objects_to_restore}") # Less verbose
            # Need an instance of the RetimeMarker operator to call its restore method? No, moved logic.
            # Create temporary instance? No, call directly if possible.
            # Let's reuse the restore method logic directly here or call it if accessible.
            # Calling requires an instance... let's reuse the logic for safety.

            restore_instance = ANIMATION_RETIMER_OT_RetimeMarker() # Temporary instance ok? Seems ok.
            for obj_name in objects_to_restore:
                obj = bpy.data.objects.get(obj_name)
                if obj:
                    # Use the restore method from the class
                    restore_instance.restore_from_original_for_object(obj)
                else:
                    print(f"Skipping restore for missing object: {obj_name}")
        # else: print("No stored keyframe data to restore.") # Less verbose


        # --- Restore Markers ---
        if original_marker_positions:
            # print("Restoring marker positions...") # Less verbose
            if scene and scene.timeline_markers: # Check scene and markers exist
                current_markers = scene.timeline_markers
                markers_to_remove = []
                processed_marker_names = set()

                for name, frame in original_marker_positions.items():
                    if name in current_markers:
                        current_markers[name].frame = frame
                        processed_marker_names.add(name)
                    # else: print(f"  Original marker {name} not found, cannot restore.") # Less verbose

                for marker in current_markers:
                     if marker.name.startswith("RT_") and marker.name not in processed_marker_names:
                          markers_to_remove.append(marker)
                          # print(f"  Removing marker potentially added during retiming: {marker.name}") # Less verbose

                for marker in markers_to_remove:
                     try: current_markers.remove(marker)
                     except (KeyError, ReferenceError): pass # Ignore if already gone

        # else: print("No original marker positions stored to restore.") # Less verbose


        # --- Clean up global data ---
        retimer_data.pop("objects_temp_keyframe_data", None)
        retimer_data.pop("objects_processed", None)
        retimer_data.pop("original_markers", None)
        retimer_data.pop("original_segments", None)

        context.area.tag_redraw()
        self.report({'INFO'}, "Retiming cancelled, changes discarded.")
        return {'FINISHED'}


# --- Other Operators ---

class ANIMATION_RETIMER_OT_DeleteMarker(bpy.types.Operator):
    """Delete the selected retiming marker (Disabled during active Retime)"""
    bl_idname = "animation_retimer.delete_marker"
    bl_label = "Delete Retiming Marker"
    bl_options = {'REGISTER', 'UNDO'}

    marker_name: bpy.props.StringProperty(name="Marker Name")

    @classmethod
    def poll(cls, context):
         return not context.window_manager.retimer_active

    def execute(self, context):
        if context.window_manager.retimer_active: # Redundant check due to poll
             self.report({'ERROR'}, "Cannot delete markers while retiming is active.")
             return {'CANCELLED'}
        if not context.scene or not context.scene.timeline_markers:
             self.report({'ERROR'}, "No scene or timeline markers found.")
             return {'CANCELLED'}

        markers = context.scene.timeline_markers
        if self.marker_name in markers:
            try:
                markers.remove(markers[self.marker_name])
                retimer_data.get("original_markers", {}).pop(self.marker_name, None)
                # self.report({'INFO'}, f"Deleted marker: {self.marker_name}") # Less verbose
            except (KeyError, ReferenceError):
                 self.report({'WARNING'}, f"Marker '{self.marker_name}' could not be removed.")
                 return {'CANCELLED'}
        else:
             self.report({'WARNING'}, f"Marker '{self.marker_name}' not found.")
             return {'CANCELLED'}
        return {'FINISHED'}

class ANIMATION_RETIMER_OT_ClearMarkers(bpy.types.Operator):
    """Remove all retiming markers (RT_ prefix) (Disabled during active Retime)"""
    bl_idname = "animation_retimer.clear_markers"
    bl_label = "Clear All Retiming Markers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
         return not context.window_manager.retimer_active

    def execute(self, context):
        if context.window_manager.retimer_active: # Redundant check
             self.report({'ERROR'}, "Cannot clear markers while retiming is active.")
             return {'CANCELLED'}
        if not context.scene or not context.scene.timeline_markers:
             self.report({'ERROR'}, "No scene or timeline markers found.")
             return {'CANCELLED'}

        markers = context.scene.timeline_markers
        to_remove = [m for m in markers if m.name.startswith("RT_")]
        if not to_remove:
             self.report({'INFO'}, "No 'RT_' markers found to clear.")
             return {'CANCELLED'}

        count = len(to_remove)
        for marker in to_remove:
            try: markers.remove(marker)
            except (KeyError, ReferenceError): pass

        retimer_data.pop("original_markers", None)
        self.report({'INFO'}, f"Cleared {count} 'RT_' markers.")
        return {'FINISHED'}


class ANIMATION_RETIMER_OT_SelectMarker(bpy.types.Operator):
    """Jump to the selected marker's position in the timeline"""
    bl_idname = "animation_retimer.select_marker"
    bl_label = "Select Retiming Marker"
    bl_options = {'REGISTER'} # No UNDO needed

    marker_name: bpy.props.StringProperty(name="Marker Name")

    def execute(self, context):
        if not context.scene or not context.scene.timeline_markers:
             self.report({'ERROR'}, "No scene or timeline markers found.")
             return {'CANCELLED'}
        scene = context.scene
        markers = scene.timeline_markers
        if self.marker_name in markers:
            marker = markers[self.marker_name]
            try:
                 scene.frame_set(marker.frame)
            except Exception as e:
                 self.report({'ERROR'}, f"Could not set frame: {e}")
                 return {'CANCELLED'}
        else:
             self.report({'WARNING'}, f"Marker '{self.marker_name}' not found.")
             return {'CANCELLED'}
        return {'FINISHED'}

# --- Panel ---

class ANIMATION_RETIMER_PT_Panel(bpy.types.Panel):
    bl_label = "Animation Retimer (Multi)"
    bl_idname = "ANIMATION_RETIMER_PT_Panel"
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Retime Tools'

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        scene = context.scene
        if not scene: # Handle case where scene might not be available
             layout.label(text="No active scene.", icon='ERROR')
             return

        # --- Main Controls ---
        col = layout.column(align=True)
        if wm.retimer_active:
            row = col.row(align=True)
            row.operator("animation_retimer.apply_retiming", text="Apply Changes", icon='CHECKMARK')
            row.operator("animation_retimer.cancel_retiming", text="Discard Changes", icon='X')
            col.label(text="Retiming Active...", icon='INFO')
            processed_objs = retimer_data.get("objects_processed", [])
            if processed_objs:
                 box = col.box()
                 box.label(text=f"Processing ({len(processed_objs)}):")
                 limit = 3
                 for i, name in enumerate(processed_objs):
                      if i >= limit: box.label(text=f"...and {len(processed_objs) - limit} more"); break
                      box.label(text=f"- {name}", icon='OBJECT_DATA')
        else:
            can_start = len(get_ordered_markers(scene)) >= 2
            row = col.row()
            row.enabled = can_start
            row.operator("animation_retimer.retime_marker", text="Start Retiming", icon='PLAY')
            if not can_start:
                 col.label(text="Add >= 2 'RT_' markers", icon='ERROR')

        layout.separator()

        # --- Marker Management ---
        row = layout.row(align=True)
        # Use poll status of operators to implicitly disable row if needed
        # row.enabled = not wm.retimer_active # Not strictly needed due to operator polls
        row.operator("animation_retimer.add_marker", text="Add Marker", icon='ADD')
        row.operator("animation_retimer.clear_markers", text="Clear All", icon='TRASH')

        layout.separator()
        layout.prop(wm, "retimer_snap_frames") # Snap option

        # --- Segments Info (when active) ---
        if wm.retimer_active:
            markers = get_ordered_markers(scene)
            if len(markers) >= 2:
                layout.separator()
                box = layout.box()
                box.label(text="Segment Status:", icon='NLA')
                original_segments_ui = retimer_data.get("original_segments", [])
                original_markers_map = retimer_data.get("original_markers",{})
                if not original_markers_map or not original_segments_ui:
                     box.label(text="Original data missing.", icon='ERROR')
                     return # Exit draw section if data is bad

                valid_current_markers = {name: m for name, m in zip(original_markers_map.keys(), markers) if name in original_markers_map}
                sorted_marker_names = sorted(valid_current_markers.keys(), key=lambda name: original_markers_map[name])

                current_segments_ui = []
                valid_segment_count = 0
                for i in range(len(sorted_marker_names) - 1):
                     name1, name2 = sorted_marker_names[i], sorted_marker_names[i+1]
                     frame1 = scene.timeline_markers.get(name1)
                     frame2 = scene.timeline_markers.get(name2)
                     if frame1 is not None and frame2 is not None:
                          current_segments_ui.append((frame1.frame, frame2.frame))
                          valid_segment_count += 1
                     else:
                          current_segments_ui.append(None) # Placeholder

                if valid_segment_count == len(original_segments_ui):
                    has_collapsed = False
                    for i, current_seg in enumerate(current_segments_ui):
                        if current_seg is None: continue # Skip already handled missing markers
                        row = box.row()
                        curr_start, curr_end = current_seg
                        orig_start, orig_end = original_segments_ui[i]
                        orig_length = orig_end - orig_start
                        curr_length = curr_end - curr_start
                        icon = 'RIGHTARROW' # Default icon
                        if curr_start > curr_end:
                            speed_text = "Collapsed/Inverted"; icon = 'ERROR'; has_collapsed = True
                        elif abs(orig_length) < 0.0001:
                            speed_text = "From Zero Length"; icon='INFO'
                        else:
                            ratio = curr_length / float(orig_length)
                            speed_text = f"{ratio:.2f}x Speed"
                            if ratio > 1: icon = 'TRIA_DOWN'
                            elif ratio < 1: icon = 'TRIA_UP'
                        row.label(text=f"Seg {i+1} [{orig_start}-{orig_end} -> {curr_start}-{curr_end}]: {speed_text}", icon=icon)
                    if has_collapsed: box.label(text="Collapsed segments!", icon='ERROR')
                else:
                     box.label(text="Segment mismatch (Markers missing?)", icon='QUESTION')


        # --- Marker Details List ---
        layout.separator()
        icon = 'TRIA_DOWN' if wm.show_retime_markers else 'TRIA_RIGHT'
        layout.prop(wm, "show_retime_markers", text="Show Marker Details", toggle=True, icon=icon, emboss=False)

        if wm.show_retime_markers:
            box = layout.box()
            markers = get_ordered_markers(scene)
            if not markers: box.label(text="No 'RT_' markers found.", icon='INFO')
            else:
                for marker in markers:
                    row = box.row(align=True); row.scale_y = 0.9
                    # Jump Button
                    op_jump = row.operator("animation_retimer.select_marker", text="", icon='RESTRICT_SELECT_OFF')
                    op_jump.marker_name = marker.name
                    # Name/Frame Property (disable editing during retime)
                    row_prop = row.row() # Sub-row to disable only the prop
                    row_prop.enabled = not wm.retimer_active
                    row_prop.prop(marker, "frame", text=marker.name)
                    # Delete Button (disabled via poll)
                    op_del = row.operator("animation_retimer.delete_marker", text="", icon='X', emboss=False)
                    op_del.marker_name = marker.name


# --- Registration ---

classes = (
    ANIMATION_RETIMER_OT_AddMarker,
    ANIMATION_RETIMER_OT_RetimeMarker,
    ANIMATION_RETIMER_OT_ApplyRetiming,
    ANIMATION_RETIMER_OT_CancelRetiming,
    ANIMATION_RETIMER_OT_DeleteMarker,
    ANIMATION_RETIMER_OT_ClearMarkers,
    ANIMATION_RETIMER_OT_SelectMarker,
    ANIMATION_RETIMER_PT_Panel,
)

def register():
    for cls in classes:
        try: bpy.utils.register_class(cls)
        except ValueError: pass # Already registered

    props = {
        "retimer_active": bpy.props.BoolProperty(name="Retimer Active", default=False),
        "retimer_snap_frames": bpy.props.BoolProperty(name="Snap Keys to Whole Frames", default=True),
        "show_retime_markers": bpy.props.BoolProperty(name="Show Markers List", default=True)
    }
    for name, prop in props.items():
         if not hasattr(bpy.types.WindowManager, name):
              setattr(bpy.types.WindowManager, name, prop)

    print("Animation Retimer (Multi-Object) Registered")


def unregister():
    print("Unregistering Animation Retimer (Multi-Object)...")
    retimer_data.clear() # Clear data on unregister

    for cls in reversed(classes):
        try: bpy.utils.unregister_class(cls)
        except RuntimeError: pass # Not registered

    props_to_delete = ["retimer_active", "retimer_snap_frames", "show_retime_markers"]
    for prop in props_to_delete:
        if hasattr(bpy.types.WindowManager, prop):
            try: delattr(bpy.types.WindowManager, prop)
            except Exception: pass

    print("Animation Retimer (Multi-Object) Unregistered")


if __name__ == "__main__":
    try: unregister()
    except Exception: pass
    register()
