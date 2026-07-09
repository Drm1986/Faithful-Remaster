# Faithful Remaster v11.10.22 — GitHub Release + Full Tutorial

This release prepares Faithful Remaster for a stronger public GitHub launch. It is built on v11.10.21 Version Sync and keeps the processing pipeline and bundled workflow JSON files unchanged.

## Main purpose

v11.10.22 turns the package into a cleaner public release:

- Reworked `README.md` as a proper GitHub landing page.
- Added a full getting-started tutorial for new users.
- Added a GitHub release checklist for the maintainer.
- Added a copy-paste release body.
- Added a video showcase plan for after the release is validated.
- Updated version metadata to `v11.10.22`.

## Preserved from recent builds

- Version Sync: app title/header follows the bundled `VERSION` file.
- Batch Queue Skip and Previous navigation.
- Live Texture Manager auto-refresh while watching or batching.
- Existing/Missing/Orphaned output detection.
- Azahar metadata action visibility limited to Azahar profiles.
- Alpha route protection.
- EFB/cutscene quarantine support.
- Clean Heart and Strong Believer workflow profiles.
- N64 Strip Safe routing.

## What did not change

- No workflow JSON files changed.
- No RGB processing logic changed.
- No Alpha processing logic changed.
- No cache hash behavior changed.
- No emulator folder routing behavior changed.

## Recommended validation

Before using this as the public release, confirm:

- The app header shows `v11.10.22`.
- The ZIP filename shows `v11.10.22`.
- A temporary profile validates successfully.
- ComfyUI test succeeds.
- Texture Manager counters update during watching.
- Batch Queue can advance, skip, go previous, and stop safely.

## Download

Download the Windows ZIP from the GitHub release assets, extract it, then run:

```text
Faithful Remaster.exe
```
