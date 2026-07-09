# Faithful Remaster v11.10.22 Release Notes

## Focus

This release prepares Faithful Remaster for a cleaner public GitHub release and adds a stronger onboarding path for new users.

## New documentation

- Full public `README.md`.
- Complete beginner tutorial: `docs/GETTING_STARTED_TUTORIAL.md`.
- Explicit ComfyUI model requirements: `docs/COMFYUI_MODEL_REQUIREMENTS.md`.
- GitHub release checklist: `docs/GITHUB_RELEASE_CHECKLIST.md`.
- Copy-paste release body: `GITHUB_RELEASE_BODY_v11.10.22.md`.

## Required ComfyUI model files for bundled workflows

The bundled workflows require the following files to be installed in ComfyUI:

```text
ComfyUI/models/upscale_models/4x-UltraSharpV2.safetensors
ComfyUI/models/upscale_models/RealESRGAN_x4plus.safetensors
ComfyUI/models/controlnet/controlnet-tile-sdxl-1.0.safetensors
ComfyUI/models/checkpoints/dreamshaperXL_lightningDPMSDE.safetensors
```

See `docs/COMFYUI_MODEL_REQUIREMENTS.md` for download links, folder locations, and Juggernaut XL notes.

## Stability preserved

- Version Sync from v11.10.21.
- Batch Queue Previous game from v11.10.20.
- Batch Queue Skip hardening from v11.10.19.
- Live Texture Manager refresh from v11.10.16.
- Existing/Missing/Orphaned output filters.
- Azahar-only metadata visibility.
- Alpha route guard.
- EFB/cutscene quarantine support.

## Workflow safety

No bundled workflow JSON files were changed in this release.

No core processing logic was changed in this release.

## Recommended validation

Before processing a full game pack:

- Install the required ComfyUI model files.
- Open the bundled UI workflow in ComfyUI and confirm all nodes/models load.
- Confirm the app shows `v11.10.22`.
- Confirm ComfyUI test succeeds.
- Run a small 3-5 texture test.
- Confirm the emulator loads the resulting replacement textures.
- Only then start a larger watching or Batch Queue session.
