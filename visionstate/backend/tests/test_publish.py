"""What is sent to Home Assistant: a sensor that is not sent (it runs, its entities stay
unavailable), and nothing that would make Home Assistant store an entity again at every check."""

import io
import json
import sqlite3
import zipfile

from fastapi.testclient import TestClient

from visionstate.db import Database, Sensor
from visionstate.main import create_app
from visionstate.settings import Settings

from .conftest import MODEL_DIR, requires_reader
from .test_integration import wait_for
from .test_reading import DisplayCamera


class RecordingMqtt:
    """Stands in for the MQTT client: remembers the last payload per topic."""

    def __init__(self, real):
        self.real, self.sent = real, {}

    async def publish(self, topic, payload, retain=False):
        self.sent[topic] = payload

    def __getattr__(self, name):  # everything else as the real bridge
        return getattr(self.real, name)


@requires_reader
def test_sensor_not_sent_to_home_assistant(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=MODEL_DIR
    )
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = DisplayCamera("0012345.6")
        mqtt = RecordingMqtt(rt.mqtt)
        rt.mqtt = mqtt
        body = {
            "name": "Meter",
            "kind": "reading",
            "source_type": "http",
            "source": "x",
            "reading": {"mode": "counter", "decimals": 1, "change_limit": "off"},
            "interval_s": 1,
            "debounce": 1,
            "publish": False,
        }
        sid = client.post("/api/v1/sensors", json=body).json()["id"]

        def view():
            return client.get(f"/api/v1/sensors/{sid}").json()

        # It reads and records as usual — but sends no value, and its entities are unavailable.
        assert wait_for(lambda: view()["reading"]["value"] == "12345.6", timeout=60)
        assert view()["publish"] is False
        assert "visionstate/meter/state" not in mqtt.sent and "visionstate/meter/image" not in mqtt.sent
        assert mqtt.sent["visionstate/meter/availability"] == "offline"
        assert mqtt.sent["visionstate/meter/enabled"] == "ON"  # the pause switch still works

        # Switched on: available at once, with the value, not only after the next check.
        assert client.patch(f"/api/v1/sensors/{sid}", json={"publish": True}).json()["publish"] is True
        assert wait_for(lambda: mqtt.sent.get("visionstate/meter/state") == "12345.6", timeout=10)
        assert mqtt.sent["visionstate/meter/availability"] == "online"

        # And off again: unavailable again, no new values.
        client.patch(f"/api/v1/sensors/{sid}", json={"publish": False})
        assert wait_for(lambda: mqtt.sent["visionstate/meter/availability"] == "offline", timeout=10)
        rt.grabber.text = "0012346.1"
        mqtt.sent.pop("visionstate/meter/state")
        assert wait_for(lambda: view()["reading"]["value"] == "12346.1", timeout=30)
        assert "visionstate/meter/state" not in mqtt.sent

        # Export and import keep it (a sensor being tuned stays unsent).
        exported = client.get(f"/api/v1/sensors/{sid}/export")
        with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
            assert json.loads(z.read("manifest.json"))["sensor"]["publish"] is False
        imported = client.post("/api/v1/import", files={"file": ("b.zip", exported.content)}).json()["id"]
        assert client.get(f"/api/v1/sensors/{imported}").json()["publish"] is False
        # New sensors are sent by default.
        assert client.get("/api/v1/config").json()["publish_default"] is True


def test_migration_keeps_existing_sensors_sent(tmp_path):
    path = tmp_path / "old.db"
    Database(path).init()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE sensor DROP COLUMN publish")  # as a database of 0.6.3b8 and older
    con.execute(
        "INSERT INTO sensor (slug, name, kind, source_type, source, interval_s, threshold, debounce, enabled, "
        "entity_prefix, created_at) VALUES ('a', 'A', 'single_state', 'http', 'x', 30, 0.7, 2, 1, 0, '2026-10-01')"
    )
    con.execute("PRAGMA user_version = 8")
    con.commit()
    con.close()
    db = Database(path)
    db.init()
    with db.session() as s:
        assert [row.publish for row in s.query(Sensor)] == [True]


