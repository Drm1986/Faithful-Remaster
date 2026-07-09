# v11.10.21 Version Sync Fix

The previous package was named v11.10.20, but the visible UI could still show v11.10.17 because `faithful_universal.py` contained a hardcoded `_VERSION = "11.10.17"` and overwrote `fr.APP_VERSION` during startup.

v11.10.21 fixes this by:

- Adding a bundled `VERSION` file with `11.10.21`.
- Loading `APP_VERSION` from `VERSION` in `faithful_remaster.py`.
- Updating `faithful_universal.py` so it preserves the core `APP_VERSION` instead of forcing a separate app version.

This change is UI/build metadata only. Workflows and texture processing logic are unchanged.
