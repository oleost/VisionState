"""Nothing phones home (CLAUDE.md): ONNX Runtime's own telemetry, on by default in its official
builds, is switched off before it starts, in the app and in its image."""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND.parent / "Dockerfile"


def test_onnx_runtime_telemetry_is_off_whatever_the_environment_says():
    code = (
        "import os; os.environ['ORT_DISABLE_TELEMETRY'] = '0'; "
        "import visionstate.backbones, onnxruntime; print(os.environ['ORT_DISABLE_TELEMETRY'])"
    )
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=BACKEND)
    assert got.stdout.strip() == "1"


def test_the_image_switches_it_off_for_every_python_run():
    assert "ORT_DISABLE_TELEMETRY=1" in DOCKERFILE.read_text(encoding="utf-8")
