# Faithful Remaster v11.10.22 — GitHub upload steps

Repository:

```text
https://github.com/Drm1986/Faithful-Remaster
```

This package is for updating the **source repository** on `main` or on a release branch. Do **not** commit the Windows release ZIP, checksum file, `Faithful Remaster.exe`, or `__pycache__` into the source repo. Upload the Windows ZIP and checksum only as GitHub Release assets.

## Recommended safe flow

```bash
git clone https://github.com/Drm1986/Faithful-Remaster.git
cd Faithful-Remaster
git checkout main
git pull origin main
git checkout -b release/v11.10.22
```

Copy the contents of this `GitHub-main-update-files` folder over the repository root, replacing existing files when asked.

Then run:

```bash
python -m py_compile faithful_remaster.py faithful_universal.py
git status
git add -A
git commit -m "Prepare Faithful Remaster v11.10.22 release"
git push -u origin release/v11.10.22
```

Open GitHub and create a Pull Request from `release/v11.10.22` into `main`. After reviewing the file list, merge it.

## Faster direct-main flow

Use this only if you are confident and have a local backup.

```bash
git clone https://github.com/Drm1986/Faithful-Remaster.git
cd Faithful-Remaster
git checkout main
git pull origin main
```

Copy the contents of this folder over the repository root, then:

```bash
python -m py_compile faithful_remaster.py faithful_universal.py
git status
git add -A
git commit -m "Prepare Faithful Remaster v11.10.22 release"
git tag v11.10.22
git push origin main
git push origin v11.10.22
```

## GitHub Release

On GitHub:

1. Go to **Releases**.
2. Click **Draft a new release**.
3. Choose or create tag: `v11.10.22`.
4. Release title: `Faithful Remaster v11.10.22 — GitHub Release Tutorial`.
5. Paste the contents of `GITHUB_RELEASE_BODY_v11.10.22.md`.
6. Upload these assets:

```text
Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip
Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip.sha256.txt
```

7. Publish the release.

## After publishing

Use this stable latest-release link after the release is live:

```text
https://github.com/Drm1986/Faithful-Remaster/releases/latest
```

Direct asset link after publishing:

```text
https://github.com/Drm1986/Faithful-Remaster/releases/latest/download/Faithful-Remaster-v11.10.22-GitHub-Release-Tutorial-Windows.zip
```

## Final public release check

- The repo README says `Current release: v11.10.22`.
- The app top title says `Faithful Remaster v11.10.22`.
- The release page shows asset ZIP + SHA256.
- Downloading the release ZIP and opening the app shows the same version number.
- The tutorial file is visible at `docs/GETTING_STARTED_TUTORIAL.md`.
- The Windows ZIP is uploaded as a release asset, not committed into the source repo.
