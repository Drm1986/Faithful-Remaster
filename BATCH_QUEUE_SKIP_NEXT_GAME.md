# Batch Queue — Skip to next game

Faithful Remaster v11.10.19 adds **⏭ Skip to next game** in the Batch Queue controls.

## Behavior

- The button is enabled only while Batch Queue is running.
- Pressing it requests a safe stop for the current active profile.
- The current texture/backend job is allowed to halt through the existing stop path; Faithful Remaster does not forcibly corrupt in-flight output files.
- When the worker exits, the batch logs the current profile as skipped and starts the next queued profile.
- If the skipped profile is the final profile, the queue finishes normally.

## Difference from Stop Batch

- **Skip to next game** stops only the current profile and continues the queue.
- **Stop Batch** stops the whole batch queue and re-enables profile switching.

## Safety notes

- Duplicate skip clicks are ignored.
- The skip button is disabled while skip is pending.
- Start Batch Queue stays disabled while the batch is running or while skip is pending.
- Queue order, profile settings, workflows, cache routes, Alpha handling, and EFB quarantine logic are unchanged.
