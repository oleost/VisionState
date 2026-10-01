"""End-to-end API test with a fake camera. Needs the bundled backbone (see conftest)."""

import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from visionstate.main import create_app

from .conftest import requires_model

DOOR_HEIGHT = {"open": 0.1, "closed": 1.0, "partial": 0.5}


def garage(state: str, seed: int) -> bytes:
    """A crude synthetic garage door with some lighting variation."""
    shade = 90 + (seed * 37) % 60
    img = Image.new("RGB", (320, 180), (shade, shade + 5, shade + 12))
    draw = ImageDraw.Draw(img)
    draw.rectangle((80, 52, 240, 152), fill=(12, 14, 16))
    bottom = 52 + int(100 * DOOR_HEIGHT[state])
    draw.rectangle((80, 52, 240, bottom), fill=(200, 204, 210))
    for y in range(60, bottom, 16):
        draw.line((80, y, 240, y), fill=(160, 166, 175), width=2)
    draw.rectangle((10 + seed % 40, 120, 30 + seed % 40, 170), fill=(60, 70, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class FakeCamera:
    def __init__(self):
        self.state = "closed"
        self.seed = 0
        self.fixed = False

    async def grab(self, source_type, source):
        if not self.fixed:  # fixed = identical lighting every frame (for change detection tests)
            self.seed += 1
        return garage(self.state, self.seed)

    async def close(self):
        pass


def wait_for(predicate, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.2)
    return False


@requires_model
def test_full_flow(settings):
    camera = FakeCamera()
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera

        cfg = client.get("/api/v1/config").json()
        assert cfg["unknown_state"] == "unknown"

        resp = client.post(
            "/api/v1/sensors",
            json={
                "name": "Garage door",
                "source_type": "http",
                "source": "http://fake",
                "states": [{"name": "Open"}, {"name": "Closed"}, {"name": "Partial"}],
                "roi": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.7},
                "interval_s": 1,
            },
        )
        assert resp.status_code == 201, resp.text
        sensor = resp.json()
        sid = sensor["id"]
        assert [s["key"] for s in sensor["states"]] == ["open", "closed", "partial"]
        assert sensor["entity_id"] == "sensor.visionstate_garage_door"

        # Label frames the way the UI does: fetch a frame, then label it by frame id.
        for state in ("open", "closed", "partial"):
            camera.state = state
            for _ in range(6):
                frame = client.get(f"/api/v1/sensors/{sid}/frame")
                assert frame.status_code == 200
                resp = client.post(
                    f"/api/v1/sensors/{sid}/capture",
                    json={"state_key": state, "frame_id": frame.headers["x-frame-id"]},
                )
                assert resp.status_code == 201

        # "ok" comes with the first trained model; wait for the one trained on all 18 labels.
        def trained_on_all():
            view = client.get(f"/api/v1/sensors/{sid}").json()
            return view["status"] == "ok" and not view["training"] and (view["model"] or {}).get("n_samples") == 18

        assert wait_for(trained_on_all)
        quality = client.get(f"/api/v1/sensors/{sid}/quality").json()
        assert quality["counts"]["labelled"] == 18
        assert quality["accuracy"] is not None and quality["accuracy"] > 0.8

        camera.state = "open"
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["live"]["published"] == "open")

        # Unlabelled upload gets a suggestion, which can be accepted in bulk.
        camera.state = "partial"
        files = [("files", (f"f{i}.jpg", garage("partial", 100 + i), "image/jpeg")) for i in range(3)]
        up = client.post(f"/api/v1/sensors/{sid}/uploads", files=files).json()
        assert up["created"] == 3
        listing = client.get(f"/api/v1/sensors/{sid}/samples", params={"filter": "unlabelled"}).json()
        assert listing["total"] == 3
        assert all(item["suggestion"]["key"] == "partial" for item in listing["items"])
        accepted = client.post(
            f"/api/v1/sensors/{sid}/samples/accept-suggestions", json={"sample_ids": up["sample_ids"]}
        )
        assert accepted.json()["updated"] == 3

        # Export and re-import as a new sensor.
        exported = client.get(f"/api/v1/sensors/{sid}/export")
        assert exported.status_code == 200
        imported = client.post("/api/v1/import", files={"file": ("b.zip", exported.content, "application/zip")})
        assert imported.status_code == 201, imported.text
        copy = client.get(f"/api/v1/sensors/{imported.json()['id']}").json()
        assert copy["slug"] == "garage_door_2" and copy["name"] == "Garage door (2)"  # told apart from the original
        assert copy["counts"]["labelled"] == 21

        thumb = client.get(f"/api/v1/samples/{up['sample_ids'][0]}/image")
        assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"

        assert client.delete(f"/api/v1/sensors/{sid}").status_code == 204
        assert len(client.get("/api/v1/sensors").json()) == 1


@pytest.fixture
def settings(tmp_path, model_dir):
    from visionstate.settings import Settings

    return Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=tmp_path / "none",
        bundled_models_dir=model_dir,
    )
