# Showcase Video Plan — After v11.10.22 Release Validation

Do not record the final announcement video until the GitHub release is complete and the public ZIP has been downloaded and tested once.

## Goal

Show that Faithful Remaster is not just an upscaler. It is a controlled texture-pack production workspace with profiles, live watching, asset browsing, workflow routing, quarantine, cache, and Batch Queue recovery.

## Recommended video structure

### 1. Hook — 10 to 20 seconds

Show split-screen gameplay:

- left: original texture dump / original emulator output;
- right: Faithful Remaster replacement output.

Keep this fast and visual.

### 2. What the tool does — 30 seconds

Say the simple idea:

```text
The emulator dumps textures. Faithful Remaster watches them, processes them through controlled workflows, and writes replacements back in the exact structure the emulator expects.
```

### 3. Profile setup — 45 to 60 seconds

Show:

- Discover Games;
- dump folder;
- load/replacement folder;
- workflow routes;
- Validate Profile.

### 4. Live watching — 45 to 60 seconds

Show:

- emulator dumping textures;
- Faithful Remaster detecting new files;
- Texture Manager counters updating;
- output appearing.

### 5. Asset Browser — 60 seconds

Show filters:

- Missing output;
- Existing output;
- Orphaned output;
- search/sort/group if useful.

Explain that this prevents guessing what has or has not been processed.

### 6. Safety features — 45 seconds

Show or mention:

- Alpha route guard;
- EFB/cutscene quarantine;
- N64 Strip Safe;
- cache and processed logs;
- version sync.

### 7. Batch Queue — 60 seconds

Show:

- multiple profiles;
- Start Batch;
- Skip to next game;
- Previous game;
- Stop Batch.

Make it clear that Skip/Previous are recovery tools, not destructive actions.

### 8. Download and tutorial — 20 seconds

Show the GitHub release page and README.

Tell users to start with the tutorial before running a full game.

## Recording tips

- Use stable scenes with obvious texture differences.
- Avoid overly busy cutscenes for the first comparison.
- Record original and remastered footage at the same emulator resolution.
- Use the same camera angle/save point when possible.
- Record the tool UI separately if the gameplay footage needs to stay clean.
- Keep file paths private if they expose personal folders.

## Suggested pinned comment

```text
Start here: download the latest release, extract the ZIP, run Faithful Remaster.exe, then follow docs/GETTING_STARTED_TUTORIAL.md before processing a full game.
```

## What not to claim yet

Avoid saying:

- every game is supported perfectly;
- every workflow is universally best;
- every texture can be safely processed;
- no manual review is needed.

Better claim:

```text
Faithful Remaster gives you a safer workflow for building faithful texture packs, with profile validation, live refresh, asset review, workflow routing, and batch recovery tools.
```
