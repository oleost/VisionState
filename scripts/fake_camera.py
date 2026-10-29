"""Fake HTTP camera for local development and UI tests.

    python scripts/fake_camera.py 8765

GET /snapshot.jpg                returns a synthetic garage door frame (random lighting noise)
GET /set?state=open|closed|partial   changes what the camera shows
GET /set?night=1                 switches to a greyscale "IR" image
GET /photo/<name>.jpg            a real photo from visionstate/backend/tests/assets (for object sensors)
GET /display.jpg?text=12.5&style=lcd|led   a drawn seven-segment display (for reading sensors)
GET /counter.jpg?value=89939.5&digits=7&decimals=3   a drawn mechanical counter with rolling digits
                                 (value = the number on the wheels; .5 = last wheel half way)
GET /set?wider=400&camera=a      counter.jpg?…&camera=a comes back 400 px wider (a camera that changed
                                 its picture size after a restart); wider=0 sets it back
GET /sound.wav                   endless sound (a quiet room), as an ESP32 microphone sends it (for sound sensors)
GET /camera.ts                   a camera with sound: H.264 video and the same sound as AAC (MPEG-TS)
GET /play?sound=siren            plays a sound once in both: siren, dog_bark, smoke_alarm, speech (the CC0
                                 recordings of backend/tests/assets/sounds) or chime (a drawn doorbell)
"""

import io
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

TESTS = Path(__file__).resolve().parent.parent / "visionstate" / "backend" / "tests"
PHOTOS = TESTS / "assets"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))  # the backend package, for the sound stream
from displays import STYLES, render_counter  # noqa: E402  (shared with the backend tests)
from displays import render as render_display  # noqa: E402

SOUND = None  # tests/sound_stream.SoundSource, made on first use (it needs the backend's packages)


def sound_source():  # noqa: ANN201
    global SOUND
    if SOUND is None:
        from sound_stream import SoundSource

        SOUND = SoundSource()
    return SOUND

STATE = {"state": "closed", "night": False}
WIDER: dict[str, int] = {}  # counter.jpg's "camera" -> pixels added on the right
DOOR_HEIGHT = {"open": 0.08, "closed": 1.0, "partial": 0.5}


def render() -> bytes:
    night = STATE["night"]
    shade = random.randint(80, 140)
    img = Image.new("RGB", (1280, 720), (shade, shade, shade) if night else (shade - 20, shade - 10, shade + 10))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 70, 1280, 610), fill=(70, 70, 70) if night else (84, 93, 105))
    d.rectangle((300, 200, 980, 610), fill=(12, 14, 16))
    d.rectangle((360, 470, 520, 610), fill=(40, 42, 46))
    bottom = 200 + int(410 * DOOR_HEIGHT[STATE["state"]])
    d.rectangle((300, 200, 980, bottom), fill=(190, 190, 190) if night else (199, 204, 211))
    for y in range(230, bottom, 60):
        d.line((300, y, 980, y), fill=(150, 150, 150) if night else (160, 168, 178), width=6)
    d.rectangle((0, 610, 1280, 720), fill=(40, 40, 40) if night else (44, 50, 56))
    x = random.randint(20, 200)
    d.rectangle((x, 520, x + 60, 700), fill=(60, 60, 60) if night else (60, 80, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path in ("/sound.wav", "/camera.ts"):
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav" if url.path == "/sound.wav" else "video/mp2t")
            self.end_headers()
            source = sound_source()
            try:
                source.send_wav(self.wfile) if url.path == "/sound.wav" else source.send_camera(self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if url.path == "/play":
            from sound_stream import chime, recording

            name = parse_qs(url.query).get("sound", ["siren"])[0]
            if name == "chime":
                sound_source().play(chime())
            elif name in ("siren", "dog_bark", "smoke_alarm", "speech"):
                sound_source().play(recording(name))
            else:
                self.send_error(404)
                return
            self._send(b"playing", "text/plain")
            return
        if url.path == "/set":
            query = parse_qs(url.query)
            if query.get("state", [""])[0] in DOOR_HEIGHT:
                STATE["state"] = query["state"][0]
            if "night" in query:
                STATE["night"] = query["night"][0] == "1"
            if "wider" in query:
                WIDER[query.get("camera", [""])[0]] = int(query["wider"][0])
            self._send(str(STATE).encode(), "text/plain")
        elif url.path == "/display.jpg":
            query = parse_qs(url.query)
            text = "".join(c for c in query.get("text", ["0"])[0] if c in "0123456789-:.")[:16] or "0"
            style = query.get("style", ["lcd"])[0]
            buf = io.BytesIO()
            render_display(text, style if style in STYLES else "lcd").save(buf, format="JPEG", quality=90)
            self._send(buf.getvalue(), "image/jpeg")
        elif url.path == "/counter.jpg":
            query = parse_qs(url.query)

            def number(name: str, default: float, low: float, high: float) -> float:
                try:
                    return min(high, max(low, float(query.get(name, [default])[0])))
                except ValueError:
                    return default

            digits = int(number("digits", 7, 1, 8))
            image = render_counter(number("value", 0, 0, 10**digits), digits, int(number("decimals", 3, 0, digits)))
            if wider := WIDER.get(query.get("camera", [""])[0], 0):
                wide = Image.new("RGB", (image.width + wider, image.height), (40, 40, 40))
                wide.paste(image, (0, 0))
                image = wide
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=90)
            self._send(buf.getvalue(), "image/jpeg")
        elif url.path.startswith("/photo/"):
            photo = PHOTOS / Path(url.path).name  # name only: no paths outside the folder
            if photo.suffix != ".jpg" or not photo.is_file():
                self.send_error(404)
                return
            self._send(photo.read_bytes(), "image/jpeg")
        else:
            self._send(render(), "image/jpeg")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
