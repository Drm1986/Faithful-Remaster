# Automatic quarantine behavior

Setting: `auto_quarantine_efb_cutscenes`

Defaults:

- Dolphin: On
- PPSSPP: On
- Other emulators: Off
- Live threshold: 12 files
- Idle flush: 5 seconds

Startup and Batch Queue scan the complete active Dump tree before indexing. Live watching classifies new stable files before they enter processing queues. Confirmed candidates are staged until the threshold is reached, then moved under `_buffer_quarantine` with one session manifest written in chunks. Smaller pending batches are flushed after the idle timeout and when the watcher ends.

The strict detector excludes Safe Blank Cleanup and general Effects/Masks classification. The v11.9.7 UI/sprite/tiny-palette mask protection layer remains in front of every automatic decision.
