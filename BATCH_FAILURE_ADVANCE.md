# Batch Failure Advance

v11.10.15 prevents one bad texture from holding the Batch Queue hostage.

A texture that throws a deterministic workflow error is added to an in-memory `failed_this_session` set. That set is used by the automatic scanner, missing-output checker and queue popper so the same file is not re-added immediately.

The file remains absent from `processed.txt`. This is intentional: after fixing the workflow, backend, or model, the next run can retry it normally. Texture Manager `Recreate Selected` also clears the session failure flag so an explicit user retry is honored.

Backend interruption retries still exist, but workflow execution errors such as wrong output size, SaveImage route mistakes, and kernel-size errors do not trigger backend restart retries.
