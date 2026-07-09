# Faithful Remaster v11.10.22 — GitHub Release + Full Tutorial

- Reworked `README.md` into a GitHub-ready landing page and quick-start guide.
- Added `docs/GETTING_STARTED_TUTORIAL.md` as a full beginner walkthrough.
- Added `docs/GITHUB_RELEASE_CHECKLIST.md` for maintainer publishing validation.
- Added `GITHUB_RELEASE_BODY_v11.10.22.md` and `RELEASE_NOTES_v11.10.22.md` for release publishing.
- Added `docs/SHOWCASE_VIDEO_PLAN.md` for the announcement video after release validation.
- Added basic GitHub issue templates for bug reports and questions.
- Updated package runtime version to `11.10.22` using the existing VERSION-file source.
- No workflow JSON files or core processing logic changed.

# Faithful Remaster v11.10.21 — Version Sync

- Fixed the version mismatch where the universal feature layer could still force the UI title/header back to v11.10.17.
- Added bundled VERSION file as the release version source for the GUI.
- The universal layer now preserves the core APP_VERSION instead of hardcoding its own app version.
- No workflow or processing logic changes.

# Faithful Remaster v11.10.20 — Batch Queue Previous Game

- Built on v11.10.19 Batch Queue Skip Hardening.
- Added **⏮ Previous game** to the Batch Queue controls.
- Previous safely stops the active profile, then returns to the previous queued profile.
- Previous is disabled on the first queued profile and during pending Skip/Previous navigation.
- Previous does not delete outputs, processed history, or hash cache.
- Stop Batch still stops the whole queue; Skip still moves forward only.
- No workflow JSON files or texture processing routes were changed.

# Faithful Remaster v11.10.19 — Batch Queue Skip Hardening

- Rebuilt from the clean v11.10.17 UI/Azahar visibility package.
- Added **⏭ Skip to next game** to the Batch Queue control row.
- Skip stops only the active profile and then advances to the next queued profile.
- Added duplicate-click protection: skip is disabled after the first skip request until the next profile starts.
- Preserved **Stop Batch** behavior for stopping the whole queue.
- Hardened batch startup failure handling so a profile that cannot start stops the batch cleanly instead of being treated as finished.
- No workflow JSON files changed.
- No texture processing logic changed.

# Faithful Remaster v11.10.17 — UI Version + Azahar Action Visibility

- Fixed app title/version being overwritten by the universal layer as v11.10.12.
- Refresh Azahar Metadata is now visible only while editing an Azahar / Citra profile.
- No workflow JSON files changed.
- No processing logic changed.

# Faithful Remaster v11.10.17 — UI Version + Azahar Action Visibility

## Fixed

- Texture Manager no longer requires manual **Refresh List** to see new dumps, newly created outputs, deleted outputs, restored quarantines, or quarantine/cleanup moves.
- During **Start Watching** and **Batch Queue**, worker log events now invalidate the Texture Manager index and trigger a throttled background refresh.
- Added a low-frequency safety poll while watching/batch is active, so dumps that appear before a log line is emitted are still discovered.
- Existing/Missing/Orphaned counters update automatically while the game is dumping textures.

## Performance / stability

- Texture Manager scans now reuse cached visual metadata for unchanged files using path + mtime + file size.
- Large packs avoid re-opening every image on every live refresh.
- Auto-refresh never starts a second scan while one is already running.
- Manual **Refresh List** remains available and still forces a full scan.

## Unchanged

- No workflow JSON files changed.
- No Clean Heart / Strong Believer / Alpha / N64 logic changed.
- No processing logic changed.
- v11.10.15 Batch Failure Advance behavior is preserved.
- v11.10.14 PPSSPP orphan fix is preserved.
