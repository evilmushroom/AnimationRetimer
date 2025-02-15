# Animation Retimer Addon (for Blender 3.6+)

**Animation Retimer** is a Blender addon that allows users to adjust animation timing dynamically using timeline markers. It enables smooth retiming while preserving keyframe integrity, making it easier to stretch or compress animations without manual keyframe adjustments.

---

## Features

- **Add Retiming Markers**: Place markers to define animation segments.
- **Interactive Retiming**: Drag markers to stretch or compress animation timing.
- **Keyframe Preservation**: Maintains keyframe interpolation and structure.
- **Undo Support**: Easily revert changes if needed.
- **Real-Time Updates**: See animation timing adjustments instantly.
- **Apply or Discard Changes**: Finalize edits or reset to the original timing.
- **User-Friendly UI**: Integrated directly into the Dope Sheet Editor.

---

## Installation

### 1. Download
   Download the `animation_retimer.py` file from this repository.

### 2. Install in Blender
   - Open Blender.  
   - Go to `Edit > Preferences > Add-ons`.  
   - Click `Install...`, select the `animation_retimer.py` file, and enable it.

### 3. Access the Addon
   - Open the **Dope Sheet Editor**.
   - Navigate to the `Retime Tools` panel in the **UI region**.

---

## Usage Instructions

1. **Open the Dope Sheet Editor** and switch to the `Retime Tools` panel.
2. **Use the following operations:**
   - *Add Retiming Marker*: Click "Add Marker" to place a marker at the current frame.
   - *Start Retiming*: Click "Start Retiming" to enable interactive adjustment.
   - *Move Markers*: Drag markers along the timeline to modify animation timing.
   - *Apply Changes*: Click "Apply Changes" to finalize the adjustments.
   - *Discard Changes*: Click "Discard Changes" to revert to the original timing.
   - *Clear Markers*: Remove all retiming markers.
3. **Watch the animation timing update in real time as markers are moved.**

---

## Requirements

- **Blender Version**: 3.6.0 or later

---

## Known Limitations

- Requires at least **two markers** to define an adjustable animation segment.
- Moving markers too close together may cause **animation compression issues**.
- Only works with **keyframe-based animations**; does not affect simulations or baked animations.

---

## Contribution

Contributions are welcome! If you’d like to suggest improvements, report bugs, or add new features:

1. **Fork this repository**.
2. **Create a new branch** for your changes.
3. **Submit a pull request**.

---

## License

This addon is licensed under the **GNU General Public License v3**.

