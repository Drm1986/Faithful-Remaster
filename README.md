# Faithful Remaster

Live AI-assisted texture remastering pipeline for emulators using ComfyUI.

Faithful Remaster watches an input texture folder, processes new textures through ComfyUI, preserves transparency through a separate alpha workflow, and writes the final images to an output texture folder.

> Experimental release. Dolphin Emulator is the primary tested target. Other emulators may work through generic input/output folder mapping.

## Features

- Live folder watching
- ComfyUI RGB remaster workflow
- Separate alpha workflow
- Automatic LoadImage / SaveImage node detection
- Texture Manager
- Missing-output detection
- Exceptions list
- Hash cache
- Priority queue
- VRAM protection
- ComfyUI status monitoring
- Optional ComfyUI launcher

## Quick start

1. Install Python 3.10 or newer.
2. Run `pip install -r requirements.txt`.
3. Install and start ComfyUI.
4. Download the required models listed below.
5. Run `faithful_remaster.py`.
6. Select the input and output texture folders.
7. Select the bundled RGB and alpha API workflows.
8. Press **Auto Detect Nodes**.
9. Start watching.

## Bundled workflows

### RGB workflow

- UI: `workflows/Faithful_RGB_Workflow_UI.json`
- API: `workflows/Faithful_RGB_Workflow_API.json`
- LoadImage node: `1`
- SaveImage node: `4`

### Alpha workflow

- UI: `workflows/Faithful_Alpha_Workflow_UI.json`
- API: `workflows/Faithful_Alpha_Workflow_API.json`
- LoadImage node: `1`
- SaveImage node: `5`

## Required ComfyUI models

- `4x-UltraSharpV2.safetensors`
- `controlnet-tile-sdxl-1.0.safetensors`
- `dreamshaperXL_lightningDPMSDE.safetensors`

Models are not included.

## Recommended settings

Testing:

```text
Hash cache = OFF
Ignore existing = OFF
Overwrite existing = ON
```

Normal gameplay:

```text
Hash cache = ON
Ignore existing = ON
Overwrite existing = OFF
```

## Emulator support

Tested:
- Dolphin Emulator

Experimental:
- PCSX2
- PPSSPP
- Lime3DS / Citra forks
- Other emulators with texture dump and replacement folders

## Legal notice

No game textures, ROMs, ISOs, copyrighted game assets, or commercial model files are included.

## License

MIT License.
