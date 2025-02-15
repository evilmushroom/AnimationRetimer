bl_info = {
    "name": "Animation Retimer",
    "blender": (3, 6, 0),
    "category": "Animation",
    "description": "Scale animation between markers with stable key merging"
}

import bpy
from collections import defaultdict
import copy  # Import deep copy module

retimer_data = {
    "original_markers": {},
    "original_keyframes": {},
    "original_start": 0,
    "original_end": 0,
    "original_segments": [],
    "_temp_keyframe_data": {}
}

class ANIMATION_RETIMER_OT_AddMarker(bpy.types.Operator):
    """Add a new retiming marker at the current frame
    
    Creates a marker that can be used to control animation timing.
    These markers define segments that can be stretched or compressed"""
    bl_idname = "animation_retimer.add_marker"
    bl_label = "Add Retime Marker"
    bl_options = {'REGISTER', 'UNDO'}  # Added UNDO support

    def execute(self, context):
        frame = context.scene.frame_current
        marker = context.scene.timeline_markers.new(name=f"RT_{frame}", frame=frame)
        retimer_data["original_markers"][marker.name] = marker.frame
        return {'FINISHED'}

def get_ordered_markers(scene):
    """Return markers sorted by frame number"""
    return sorted(
        [m for m in scene.timeline_markers if m.name.startswith("RT_")],
        key=lambda m: m.frame
    )

def find_segment(frame, segments):
    """Find which segment a frame belongs to"""
    for i, (start, end) in enumerate(segments):
        if start <= frame <= end:
            return i
    return -1

