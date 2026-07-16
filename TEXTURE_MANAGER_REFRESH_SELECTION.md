# Texture Manager Refresh Selection Fix

Faithful Remaster v11.10.25 fixes an annoying Texture Manager behavior where live refresh could rebuild the visible list and select the first texture again.

## What changed

- Before rebuilding the Texture Manager list, the app captures the selected texture path(s), primary selection, top visible item, and scroll position.
- After the refreshed rows finish loading, the app restores the same selected texture when it is still present in the filtered view.
- If the selected texture disappeared because of filters, quarantine, deletion, or folder changes, the app keeps the user near the previous scroll area instead of forcing the first texture.
- The first texture is still auto-selected only on the first empty list load, so the preview panel remains useful when opening Texture Manager for the first time.

No workflow JSON files were changed.
