# Emulator setup

Faithful Remaster needs:

1. A texture Dump folder where the emulator writes original textures.
2. A Load/Replacement folder where the emulator reads enhanced textures.
3. Texture dumping enabled.
4. Custom/replacement texture loading enabled.

## Dolphin

```text
Dump: %APPDATA%\Dolphin Emulator\Dump\Textures\<GAME_ID>
Load: %APPDATA%\Dolphin Emulator\Load\Textures\<GAME_ID>
```

## PPSSPP

```text
Dump: ...\PSP\TEXTURES\<GAME_ID>\new
Load: ...\PSP\TEXTURES\<GAME_ID>
```

## Azahar

```text
Dump: %APPDATA%\Azahar\dump\textures\<TITLE_ID>
Load: %APPDATA%\Azahar\load\textures\<TITLE_ID>
```

Azahar may require `pack.json` in the Load folder. Keep automatic pack synchronization enabled unless there is a specific reason to disable it.

## DuckStation and PCSX2

Use the emulator's own texture dump/replacement options to identify the exact per-game folders, then save them in a Faithful Remaster profile.
