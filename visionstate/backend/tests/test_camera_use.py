"""An ESP32 camera of ours (esphome/): known by the X-VisionState-Camera header of its snapshots, it
is set up with each sensor's camera settings before its picture (firmware 1.5.0 on: ?size=&quality=
&clock=&exposure=&gain=; older firmware: ?use=, the preset they are); any other camera is asked as it
always was."""

import io
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate.main import create_app
from visionstate.settings import CAMERA_PRESET_BY_KIND, CAMERA_PRESETS, camera_preset, camera_settings, merge_triggers
from visionstate.sources import CameraUses, said_settings

from .test_sound import wait_for
from .test_storage import settings  # noqa: F401 (fixture)

JPEG = io.BytesIO()
Image.new("RGB", (64, 48), (90, 90, 90)).save(JPEG, "JPEG")
DARK = io.BytesIO()
Image.new("RGB", (64, 48), (4, 4, 4)).save(DARK, "JPEG")

METER = CAMERA_PRESETS["meter"]["settings"]
STATES_QUERY = "size=800x600&quality=10&clock=20&exposure=auto&gain=4"
METER_QUERY = "size=1600x1200&quality=8&clock=20&exposure=auto&gain=4"


class Camera(BaseHTTPRequestHandler):
    ours = True
    firmware = "settings"  # 1.5.0 on; an older firmware says the preset it is set up as
    asked: list[str] = []  # noqa: RUF012 - shared by the server's threads on purpose
    now = ""  # how it is set up (X-VisionState-Camera-Settings), as the firmware keeps it
    use = "states"
    flash = False  # takes a picture with its own flash (?flash=<ms>, firmware 1.4.0 on)
    dark = False  # the place is dark: a picture without the flash is (nearly) black
    flashed = 0  # pictures answered with the flash

    def log_message(self, *args):  # noqa: ANN002
        pass

    def do_GET(self):  # noqa: N802
        cls = type(self)
        cls.asked.append(self.path)
        query = parse_qs(urlparse(self.path).query)
        if "use=" in self.path:
            cls.use = query["use"][0]
        if "size" in query:
            keys = ("size", "quality", "clock", "exposure", "gain")
            cls.now = "&".join(f"{k}={query[k][0]}" for k in keys if k in query)
        asked_flash = self.flash and "flash=" in self.path
        # With dark=: the flash only when the picture it keeps ready is dark (as the firmware does).
        lit = asked_flash and (self.dark or "dark=" not in self.path)
        body = DARK.getvalue() if self.dark and not lit else JPEG.getvalue()
        cls.flashed += lit
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        if self.ours:
            self.send_header("X-VisionState-Camera", "settings" if cls.firmware == "settings" else cls.use)
            if cls.firmware == "settings":
                self.send_header("X-VisionState-Camera-Settings", cls.now or "size=unknown&quality=-1")
                self.send_header("X-VisionState-Frame-Ms", "70")
                self.send_header("X-VisionState-Camera-Chip", "OV2640")
            if self.flash:
                self.send_header("X-VisionState-Flash", "1")
            if asked_flash:
                self.send_header("X-VisionState-Flashed", "1" if lit else "0")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def camera():
    Camera.asked, Camera.use, Camera.ours, Camera.flash, Camera.dark = [], "states", True, False, False
    Camera.firmware, Camera.now, Camera.flashed = "settings", "", 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def test_each_kind_starts_from_a_preset_and_the_name_follows_the_values():
    assert set(CAMERA_PRESET_BY_KIND.values()) <= set(CAMERA_PRESETS)
    assert camera_settings(None, "reading") == METER
    assert camera_preset(METER) == "meter"
    assert camera_preset({**METER, "clock_mhz": 10}) is None  # custom
    # A sensor's own settings, and a camera use from before them (0.15.0b18 and older).
    assert camera_settings({"camera": {**METER, "quality": 12}}, "states")["quality"] == 12
    assert merge_triggers({"camera_use": "meter"})["camera"] == METER
    assert "camera_use" not in merge_triggers({"camera_use": "meter"})
    assert merge_triggers({})["camera"] is None


