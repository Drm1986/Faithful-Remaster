# Faithful Remaster v11.10.25 — Selection-Preserving Texture Manager Refresh

This release fixes a Texture Manager annoyance introduced by live refresh: when the list refreshed while watching, the current selection could be lost and the UI could jump back to the first texture.

The Texture Manager now preserves the selected texture and scroll position across automatic and manual list rebuilds.

---

## New in v11.10.25

- Fixed Texture Manager refresh jumping back to the first texture.
- Preserves selected texture path(s) after refresh when those textures are still visible.
- Preserves the scroll/viewport neighborhood if the selected item disappeared.
- Keeps first-row auto-selection only for the first empty load.
- No bundled workflow JSON files were changed.
- No ComfyUI workflow behavior was changed.

---

## Why this matters

Live refresh is useful during watching and Batch Queue work, but it should not interrupt manual review.

Before this fix, selecting a texture for inspection could be frustrating because the next refresh could rebuild the list and select the first texture again. That made large folders painful to review.

v11.10.25 keeps the user focused on the texture they are currently inspecting.

---

## Preserved from v11.10.24

- Startup quarantine for tiny sparse alpha/mask dumps.
- Sparse alpha/mask live quarantine fallback.
- Reversible quarantine folder with metadata.
- Batch Queue integration for startup quarantine.

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

---

## Recent build history

### v11.10.25 — Selection-Preserving Texture Manager Refresh

- Fixed Texture Manager refresh jumping back to the first texture.
- Preserved selected texture and viewport across list rebuilds.

### v11.10.24 — Sparse Alpha Mask Quarantine

- Added startup quarantine for tiny sparse alpha/mask dumps.
- Scans the full dump folder before queue creation.

### v11.10.23 — Sparse Alpha Mask Filter

- Added conservative detection and skip fallback for tiny sparse alpha/mask dumps.

### v11.10.22 — GitHub Release + Full Tutorial

- Added stronger README, tutorial, release checklist, and ComfyUI model requirements.

---

## Quick start

1. Download the Windows ZIP asset below.
2. Extract it to a normal folder.
3. Run `Faithful Remaster.exe`.
4. Start ComfyUI and confirm the API URL is correct.
5. Read the tutorial inside `docs/GETTING_STARTED_TUTORIAL.md`.

---

## Download

Download:

`Faithful-Remaster-v11.10.25-Texture-Manager-Refresh-Selection-Windows.zip`

Optional checksum file:

`Faithful-Remaster-v11.10.25-Texture-Manager-Refresh-Selection-Windows.zip.sha256.txt`
