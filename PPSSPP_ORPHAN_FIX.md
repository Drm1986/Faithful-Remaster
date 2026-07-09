# PPSSPP Orphaned Output Fix

PPSSPP normally uses a nested texture layout:

```text
PSP\TEXTURES\<GAME_ID>\new      # dumps
PSP\TEXTURES\<GAME_ID>           # replacements/load
```

Because the dump folder lives inside the replacement root, the v11.10.13 orphaned-output scanner could scan the `new` folder as if it were replacement output. That made valid dumps appear as `ORPHANED OUTPUT`.

v11.10.14 fixes this by excluding the active dump subtree from the output/orphan scan before matching outputs. The active texture list, Existing output, Missing output, and Orphaned output filters now use the intended PPSSPP layout.

No workflows and no processing logic were changed.
