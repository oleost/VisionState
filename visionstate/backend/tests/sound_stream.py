"""A stream of sound for tests: an HTTP server that sends WAV audio without end, the way an ESP32
microphone (or ``scripts/fake_camera.py``'s ``/sound.wav``) does. What it plays can be changed
while it runs (``play``)."""

from __future__ import annotations

import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from visionstate import sounds

RATE = 16_000
SOUNDS = Path(__file__).parent / "assets" / "sounds"  # CC0 recordings, see assets/README.md


def recording(name: str) -> np.ndarray:
    """One of the CC0 recordings in tests/assets/sounds, 16 kHz mono."""
    return sounds.decode_audio((SOUNDS / f"{name}.m4a").read_bytes(), RATE)


def noise(seconds: float, level: float = 0.003, seed: int = 0) -> np.ndarray:
    """A quiet room: faint noise."""
    return (np.random.default_rng(seed).standard_normal(int(seconds * RATE)) * level).astype(np.float32)


def wav_header(rate: int = RATE) -> bytes:
    """A WAV header for 16-bit mono PCM of unknown length (the largest size), as a live stream sends it."""
    size = 0x7FFFFFFF
    return (
        b"RIFF"
        + struct.pack("<I", size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", size - 36)
    )


class SoundServer:
    """Serves ``/sound.wav``: ``background`` (looped), with ``play``-ed sounds mixed in once each.
    ``speed`` above 1 sends faster than real time (tests that need not wait)."""

    def __init__(self, background: np.ndarray | None = None, speed: float = 1.0):
        self.background = noise(5.0) if background is None else background
        self.speed = speed
        self._queue: list[np.ndarray] = []
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.end_headers()
                try:
                    self.wfile.write(wav_header())
                    server._send(self.wfile)
                except BrokenPipeError, ConnectionResetError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/sound.wav"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def play(self, samples: np.ndarray) -> None:
        with self._lock:
            self._queue.append(samples.astype(np.float32))

    def _send(self, out) -> None:  # noqa: ANN001 - the response's file
        chunk = RATE // 10
        position = 0
        playing = np.zeros(0, dtype=np.float32)
        while True:
            with self._lock:
                if not len(playing) and self._queue:
                    playing = self._queue.pop(0)
            part = np.resize(np.roll(self.background, -position), chunk)
            position = (position + chunk) % len(self.background)
            if len(playing):
                extra = playing[:chunk]
                part[: len(extra)] += extra
                playing = playing[chunk:]
            out.write((np.clip(part, -1, 1) * 32767).astype("<i2").tobytes())
            out.flush()
            time.sleep(chunk / RATE / self.speed)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
