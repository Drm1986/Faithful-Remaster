# Faithful Remaster v11.10.22 — GitHub Release + Full Tutorial

This package is focused on making **Faithful Remaster** easier to download, understand, test, and publish publicly.

It is built on **v11.10.21 Version Sync** and preserves the current processing behavior.

---

## Why this release matters

Previous builds were mostly stability, workflow-safety, and batch-queue improvement builds.

**v11.10.22** is the first package prepared as a serious public GitHub release, with stronger documentation, a full beginner tutorial, release notes, and a guided onboarding path for new users.

---

## Required ComfyUI models for the bundled workflows

Faithful Remaster does **not** include ComfyUI model weights. If you use the bundled workflows, install these files first:

```text
ComfyUI/models/upscale_models/4x-UltraSharpV2.safetensors
ComfyUI/models/upscale_models/RealESRGAN_x4plus.safetensors
ComfyUI/models/controlnet/controlnet-tile-sdxl-1.0.safetensors
ComfyUI/models/checkpoints/dreamshaperXL_lightningDPMSDE.safetensors
```

Read the full setup guide after extracting the ZIP:

```text
docs/COMFYUI_MODEL_REQUIREMENTS.md
docs/GETTING_STARTED_TUTORIAL.md
```

Note: the bundled RGB workflows currently reference `dreamshaperXL_lightningDPMSDE.safetensors`. Juggernaut XL can be used only after editing the workflow checkpoint node and exporting/updating the matching API workflow.

---

## New in v11.10.22

- Full GitHub-ready `README.md`.
- Complete beginner tutorial: `docs/GETTING_STARTED_TUTORIAL.md`.
- Full ComfyUI model requirement guide: `docs/COMFYUI_MODEL_REQUIREMENTS.md`.
- Maintainer publishing checklist: `docs/GITHUB_RELEASE_CHECKLIST.md`.
- Copy-paste release body for GitHub.
- Version updated to `v11.10.22` across the package source.

---

## Recent build history

### v11.10.22 — GitHub Release + Full Tutorial

- Prepared the project for public GitHub release.
- Added stronger documentation and onboarding files.
- Added explicit ComfyUI model requirements for bundled workflows.
- No workflow JSON changes.
- No core processing logic changes.

### v11.10.21 — Version Sync

- Fixed version mismatch between the app source and universal UI layer.
- Added a single `VERSION` source.
- Ensured the visible app title and package version match.

### v11.10.20 — Batch Queue Previous Game

- Added Batch Queue **Previous game** support.
- Improved manual navigation during batch review and testing.

### v11.10.19 — Batch Queue Skip Hardening

- Hardened **Skip to next game** behavior.
- Improved safer transition between games/packs during batch runs.
- Reduced end-of-pack queue errors.

### v11.10.18 — Batch Queue Skip Button

- Added **Skip to next game** control for Batch Queue workflows.
- Focused on user control during long batch processing sessions.

### v11.10.17 — UI Version + Azahar Visibility

- Improved UI version visibility.
- Restricted **Refresh Azahar metadata** action to Azahar profiles only.
- Continued UI cleanup around profiles and advanced settings.

### v11.10.16 — Live Texture Manager Refresh

- Added live refresh behavior for Texture Manager / Asset Browser.
- Reduced the need for manual refresh while dumps are being created.
- Improved detection of newly dumped or changed texture files.

---

## Preserved stability features

- Existing / Missing / Orphaned output filters.
- Azahar-only metadata action visibility.
- Profile validation improvements.
- Alpha route guard.
- EFB / cutscene quarantine support.
- Workflow selection safety.
- Cache hash stability.
- Clean Heart, Strong Believer, and N64 Strip Safe workflow support.
- Batch Queue Skip and Previous navigation.
- Version Sync.

---

## Important note

This release **does not modify the bundled workflow JSON files** and **does not change the core processing logic**.

It is a release-readiness, documentation, tutorial, and packaging release.

---

## Quick start

1. Download the Windows ZIP asset below.
2. Extract it to a normal folder.
3. Install the required ComfyUI models listed above.
4. Run `Faithful Remaster.exe`.
5. Start ComfyUI and confirm the API URL is correct.
6. Read the tutorial inside:

   `docs/GETTING_STARTED_TUTORIAL.md`

---

## Recommended first test

Use one small profile first.

Validate the profile, test ComfyUI, process a few textures, and confirm the output appears correctly in your emulator before starting a large Batch Queue run.

---

## Download

Download:

`Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip`

Optional checksum file:

`Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip.sha256.txt`
