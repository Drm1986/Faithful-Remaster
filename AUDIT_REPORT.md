# v11.10.6 Dump-Path Routing Audit

## Scope

Reviewed the profile folder auto-fill and Discover Games pipeline for all supported structured emulators.

## Corrected defects

1. Discover Games previously required separate Input Root and Output Root fields even when the output location was deterministically related to the dump location.
2. PCSX2 and DuckStation had no default root or per-game `dumps` / `replacements` routing.
3. Per-profile dump browsing did not identify an emulator from the selected folder.
4. Game IDs could be read from the technical leaf (`dumps`) instead of its parent serial folder.
5. Automatic title lookup could remove a serial's hyphen from the profile Game ID.
6. The old discovery loop assumed every emulator stored game folders directly below one input root.

## Validation

- Python compilation passed for the core and universal modules.
- Headless GUI startup passed and confirmed there is no Discover Games output-root field.
- Profile browse test passed for PCSX2, including emulator detection, exact serial preservation, dump path and replacement path.
- Discover Games GUI integration passed for multiple PCSX2 games.
- Routing regression tests passed for Dolphin, Flycast, PCSX2, DuckStation, PPSSPP, Azahar, RMG and Project64.
- Ambiguous PlayStation-layout tests passed for current-profile tie-breaking and DuckStation `config.yaml` detection.
