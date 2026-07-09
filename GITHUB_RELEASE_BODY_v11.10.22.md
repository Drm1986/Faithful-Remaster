# Faithful Remaster v11.10.22 — GitHub Release + Full Tutorial

This package is focused on making Faithful Remaster easier to download, understand, test, and showcase publicly. It is built on **v11.10.21 Version Sync** and preserves the current processing behavior.

## Why this release matters

The previous builds were mostly stability and workflow-safety builds. v11.10.22 is the first package prepared as a serious public GitHub release with stronger documentation and a guided onboarding path.

## New in v11.10.22

- Full GitHub-ready `README.md`.
- Complete beginner tutorial: `docs/GETTING_STARTED_TUTORIAL.md`.
- Maintainer publishing checklist: `docs/GITHUB_RELEASE_CHECKLIST.md`.
- Copy-paste release notes and release body.
- Showcase video plan for after release validation.
- Version updated to `v11.10.22` across the package source.

## Preserved stability features

- Version Sync from v11.10.21.
- Batch Queue Previous game from v11.10.20.
- Batch Queue Skip hardening from v11.10.19.
- Live Texture Manager refresh from v11.10.16.
- Existing/Missing/Orphaned output filters.
- Azahar-only metadata action visibility.
- Alpha route guard.
- EFB/cutscene quarantine support.
- Clean Heart, Strong Believer, and N64 Strip Safe workflows.

## Important note

This release does **not** modify the bundled workflow JSON files and does **not** change the core processing logic. It is a release-readiness and tutorial package.

## Quick start

1. Download the Windows ZIP asset below.
2. Extract it to a normal folder.
3. Run `Faithful Remaster.exe`.
4. Start ComfyUI and confirm the API URL is correct.
5. Read the tutorial inside `docs/GETTING_STARTED_TUTORIAL.md`.

## Recommended first test

Use one small profile first. Validate the profile, test ComfyUI, process a few textures, and confirm the output appears in your emulator before starting a large Batch Queue run.
