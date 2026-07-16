# Faithful Remaster v11.10.26 — Texture Manager Fast Scan Hotfix

This hotfix fixes a Texture Manager regression where large dump folders could stay on `Scanning textures…` and show no textures.

## Fixed

- Texture Manager now lists textures quickly using filesystem metadata first.
- Expensive mask/alpha/resolution classification is deferred until a visual filter, visual group, or visual sort needs it.
- The selected texture and scroll position preservation from v11.10.25 is retained.

## Preserved

- Sparse alpha mask quarantine from v11.10.24.
- Batch Queue Skip/Previous controls.
- Version Sync.
- Existing/Missing/Orphaned output filters.
- Alpha route and EFB/cutscene quarantine safeguards.

## Important

No bundled workflow JSON files were changed. No ComfyUI processing behavior was changed.
