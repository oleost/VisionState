"""Seed the demo sensors: one of each kind, the sensors on the README screenshots.

    python scripts/seed_demo.py --url http://127.0.0.1:8099 --images http://127.0.0.1:8765/photo
    python scripts/seed_demo.py --app c088851d_visionstate      # an installed app, through Ingress

- Driveway (objects): people, cars, a bicycle and a cat; Tom (the man on the left) is recognised
  by his face, taught on this camera from this picture and from one of him alone (demo_tom), and
  from a photo of him (demo_tom_photo, as with Add photos on the People page); the silver car is
  taught as the own label "Our car". The faces are small (the pictures are 1586 px wide), so the
  sensor looks for faces down to 26 px; the girl's is too small to call her a stranger, so she
  counts as a person.
- Garage door (states): trained on 36 frames drawn from one photo (closed, open, partial).
- Water meter (reading): the wheels of a real water meter, one of them half way between digits.
- Hallway (sound), only with ``--sound <stream URL>``: listens for a smoke alarm, a siren, a dog,
  crying and someone talking, keeps clips, and knows the own sound "Doorbell" from five drawn
  ding-dongs (tests/sound_stream.chime). It needs the sound model (tools/soundmodel/README.md). With
  the fake camera: ``--sound http://127.0.0.1:<port>/camera.ts``, and ``/play?sound=smoke_alarm``
  plays a smoke alarm into it.

The cameras are HTTP snapshot URLs: ``--images`` + ``/<name>.jpg``, by default the test photos on
GitHub (reachable from any Home Assistant). With the fake camera use ``http://127.0.0.1:<port>/photo``.
Sensors and people that already exist (same name) are left alone, so running it twice is harmless.
``--app`` needs HA_URL and HA_TOKEN (a long-lived token of an administrator) in the environment.
Run it with the backend's Python (it needs httpx, numpy, Pillow and, for --app, websockets).
Images: visionstate/backend/tests/assets (sources and licences in its README.md).
"""

import argparse
import asyncio
import io
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ASSETS = (
    Path(__file__).resolve().parent.parent
    / "visionstate"
    / "backend"
    / "tests"
    / "assets"
)
GITHUB = "https://raw.githubusercontent.com/oleost/VisionState/beta/visionstate/backend/tests/assets"

# The garage door in demo_garage.jpg (pixels) and the sensor's region around it.
DOOR = {"tl": (130, 45), "tr": (620, 130), "br": (615, 585), "bl": (130, 685)}
GARAGE_ROI = {"x": 0.1, "y": 0.06, "w": 0.43, "h": 0.94}


def garage_frames(per_state: int = 12) -> dict[str, list[bytes]]:
    """Frames of the garage door closed, open and half open, with some light and camera jitter."""
    rnd = random.Random(7)
    base = Image.open(ASSETS / "demo_garage.jpg").convert("RGB")
    w, h = base.size

    def lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def raised(frac: float) -> Image.Image:
        """The door lifted by ``frac``: daylight shows below it."""
        top_l, top_r = (
            lerp(DOOR["tl"], DOOR["bl"], 1 - frac),
            lerp(DOOR["tr"], DOOR["br"], 1 - frac),
        )
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon([top_l, top_r, DOOR["br"], DOOR["bl"]], fill=255)
        m = (
            np.asarray(mask.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)[
                :, :, None
            ]
            / 255
        )
        outside = np.clip(
            235
            - 70 * np.linspace(0, 1, h)[:, None]
            + np.random.default_rng(1).normal(0, 6, (h, w)),
            0,
            255,
        )
        out = np.asarray(base, dtype=np.float32) * (1 - m) + outside[:, :, None] * m
        img = Image.fromarray(np.clip(out, 0, 255).astype("uint8"))
        ImageDraw.Draw(img).line([top_l, top_r], fill=(70, 70, 70), width=6)
        glow = Image.new("L", (w, h), 0)
        ImageDraw.Draw(glow).polygon(
            [DOOR["bl"], DOOR["br"], (w, h), (0, h)], fill=int(90 * frac)
        )
        return Image.composite(
            Image.new("RGB", (w, h), (255, 255, 255)),
            img,
            glow.filter(ImageFilter.GaussianBlur(40)),
        )

    def jitter(img: Image.Image) -> bytes:
        img = ImageEnhance.Brightness(img).enhance(rnd.uniform(0.75, 1.2))
        img = ImageEnhance.Contrast(img).enhance(rnd.uniform(0.85, 1.15))
        dx, dy = rnd.randint(-10, 10), rnd.randint(-8, 8)
        img = img.transform(
            img.size,
            Image.Transform.AFFINE,
            (1, 0, dx, 0, 1, dy),
            fillcolor=(40, 40, 40),
        )
        noise = np.random.default_rng(rnd.randint(0, 1 << 30)).normal(
            0, rnd.uniform(2, 7), (h, w, 3)
        )
        img = Image.fromarray(
            np.clip(np.asarray(img, dtype=np.float32) + noise, 0, 255).astype("uint8")
        )
        buf = io.BytesIO()
        img.filter(ImageFilter.GaussianBlur(rnd.uniform(0, 1.2))).save(
            buf, format="JPEG", quality=88
        )
        return buf.getvalue()

    make = {
        "closed": lambda: base,
        "open": lambda: raised(rnd.uniform(0.85, 0.95)),
        "partial": lambda: raised(rnd.uniform(0.35, 0.6)),
    }
    return {key: [jitter(fn()) for _ in range(per_state)] for key, fn in make.items()}


