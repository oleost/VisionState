"""Lights shared by checks and open views: switched on once, off when nobody needs them any more."""

import asyncio
import time

from fastapi.testclient import TestClient

from visionstate.lights import Lights
from visionstate.main import create_app
from visionstate.settings import Settings

from .test_triggers import FakeHa


def test_light_is_shared_and_only_switched_off_when_free():
    async def scenario():
        log: list[str] = []
        ha = FakeHa(log)
        lights = Lights(lambda: ha)
        await lights.hold("light.flash", "check:1")
        await lights.hold("light.flash", "view:a", lease_s=30)  # already on (by us): not switched again
        assert log == ["on"]
        assert lights.wait_s("light.flash", 0.0) == 0.0 and 0 < lights.wait_s("light.flash", 5.0) <= 5.0
        await lights.release("light.flash", "check:1")
        assert log == ["on"]  # the view still holds it
        await lights.release("light.flash", "view:a")
        assert log == ["on", "off"] and lights.wait_s("light.flash", 0.0) is None

        # A lease that is not renewed runs out (closed tab): the sweep switches the light off.
        await lights.hold("light.flash", "view:b", lease_s=0.05)
        await asyncio.sleep(0.1)
        await lights.sweep()
        assert log == ["on", "off", "on", "off"]

        # Just switched off by us, Home Assistant may still say "on" for a moment: still ours, so it
        # is switched on and waited for (else the frame would be taken in the dark).
        ha.states["light.flash"] = "on"
        light = await lights.hold("light.flash", "check:2")
        assert light.ours and log[-1] == "on" and 0 < lights.wait_s("light.flash", 5.0) <= 5.0
        await lights.release("light.flash", "check:2")
        assert log[-1] == "off"

        # Somebody else's light (already on, not just switched off by us) is used but never switched off.
        lights._switched_off.clear()
        log.clear()
        log += ["on", "off", "on", "off"]
        ha.states["light.flash"] = "on"
        await lights.hold("light.flash", "view:c", lease_s=30)
        assert lights.wait_s("light.flash", 5.0) == 0.0  # it is bright already
        await lights.release("light.flash", "view:c")
        assert log == ["on", "off", "on", "off"] and ha.states["light.flash"] == "on"

        # An error is reported, and the holder does not count (nothing to switch off later).
        ha.states["light.flash"], ha.fail = "off", True
        light = await lights.hold("light.flash", "view:d", lease_s=30)
        assert light.error == "no connection" and lights.wait_s("light.flash", 0.0) is None
        ha.fail = False
        assert (await lights.hold("light.flash", "view:d", lease_s=30)).error == ""  # tried again
        await lights.release("light.flash", "view:d")
        assert ha.states["light.flash"] == "off"

    asyncio.run(scenario())


class Camera:
    def __init__(self):
        self.grabs = 0

    async def grab(self, source_type, source):
        self.grabs += 1
        from io import BytesIO

        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (64, 48), (200, 200, 200)).save(buf, format="JPEG")
        return buf.getvalue()

    async def close(self):
        pass


def test_a_view_holds_the_light_and_then_gets_fresh_frames(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=tmp_path
    )
    log: list[str] = []
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        camera, rt.ha = Camera(), FakeHa(log)
        rt.grabber = camera
        body = {
            "name": "Meter",
            "source_type": "http",
            "source": "x",
            "states": [{"name": "A"}, {"name": "B"}],
            "enabled": False,  # no checks: only the view switches the light
            "triggers": {"regular": False, "light_entity": "light.flash", "light_delay_s": 0.3},
        }
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        hold = {"entity_id": "light.flash", "holder": "view-123456", "delay_s": 0.3}

        assert client.post("/api/v1/lights/hold", json={**hold, "entity_id": "camera.x"}).status_code == 422
        assert client.post("/api/v1/lights/hold", json={**hold, "holder": "x"}).status_code == 422

        # Without the light, a view gets no fresh frame of a sensor with a light (nothing cached yet).
        before = camera.grabs
        client.get(f"/api/v1/sensors/{sid}/frame")
        assert camera.grabs == before + 1  # no check frame to show yet: one fresh frame
        # The view holds the light; frames are fresh once it had time to get bright.
        held = client.post("/api/v1/lights/hold", json=hold).json()
        assert held["on"] is True and 0 < held["wait_s"] <= 0.3 and log == ["on"]
        time.sleep(0.35)
        assert client.post("/api/v1/lights/hold", json=hold).json()["wait_s"] == 0.0  # renewed, bright
        assert rt.frame_for_view(sid) is None  # fresh frames now
        before = camera.grabs
        assert client.get(f"/api/v1/sensors/{sid}/frame").status_code == 200
        assert camera.grabs == before + 1

        # Closing the view (or its switch) lets go: the light goes off.
        assert client.post("/api/v1/lights/hold", json={**hold, "on": False}).json()["on"] is False
        assert log == ["on", "off"]
        assert client.get("/api/v1/config").json()["light_view"] == {"renew_s": 10.0, "lease_s": 30.0}
