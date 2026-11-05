"""VisionState backend."""

import os

# ONNX Runtime's official builds (Linux too, since 1.29) send telemetry to Microsoft unless this is
# set before it starts (its docs/Privacy.md). VisionState sends nothing anywhere (CLAUDE.md), so it
# is switched off before anything of ours imports ONNX Runtime, whatever the environment says. The
# app image sets it as well (Dockerfile), for every Python run in it.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
