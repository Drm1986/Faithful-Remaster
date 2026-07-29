# Changelog

## Faithful Remaster v11.10.43

This update focuses on safer cleanup, Azahar / 3DS framebuffer handling, Texture Manager batch behavior, and more reliable queue/output detection.

### Fixed

- Fixed black RGB textures with useful alpha data being mistaken for empty or blank dumps.
- Protected alpha-only glyphs, letters, masks, and overlays from safe blank cleanup.
- Fixed a case where real dumps with missing outputs could remain skipped after cleanup.
- Fixed Texture Manager mass mode changes affecting only the first selected item visually.
- Fixed stale queue behavior where already-existing outputs could still be sent to ComfyUI.
- Fixed `WindowsPath is not JSON serializable` after existing-output detection.
- Fixed N64 database source path and reduced legacy 404 noise.

### Improved

- Improved Azahar / 3DS padded framebuffer detection:
  - 512x256 dumps containing a 400x240 viewport;
  - white fade frames;
  - black fade frames;
  - dark transition frames;
  - washed-out fade frames.
- Added false-positive protection so colored padded texture atlases are preserved.
- Improved missing-output detection after quarantine / cleanup.
- Restored EFB / cutscene cleanup controls across profiles.
- Improved N64/RMG title detection using current No-Intro database paths and fuzzy folder-name matching.
- Added Texture Manager copy helpers:
  - copy texture filename;
  - copy original path;
  - copy output path;
  - copy all details.

### Notes

- Azahar / 3DS cleanup is now safer for Ocarina of Time 3D-style dumps.
- Alpha masks and glyph sheets should no longer be treated as blank files.
- Existing outputs should not be reprocessed unnecessarily.

## Faithful Remaster v11.10.42

- Missing Load Check now uses stricter output checks for the active profile.
- Processed entries with missing outputs can be requeued correctly.
- Sparse alpha / mask dumps are no longer hidden from missing-output checks merely because they are mask-like.

## Faithful Remaster v11.10.41

- Added false-positive protection for Azahar padded texture atlases.
- Colored/brown padded 512x256 artwork textures are preserved instead of being quarantined as framebuffers.

## Faithful Remaster v11.10.40

- Added broader Azahar padded framebuffer detection, even when the profile emulator value is generic or missing.
- Detected common `tex1_512x256_..._1_mip0.png` 3DS framebuffer patterns.

## Faithful Remaster v11.10.39

- Added Azahar fade-frame detection for white, black, dark, and low-variation transition buffers.

## Faithful Remaster v11.10.38

- Added Azahar padded 3DS framebuffer detection for 512x256 dumps containing a 400x240 viewport.

## Faithful Remaster v11.10.37

- Fixed mass changing texture modes in Texture Manager so multi-select changes refresh correctly.

## Faithful Remaster v11.10.36

- Restored EFB / cutscene cleanup settings visibility across all profiles.

## Faithful Remaster v11.10.35

- Fixed N64 database source path.
- Improved RMG / Project64 game-title detection.
- Added fuzzy N64 folder-title matching.

## Faithful Remaster v11.10.34

- Fixed `WindowsPath is not JSON serializable` crash.
- Added final existing-output guard before ComfyUI submission.
- Purged already-existing outputs from queues.

## Faithful Remaster v11.10.33

- Added Texture Manager copy helpers for filenames and paths.

## Faithful Remaster v11.10.32

- Improved existing-output detection when adopting existing Dolphin load packs.
- Updated N64 database path handling.

## Faithful Remaster v11.10.31

- Added DuckStation / FF8-friendly duplicate handling.
- Improved Texture Manager scanning and scrolling.
- Added Batch Queue Skip and Previous controls.
- Improved quarantine-based cleanup safety.
