# On-demand mode comparison

Open **Texture Manager**, select exactly one active texture, then press **Compare modes**.

Faithful Remaster opens a separate viewer containing:

- **Triple** — Original, Clean Heart and Strong Believer side by side.
- **Split A/B** — choose any two views and drag the vertical divider.
- **Overlay A/B** — choose any two views and adjust the blend percentage.
- synchronized mouse-wheel zoom and click-drag pan across every view;
- **Fit** or double-click to reset the image framing;
- a checkerboard background so alpha differences remain visible.

Clean Heart and Strong Believer previews are generated only when comparison is requested. The comparison route uses the same workflow file, configured SaveImage node, backend, expected output scale, separate Alpha workflow and Alpha fallback rules as production processing.

Comparison never changes the game's Load folder, processed log, queue, current output or per-texture workflow override. Valid production hash-cache results are reused. Newly generated previews are stored under the normal route-specific hash cache, so normal processing can reuse them later without another ComfyUI job.

**Refresh comparison** forces both previews to be generated again. Original dumps are always read-only.

## Preview background

Use **Black background** in the comparison toolbar when pale, glowing or partially transparent details are difficult to judge against the checkerboard. The setting applies immediately to Triple, Split A/B and Overlay A/B and is remembered for future comparison windows. It changes only the viewer background.
