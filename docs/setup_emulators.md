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

## Flycast — Dreamcast / Naomi / Atomiswave

Flycast uses sibling `texdump` and `textures` folders inside its data directory.

```text
Dump: ...\Flycast\data\texdump\<GAME_ID>
Load: ...\Flycast\data\textures\<GAME_ID>
```

Enable **Dump Textures** to create the game folder. For normal replacement testing, disable dumping and enable **Load Custom Textures**. Faithful Remaster keeps the exact relative filename and subfolder structure.

The **Auto-fill Folders** button searches common portable Flycast locations. If detection misses a custom installation, browse to the game's folder under `data\texdump`; the matching output folder is derived automatically.

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

## PCSX2

```text
Dump: ...\textures\<GAME_SERIAL>\dumps
Load: ...\textures\<GAME_SERIAL>\replacements
```

## DuckStation

DuckStation allows the texture root to be changed in its settings. Within that root:

```text
Dump: ...\textures\<GAME_SERIAL>\dumps
Load: ...\textures\<GAME_SERIAL>\replacements
```

DuckStation normally creates `config.yaml` inside the game's texture folder; Faithful Remaster uses it to distinguish a custom DuckStation root from PCSX2 when required.


## Flycast automatic game names

When **Profiles → Discover Games** scans the Flycast `data\texdump` root, the app looks up each folder ID in the local Dreamcast title database. On the first Flycast scan, only the Dreamcast metadata source is installed automatically in a background thread. The review list shows the readable title and the original folder ID. Unknown IDs remain valid and use the folder name as a fallback.


## Discover Games

Only the **Dump folder** is required. Select either the global texture root or a per-game dump folder. Faithful Remaster detects the emulator and derives the matching Load / Replacement folder automatically. If a completely custom PlayStation path is indistinguishable between PCSX2 and DuckStation, the program asks once rather than choosing silently.
