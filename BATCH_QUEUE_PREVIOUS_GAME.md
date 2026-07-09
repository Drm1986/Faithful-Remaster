# Batch Queue — Previous game

Faithful Remaster v11.10.20 adds **⏮ Previous game** to the Batch Queue controls.

## Behavior

- Available only while Batch Queue is running and the current profile is not the first queued profile.
- Stops the current profile using the same safe stop event as Stop/Skip.
- Waits for the active worker to halt before launching another profile.
- Moves the Batch Queue cursor back one profile.
- Does not delete Load outputs, hash cache or processed history.

## Why conservative

Previous is intended as queue navigation, not destructive rollback. If a true reprocess is needed, use the existing cache/output cleanup controls intentionally.

## Preserved behavior

- Stop Batch: stops the whole queue.
- Skip to next game: stops the current profile and advances forward.
- Previous game: stops the current profile and returns to the previous queued profile.