class CountingMqtt(RecordingMqtt):
    def __init__(self, real):
        super().__init__(real)
        self.count: dict[str, int] = {}

    async def publish(self, topic, payload, retain=False):
        self.sent[topic] = payload
        self.count[topic] = self.count.get(topic, 0) + 1


def test_a_picture_is_sent_when_it_shows_something_new(tmp_path, monkeypatch):
    """Home Assistant stores every picture an image entity gets: the same picture is sent again
    only every PUBLISH["refresh_s"], a new one (another state) at once."""
    import asyncio
    from types import SimpleNamespace

    from visionstate.engine import Runtime, publishing
    from visionstate.settings import PUBLISH

    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=MODEL_DIR
    )
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init()
    rt = Runtime(settings, db)
    mqtt = CountingMqtt(rt.mqtt)
    rt.mqtt = mqtt  # type: ignore[assignment]
    now = [1000.0]
    monkeypatch.setattr(publishing.time, "time", lambda: now[0])
    encoded = []

    def send(cfg, shows):  # noqa: ANN001
        asyncio.run(rt._send_on_change(cfg, "t/image", shows, lambda: encoded.append(shows) or b"jpeg"))

    cfg = SimpleNamespace(id=1, publish=True)
    send(cfg, "closed")
    now[0] += 10
    send(cfg, "closed")  # the same: not sent, not even encoded
    assert mqtt.count["t/image"] == 1 and encoded == ["closed"]
    send(cfg, "open")  # something new: at once
    now[0] += PUBLISH["refresh_s"] - 1
    send(cfg, "open")
    assert mqtt.count["t/image"] == 2
    now[0] += 2
    send(cfg, "open")  # the same for long enough: sent again, so a dashboard is not left behind
    assert mqtt.count["t/image"] == 3
    rt.live_state(1).sent_on_change.clear()  # what publish_discovery does (MQTT connected again)
    send(cfg, "open")
    assert mqtt.count["t/image"] == 4
    send(SimpleNamespace(id=2, publish=False), "open")  # not sent to Home Assistant
    assert mqtt.count["t/image"] == 4
    db.engine.dispose()


@requires_reader
def test_values_that_change_at_every_check_are_not_sent(tmp_path):
    """No timestamp in the attributes, the confidence in whole percent, and nothing sent again
    while it stays the same (the pictures included): nothing makes Home Assistant store the
    entities again at every check. A new value is sent at once."""
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=MODEL_DIR
    )
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        camera = CountingCamera("0012345.6")
        rt.grabber = camera
        mqtt = CountingMqtt(rt.mqtt)
        rt.mqtt = mqtt
        body = {
            "name": "Meter",
            "kind": "reading",
            "source_type": "http",
            "source": "x",
            "reading": {"mode": "counter", "decimals": 1, "change_limit": "off"},
            "interval_s": 1,
            "debounce": 1,
        }
        client.post("/api/v1/sensors", json=body)
        base = "visionstate/meter"
        assert wait_for(lambda: mqtt.sent.get(f"{base}/state") == "12345.6", timeout=60)
        grabs = camera.grabs
        assert wait_for(lambda: camera.grabs >= grabs + 3, timeout=60)  # three more checks of the same
        attributes = mqtt.sent[f"{base}/attributes"]
        attributes = json.loads(attributes) if isinstance(attributes, str) else attributes
        assert "last_update" not in attributes
        assert mqtt.sent[f"{base}/confidence"].isdigit()  # whole percent
        for entity in ("state", "confidence", "attributes", "availability", "image", "reader_image"):
            assert mqtt.count[f"{base}/{entity}"] == 1, (entity, mqtt.count)
        camera.text = "0012346.6"
        assert wait_for(lambda: mqtt.sent.get(f"{base}/state") == "12346.6", timeout=60)
        assert wait_for(lambda: mqtt.count[f"{base}/image"] == 2, timeout=10)


class CountingCamera(DisplayCamera):
    def __init__(self, text: str):
        super().__init__(text)
        self.grabs = 0

    async def grab(self, source_type, source):
        self.grabs += 1
        return await super().grab(source_type, source)
