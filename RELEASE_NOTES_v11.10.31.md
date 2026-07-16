# Faithful Remaster v11.10.31

This update focuses on DuckStation support, Texture Manager usability, duplicate cleanup, and profile-specific safety options.

## Highlights

- **DuckStation live duplicate guard**
- **FF8-friendly texture handling**
- **Faster Texture Manager scanning**
- **Vertical scrolling in Texture Manager**
- **ST/STP cleanup tools for DuckStation profiles**
- **Batch Queue Skip and Previous controls**
- **Platform-specific cleanup options now appear only where relevant**
- **Safer quarantine-based cleanup before textures are sent to ComfyUI**

## Notes

### DuckStation / FF8

- Thin background strips are preserved.
- Repeated texture uploads are handled conservatively.
- ST/STP textures are not removed globally.

### Dolphin / PPSSPP

- Cinematic and cutscene cleanup options remain available only for relevant profiles.

### Dolphin

- Sparse alpha duplicate cleanup remains Dolphin-only.
