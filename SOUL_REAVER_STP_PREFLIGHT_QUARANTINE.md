# Soul Reaver STP Texpage Preflight Quarantine

Faithful Remaster v11.10.27 adds a reversible pre-remaster safety pass for Soul Reaver-style texpage alpha duplicates.

## Problem

Soul Reaver can dump visually identical texpage pairs such as:

- `texpage-P4-...png`
- `texpage-STP4-...png`

The RGB can be identical, while the alpha channel/semantics differ. Keeping the STP variant inside the active dump/replacement path can create black alpha squares in-game.

## New safety setting

`Pre-remaster quarantine matching STP/ST texpage duplicates`

Default: **enabled**

Before Start Watching or each Batch Queue profile builds its first processing queue, the app scans the active dump folder by filename and quarantines only STP/ST texpage files that have an exact matching normal P/T counterpart in the same folder.

Examples:

- Keep: `texpage-P4-936A8B...-128x64-P0-15.png`
- Quarantine: `texpage-STP4-936A8B...-128x64-P0-15.png`

Also supported:

- Keep: `texpage-T4-...png`
- Quarantine: `texpage-ST4-...png`

Files without a matching normal counterpart are left untouched.

## Safety

- No permanent deletion.
- Files are moved to the profile `_cleanup_quarantine` folder with restore metadata.
- The preflight pass runs before sparse alpha, EFB/cutscene cleanup, indexing, and remastering.
- The scan is filename-only, so it is fast even on large dump folders.