class Api:
    """The app's API, directly (``--url``) or through Home Assistant Ingress (``--app``)."""

    def __init__(self, base: str, cookies: dict | None = None):
        self.client = httpx.Client(
            base_url=base.rstrip("/") + "/api/v1", cookies=cookies, timeout=180
        )

    def call(self, method: str, path: str, **kwargs):
        r = self.client.request(method, path, **kwargs)
        if r.status_code >= 400:
            sys.exit(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None


def ingress(app: str) -> Api:
    from websockets.asyncio.client import connect

    ha = os.environ["HA_URL"].rstrip("/")
    token = os.environ["HA_TOKEN"]

    async def supervisor(endpoint: str, method: str = "get") -> dict:
        async with connect(ha.replace("http", "ws", 1) + "/api/websocket") as ws:
            await ws.recv()
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            await ws.recv()
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "type": "supervisor/api",
                        "endpoint": endpoint,
                        "method": method,
                    }
                )
            )
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == 1:
                    if not msg.get("success"):
                        sys.exit(f"Supervisor {endpoint}: {msg.get('error')}")
                    return msg["result"]

    session = asyncio.run(supervisor("/ingress/session", "post"))["session"]
    entry = asyncio.run(supervisor(f"/addons/{app}/info"))["ingress_entry"]
    return Api(ha + entry, {"ingress_session": session})


