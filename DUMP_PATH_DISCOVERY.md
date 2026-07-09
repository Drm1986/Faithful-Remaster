# Dump-path discovery

## Discover Games

Open **Profiles → Discover games**, choose one folder, then click **Scan for Games**.

You may choose:

- the emulator's global dump/texture root, or
- one game's folder, or
- the final dump leaf such as `dumps`, `new`, or `GLideNHQ`.

Faithful Remaster identifies the emulator from folder names, standard directory structure, game-ID shape, and small metadata files such as DuckStation's `config.yaml`. It then derives the replacement path for each game.

PCSX2 and DuckStation intentionally share the same basic layout. When the parent folders and metadata do not identify which emulator owns a custom path, the program asks once instead of guessing silently.

## Profile setup

When browsing for a **Dump folder** in Game Setup, the program:

1. detects the emulator when confidence is high;
2. normalizes a selected per-game folder to the exact dump leaf;
3. obtains the Game ID from the correct folder level;
4. fills the exact Replacement / Load folder.

The real emulator folders are never renamed.
