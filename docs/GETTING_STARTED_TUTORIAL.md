# Faithful Remaster — Complete Getting Started Tutorial

This tutorial walks through the safest way to install requirements, create your first texture-remastering profile, process a small test set, review the outputs, and then scale up to Batch Queue.

The tutorial assumes you are using the Windows package for **Faithful Remaster v11.10.31**.

## 1. What Faithful Remaster does

Emulators can dump the original textures used by a game. They can also load replacement textures if those replacements keep the expected filename and folder structure. Faithful Remaster sits between those two folders:

```text
Emulator Dump Folder  ->  Faithful Remaster  ->  Emulator Load/Replacement Folder
```

The app watches the dump folder, sends new textures to the selected workflow/backend, and writes finished textures to the matching output path.

## 2. What you need before starting

You need:

- A Windows PC.
- An emulator with texture dumping and custom texture loading.
- ComfyUI running locally or remotely.
- The models required by the bundled ComfyUI workflows.
- Enough free disk space for dumps, outputs, cache, checkpoints, ControlNet models, and upscalers.

Recommended first test: use a small game area with only a few dumped textures. Do not start with a huge full-game dump.

## 3. Install ComfyUI and required models

Faithful Remaster does not include AI model weights. The bundled workflows require ComfyUI plus specific model files.

Read this file before processing:

```text
docs/COMFYUI_MODEL_REQUIREMENTS.md
```

Required for the bundled workflows:

```text
ComfyUI/models/upscale_models/4x-UltraSharpV2.safetensors
ComfyUI/models/upscale_models/RealESRGAN_x4plus.safetensors
ComfyUI/models/controlnet/controlnet-tile-sdxl-1.0.safetensors
ComfyUI/models/checkpoints/dreamshaperXL_lightningDPMSDE.safetensors
```

Important notes:

- The workflow filenames must match exactly.
- The bundled RGB workflows currently use `dreamshaperXL_lightningDPMSDE.safetensors` as the SDXL checkpoint.
- Restart ComfyUI after adding or renaming model files.

Useful official/source links:

- ComfyUI: https://github.com/comfy-org/ComfyUI
- ComfyUI docs: https://docs.comfy.org/
- ComfyUI-Manager: https://github.com/comfy-org/ComfyUI-Manager
- UltraSharpV2: https://huggingface.co/Kim2091/UltraSharpV2/blob/main/4x-UltraSharpV2.safetensors
- RealESRGAN x4plus safetensors: https://huggingface.co/GraydientPlatformAPI/safetensor-upscalers/blob/main/RealESRGAN_x4plus.safetensors
- ControlNet Tile SDXL: https://huggingface.co/f5aiteam/ComfyUI/blob/main/ControlNet/controlnet-tile-sdxl-1.0.safetensors
- DreamShaper XL Lightning DPMSDE: https://huggingface.co/oguzm/dreamshaper-xl-lightning-dpmsde/blob/main/dreamshaperXL_lightningDPMSDE.safetensors

## 4. Extract the package

Extract the ZIP to a simple folder such as:

```text
D:\Tools\Faithful Remaster\
```

Avoid running directly from inside the ZIP. Avoid folders that require admin permission, such as `Program Files`, while testing.

Run:

```text
Faithful Remaster.exe
```

## 5. Start ComfyUI

Start ComfyUI before processing. The default backend points to:

```text
http://127.0.0.1:8188
```

Before using Faithful Remaster, open the bundled UI workflows in ComfyUI and verify that all nodes and models load.

In Faithful Remaster:

1. Open the backend/settings area.
2. Confirm the API URL.
3. Press **Test ComfyUI**.
4. Do not continue until the connection test succeeds.

If the test fails, check that ComfyUI is open, the port is correct, and the required workflow nodes load in ComfyUI.

## 6. Enable emulator texture dumping

Every emulator has two important options:

- dump original textures;
- load custom/replacement textures.

For discovery, enable dumping and play the game until the emulator creates the game's dump folder. For replacement testing, you usually keep custom texture loading enabled and may later disable dumping once you have enough dumped textures.

Common layouts:

| Emulator | Dump folder | Output folder |
|---|---|---|
| Dolphin | `%APPDATA%\Dolphin Emulator\Dump\Textures\<GAME_ID>` | `%APPDATA%\Dolphin Emulator\Load\Textures\<GAME_ID>` |
| Azahar | `%APPDATA%\Azahar\dump\textures\<TITLE_ID>` | `%APPDATA%\Azahar\load\textures\<TITLE_ID>` |
| PCSX2 | `...\textures\<SERIAL>\dumps` | `...\textures\<SERIAL>\replacements` |
| DuckStation | `...\textures\<SERIAL>\dumps` | `...\textures\<SERIAL>\replacements` |
| PPSSPP | `...\PSP\TEXTURES\<GAME_ID>\new` | `...\PSP\TEXTURES\<GAME_ID>` |
| Flycast | `...\Flycast\data\texdump\<GAME_ID>` | `...\Flycast\data\textures\<GAME_ID>` |

## 7. Create or discover a profile

Open **Profiles**.

Recommended route:

1. Use **Discover Games** if the emulator already created dump folders.
2. Pick the game from the review list.
3. Confirm the readable name and the real folder ID.
4. Save the profile.

Manual route:

