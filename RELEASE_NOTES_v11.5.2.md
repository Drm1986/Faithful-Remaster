# Faithful Remaster v11.5.2 Beta

Faithful Remaster is now available for public beta testing and community workflow development.

## New in v11.5.2

- ComfyUI status appears directly beside **Check Comfy Now**.
- Clear `Checking…`, `Online`, and `Offline` states.
- Displays ComfyUI URL, response time, queue state, or the failure reason.
- Manual checks run in a background thread, keeping the UI responsive.
- Automatic checks update the same inline indicator.
- Results continue to appear in Monitor logs.

## We need community help

Please help with:

- Testing more games and emulators
- Improving the RGB workflow
- Improving the separate alpha workflow
- Comparing upscalers, checkpoints, ControlNet settings, samplers, and denoise values
- Reporting reproducible folder and texture-loading issues

## Existing major features

- Dolphin, DuckStation, PCSX2, PPSSPP, and Azahar support
- Emulator-specific game profiles
- Live Original / Enhanced preview
- Integrated Monitor logs
- Separate alpha workflow enabled by default
- Azahar `pack.json` synchronization
- Persistent settings, database, profiles, cache, and logs

## Requirements

- Windows
- Python 3.10+
- Pillow
- ComfyUI
- Required models listed in the README

This is a beta release. Keep backups and test a small number of textures first. No ROMs, ISOs, copyrighted game assets, or model weights are included.


## v11.5.2 hotfix

- Fixed `Check Comfy Now` staying on `Not checked`.
- Online/Offline status now updates beside the button.
- The response URL, timing, queue information, or error reason is shown inline.
- Manual check results are also written to Monitor logs.
- Automatic checks no longer block the UI.