def test_what_a_camera_says_and_what_it_is_asked():
    said = said_settings("size=1600x1200&quality=8&clock=20&exposure=auto&gain=4")
    assert said == METER
    assert said_settings("size=unknown&quality=-1&clock=-1&exposure=unknown&gain=-1") is None
    uses = CameraUses()
    assert uses.query("http://cam/snapshot.jpg", METER) is None  # not known as ours yet
    uses.ours["http://cam/snapshot.jpg"] = "settings"
    assert uses.query("http://cam/snapshot.jpg", METER) == METER_QUERY
    uses.ours["http://cam/snapshot.jpg"] = "states"  # an older firmware: only presets
    assert uses.query("http://cam/snapshot.jpg", METER) == "use=meter"
    assert uses.query("http://cam/snapshot.jpg", {**METER, "clock_mhz": 10}) is None


def test_a_camera_of_ours_is_set_up_with_each_sensors_settings(settings, camera, caplog):  # noqa: F811
    caplog.set_level("INFO", logger="visionstate.sources")
    url = f"http://visionstate:secret-pw@127.0.0.1:{camera.server_address[1]}/snapshot.jpg"  # as the guide writes it
    with TestClient(create_app(settings)) as client:
        base = {"source_type": "http", "source": url, "interval_s": 1}
        door = client.post(
            "/api/v1/sensors", json={**base, "name": "Door", "states": [{"name": "Open"}, {"name": "Closed"}]}
        ).json()["id"]

        def view(sid: int) -> dict:
            return client.get(f"/api/v1/sensors/{sid}").json()

        # Known as ours from its first picture; a state sensor sends its kind's preset.
        assert wait_for(lambda: view(door)["camera"] is not None, timeout=20)
        assert view(door)["camera"]["settings"] and view(door)["triggers"]["camera"] is None
        assert wait_for(lambda: any(STATES_QUERY in p for p in Camera.asked), timeout=20)
        assert wait_for(lambda: view(door)["camera"]["now"] == CAMERA_PRESETS["states"]["settings"], timeout=20)
        assert view(door)["camera"]["frame_ms"] == 70 and view(door)["camera"]["chip"] == "OV2640"
        # A meter on the same camera: each sensor's pictures with its own settings, and each says so.
        meter = client.post(
            "/api/v1/sensors", json={**base, "name": "Meter", "kind": "reading", "reading": {"mode": "counter"}}
        ).json()["id"]
        assert wait_for(lambda: any(METER_QUERY in p for p in Camera.asked), timeout=20)
        assert wait_for(lambda: view(door)["camera"]["others"] == ["Meter"], timeout=20)
        assert view(meter)["camera"]["others"] == ["Door"]
        # Its own settings: the meter at the door's, so they are the same again.
        triggers = view(meter)["triggers"]
        same = {**triggers, "camera": CAMERA_PRESETS["states"]["settings"]}
        assert client.patch(f"/api/v1/sensors/{meter}", json={"triggers": same}).status_code == 200
        assert wait_for(lambda: view(door)["camera"]["others"] == [], timeout=20)
        # A custom setting goes along as it is; one the camera cannot take is refused.
        custom = {**triggers, "camera": {**METER, "clock_mhz": 10, "exposure_ms": 15}}
        assert client.patch(f"/api/v1/sensors/{meter}", json={"triggers": custom}).status_code == 200
        assert wait_for(lambda: any("clock=10&exposure=15" in p for p in Camera.asked), timeout=20)
        for bad in ({"size": "4000x3000"}, {"clock_mhz": 13}, {"gain": 3}, {"quality": 2}, {"exposure_ms": 9999}):
            refused = {**triggers, "camera": {**METER, **bad}}
            assert client.patch(f"/api/v1/sensors/{meter}", json={"triggers": refused}).status_code == 422
    # Said in the log, to look into it later; never with the camera's password.
    assert "is a VisionState ESP32" in caplog.text
    assert "secret-pw" not in caplog.text


