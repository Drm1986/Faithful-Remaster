# Troubleshooting

## ComfyUI is offline

- Start ComfyUI.
- Confirm the configured URL.
- Open the URL in a browser.
- Use the Faithful Remaster connection test.

## A texture is processed but not visible in the emulator

- Confirm the output filename is unchanged.
- Confirm the Load folder is the exact folder used by the emulator.
- Restart the game or emulator.
- Avoid loading an old save state during the first test.
- Confirm custom textures are enabled.

## Azahar ignores generated textures

- Open the game's custom texture location from Azahar itself.
- Confirm the Title ID is correct.
- Enable custom texture loading.
- Keep automatic `pack.json` synchronization enabled.
- Restart the game after new textures are added.

## Preview collapses or changes layout

Use v11.4.3 or newer. Preview panels use fixed-size canvases.

## Settings or profiles appear missing

Open:

```text
%APPDATA%\Faithful Remaster
```

v11.5.0 and newer read user data from this shared folder. The Advanced tab contains a button to open it.

## Database has no names

- Press **Update Game Database** once.
- Watch the Monitor logs for imported-entry counts.
- Some 3DS Title IDs may not exist in the selected public metadata source; game names can still be entered manually.