1. Create a new profile.
2. Enter a game name.
3. Select the dump folder.
4. Select or auto-fill the output folder.
5. Choose the emulator/backend type if asked.
6. Save.

Do not continue until the profile paths are correct.

## 8. Select workflows

Faithful Remaster uses separate workflow routes for different texture types.

Typical setup:

- RGB workflow: normal color textures.
- Alpha workflow: masks, transparency, grayscale, and alpha-sensitive textures.
- N64 Strip Safe workflow: very thin strip textures when that route is appropriate.

Use bundled API workflows for app processing. UI workflows are for opening and inspecting in ComfyUI.

After selecting workflows, press:

```text
Auto Detect All Nodes
```

Then save the profile again.

## 9. Validate the profile

Press **Validate Profile** before starting.

A good profile should show:

- dump folder exists;
- output folder exists or can be created;
- RGB route is configured;
- Alpha route is configured if you process alpha textures;
- processed log target is valid;
- cache is available;
- emulator-specific rules look sane.

Fix validation warnings before a serious run.

## 10. First small processing test

Do not start with the whole game. Start small.

1. Launch the game.
2. Let the emulator dump a few textures.
3. Open Texture Manager.
4. Press Refresh List, or rely on live refresh while watching.
5. Select a few obvious textures.
6. Start watching or process a controlled small set.
7. Wait for outputs to appear.
8. Restart the game or reload the scene if the emulator needs it.
9. Confirm the textures show in-game.

If the in-game output looks wrong, stop and fix the workflow/profile before processing hundreds of files.

## 11. Understand Texture Manager filters

Texture Manager can show different views:

- **Active textures**: textures currently present in the dump folder.
- **Missing output**: dump exists but matching output does not exist.
- **Existing output**: dump exists and output exists.
- **Orphaned output**: output exists but matching dump no longer exists.

Use these filters to avoid guessing what still needs processing.

## 12. Use Compare modes before replacing large sets

For one selected active texture, use **Compare modes** to compare available output modes. This is useful before committing to a workflow style for a full pack.

A good comparison pass checks:

- edges and outlines;
- text clarity;
- foliage density;
- stone/wood detail;
- UI elements;
- transparency and halos;
- faithfulness to the original art direction.

## 13. Quarantine risky textures

Some textures are not normal game assets. Dynamic EFB/cutscene/framebuffer-like dumps can break packs or waste time if processed blindly.

Use quarantine tools when a texture appears to be:

- a full-screen scene capture;
- a cutscene frame;
- a temporary framebuffer;
- a rapidly changing effect;
- a texture that should not become a permanent replacement.

Quarantine is a safety workflow, not a failure.

## 14. Batch Queue workflow

After one profile works, Batch Queue can process multiple profiles.

Recommended Batch Queue sequence:

1. Add only two profiles first.
2. Confirm each profile validates.
3. Start Batch Queue.
4. Watch the monitor log.
5. Test **Skip to next game** once.
6. Test **Previous game** once.
7. Confirm the queue state remains correct.
8. Only then add more profiles.

Controls:

- **Stop Batch** stops the whole queue.
- **Skip to next game** safely stops the current profile and advances.
- **Previous game** safely stops the current profile and returns to the previous queued profile.

## 15. Cache and processed logs

Faithful Remaster keeps cache and processed logs so repeated runs do not redo everything unnecessarily.

Be careful when changing workflows:

- If you intentionally changed output style, consider whether old cached outputs should be reused.
- If a workflow was broken, failed textures may need retrying after the workflow/backend is fixed.
- Do not delete cache blindly unless you understand what you are resetting.

## 16. Recommended quality-control pass

Before sharing a texture pack:

- inspect a normal gameplay area;
- inspect menus and UI;
- inspect transparent objects;
- inspect text/signs;
- inspect cutscenes;
- inspect save/load transitions;
- check for shimmering or over-sharpened textures;
- check that no framebuffer/cutscene dumps were accidentally replaced.

## 17. Common problems

### ComfyUI test fails

- Start ComfyUI.
- Confirm the API URL.
- Check firewall/security tools.
- Open the ComfyUI URL in a browser.
- Open the bundled UI workflow in ComfyUI and check for missing models or missing nodes.

### A workflow opens with red missing models

- Check `docs/COMFYUI_MODEL_REQUIREMENTS.md`.
- Confirm every model is in the correct ComfyUI folder.
- Confirm filenames match exactly.
- Restart ComfyUI after adding models.

### Output exists but emulator does not show it

- Confirm custom texture loading is enabled.
- Confirm output folder is correct.
- Confirm filename and relative path are unchanged.
- Restart the emulator.
- Avoid old save states during first testing.

### Texture Manager numbers look wrong

- Press Refresh List.
- Confirm the profile points to the active dump folder.
- Confirm the emulator is dumping into the folder you selected.


## Sparse alpha/mask quarantine

If a game produces thousands of tiny transparent mask files, keep **Quarantine sparse alpha/mask dumps before watching / each Batch profile** enabled.

When Start Watching begins, Faithful Remaster scans the full dump folder and moves matching tiny sparse alpha/mask dumps into reversible quarantine before the processing queue is built. This is especially useful for Super Mario Strikers-style 80x80 mask floods.

Quarantined files are not deleted. They can be reviewed from the Texture Manager quarantine view.

