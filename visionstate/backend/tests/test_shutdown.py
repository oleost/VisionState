"""Stopping the app the way Home Assistant does (SIGTERM) must end with exit code 0."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from .conftest import requires_model

BACKEND = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@requires_model
@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM semantics are POSIX-only")
def test_sigterm_stops_cleanly_with_exit_code_zero(tmp_path, model_dir):
    port = free_port()
    env = {
        **os.environ,
        "VISIONSTATE_DATA": str(tmp_path / "data"),
        "VISIONSTATE_MEDIA": str(tmp_path / "media"),
        "VISIONSTATE_FRONTEND": str(tmp_path / "none"),
        "VISIONSTATE_BUNDLED_MODELS": str(model_dir),
        "VISIONSTATE_PORT": str(port),
    }
    proc = subprocess.Popen([sys.executable, "-m", "visionstate"], cwd=BACKEND, env=env)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/v1/status", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            pytest.fail("app did not start")
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
