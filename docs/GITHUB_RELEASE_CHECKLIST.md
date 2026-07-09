# GitHub Release Checklist for v11.10.22

Use this checklist before publishing or replacing release assets.

## Repository files

- [ ] `README.md` is English-only and renders correctly on GitHub.
- [ ] `CHANGELOG.md` is formatted correctly.
- [ ] `docs/GETTING_STARTED_TUTORIAL.md` exists.
- [ ] `docs/COMFYUI_MODEL_REQUIREMENTS.md` exists.
- [ ] `docs/setup_comfy.md` links to the model requirements guide.
- [ ] No internal planning notes are included.
- [ ] No private owner notes are included.

## ComfyUI requirements

- [ ] The tutorial lists the required upscalers.
- [ ] The tutorial lists the required ControlNet Tile SDXL file.
- [ ] The tutorial lists the required SDXL checkpoint used by the bundled workflows.
- [ ] The tutorial explains that Juggernaut XL requires editing/exporting the workflow if used instead of DreamShaper.
- [ ] Model folder locations are clear.
- [ ] Download/source links are present.

## Application validation

- [ ] App title shows `Faithful Remaster v11.10.22`.
- [ ] `VERSION` file says `11.10.22`.
- [ ] `APP_VERSION` resolves to `11.10.22`.
- [ ] The package ZIP filename says `v11.10.22`.
- [ ] ComfyUI test succeeds.
- [ ] A small RGB texture test succeeds.
- [ ] An alpha/mask test route is checked or intentionally skipped.
- [ ] Texture Manager filters work.
- [ ] Batch Queue Skip is tested.
- [ ] Batch Queue Previous is tested.

## Release assets

- [ ] Upload the Windows ZIP asset.
- [ ] Upload the `.sha256.txt` checksum asset.
- [ ] Delete older replacement assets if updating the same release.
- [ ] Paste the release body from `GITHUB_RELEASE_BODY_v11.10.22.md`.
- [ ] Confirm the release description is English-only.
