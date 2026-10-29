"""A stream of sound for tests: an HTTP server that sends WAV audio without end, the way an ESP32
microphone does, or a camera's stream with sound (``scripts/fake_camera.py`` serves the same). What
it plays can be changed while it runs (``play``)."""

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


def chime(seconds: float = 3.0, gain: float = 0.3, seed: int = 0) -> np.ndarray:
    """A drawn two-tone doorbell (ding-dong) with a little noise."""
    t = np.arange(int(seconds * RATE)) / RATE
    out = np.zeros_like(t)
    for start, freq in ((0.3, 784.0), (1.0, 622.0)):
        m = t >= start
        decay = np.exp(-(t[m] - start) * 2.5)
        out[m] += gain * decay * (np.sin(2 * np.pi * freq * t[m]) + 0.3 * np.sin(2 * np.pi * 2 * freq * t[m]))
    return (out + noise(seconds, 0.002, seed)).astype(np.float32)


class SoundSource:
    """Endless sound: ``background`` (looped), with ``play``-ed sounds mixed in once each, sent as
    WAV (``send_wav``) or as a camera's MPEG-TS (``send_camera``: H.264 video — a bar moving across, a
    key frame every two seconds — with the sound as AAC). ``speed`` above 1 sends faster than real
    time (tests that need not wait)."""

    def __init__(self, background: np.ndarray | None = None, speed: float = 1.0):
        self.background = noise(5.0) if background is None else background
        self.speed = speed
        self._queue: list[np.ndarray] = []
        self._lock = threading.Lock()

    def play(self, samples: np.ndarray) -> None:
        with self._lock:
            self._queue.append(samples.astype(np.float32))

    def _chunks(self):  # noqa: ANN202 - endless tenths of a second of sound
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
            yield np.clip(part, -1, 1).astype(np.float32)
            time.sleep(chunk / RATE / self.speed)

    def send_wav(self, out) -> None:  # noqa: ANN001 - the response's file
        out.write(wav_header())
        for part in self._chunks():
            out.write((part * 32767).astype("<i2").tobytes())
            out.flush()

    def send_camera(self, out) -> None:  # noqa: ANN001 - the response's file
        import av

        with av.open(out, mode="w", format="mpegts") as container:
            video = container.add_stream("libx264", rate=10)
            video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
            video.options = {"g": "20", "preset": "ultrafast", "tune": "zerolatency"}
            audio = container.add_stream("aac", rate=RATE, layout="mono")
            for n, part in enumerate(self._chunks()):
                picture = np.zeros((180, 320, 3), dtype=np.uint8)
                picture[:, (n * 4) % 300 : (n * 4) % 300 + 20] = 255
                frame = av.VideoFrame.from_ndarray(picture, format="rgb24")
                frame.pts = n
                for packet in video.encode(frame):
                    container.mux(packet)
                sound = av.AudioFrame.from_ndarray(part[None, :], format="flt", layout="mono")
                sound.sample_rate, sound.pts = RATE, n * len(part)
                for packet in audio.encode(sound):
                    container.mux(packet)
                out.flush()


class SoundServer(SoundSource):
    """A SoundSource on an HTTP server of its own: ``url`` is ``/sound.wav``, or with ``video`` a
    camera, ``/camera.ts``."""

    def __init__(self, background: np.ndarray | None = None, speed: float = 1.0, video: bool = False):
        super().__init__(background, speed)
        source = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

            def do_GET(self) -> None:  # noqa: N802
                camera = self.path.endswith(".ts")
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t" if camera else "audio/wav")
                self.end_headers()
                try:
                    source.send_camera(self.wfile) if camera else source.send_wav(self.wfile)
                except BrokenPipeError, ConnectionResetError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        name = "camera.ts" if video else "sound.wav"
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/{name}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
