# Faithful Remaster v11.10.26 — Texture Manager Fast Scan Hotfix

This hotfix targets a Texture Manager regression where large dump folders could stay on `Scanning textures…` and show no textures.

## Fixed

- Texture Manager default refresh now uses a fast metadata-first scan.
- The list appears without opening/classifying every image first.
- Expensive mask/alpha/resolution classification is deferred until the user requests a visual filter, group, or sort.
- Preserves the v11.10.25 selection/scroll restoration behavior.

## Why this matters

Some games can generate tens of thousands of tiny dump files. Opening and visually classifying every image before showing the list can make the Texture Manager appear frozen.

## Preserved features

- Sparse alpha mask quarantine from v11.10.24.
- Batch Queue Skip and Previous controls.
- Live Texture Manager refresh.
- Existing / Missing / Orphaned output filters.
- Azahar-only metadata action visibility.
- Alpha route guard.
- EFB / cutscene quarantine support.
- Version Sync.

## Important note

This release does not modify bundled workflow JSON files and does not change ComfyUI processing logic.

## Recommended test

Open a profile with a large dump folder and open Texture Manager. The list should appear instead of staying on `Scanning textures…`. Then start watching and confirm the selected texture does not jump back to the first row during refresh.

## Download

Download:

`Faithful-Remaster-v11.10.26-Texture-Manager-Fast-Scan-Windows.zip`

Optional checksum file:

`Faithful-Remaster-v11.10.26-Texture-Manager-Fast-Scan-Windows.zip.sha256.txt`