def test_an_older_firmware_is_asked_for_the_preset(settings, camera):  # noqa: F811
    Camera.firmware = "states"
    url = f"http://127.0.0.1:{camera.server_address[1]}/snapshot.jpg"
    with TestClient(create_app(settings)) as client:
        base = {"source_type": "http", "source": url, "interval_s": 1}
        meter = client.post(
            "/api/v1/sensors", json={**base, "name": "Meter", "kind": "reading", "reading": {"mode": "counter"}}
        ).json()["id"]
        assert wait_for(lambda: any("use=meter" in p for p in Camera.asked), timeout=20)
        view = client.get(f"/api/v1/sensors/{meter}").json()
        assert view["camera"]["settings"] is False and view["camera"]["now"] is None
        # A custom setting an older firmware cannot take: nothing asked, the camera stays as it is.
        triggers = {**view["triggers"], "camera": {**METER, "clock_mhz": 10}}
        assert client.patch(f"/api/v1/sensors/{meter}", json={"triggers": triggers}).status_code == 200
        time.sleep(1.5)
        Camera.asked.clear()
        assert wait_for(lambda: len(Camera.asked) >= 3, timeout=20)
        assert not any("use=" in p or "size=" in p for p in Camera.asked)


def test_any_other_camera_is_asked_as_it_was(settings, camera):  # noqa: F811
    Camera.ours = False
    url = f"http://127.0.0.1:{camera.server_address[1]}/snapshot.jpg"
    with TestClient(create_app(settings)) as client:
        body = {"source_type": "http", "source": url, "interval_s": 1, "name": "Door"}
        sid = client.post("/api/v1/sensors", json={**body, "states": [{"name": "Open"}, {"name": "Closed"}]}).json()[
            "id"
        ]
        assert wait_for(lambda: len(Camera.asked) >= 3, timeout=20)
        assert all(p == "/snapshot.jpg" for p in Camera.asked)
        assert client.get(f"/api/v1/sensors/{sid}").json()["camera"] is None


class FlashHa:
    """Home Assistant with the camera's device: its IP address entity and its flash."""

    enabled = True

    def __init__(self, host: str):
        self.host = host
        self.switched: list[tuple[str, bool]] = []

    async def registries(self):
        entities = [
            {
                "entity_id": "sensor.cam_ip_address",
                "platform": "esphome",
                "original_name": "IP address",
                "device_id": "d",
            },
            {"entity_id": "light.cam_flash", "platform": "esphome", "original_name": "Flash", "device_id": "d"},
            {"entity_id": "light.hall", "platform": "hue", "device_id": "h"},
        ]
        return entities, [{"id": "d"}, {"id": "h"}]

    async def raw_states(self):
        return {"sensor.cam_ip_address": {"state": self.host}}

    async def state(self, entity_id):
        return "off"

    dark = False  # a Home Assistant camera's picture: dark until the lamp is on

    async def snapshot(self, entity_id):
        lit = any(on for _, on in self.switched[-1:])
        return DARK.getvalue() if self.dark and not lit else JPEG.getvalue()

    async def switch(self, entity_id, on):
        self.switched.append((entity_id, on))

    async def ping(self):
        return True

    async def entities(self):
        return []

    async def cameras(self):
        return []

    async def close(self):
        pass


def flash_sensor(client, url: str, light: str, when: str = "always") -> int:
    triggers = {"light_entity": light, "light_delay_s": 0.5, "light_when": when, "burst_duration_s": 0}
    body = {"source_type": "http", "source": url, "interval_s": 1, "name": "Door", "triggers": triggers}
    return client.post("/api/v1/sensors", json={**body, "states": [{"name": "Open"}, {"name": "Closed"}]}).json()["id"]


