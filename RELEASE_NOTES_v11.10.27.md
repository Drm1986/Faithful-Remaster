# Faithful Remaster v11.10.27 — Soul Reaver STP Preflight Quarantine

## Added

- New pre-remaster safety setting: **Pre-remaster quarantine matching STP/ST texpage duplicates**.
- Before Start Watching or each Batch Queue profile begins, Faithful Remaster scans the dump folder for Soul Reaver-style texpage duplicate pairs.
- When `texpage-STP<n>-...` has a matching `texpage-P<n>-...`, the STP file is moved to reversible quarantine before processing.
- Also supports `texpage-ST<n>-...` matched against `texpage-T<n>-...`.

## Why

Soul Reaver STP texpage replacements can cause black alpha square artifacts in-game even when the preview looks visually correct.

## Safety

- Quarantine only, no deletion.
- Matching is exact by filename identity except for the STP/ST prefix.
- Files without a normal P/T counterpart are kept.
- Workflows and processing logic are unchanged.
