# Sparse Alpha Mask Quarantine

Faithful Remaster v11.10.24 adds a reversible quarantine pass for tiny sparse alpha/mask dumps.

## Problem

Some Dolphin games can dump a very large number of tiny transparent grayscale mask or silhouette files. Super Mario Strikers can produce around 30,000 repeated 80x80 files of this type.

These files are usually not useful texture-remaster targets. Sending them to ComfyUI wastes queue time and can create noisy replacement packs.

## Behavior

When the setting below is enabled:

```text
auto_quarantine_sparse_alpha_masks_on_start = True
```

Faithful Remaster scans the whole active dump folder at the beginning of **Start Watching** and before each Batch Queue profile begins. Detected sparse alpha/mask files are moved out of the active dump tree before the processing queue is built.

If a matching file appears later during live watching, it is also quarantined before it can be sent to ComfyUI.

## Quarantine location

Files are moved to the current profile quarantine:

```text
_cleanup_quarantine/sparse-alpha-startup-YYYYMMDD-HHMMSS-xxxxxx/
```

The quarantine manifest records the original dump-relative path, category, reason, and timestamp. This makes the move reversible from the Texture Manager quarantine view.

## Detection rules

The detector is intentionally narrow. It targets small images that are:

- usually between 8x8 and 128x128;
- transparent or alpha-bearing;
- very low visible coverage;
- grayscale/neutral;
- RGB values closely matching the alpha channel;
- simple mask-like data with few RGBA levels.

Colored icons, UI art, normal sprites, and ordinary texture atlases should remain active.

## Safety

No files are permanently deleted. The feature only moves high-confidence sparse masks into quarantine.
