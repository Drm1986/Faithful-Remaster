# GitHub Release Checklist — Faithful Remaster v11.10.22

Use this checklist before publishing the GitHub release.

## 1. Local package check

- [ ] Extract the ZIP into a clean folder.
- [ ] Run `Faithful Remaster.exe`.
- [ ] Confirm the visible app version is `v11.10.22`.
- [ ] Confirm `VERSION` contains `11.10.22`.
- [ ] Confirm the package filename contains `v11.10.22`.
- [ ] Confirm no old runtime version appears in the app header.

## 2. Functional smoke test

- [ ] Start ComfyUI.
- [ ] Test backend connection.
- [ ] Create or load a small profile.
- [ ] Validate profile.
- [ ] Process 3-5 RGB textures.
- [ ] Check at least one Alpha/mask-sensitive path or skip it intentionally.
- [ ] Confirm output files appear in the emulator replacement folder.
- [ ] Confirm emulator can load at least one replacement texture.

## 3. Texture Manager test

- [ ] Active textures list opens.
- [ ] Missing output filter works.
- [ ] Existing output filter works.
- [ ] Orphaned output filter works.
- [ ] Counters update after new dump appears.
- [ ] Counters update after output appears.
- [ ] Live auto-refresh works while watching.

## 4. Batch Queue test

- [ ] Add two small profiles.
- [ ] Start Batch Queue.
- [ ] Use **Skip to next game** once.
- [ ] Use **Previous game** once.
- [ ] Use **Stop Batch** once.
- [ ] Confirm no duplicate worker starts.
- [ ] Confirm queue state remains readable after navigation.

## 5. Documentation check

- [ ] `README.md` renders correctly on GitHub.
- [ ] Screenshots referenced by README exist.
- [ ] `docs/GETTING_STARTED_TUTORIAL.md` opens correctly.
- [ ] `GITHUB_RELEASE_BODY_v11.10.22.md` is ready to paste.
- [ ] `RELEASE_NOTES_v11.10.22.md` is accurate.

## 6. Create GitHub release

Suggested tag:

```text
v11.10.22
```

Suggested title:

```text
Faithful Remaster v11.10.22 — GitHub Release + Full Tutorial
```

Upload these assets:

```text
Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip
Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip.sha256.txt
```

Paste the content of:

```text
GITHUB_RELEASE_BODY_v11.10.22.md
```

## 7. After publishing

- [ ] Open the public release page in a private/incognito browser.
- [ ] Download the ZIP from the public release asset.
- [ ] Extract and run it.
- [ ] Confirm the downloaded release still shows `v11.10.22`.
- [ ] Copy the final release link for the video description.
- [ ] Only then record or publish the announcement video.

## 8. Suggested Git commands if publishing from a local repo

```bash
git checkout -b release/v11.10.22
git add README.md README_AR.md CHANGELOG.md RELEASE_NOTES_v11.10.22.md GITHUB_RELEASE_BODY_v11.10.22.md docs/GETTING_STARTED_TUTORIAL.md docs/GITHUB_RELEASE_CHECKLIST.md docs/SHOWCASE_VIDEO_PLAN.md VERSION faithful_remaster.py faithful_universal.py
git commit -m "Prepare Faithful Remaster v11.10.22 GitHub release"
git tag v11.10.22
git push origin release/v11.10.22 --tags
```

If you publish directly from GitHub web UI, create a draft release from the Releases page, set the tag to `v11.10.22`, upload the ZIP asset, paste the release body, then publish after final review.
