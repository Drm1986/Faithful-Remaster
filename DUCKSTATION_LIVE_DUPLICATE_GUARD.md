# v11.10.31 — DuckStation Live Duplicate Guard

This build adds DuckStation-scoped duplicate protection for FF8-style PS1 dumps.

## DuckStation only

New DuckStation duplicate controls are visible only when the active profile emulator is DuckStation.

- DuckStation duplicate cleanup before Comfy queue
- DuckStation live duplicate guard while watching

The old standalone STP duplicate option is no longer exposed as a separate user option. Its duplicate-only STP/P and ST/T safety path is folded into the DuckStation duplicate cleanup setting.

## Startup cleanup

Before the first queue is built, DuckStation profiles scan the dump folder for exact visual duplicate `texupload` / `texpage` files. A duplicate is moved only when it has:

- the same DuckStation filename structure after ignoring the first data/index hash
- identical decoded RGBA pixels

The first canonical texture stays active. Duplicate dumps are moved to reversible quarantine before ComfyUI receives them.

## Live guard

While watching, each new DuckStation texture is checked again immediately before it can be sent to ComfyUI. If it is an exact visual duplicate of a canonical texture already seen in the session, it is quarantined and skipped.

## Emulator-specific visibility

- Cinematic / EFB / cutscene options are shown and run only for Dolphin and PPSSPP profiles.
- Sparse alpha/mask quarantine options are shown and run only for Dolphin profiles.
- DuckStation duplicate cleanup appears and runs only for DuckStation profiles.
- STP Texture Manager cleanup buttons are visible only for DuckStation profiles.

All moves are quarantine moves, not permanent deletion.
