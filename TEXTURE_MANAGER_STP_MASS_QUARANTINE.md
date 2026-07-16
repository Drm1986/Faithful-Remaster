# Texture Manager STP Mass Quarantine

v11.10.29 adds manual aggressive STP cleanup buttons for Soul Reaver testing.

## Buttons

### Quarantine all STP Dumps

Moves all dump textures matching:

- `texpage-STP<number>-...`
- `texpage-ST<number>-...`

from the active dump folder into profile quarantine.

### Quarantine all STP Outputs

Moves all replacement/load textures matching the same STP/ST patterns out of the active replacement pack into replacement quarantine.

## Why manual?

Some Soul Reaver wall textures may only exist as STP/ST and can be useful. Automatic processing therefore remains duplicate-only. The new buttons are intentionally manual and ask for confirmation before moving anything.
