# Faithful Remaster v11.10.29 — Texture Manager STP Mass Quarantine

Focused Texture Manager cleanup build.

## Added

- Horizontal side-scroll support for the main Texture Manager body.
  - Useful on smaller screens when the preview/actions pane is clipped.
  - The texture filename list also has a horizontal scrollbar for long texpage names.

- New Texture Manager action: **Quarantine all STP Dumps**
  - Scans the active dump folder.
  - Moves every `texpage-STP<n>-...` and `texpage-ST<n>-...` texture to reversible quarantine.
  - Manual only, because some non-duplicated STP/ST textures may still be useful in Soul Reaver.

- New Texture Manager action: **Quarantine all STP Outputs**
  - Scans the active replacement/load folder.
  - Moves every `texpage-STP<n>-...` and `texpage-ST<n>-...` output out of the active replacement pack.
  - Manual only and reversible via the quarantine manifest.

## Preserved

- Duplicate-only STP guard from v11.10.28 is unchanged.
- Non-duplicated STP/ST files are still allowed by automatic processing unless the new manual all-STP buttons are used.
- Workflows were not modified.
