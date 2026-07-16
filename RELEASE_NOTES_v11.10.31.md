# Faithful Remaster v11.10.31

This release updates the public Windows package and documentation from v11.10.22 to v11.10.31.

## Main changes

- DuckStation live duplicate guard.
- FF8-friendly texture handling.
- Faster Texture Manager scanning.
- Texture Manager vertical scrolling.
- ST/STP cleanup tools for DuckStation profiles.
- Batch Queue Skip and Previous controls.
- Platform-specific cleanup options.
- Safer quarantine-based cleanup before textures are sent to ComfyUI.

## Notes

- Thin FF8 background strips are preserved.
- Repeated DuckStation texture uploads are handled conservatively.
- ST/STP textures are not removed globally.
- Cinematic and cutscene cleanup options remain limited to Dolphin and PPSSPP profiles.
- Sparse alpha duplicate cleanup remains Dolphin-only.
