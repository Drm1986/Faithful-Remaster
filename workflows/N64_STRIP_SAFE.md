# N64 Strip Safe workflow

The shipped API graph is intentionally non-diffusive:

```text
LoadImage → RealESRGAN x4 → SaveImage
```

Mirrored padding and final crop are performed by the routing layer, not by custom ComfyUI nodes. This keeps the workflow replaceable and avoids requiring an additional node pack.

To change it later, edit the `N64 Strip Safe` profile in Workflow Profile Manager and select different UI/API JSON files.
