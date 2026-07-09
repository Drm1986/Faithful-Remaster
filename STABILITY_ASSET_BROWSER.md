# Stability + Asset Browser Build

Faithful Remaster v11.10.14 keeps the v11.10.13 stabilization scope and adds a PPSSPP Texture Manager orphan-detection fix. It deliberately avoids workflow or processing-pipeline changes.

## Texture Manager filters

The Texture Manager filter menu now includes output-state filters:

- **Missing output** — shows dump textures that do not currently have a matching replacement/load file.
- **Existing output** — shows dump textures that already have a matching replacement/load file.
- **Orphaned output** — shows replacement/load files that no longer have a matching dump texture.

`Missing output` and `Existing output` are active-dump filters. Selecting both shows both. `Orphaned output` switches to a separate output-only viewer so stale pack files are not mixed into the active dump list.

## Orphaned output viewer

Orphaned outputs can be previewed and deleted from Texture Manager. They cannot be assigned modes, recreated, compared, or added to exception rules because they have no source dump.

## Profile validation

Profiles now include a clear **Validate Profile** action that checks:

- emulator and game ID summary;
- dump folder existence;
- replacement/load folder target;
- RGB workflow route for the selected mode;
- Alpha workflow route and node IDs;
- processed-log target;
- hash-cache state.

Validation is read-only. It does not contact ComfyUI, process textures, delete files, move files, or mutate workflow routes.

## Advanced organization

The daily Texture Manager filters are now separate from output-state and source-switching views. Quarantined and Orphaned remain exclusive data sources. Potentially destructive cleanup remains behind explicit buttons or advanced cleanup controls.

## Non-goals in this build

No workflow JSON files changed.
No Clean Heart or Strong Believer settings changed.
No Alpha workflow changed.
No N64 Strip Safe workflow changed.
No EFB detector thresholds changed.
No processing logic changed beyond read-only asset-browser indexing.
