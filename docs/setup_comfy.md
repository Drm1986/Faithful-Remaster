# ComfyUI setup

Faithful Remaster uses ComfyUI as the external processing engine. The app does not include ComfyUI, checkpoints, ControlNet models, or upscaler weights.

## 1. Install ComfyUI

Install ComfyUI and confirm it runs before launching a large Faithful Remaster job.

Official links:

- ComfyUI GitHub: https://github.com/comfy-org/ComfyUI
- ComfyUI documentation: https://docs.comfy.org/
- ComfyUI-Manager: https://github.com/comfy-org/ComfyUI-Manager

Default local API:

```text
http://127.0.0.1:8188
```

## 2. Install the required models for the bundled workflows

Read the full model table here:

```text
docs/COMFYUI_MODEL_REQUIREMENTS.md
```

Minimum required files for the bundled v11.10.25 workflows:

```text
ComfyUI/models/upscale_models/4x-UltraSharpV2.safetensors
ComfyUI/models/upscale_models/RealESRGAN_x4plus.safetensors
ComfyUI/models/controlnet/controlnet-tile-sdxl-1.0.safetensors
ComfyUI/models/checkpoints/dreamshaperXL_lightningDPMSDE.safetensors
```

Important:

- Filenames must match the workflow exactly.
- If you use a different checkpoint such as Juggernaut XL, update the workflow checkpoint node and export/update the API workflow.
- Restart ComfyUI after adding models.

## 3. Verify the bundled UI workflows

1. Start ComfyUI.
2. Open each bundled UI workflow from the `workflows` folder.
3. Confirm all nodes load.
4. Confirm the model dropdowns find the installed models.
5. Queue one test image directly in ComfyUI.
6. Fix missing models/nodes before using Faithful Remaster.

## 4. Connect Faithful Remaster

1. Open Faithful Remaster.
2. Confirm the ComfyUI API URL.
3. Press **Test ComfyUI**.
4. Select the bundled API workflows.
5. Press **Auto Detect All Nodes**.
6. Save the profile.
7. Run a small texture test before using a full Batch Queue.