def wait(what: str, check, timeout: float = 180):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(2)
    sys.exit(f"Timed out waiting for {what}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="the app's address, e.g. http://127.0.0.1:8099")
    target.add_argument(
        "--app", help="slug of an installed app, reached through Ingress"
    )
    parser.add_argument(
        "--images",
        default=GITHUB,
        help="where the cameras get the photos (default: GitHub)",
    )
    parser.add_argument(
        "--sound",
        help="a stream with sound for the Hallway sound sensor (left out without it)",
    )
    args = parser.parse_args()
    api = Api(args.url) if args.url else ingress(args.app)
    photo = lambda name: f"{args.images.rstrip('/')}/{name}.jpg"
    # The cameras must see the pictures as they are here: right after a push, GitHub serves the
    # older ones for a few minutes, and a face taught from an older picture is someone else's.
    for name in ("demo_driveway", "demo_tom", "demo_garage", "counter_turning"):
        served = httpx.get(photo(name), timeout=30, follow_redirects=True).content
        if served != (ASSETS / f"{name}.jpg").read_bytes():
            sys.exit(
                f"{photo(name)} is not the picture in tests/assets (GitHub serves a pushed change after a few minutes)"
            )
    existing = {s["name"]: s for s in api.call("GET", "/sensors")}

    def create(spec: dict) -> dict | None:
        if spec["name"] in existing:
            print(f"{spec['name']}: already there")
            return None
        sensor = api.call("POST", "/sensors", json=spec)
        print(f"{spec['name']}: created (id {sensor['id']})")
        return sensor

    garage = create(
        {
            "name": "Garage door",
            "source_type": "http",
            "source": photo("demo_garage"),
            "roi": GARAGE_ROI,
            "states": [{"name": "Closed"}, {"name": "Open"}, {"name": "Partial"}],
            "interval_s": 30,
        }
    )
    if garage:
        for key, frames in garage_frames().items():
            files = [
                ("files", (f"{key}_{i:02d}.jpg", data, "image/jpeg"))
                for i, data in enumerate(frames)
            ]
            api.call(
                "POST",
                f"/sensors/{garage['id']}/uploads",
                files=files,
                data={"state_key": key},
            )

    meter = create(
        {
            "name": "Water meter",
            "kind": "reading",
            "source_type": "http",
            "source": photo("counter_turning"),
            "reading": {
                "mode": "counter",
                "display": "counter",
                "digits": 7,
                "decimals": 3,
                "unit": "m³",
                "device_class": "water",
            },
            "interval_s": 30,
            "debounce": 1,
        }
    )

    driveway = create(
        {
            "name": "Driveway",
            "kind": "objects",
            "source_type": "http",
            "source": photo("demo_driveway"),
            "objects": {
                "classes": ["person", "car", "bicycle", "cat"],
                "faces": {"enabled": True, "ask_unknown": False, "min_face_px": 26},
            },
            "interval_s": 10,
        }
    )
    if driveway:

        def seen(key: str, after: str | None = None):
            """The last check's frame and its boxes of ``key``, left to right (None until there are
            any, in a frame other than ``after``)."""
            view = api.call("GET", f"/sensors/{driveway['id']}")
            boxes = sorted(
                (d for d in view["objects"]["detections"] if d["key"] == key),
                key=lambda d: d["box"][0],
            )
            frame_id = view["live"]["frame_id"]
            return (frame_id, boxes) if boxes and frame_id != after else None

        def teach_face(frame_id: str, box: dict, **who) -> httpx.Response:
            body = {
                "frame_id": frame_id,
                "box": box["box"],
                "score": box["score"],
                **who,
            }
            return api.client.post(f"/sensors/{driveway['id']}/faces", json=body)

    if driveway and not any(
        p["name"] == "Tom" for p in api.call("GET", "/people")["people"]
    ):
        # Tom is the man on the left; the face model loads (or downloads) once a sensor
        # recognises faces, so the first tries may be answered 409.
        for _ in range(60):
            frame_id, people = wait(
                "the people on the driveway", lambda: seen("person")
            )
            r = teach_face(frame_id, people[0], new_person="Tom")
            if r.status_code != 409:
                break
            time.sleep(3)
        if r.status_code >= 400:
            sys.exit(f"Tom: {r.status_code} {r.text[:300]}")
        # A second face of him, from a picture of him alone on the same camera: the camera looks at
        # it for a moment, then back at the driveway.
        api.call(
            "PATCH", f"/sensors/{driveway['id']}", json={"source": photo("demo_tom")}
        )
        try:
            frame_id, people = wait(
                "Tom alone",
                lambda: (s := seen("person", frame_id)) and len(s[1]) == 1 and s,
            )
            r = teach_face(frame_id, people[0], person="tom")
            if r.status_code >= 400:
                sys.exit(f"Tom alone: {r.status_code} {r.text[:300]}")
        finally:
            api.call(
                "PATCH",
                f"/sensors/{driveway['id']}",
                json={"source": photo("demo_driveway")},
            )
        tom_alone = frame_id
        files = [
            (
                "files",
                (
                    "demo_tom_photo.jpg",
                    (ASSETS / "demo_tom_photo.jpg").read_bytes(),
                    "image/jpeg",
                ),
            )
        ]
        r = api.client.post("/people/photos", files=files, data={"person": "tom"})
        if r.status_code >= 400 or not r.json()["added"]:
            sys.exit(f"Tom's photo: {r.status_code} {r.text[:300]}")
        print(f"Tom: {r.json()['faces']} faces taught")
    else:
        tom_alone = None
    if driveway:
        # Our car is the silver one in front: the largest car box, in a frame of the driveway with
        # both people on it (not one of Tom alone, also not one a check took while it switched back).
        def driveway_cars():
            people = seen("person", tom_alone)
            cars = seen("car", tom_alone)
            return (
                cars
                if people and cars and len(people[1]) == 2 and people[0] == cars[0]
                else None
            )

        frame_id, cars = wait("the cars on the driveway", driveway_cars)
        box = max(
            cars,
            key=lambda d: (d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1]),
        )
        api.call(
            "POST",
            f"/sensors/{driveway['id']}/taught",
            json={
                "frame_id": frame_id,
                "box": box["box"],
                "detected": "car",
                "score": box["score"],
                "new_label": "Our car",
            },
        )
        print("Our car: taught")
    if meter:
        value = wait(
            "the water meter",
            lambda: api.call("GET", f"/sensors/{meter['id']}")["reading"]["value"],
        )
        print("Water meter reads", value)
    hallway = (
        create(
            {
                "name": "Hallway",
                "kind": "sound",
                "source_type": "rtsp",
                "source": args.sound,
                "sound": {
                    "sounds": ["smoke_alarm", "siren", "dog_bark", "crying", "speech"],
                    "clips": True,
                },
            }
        )
        if args.sound
        else None
    )
    if hallway:
        sys.path.insert(0, str(ASSETS.parent.parent))  # the backend, for the drawn doorbell
        sys.path.insert(0, str(ASSETS.parent))
        from sound_stream import chime, wav_header  # noqa: PLC0415

        def wav(samples: np.ndarray) -> bytes:
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
            return wav_header() + pcm.tobytes()

        bells = [chime(gain=0.2 + 0.05 * i, seed=i) for i in range(5)]
        files = [
            ("files", (f"doorbell_{i}.wav", wav(bell), "audio/wav"))
            for i, bell in enumerate(bells)
        ]
        made = api.call(
            "POST",
            f"/sensors/{hallway['id']}/sounds/upload",
            files=files,
            data={"new_name": "Doorbell"},
        )
        print(f"Doorbell: {len(made['added'])} clips taught")


if __name__ == "__main__":
    main()
