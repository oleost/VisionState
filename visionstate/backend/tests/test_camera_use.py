"""An ESP32 camera of ours (esphome/): known by the X-VisionState-Camera header of its snapshots, it
is asked to be set up for what its sensors look at (?use=...), the most demanding of them; any other
camera is asked as it always was."""

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate.main import create_app
from visionstate.settings import CAMERA_USE_BY_KIND, CAMERA_USES

from .test_sound import wait_for
from .test_storage import settings  # noqa: F401 (fixture)

JPEG = io.BytesIO()
Image.new("RGB", (64, 48), (90, 90, 90)).save(JPEG, "JPEG")


class Camera(BaseHTTPRequestHandler):
    ours = True
    asked: list[str] = []  # noqa: RUF012 - shared by the server's threads on purpose
    use = "states"

    def log_message(self, *args):  # noqa: ANN002
        pass

    def do_GET(self):  # noqa: N802
        type(self).asked.append(self.path)
        if "use=" in self.path:
            type(self).use = self.path.split("use=", 1)[1].split("&", 1)[0]
        body = JPEG.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        if self.ours:
            self.send_header("X-VisionState-Camera", type(self).use)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def camera():
    Camera.asked, Camera.use, Camera.ours = [], "states", True
    server = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def test_the_most_demanding_kind_is_used_first():
    assert list(CAMERA_USES)[0] == "meter" and CAMERA_USE_BY_KIND["reading"] == "meter"


def test_a_camera_of_ours_is_set_up_for_its_sensors_and_shared_by_the_most_demanding(settings, camera, caplog):  # noqa: F811
    caplog.set_level("INFO", logger="visionstate.sources")
    url = f"http://visionstate:secret-pw@127.0.0.1:{camera.server_address[1]}/snapshot.jpg"  # as the guide writes it
    with TestClient(create_app(settings)) as client:
        base = {"source_type": "http", "source": url, "interval_s": 1}
        door = client.post(
            "/api/v1/sensors", json={**base, "name": "Door", "states": [{"name": "Open"}, {"name": "Closed"}]}
        ).json()["id"]

        def view(sid: int) -> dict:
            return client.get(f"/api/v1/sensors/{sid}").json()

        # Known as ours from its first picture; a state sensor asks for "states".
        assert wait_for(lambda: view(door)["camera"] is not None, timeout=20)
        assert view(door)["camera"]["wanted"] == "states" and view(door)["camera"]["automatic"]
        assert wait_for(lambda: any("use=states" in p for p in Camera.asked), timeout=20)
        # A meter on the same camera: both now ask for it, so the camera is not switched back and forth.
        meter = client.post(
            "/api/v1/sensors", json={**base, "name": "Meter", "kind": "reading", "reading": {"mode": "counter"}}
        ).json()["id"]
        assert wait_for(lambda: view(door)["camera"]["asked"] == "meter", timeout=20)
        Camera.asked.clear()
        assert wait_for(lambda: len(Camera.asked) >= 4, timeout=20)
        assert all("use=meter" in p for p in Camera.asked)
        # Chosen by hand: a sensor's own choice counts, an unknown one is refused.
        triggers = view(door)["triggers"]
        assert client.patch(f"/api/v1/sensors/{meter}", json={"enabled": False}).status_code == 200
        patched = client.patch(f"/api/v1/sensors/{door}", json={"triggers": {**triggers, "camera_use": "movement"}})
        assert patched.status_code == 200
        assert wait_for(lambda: view(door)["camera"]["asked"] == "movement", timeout=20)
        assert not view(door)["camera"]["automatic"]
        bad = client.patch(f"/api/v1/sensors/{door}", json={"triggers": {**triggers, "camera_use": "fish"}})
        assert bad.status_code == 422
    # Said in the log, to look into it later; never with the camera's password.
    assert "is a VisionState ESP32" in caplog.text and "set up for Meters and displays" in caplog.text
    assert "secret-pw" not in caplog.text


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
