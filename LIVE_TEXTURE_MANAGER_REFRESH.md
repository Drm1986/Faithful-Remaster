# Live Texture Manager Refresh

v11.10.16 keeps Texture Manager counts synchronized while a profile is being watched or processed in Batch Queue.

## What refreshes automatically

- New dump files created by the emulator.
- New Load/Replacement outputs created by Faithful Remaster.
- Missing output -> Existing output transitions.
- Deleted output -> Missing output transitions.
- Quarantine and restore operations.
- Orphaned output counts.

## How it works

Worker events mark the manager index as dirty. The UI then performs a throttled background refresh instead of requiring the user to press Refresh List. A slow periodic check also runs while watching/batch is active.

For large packs, unchanged file metadata is reused using path, modification time and file size, so repeated live refreshes do not reclassify every texture image.

Manual Refresh List still forces a refresh when desired.
