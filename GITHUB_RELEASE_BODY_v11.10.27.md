## Faithful Remaster v11.10.27 — Soul Reaver STP Preflight Quarantine

This build adds a targeted alpha-safety pass for Soul Reaver dump folders.

### New

- **Pre-remaster quarantine matching STP/ST texpage duplicates** setting.
- Runs before Start Watching / each Batch Queue profile builds the first queue.
- Detects duplicate texpage pairs such as:
  - keep `texpage-P4-...png`
  - quarantine `texpage-STP4-...png`
- Also supports `T` / `ST` pairs.

### Safety behavior

- Reversible quarantine only; no permanent deletion.
- Only quarantines STP/ST files when the matching P/T file exists in the same folder.
- STP/ST files with no normal counterpart are left untouched.
- Workflow JSON files are unchanged.

### Intended fix

Prevents Soul Reaver black-alpha square artifacts caused by unsafe STP texpage replacements entering the remaster/replacement pipeline.