def test_a_camera_of_ours_takes_its_picture_with_its_own_flash(settings, camera):  # noqa: F811
    """The sensor's light is the camera's own flash, and the camera can take a picture with it: the
    picture is asked for with the flash (?flash=, the light's wait in ms), and nothing is switched
    through Home Assistant. Another light is switched there as before."""
    Camera.flash = True
    url = f"http://127.0.0.1:{camera.server_address[1]}/snapshot.jpg"
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.ha = ha = FlashHa("127.0.0.1")
        flash_sensor(client, url, "light.cam_flash")
        # The first picture says the camera can (before it, the light was switched as before).
        assert wait_for(lambda: any("flash=500" in p for p in Camera.asked), timeout=20)
        ha.switched.clear()
        asked = sum("flash=500" in p for p in Camera.asked)
        assert wait_for(lambda: sum("flash=500" in p for p in Camera.asked) >= asked + 3, timeout=20)
        assert ("light.cam_flash", True) not in ha.switched
        # The camera reached by its name: found by its address all the same.
        Camera.asked.clear()
        named = url.replace("127.0.0.1", "localhost")
        flash_sensor(client, named, "light.cam_flash")
        assert wait_for(lambda: named in client.app.state.runtime.camera_uses.flash, timeout=20)  # its first picture
        ha.switched.clear()
        assert wait_for(lambda: sum("flash=500" in p for p in Camera.asked) >= 4, timeout=20)
        assert ("light.cam_flash", True) not in ha.switched
        # A lamp of its own is not the camera's flash: switched through Home Assistant.
        Camera.asked.clear()
        flash_sensor(client, url, "light.hall")
        assert wait_for(lambda: ("light.hall", True) in ha.switched, timeout=20)


def test_the_flash_only_when_it_is_dark_is_the_cameras_choice(settings, camera):  # noqa: F811
    """The camera's own flash only when it is dark: one request (?flash=&dark=), the camera decides
    from the picture it keeps ready, and says whether it used the flash."""
    Camera.flash = True
    url = f"http://127.0.0.1:{camera.server_address[1]}/snapshot.jpg"
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.ha = FlashHa("127.0.0.1")
        sid = flash_sensor(client, url, "light.cam_flash", when="dark")
        assert wait_for(lambda: any("dark=" in p for p in Camera.asked), timeout=20)
        Camera.asked.clear()
        assert wait_for(lambda: len(Camera.asked) >= 3, timeout=20)
        assert all("flash=500" in p and "dark=" in p for p in Camera.asked)  # no picture without it first
        assert Camera.flashed == 0  # light enough: the camera did not use it
        Camera.dark = True
        assert wait_for(lambda: Camera.flashed >= 2, timeout=20)
        assert client.get(f"/api/v1/sensors/{sid}").status_code == 200


def test_another_light_only_when_it_is_dark(settings, camera):  # noqa: F811
    """A lamp switched through Home Assistant only when it is dark: a frame without it first."""
    url = f"http://127.0.0.1:{camera.server_address[1]}/snapshot.jpg"
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.ha = ha = FlashHa("127.0.0.1")
        flash_sensor(client, url, "light.hall", when="dark")
        # Light enough without it: never switched on.
        assert wait_for(lambda: len(Camera.asked) >= 4, timeout=20)
        assert ("light.hall", True) not in ha.switched
        # Dark: switched on for the picture.
        Camera.dark = True
        assert wait_for(lambda: ("light.hall", True) in ha.switched, timeout=20)
        bad = client.post(
            "/api/v1/sensors",
            json={"source_type": "http", "source": url, "name": "X", "triggers": {"light_when": "sometimes"}},
        )
        assert bad.status_code == 422


def test_a_lamp_only_when_it_is_dark_on_a_home_assistant_camera(settings):  # noqa: F811
    """Not an ESP32 of ours: a Home Assistant camera and a lamp switched there, only when it is dark."""
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.ha = rt.grabber.ha = ha = FlashHa("127.0.0.1")
        triggers = {"light_entity": "light.hall", "light_delay_s": 0.2, "light_when": "dark", "burst_duration_s": 0}
        body = {"source_type": "ha_camera", "source": "camera.door", "interval_s": 1, "name": "Door"}
        client.post("/api/v1/sensors", json={**body, "triggers": triggers, "states": [{"name": "A"}, {"name": "B"}]})
        time.sleep(4)  # a few checks, light enough: the lamp never switched on
        assert ("light.hall", True) not in ha.switched
        ha.dark = True
        assert wait_for(lambda: ("light.hall", True) in ha.switched, timeout=20)
        assert wait_for(lambda: ("light.hall", False) in ha.switched, timeout=20)  # and off again
