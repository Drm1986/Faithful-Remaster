# Texture Manager Sort + Group

Faithful Remaster v11.10.11 adds an asset-browser style organizer to Texture Manager.

## Controls

The Texture Manager toolbar now has:

- **Sort** — changes the ordering of the visible list.
- **Group** — inserts section headers without changing the files.
- **Filter** — still controls what is visible.
- **Search** — still narrows the current view.

The three layers work together:

`Filter → Search → Sort → Group`

No sorting or grouping option moves, renames, processes or deletes any texture.

## Sort options

- Newest first
- Oldest first
- Name A → Z
- Name Z → A
- Resolution largest
- Resolution smallest
- File size largest
- File size smallest
- Unprocessed first
- Processed first
- Alpha first
- Opaque first
- Masks / Gray first
- Color / RGB first
- Mode override first
- Exceptions first

## Group options

- No grouping
- Processing status
- Texture type
- Alpha / opacity
- Resolution
- Mode override
- File size class
- Quarantine reason

## Notes

- Group headers are not textures. Selecting a header does nothing.
- Multi-select still works across grouped sections.
- The setting is saved per profile when possible, with a global fallback.
- Quarantined remains a separate data source and is never mixed with active textures.
