# Material-aware Ultra 4K planning (experimental v1)

This is a **safe, advisory-only prototype**. It does **not** change the current Faithful Remaster queue, ComfyUI workflow, Clean Heart or Strong Believer outputs. No DLSS runtime/API is required.

## Purpose
Classify textures into stone, plaster, wood, foliage, metal, fabric, skin, ground, water/sky, UI, mixed, or unknown. Create a conservative processing recipe carrying a denoise upper bound, 4K-side target (preserving aspect ratio), game art guidance, group identity, and review requirements.

## Example
```python
from material_aware import plan_texture
recipe = plan_texture("palace_sandstone.png", 256, 128,
                      game_style={"game_id":"pop_sot",
                                  "style_instruction":"warm painterly Arabian palace art"},
                      preset="Clean Heart", seamless=True)
print(recipe.to_dict())
```

## Important limits
- Emulator dump filenames are usually hashes: without an optional **actual vision classifier** predictions input, classification is usually unknown. The classifier is an interface, **not** an implemented image understanding model.
- Repeated visual families are only assigned a stable group ID; there is not yet perceptual clustering or cross-output consistency scoring.
- Denoise caps are heuristic starting limits, **not calibrated model settings**. Integration must verify that a compatible denoise control exists in the selected ComfyUI graph.
- Source alpha, UV atlas regions, animation timing, wrapping edges, and output encoding must be protected by the downstream pipeline; this module only marks these safeguards.
- No automatic quality gate, generation, or model download is implemented. An adapter should be built and evaluated before enabling.
- Existing dump/replacement dimensions and emulator texture conventions take precedence over the proposed max-side target.

## Next integration checkpoint
1. Add an explicit opt-in UI toggle and choose a real image classifier; calibrate confidence with labeled dumps.
2. Bind recipe categories to the selected ComfyUI workflow schema; never silently overwrite user profiles.
3. Implement real image embeddings and per-game cluster references, plus edge/alpha/atlas validation.
4. Evaluate against manual gold-standard samples from Dolphin and DuckStation before bulk execution.

Run tests: `python -m unittest discover -s tests -p 'test_material_aware.py'`.
