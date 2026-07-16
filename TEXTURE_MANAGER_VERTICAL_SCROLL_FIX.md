# Texture Manager Vertical Scroll Fix

Version: v11.10.30

This build corrects the Texture Manager scroll behavior from the v11.10.29 STP mass quarantine build.

## Changes

- The main Texture Manager body now uses vertical scrolling.
- The Actions panel has more vertical room so the STP quarantine actions are reachable on smaller displays.
- The texture filename list still keeps its own horizontal scrollbar for long `texpage...` names.

## STP buttons

The following buttons remain in Texture Manager → Actions:

- Clean duplicate STP Outputs
- Quarantine all STP Dumps
- Quarantine all STP Outputs

These are manual actions. Automatic processing guard remains duplicate-only.
