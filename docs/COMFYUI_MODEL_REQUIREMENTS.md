# ComfyUI Model Requirements for Bundled Workflows

Faithful Remaster does not include AI model weights. The Windows package includes workflow JSON files, but ComfyUI must already have the required models in the correct folders before those workflows can run.

The bundled v11.10.22 workflows reference these exact filenames. If a filename is different, either rename the model file to match the workflow or open the UI workflow in ComfyUI, choose the model manually, and export/update the matching API workflow.

## Required base setup

| Requirement | Purpose | Link |
|---|---|---|
| ComfyUI | Local workflow engine/API used by Faithful Remaster | https://github.com/comfy-org/ComfyUI |
| ComfyUI documentation | Installation, model folders, and troubleshooting | https://docs.comfy.org/ |
| ComfyUI-Manager | Recommended for installing/updating extensions and missing nodes | https://github.com/comfy-org/ComfyUI-Manager |

Default local API expected by Faithful Remaster:

```text
http://127.0.0.1:8188
```

## Required model files

### 1. Upscale models

Put these files in:

```text
ComfyUI/models/upscale_models/
```

| Required filename | Used by | Source |
|---|---|---|
| `4x-UltraSharpV2.safetensors` | Alpha workflow and RGB workflows | https://huggingface.co/Kim2091/UltraSharpV2/blob/main/4x-UltraSharpV2.safetensors |
| `RealESRGAN_x4plus.safetensors` | N64 Strip Safe workflow and RGB workflows | https://huggingface.co/GraydientPlatformAPI/safetensor-upscalers/blob/main/RealESRGAN_x4plus.safetensors |

Notes:

- The official Real-ESRGAN project distributes `RealESRGAN_x4plus.pth`. The bundled Faithful Remaster workflow currently expects `RealESRGAN_x4plus.safetensors` exactly.
- If you use the official `.pth` file instead, open the bundled UI workflow in ComfyUI, select `RealESRGAN_x4plus.pth` in the Upscale Model Loader node, and export/update the matching API workflow.
- Official Real-ESRGAN project page: https://github.com/xinntao/Real-ESRGAN

### 2. ControlNet Tile SDXL

Put this file in:

```text
ComfyUI/models/controlnet/
```

| Required filename | Used by | Source |
|---|---|---|
| `controlnet-tile-sdxl-1.0.safetensors` | RGB Clean Heart and RGB Strong Believer workflows | https://huggingface.co/f5aiteam/ComfyUI/blob/main/ControlNet/controlnet-tile-sdxl-1.0.safetensors |

Alternative source:

- Original Xinsir model page: https://huggingface.co/xinsir/controlnet-tile-sdxl-1.0/blob/main/diffusion_pytorch_model.safetensors
- If you download the Xinsir file, rename `diffusion_pytorch_model.safetensors` to:

```text
controlnet-tile-sdxl-1.0.safetensors
```

### 3. SDXL checkpoint used by the bundled RGB workflows

Put this file in:

```text
ComfyUI/models/checkpoints/
```

| Required filename | Used by | Source |
|---|---|---|
| `dreamshaperXL_lightningDPMSDE.safetensors` | RGB Clean Heart and RGB Strong Believer workflows | https://huggingface.co/oguzm/dreamshaper-xl-lightning-dpmsde/blob/main/dreamshaperXL_lightningDPMSDE.safetensors |

## About Juggernaut XL

The bundled v11.10.22 workflow JSON files currently reference `dreamshaperXL_lightningDPMSDE.safetensors`, not Juggernaut XL.

If you want to use Juggernaut XL instead, download it into:

```text
ComfyUI/models/checkpoints/
```

Recommended Juggernaut XL v9 page:

```text
https://huggingface.co/RunDiffusion/Juggernaut-XL-v9/blob/main/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors
```

Then open the bundled UI workflow in ComfyUI, change the checkpoint loader to the Juggernaut file, test the workflow, and export/update the API workflow used by Faithful Remaster. Do not just place Juggernaut in the folder and expect the current bundled API workflow to use it automatically.

## Built-in nodes expected by the bundled workflows

The bundled workflows use common ComfyUI nodes such as:

- `LoadImage`
- `SaveImage`
- `UpscaleModelLoader`
- `ImageUpscaleWithModel`
- `CheckpointLoaderSimple`
- `ControlNetLoader`
- `ControlNetApplyAdvanced`
- `KSampler`
- `VAEEncode`
- `VAEDecode`
- `GLSLShader`

`GLSLShader` is a ComfyUI built-in node, but it may require a current ComfyUI build. If the node is missing, update ComfyUI and restart it.

GLSLShader documentation:

```text
https://docs.comfy.org/built-in-nodes/GLSLShader
```

## Quick folder checklist

```text
ComfyUI/
  models/
    upscale_models/
      4x-UltraSharpV2.safetensors
      RealESRGAN_x4plus.safetensors
    controlnet/
      controlnet-tile-sdxl-1.0.safetensors
    checkpoints/
      dreamshaperXL_lightningDPMSDE.safetensors
```

Optional Juggernaut checkpoint:

```text
ComfyUI/models/checkpoints/Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors
```

## Validation before using Faithful Remaster

1. Start ComfyUI.
2. Open each bundled UI workflow in ComfyUI.
3. Confirm no node is red or missing.
4. Confirm every model dropdown resolves to an installed model.
5. Queue one test image in ComfyUI directly.
6. Only after that, open Faithful Remaster and press **Test ComfyUI**.
7. Run a very small texture test before starting a large Batch Queue run.