class ANIMATION_RETIMER_OT_RetimeMarker(bpy.types.Operator):
    """Start or stop the retiming process
    
    When active, allows you to adjust the timing of your animation by moving markers.
    The animation will update in real-time to preview the changes"""
    bl_idname = "animation_retimer.retime_marker"
    bl_label = "Toggle Retime"
    bl_options = {'REGISTER', 'UNDO'}  # Already had UNDO support

    _timer = None
    _last_marker_positions = {}
    _temp_keyframe_data = {}

    def store_initial_keyframe_data(self, action):
        """Store complete initial keyframe data with unique per-axis storage"""
        self._temp_keyframe_data = {}
        retimer_data["_temp_keyframe_data"] = {}

        for fcurve in action.fcurves:
            key = (fcurve.data_path, fcurve.array_index)
            keyframe_data = [
                (kp.co.x, kp.co.y, kp.interpolation, copy.deepcopy(kp.handle_left), copy.deepcopy(kp.handle_right))
                for kp in fcurve.keyframe_points
            ]
            self._temp_keyframe_data[key] = keyframe_data
            retimer_data["_temp_keyframe_data"][key] = keyframe_data

        print("Stored keyframe data:", self._temp_keyframe_data)

    def restore_from_original(self, action):
        """Restore keyframes from original data safely per F-Curve with axis awareness"""
        for fcurve in action.fcurves:
            key = (fcurve.data_path, fcurve.array_index)  # Use unique key
            
            if key not in self._temp_keyframe_data:
                print(f"Warning: Missing original keyframe data for {key}. Skipping...")
                continue  # Skip missing F-Curve instead of crashing
            
            # Remove all current keyframes
            while len(fcurve.keyframe_points) > 0:
                fcurve.keyframe_points.remove(fcurve.keyframe_points[0])
            
            # Add back original keyframes
            for x, y, interp, hl, hr in self._temp_keyframe_data[key]:
                kp = fcurve.keyframe_points.insert(x, y)
                kp.interpolation = interp
                kp.handle_left = copy.deepcopy(hl)
                kp.handle_right = copy.deepcopy(hr)
            
            fcurve.update()

    def process_retiming(self, context):
        wm = context.window_manager
        obj = context.object
        if not obj or not obj.animation_data or not obj.animation_data.action:
            return

        markers = get_ordered_markers(context.scene)
        if len(markers) < 2:
            return

        # Get marker positions
        current_positions = {m.name: m.frame for m in markers}

        if current_positions == self._last_marker_positions:
            return

        self._last_marker_positions = current_positions.copy()

        # Sort markers to ensure order
        original_markers = sorted(
            [(name, retimer_data["original_markers"][name]) for name in retimer_data["original_markers"]],
            key=lambda x: x[1]
        )

        # Create segment mappings
        original_segments = []
        current_segments = []
        for i in range(len(markers) - 1):
            orig_start, orig_end = original_markers[i][1], original_markers[i + 1][1]
            curr_start, curr_end = markers[i].frame, markers[i + 1].frame

            original_segments.append((orig_start, orig_end))
            current_segments.append((curr_start, curr_end))

        # Edge Case Handling: Check for marker inversion
        for i in range(len(markers) - 1):
            curr_start, curr_end = markers[i].frame, markers[i + 1].frame
            if curr_start >= curr_end:
                self.report({'INFO'}, "Segment collapsed - animation will be compressed")
                # Remove return statement to allow processing to continue

        action = obj.animation_data.action
        self.restore_from_original(action)  # Ensure we're modifying original keyframes

        # Adjust keyframes based on marker positions
        for fcurve in action.fcurves:
            key = (fcurve.data_path, fcurve.array_index)  # Use the correct key for each axis
            new_keys = []
            
            for orig_x, orig_y, interp, hl, hr in self._temp_keyframe_data[key]:
                segment_idx = find_segment(orig_x, original_segments)

                if segment_idx == -1:
                    if orig_x < original_segments[0][0]:  # Before first marker
                        offset = current_segments[0][0] - original_segments[0][0]
                        new_x = orig_x + offset
                    else:  # After last marker
                        offset = current_segments[-1][1] - original_segments[-1][1]
                        new_x = orig_x + offset
                else:
                    # Rescale within segment
                    orig_start, orig_end = original_segments[segment_idx]
                    curr_start, curr_end = current_segments[segment_idx]

                    # Prevent division by zero
                    orig_length = max(orig_end - orig_start, 0.001)
                    curr_length = max(curr_end - curr_start, 0.001)
                    
                    # Calculate position proportionally
                    position_in_segment = (orig_x - orig_start) / orig_length
                    new_x = curr_start + (position_in_segment * curr_length)

                if wm.retimer_snap_frames:
                    new_x = round(new_x)

                new_keys.append((new_x, orig_y, interp, hl, hr))

            # Apply updated keyframes
            fcurve.keyframe_points.clear()
            for x, y, interp, hl, hr in sorted(new_keys, key=lambda k: k[0]):
                kp = fcurve.keyframe_points.insert(x, y)
                kp.interpolation = interp
                kp.handle_left = hl
                kp.handle_right = hr
            
            fcurve.update()

        # Fix: Prevent excessive merging of keyframes
        if wm.retimer_snap_frames:
            for fcurve in action.fcurves:
                seen_frames = {}
                to_remove = []

                for idx, kp in enumerate(fcurve.keyframe_points):
                    frame = round(kp.co.x)
                    if frame in seen_frames:
                        # Instead of averaging, keep only one key per frame
                        to_remove.append(idx)
                    else:
                        seen_frames[frame] = kp

                for idx in reversed(to_remove):
                    fcurve.keyframe_points.remove(fcurve.keyframe_points[idx])

                fcurve.update()

    def modal(self, context, event):
        wm = context.window_manager
        if not wm.retimer_active:
            self.cancel(context)
            return {'CANCELLED'}
        if event.type == 'ESC':
            # Restore original keyframes on cancel
            self.restore_original_keyframes(context)
            wm.retimer_active = False
            self.cancel(context)
            return {'CANCELLED'}
        if event.type == 'TIMER':
            self.process_retiming(context)
        return {'PASS_THROUGH'}

    def restore_original_keyframes(self, context):
        obj = context.object
        if not obj or not obj.animation_data or not obj.animation_data.action:
            return

        action = obj.animation_data.action
        for fcurve in action.fcurves:
            # Store current keyframes
            orig_keyframes = retimer_data["original_keyframes"].get(fcurve.data_path, [])
            
            # Clear existing keyframes
            while len(fcurve.keyframe_points) > 0:
                fcurve.keyframe_points.remove(fcurve.keyframe_points[0])
            
            # Restore original keyframes
            for x, y in orig_keyframes:
                kp = fcurve.keyframe_points.insert(x, y)
            
            fcurve.update()

    def execute(self, context):
        wm = context.window_manager
        if not wm.retimer_active:
            markers = get_ordered_markers(context.scene)
            if len(markers) < 2:
                self.report({'ERROR'}, "Add at least 2 markers first!")
                return {'CANCELLED'}

            retimer_data["original_markers"] = {m.name: m.frame for m in markers}
            retimer_data["original_start"] = markers[0].frame
            retimer_data["original_end"] = markers[-1].frame
            retimer_data["original_segments"] = [
                (markers[i].frame, markers[i+1].frame) 
                for i in range(len(markers)-1)
            ]

            obj = context.object
            if obj and obj.animation_data and obj.animation_data.action:
                # Store initial keyframe data before entering modal mode
                self.store_initial_keyframe_data(obj.animation_data.action)
                retimer_data["original_keyframes"] = {
                    fcurve.data_path: [(kp.co.x, kp.co.y) for kp in fcurve.keyframe_points]
                    for fcurve in obj.animation_data.action.fcurves
                }
            else:
                self.report({'ERROR'}, "No animated object selected!")
                return {'CANCELLED'}

            self._timer = wm.event_timer_add(0.1, window=context.window)
            wm.modal_handler_add(self)
            wm.retimer_active = True
            return {'RUNNING_MODAL'}
        else:
            wm.retimer_active = False
            self.cancel(context)
            return {'FINISHED'}

    def cancel(self, context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
        self._timer = None
        wm.retimer_active = False

def make_marker_distinct(marker):
    """Ensure the marker is visually distinct"""
    marker.name = f"RT_{marker.frame}"  # Prefix all retime markers

def lock_retiming_markers():
    """Prevent accidental renaming of retime markers"""
    for marker in bpy.context.scene.timeline_markers:
        if marker.name.startswith("RT_"):
            marker.select = False  # Prevent accidental selection
            marker.color = 'RETIME'  # Set color for retime markers

class ANIMATION_RETIMER_PT_Panel(bpy.types.Panel):
    bl_label = "Animation Retimer"
    bl_idname = "ANIMATION_RETIMER_PT_Panel"
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Retime Tools'

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        scene = context.scene
        
        # Main action buttons at the top
        layout.separator()
        if wm.retimer_active:
            row = layout.row()
            row.operator("animation_retimer.apply_retiming", text="Apply Changes", icon='CHECKMARK')
            row.operator("animation_retimer.cancel_retiming", text="Discard Changes", icon='X')
        else:
            layout.operator("animation_retimer.retime_marker", text="Start Retiming", icon='PLAY')
            
        layout.separator()
        
        # Marker management controls
        row = layout.row(align=True)
        row.operator("animation_retimer.add_marker", text="Add Marker", icon='ADD')
        row.operator("animation_retimer.clear_markers", text="Clear All", icon='TRASH')
        
        # Segments information (when active)
        if wm.retimer_active:
            markers = get_ordered_markers(scene)
            if len(markers) >= 2:
                layout.separator()
                box = layout.box()
                box.label(text="Segment Status:", icon='NLA')
                
                has_collapsed = False
                current_segments = [
                    (markers[i].frame, markers[i+1].frame) 
                    for i in range(len(markers)-1)
                ]
                
                for i, (curr_start, curr_end) in enumerate(current_segments):
                    row = box.row()
                    orig_start, orig_end = retimer_data["original_segments"][i]
                    orig_length = orig_end - orig_start
                    curr_length = curr_end - curr_start
                    
                    if orig_length > 0 and curr_length > 0:
                        ratio = curr_length / orig_length
                        speed_text = f"{ratio:.1f}x"
                        if ratio > 1:
                            icon = 'TRIA_DOWN'
                        else:
                            icon = 'TRIA_UP'
                    else:
                        speed_text = "Collapsed"
                        icon = 'INFO'
                        has_collapsed = True
                    
                    row.label(text=f"Segment {i+1}: {speed_text}", icon=icon)
        
        # Marker details toggle and list at the bottom
        layout.separator()
        layout.prop(wm, "show_retime_markers", text="Show Marker Details", toggle=True)
        
        if wm.show_retime_markers:
            box = layout.box()
            box.label(text="Marker Management:", icon='MARKER')
            markers = sorted([m for m in scene.timeline_markers if m.name.startswith("RT_")], 
                           key=lambda m: m.frame)
            
            for marker in markers:
                row = box.row(align=True)
                row.operator("animation_retimer.select_marker", 
                           text="", icon='RESTRICT_SELECT_OFF').marker_name = marker.name
                row.prop(marker, "frame", text=marker.name)
                row.operator("animation_retimer.delete_marker", 
                           text="", icon='X').marker_name = marker.name

class ANIMATION_RETIMER_OT_DeleteMarker(bpy.types.Operator):
    """Delete the selected retiming marker
    
    Removes a specific marker from the timeline.
    This will affect how the animation is retimed if the retiming process is active"""
    bl_idname = "animation_retimer.delete_marker"
    bl_label = "Delete Retiming Marker"
    bl_options = {'REGISTER', 'UNDO'}  # Added UNDO support
    
    marker_name: bpy.props.StringProperty()

    def execute(self, context):
        markers = context.scene.timeline_markers
        if self.marker_name in markers:
            markers.remove(markers[self.marker_name])
            if self.marker_name in retimer_data["original_markers"]:
                del retimer_data["original_markers"][self.marker_name]
        return {'FINISHED'}

class ANIMATION_RETIMER_OT_ClearMarkers(bpy.types.Operator):
    """Remove all retiming markers from the timeline
    
    Clears all RT_ prefixed markers, effectively resetting the retiming setup.
    This operation cannot be undone"""
    bl_idname = "animation_retimer.clear_markers"
    bl_label = "Clear All Retiming Markers"
    bl_options = {'REGISTER', 'UNDO'}  # Added UNDO support

    def execute(self, context):
        markers = context.scene.timeline_markers
        to_remove = [m for m in markers if m.name.startswith("RT_")]
        
        for marker in to_remove:
            markers.remove(marker)

        retimer_data["original_markers"].clear()
        return {'FINISHED'}

class ANIMATION_RETIMER_OT_SelectMarker(bpy.types.Operator):
    """Jump to the selected marker's position in the timeline
    
    Moves the timeline cursor to the frame where this marker is located.
    Useful for quick navigation between retiming points"""
    bl_idname = "animation_retimer.select_marker"
    bl_label = "Select Retiming Marker"
    bl_options = {'REGISTER', 'UNDO'}  # Added UNDO support
    
    marker_name: bpy.props.StringProperty()

    def execute(self, context):
        scene = context.scene
        markers = scene.timeline_markers

        if self.marker_name in markers:
            marker = markers[self.marker_name]
            scene.frame_current = marker.frame

        return {'FINISHED'}

class ANIMATION_RETIMER_OT_ApplyRetiming(bpy.types.Operator):
    """Apply the current retiming changes
    
    Finalizes the current retiming operation.
    This will make the timing changes permanent"""
    bl_idname = "animation_retimer.apply_retiming"
    bl_label = "Apply Retiming"
    bl_options = {'REGISTER', 'UNDO'}  # Added UNDO support
    
    def execute(self, context):
        context.window_manager.retimer_active = False
        return {'FINISHED'}

class ANIMATION_RETIMER_OT_CancelRetiming(bpy.types.Operator):
    """Discard all retiming changes
    
    Reverts the animation back to its original timing.
    All marker positions will be reset to their original locations"""
    bl_idname = "animation_retimer.cancel_retiming"
    bl_label = "Discard Changes"
    bl_options = {'REGISTER', 'UNDO'}  # Added UNDO support
    
    def execute(self, context):
        obj = context.object
        if obj and obj.animation_data and obj.animation_data.action:
            action = obj.animation_data.action
            
            if "_temp_keyframe_data" not in retimer_data:
                self.report({'ERROR'}, "No stored keyframe data found")
                return {'CANCELLED'}
            
            for fcurve in action.fcurves:
                key = (fcurve.data_path, fcurve.array_index)
                if key in retimer_data["_temp_keyframe_data"]:
                    while len(fcurve.keyframe_points) > 0:
                        fcurve.keyframe_points.remove(fcurve.keyframe_points[0])
                    
                    for x, y, interp, hl, hr in retimer_data["_temp_keyframe_data"][key]:
                        kp = fcurve.keyframe_points.insert(x, y)
                        kp.interpolation = interp
                        kp.handle_left = copy.deepcopy(hl)
                        kp.handle_right = copy.deepcopy(hr)
                
                fcurve.update()
        
        markers = get_ordered_markers(context.scene)
        for m in markers:
            if m.name in retimer_data["original_markers"]:
                m.frame = retimer_data["original_markers"][m.name]
        
        context.window_manager.retimer_active = False
        return {'FINISHED'}

classes = (
    ANIMATION_RETIMER_OT_AddMarker,
    ANIMATION_RETIMER_OT_RetimeMarker,
    ANIMATION_RETIMER_PT_Panel,
    ANIMATION_RETIMER_OT_DeleteMarker,
    ANIMATION_RETIMER_OT_ClearMarkers,
    ANIMATION_RETIMER_OT_SelectMarker,
    ANIMATION_RETIMER_OT_ApplyRetiming,
    ANIMATION_RETIMER_OT_CancelRetiming
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.retimer_active = bpy.props.BoolProperty(default=False)
    bpy.types.WindowManager.retimer_snap_frames = bpy.props.BoolProperty(
        name="Snap to Whole Frames",
        default=True
    )
    bpy.types.WindowManager.show_retime_markers = bpy.props.BoolProperty(name="Show Markers List", default=True)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.retimer_active
    del bpy.types.WindowManager.retimer_snap_frames
    del bpy.types.WindowManager.show_retime_markers

if __name__ == "__main__":
    register()