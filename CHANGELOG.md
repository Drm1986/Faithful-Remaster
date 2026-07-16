# Changelog

## Faithful Remaster v11.10.31

### Added
- DuckStation live duplicate guard.
- FF8-friendly texture handling.
- ST/STP cleanup tools for DuckStation profiles.
- Batch Queue Skip and Previous controls.

### Improved
- Faster Texture Manager scanning.
- Vertical scrolling in Texture Manager.
- Safer duplicate cleanup before textures are sent to ComfyUI.
- Platform-specific cleanup options now appear only where relevant.

### Notes
- Thin FF8 background strips are preserved.
- Repeated DuckStation texture uploads are handled conservatively.
- ST/STP textures are not removed globally.
- Cinematic and cutscene cleanup options are limited to Dolphin and PPSSPP profiles.
- Sparse alpha duplicate cleanup remains Dolphin-only.
# Faithful Remaster Changelog

## v11.10.31

### Added
- DuckStation-specific duplicate detection.
- Live duplicate guard while watching new DuckStation texture dumps.
- FF8-safe handling for repeated DuckStation texture uploads.
- Texture Manager actions for ST/STP cleanup in DuckStation profiles.
- Vertical scrolling in Texture Manager.
- Batch Queue controls: Skip to next game and Previous game.

### Improved
- Texture Manager scan speed on large dump folders.
- DuckStation duplicate cleanup now runs before textures are sent to ComfyUI.
- FF8 thin background strips are preserved instead of being treated as garbage.
- ST/STP textures are not globally removed; only safe duplicate matches are handled automatically.
- Cleanup options are now shown only for relevant emulator profiles.
- Cleanup actions move files to quarantine instead of permanently deleting them.

### Fixed
- Texture Manager action buttons could be inaccessible on smaller screens.
- Batch Queue state handling around skip, previous, and profile switching.
- Reduced unnecessary ComfyUI jobs caused by repeated DuckStation dumps.

## v11.10.30

- Added vertical scrolling to the main Texture Manager body.
- Improved access to Texture Manager actions on compact screens.

## v11.10.29

- Added manual ST/STP mass quarantine actions for DuckStation profiles.
- Added Texture Manager cleanup actions for dump and replacement folders.

## v11.10.28

- Added duplicate-only ST/STP guard.
- Kept non-duplicated ST/STP textures active.
- Added replacement-folder duplicate cleanup support.

## v11.10.27

- Added pre-remaster quarantine for matching ST/STP texpage duplicates.
- Quarantine stores restore metadata and remains reversible.

## v11.10.26

- Improved Texture Manager fast scanning for large dump folders.
- Deferred expensive visual classification until needed.

## v11.10.25

- Preserved Texture Manager selection during manual and automatic refresh.
- Reduced selection jumps while watching.

## v11.10.24

- Added sparse alpha/mask quarantine on Start Watching and Batch Queue profile start.
- Added live fallback quarantine for sparse alpha/mask dumps.

## v11.10.23

- Added conservative detection for tiny sparse alpha/mask dumps.
- Added fallback skip behavior for sparse alpha/mask textures.

## v11.10.22

- Prepared the first public GitHub release package.
- Added stronger README, tutorial, release checklist, and ComfyUI setup notes.
- No bundled workflow JSON files were changed.

