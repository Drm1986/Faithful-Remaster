# Texture Manager Fast Scan — v11.10.26

This hotfix prevents the Texture Manager from getting stuck on `Scanning textures…` when a dump folder contains tens of thousands of files.

## What changed

- Normal Texture Manager refresh now lists textures using filesystem metadata first.
- Expensive Pillow visual classification is no longer performed for every texture during the default scan.
- Mask/alpha/resolution classification is only requested when the user uses a visual filter, visual group, or visual sort.
- The v11.10.25 selection/scroll preservation fix remains active.

## Why

Large packs such as Super Mario Strikers can contain around 30,000 tiny sparse/mask dump files. Opening and classifying every image before showing the list made the UI appear hung.

## Safety

No workflow JSON files were changed. No ComfyUI processing behavior was changed.
