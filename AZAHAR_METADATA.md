# Azahar metadata resolution

Faithful Remaster treats the 16-digit directory name as the immutable Azahar Title ID. The display identity is separate.

## Resolution order

1. Manual game name / manual artwork
2. Cached Azahar SMDH title and icon
3. Fresh SMDH metadata from the Azahar library or installed titles
4. Local universal title database
5. Raw 16-digit Title ID

## Persistent files

```text
%APPDATA%\Faithful Remaster\azahar_game_metadata.json
%APPDATA%\Faithful Remaster\azahar_icons\<TITLE_ID>.png
```

## Safety

The metadata refresh is read-only for Azahar files. It never changes ROMs, installed titles, `qt-config.ini`, or texture folder names.
