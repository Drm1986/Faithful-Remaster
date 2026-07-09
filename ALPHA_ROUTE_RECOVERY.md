# Recovering textures processed with the wrong Alpha route

v11.10.3 automatically repairs profiles whose Alpha API pointed to the bundled N64 Strip Safe or RGB workflow.

The correct route is:

- `workflows\Faithful_Alpha_Workflow_API.json`
- LoadImage: `1`
- SaveImage: `5`
- Invert output: Off

The application does not delete generated Load-folder textures automatically. For textures already showing damaged transparency:

1. Start v11.10.3 and open the affected game profile.
2. Confirm the Alpha route above under **Settings → Workflows & Backends**.
3. Turn **Overwrite existing outputs** On.
4. Reprocess the affected Alpha textures (or the game once if their names are unknown).
5. Turn **Overwrite existing outputs** Off again.

The v11.10.3 cache signature prevents outputs cached under the wrong route from being restored.
