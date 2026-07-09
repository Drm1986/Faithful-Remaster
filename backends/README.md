# Backend profiles

`backend_profiles.json` contains the bundled adapter examples. Runtime edits are saved in `%APPDATA%\Faithful Remaster\backend_profiles.json`.

Supported adapter types:

- `comfyui`: uses `api_url`, and optionally `start_file`.
- `external_command`: runs `command_template` and expects the command to create `{output}`.

The program core does not need to be modified when backend URLs, launchers or command templates change.
