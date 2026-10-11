"""Seed the demo sensors: one of each kind, the sensors on the README screenshots.

    python scripts/seed_demo.py --url http://127.0.0.1:8099 --images http://127.0.0.1:8765/photo
    python scripts/seed_demo.py --app c088851d_visionstate      # an installed app, through Ingress

- Driveway (objects): a person and a car; Emma is recognised by her face (taught from two other
  photos of her) and the car is taught as the own label "Our car".
- Garage door (states): trained on 36 frames drawn from one photo (closed, open, partial).
- Water meter (reading): the wheels of a real water meter, one of them half way between digits.

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
    args = parser.parse_args()
    api = Api(args.url) if args.url else ingress(args.app)
    photo = lambda name: f"{args.images.rstrip('/')}/{name}.jpg"
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
                "classes": ["person", "car"],
                "faces": {"enabled": True, "ask_unknown": False},
            },
            "interval_s": 10,
        }
    )
    if not any(p["name"] == "Emma" for p in api.call("GET", "/people")["people"]):
        # The face model loads (or downloads) once a sensor recognises faces.
        files = [
            ("files", (f"{n}.jpg", (ASSETS / f"{n}.jpg").read_bytes(), "image/jpeg"))
            for n in ("demo_emma_car", "demo_emma_guitar")
        ]
        for _ in range(60):
            r = api.client.post(
                "/people/photos", files=files, data={"new_person": "Emma"}
            )
            if r.status_code != 409:
                break
            time.sleep(3)
        print("Emma:", r.status_code, r.json().get("added"), "faces")
    if driveway:

        def car():
            view = api.call("GET", f"/sensors/{driveway['id']}")
            cars = [d for d in view["objects"]["detections"] if d["key"] == "car"]
            return (view["live"]["frame_id"], cars[0]) if cars else None

        frame_id, box = wait("the car on the driveway", car)
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


if __name__ == "__main__":
    main()
