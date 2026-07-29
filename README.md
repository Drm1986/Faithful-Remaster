# Faithful Remaster v11.10.43

Faithful Remaster is a Windows texture-remastering workspace for emulator texture packs. It watches emulator dump folders, sends textures through replaceable ComfyUI workflows, writes finished files into the emulator load/replacement folder, and helps you audit missing, existing, orphaned, quarantined, cached, and ready textures.

Faithful Remaster is not an emulator, not a model downloader, and not a one-click guarantee that every texture in every game will look perfect. It is a controlled production workspace for texture-pack creation.

## Announcement / showcase video

Watch the current showcase and tutorial video:

https://www.youtube.com/watch?v=7NyKl2O9K_I&t=271s

## Download

Latest version: **Faithful Remaster v11.10.43**

Download the Windows ZIP from the latest GitHub Release and extract it into a normal folder, for example:

```text
D:\Tools\Faithful Remaster\
```

Then run:

```text
Faithful Remaster.exe
```

If Windows blocks the app because it is unsigned, choose **More info → Run anyway** only if you downloaded it from the official release page.

## What this tool is for

Use Faithful Remaster when you want to:

- monitor a game's dump folder while playing;
- remaster newly dumped textures automatically;
- preserve original emulator filenames and folder structure;
- compare output modes before replacing game textures;
- build multiple game packs through Batch Queue;
- review missing, existing, orphaned, and quarantined outputs;
- protect known problematic dumps from accidental processing.

## Highlights in v11.10.43

- Fixed black RGB textures with useful alpha data being mistaken for empty/blank dumps.
- Protected alpha-only glyphs, letters, masks, and transparent overlays from safe blank cleanup.
- Improved Azahar / 3DS framebuffer cleanup, including padded 400x240-in-512x256 dumps and fade frames.
- Reduced false positives in Azahar cleanup by preserving colored padded texture atlases.
- Improved missing-output detection after cleanup, so real dumps without load outputs can be rebuilt.
- Fixed Texture Manager mass mode changes so multi-selected textures update correctly.
- Restored EFB / cutscene cleanup controls across all profiles.
- Improved N64/RMG game-title detection and cleaned up the N64 database source path.
- Fixed queue handling so existing outputs are not sent to ComfyUI again.
- Added Texture Manager copy helpers for filenames and paths.

## Quick start

1. Start ComfyUI and confirm the API is reachable at:

```text
http://127.0.0.1:8188
```

2. Open Faithful Remaster.
3. Create or discover a game profile.
4. Set the emulator Dump folder and Load/Replacement folder.
5. Choose the bundled RGB and Alpha API workflows.
6. Press **Validate Profile**.
7. Press **Test ComfyUI**.
8. Enable texture dumping in your emulator and play until textures appear.
9. Return to Faithful Remaster and press **Start Watching**.

For the full walkthrough, read `docs/GETTING_STARTED_TUTORIAL.md`.

## Required ComfyUI setup

Faithful Remaster does not bundle ComfyUI, model checkpoints, upscalers, or ControlNet models. Install ComfyUI separately, then add the models required by the workflow you choose.

The bundled example workflows are designed around faithful 4x texture restoration and may use:

- ControlNet Tile SDXL;
- RealESRGAN / UltraSharp-style upscalers;
- the standard custom nodes required by your selected workflow.

Use the workflow notes and tutorial for exact setup.

## Supported emulator profiles

Faithful Remaster supports profile-based texture workflows for multiple emulators, including:

- Dolphin — Nintendo Wii / GameCube
- PPSSPP — PlayStation Portable
- PCSX2 — PlayStation 2
- DuckStation — PlayStation 1
- Azahar / Citra-style 3DS texture folders
- RMG / Project64 — Nintendo 64
- Flycast — Sega Dreamcast

## Notes by platform

### DuckStation / FF8

- Repeated texture uploads are handled conservatively.
- Thin background strips are preserved.
- ST/STP textures are not globally removed.
- Duplicate cleanup is limited to safe matches or manual quarantine actions.

### Azahar / 3DS

- Padded framebuffer dumps and fade frames can be quarantined safely.
- Colored padded texture atlases are protected from EFB cleanup.
- Alpha glyph/mask textures are preserved and no longer treated as empty dumps.

### Dolphin / PPSSPP / other profiles

- EFB and cutscene cleanup controls are visible across profiles.
- Cleanup actions use quarantine instead of permanent deletion.

## Repository

- Main app: `faithful_remaster.py`
- Universal helpers: `faithful_universal.py`
- Bundled workflows: `workflows/`
- Documentation: `docs/`
- Changelog: `CHANGELOG.md`
- Version: `VERSION`

## License

See `LICENSE`.
