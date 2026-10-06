"""Home Assistant's side of a sensor: commands over MQTT, reconnecting, and switching models."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate.main import create_app
from visionstate.mqtt import REVIEW_TOPICS
from visionstate.settings import Settings

from .conftest import MODEL_DIR
from .test_integration import wait_for
from .test_publish import RecordingMqtt


class CountingCamera:
    def __init__(self):
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (120, 120, 120)).save(buf, format="JPEG")
        self.jpeg, self.grabs = buf.getvalue(), 0

    async def grab(self, source_type, source):
        self.grabs += 1
        return self.jpeg

    async def close(self):
        pass


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=MODEL_DIR
    )
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = CountingCamera()
        rt.mqtt = RecordingMqtt(rt.mqtt)
        body = {
            "name": "Door",
            "source_type": "http",
            "source": "http://cam/snap.jpg",
            "states": [{"name": "Open"}, {"name": "Closed"}],
            "enabled": False,  # paused: no checks unless asked for
        }
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        yield client, rt, sid


def test_pause_switch_from_home_assistant(app):
    client, rt, sid = app
    client.portal.call(rt._on_command, "door", "enabled", "ON")
    assert client.get(f"/api/v1/sensors/{sid}").json()["enabled"] is True
    assert rt.mqtt.sent["visionstate/door/enabled"] == "ON"
    client.portal.call(rt._on_command, "door", "enabled", " off\n")
    assert client.get(f"/api/v1/sensors/{sid}").json()["enabled"] is False
    assert rt.mqtt.sent["visionstate/door/enabled"] == "OFF"
    client.portal.call(rt._on_command, "no_such_sensor", "enabled", "ON")  # ignored
    client.portal.call(rt._on_command, "door", "enabled", "ON")
    client.portal.call(rt._on_command, "door", "enabled", "��")  # not from the switch: ignored
    assert client.get(f"/api/v1/sensors/{sid}").json()["enabled"] is True


def test_check_now_from_home_assistant_also_when_paused(app):
    client, rt, sid = app
    before = rt.grabber.grabs
    client.portal.call(rt._on_command, "door", "classify", "PRESS")
    assert wait_for(lambda: rt.grabber.grabs > before, timeout=10)
    assert client.get(f"/api/v1/sensors/{sid}").json()["enabled"] is False


def test_reconnect_publishes_everything_again(app):
    client, rt, sid = app
    rt.mqtt.real.publish = rt.mqtt.publish  # discovery goes through the real bridge's publish
    rt.mqtt.sent.clear()
    client.portal.call(rt._on_mqtt_connect)
    sent = rt.mqtt.sent
    assert REVIEW_TOPICS["count"] in sent
    assert any(t.startswith("homeassistant/") and "door" in t and t.endswith("/config") for t in sent)
    assert sent["visionstate/door/enabled"] == "OFF"


def test_detector_load_failure_is_reported_without_credentials(app, monkeypatch):
    client, rt, sid = app

    async def broken(detector_id):
        raise OSError("download from https://user:secret@example.com/model failed")

    monkeypatch.setattr(rt, "load_detector", broken)
    rt.detector = None
    with pytest.raises(OSError):
        client.portal.call(rt.ensure_detector)
    assert "secret" not in rt.detector_error and "***@" in rt.detector_error


def test_choosing_a_model_loads_it_only_when_in_use(app, monkeypatch):
    """Without object or reading sensors a new detector/reader is only remembered, not loaded."""
    client, rt, sid = app
    loaded = []

    async def load(model_id):
        loaded.append(model_id)

    monkeypatch.setattr(rt, "load_detector", load)
    monkeypatch.setattr(rt, "load_reader", load)
    rt.detector = rt.reader = None
    client.portal.call(rt.set_detector, "some-detector")
    client.portal.call(rt.set_reader, "some-reader")
    assert loaded == []
    assert rt.db.get_setting("detector") == "some-detector"
    assert rt.db.get_setting("reader") == "some-reader"

    rt.detector = object()  # loaded before: replaced right away
    client.portal.call(rt.set_detector, "other-detector")
    assert loaded == ["other-detector"]
