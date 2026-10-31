"""A sensor's Home Assistant tab: which entities exist, what happens to those chosen away, the
objects' snapshots, and how a sensor from before the tab decides once."""

import asyncio
import io
import sqlite3

from fastapi.testclient import TestClient
from PIL import Image

from visionstate import imaging
from visionstate.db import Database, Sensor
from visionstate.ha_events import our_enabled
from visionstate.main import create_app
from visionstate.mqtt import (
    MqttConfig,
    SensorDescriptor,
    discovery_messages,
    entity_options,
    main_entities,
    muted_topics,
)
from visionstate.settings import KIND_OBJECTS, KIND_READING, PUBLISH, Settings, merge_reading

from .conftest import MODEL_DIR
from .test_integration import wait_for
from .test_publish import RecordingMqtt

OBJECTS = [("car", "Car", "mdi:car"), ("person", "Person", "mdi:account")]


def objects(**kwargs) -> SensorDescriptor:
    return SensorDescriptor("drive", "Drive", [], KIND_OBJECTS, OBJECTS, **kwargs)


def counter(**kwargs) -> SensorDescriptor:
    return SensorDescriptor("meter", "Meter", [], KIND_READING, [], merge_reading({"mode": "counter"}), **kwargs)


def entities(sensor: SensorDescriptor) -> set[str]:
    return {topic.split("/")[-2] for topic, _ in discovery_messages("ha", sensor)}


def settings_in(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=MODEL_DIR
    )


def test_a_new_sensor_has_what_it_always_had_and_no_more():
    assert entities(objects()) == {
        "car",
        "car_count",
        "person",
        "person_count",
        "frame",
        "classify",
        "enabled",
    }
    assert entities(counter()) == {"state", "confidence", "frame", "classify", "enabled"}


def test_what_is_chosen_is_on_in_home_assistant():
    sensor = counter(chosen={"rate": True, "raw": True})
    configs = {topic.split("/")[-2]: payload for topic, payload in discovery_messages("ha", sensor)}
    assert {"rate", "raw"} <= set(configs)
    # VisionState alone decides: no second switch in Home Assistant.
    assert not [name for name, config in configs.items() if "enabled_by_default" in config]
    assert "reader_image" not in configs


def test_an_entity_chosen_away_is_not_announced_and_gets_no_values():
    sensor = objects(chosen={"car_count": False, "car_snapshot": True, "frame": False})
    assert entities(sensor) == {"car", "car_snapshot", "person", "person_count", "classify", "enabled"}
    muted = muted_topics(sensor)
    assert "visionstate/drive/objects/car/count" in muted and "visionstate/drive/image" in muted
    assert "visionstate/drive/objects/person/snapshot" in muted
    assert "visionstate/drive/objects/car/snapshot" not in muted
    # A value that entity shares with one it has still goes out.
    assert "visionstate/drive/objects/car/state" not in muted
    assert [uid for uid, _ in main_entities(sensor)] == [
        "visionstate_drive_car",
        "visionstate_drive_person",
        "visionstate_drive_person_count",
    ]


def test_a_sensor_not_in_home_assistant_announces_nothing():
    sensor = objects(in_ha=False)
    assert discovery_messages("ha", sensor) == [] and main_entities(sensor) == []
    assert "visionstate/drive/objects/car/state" in muted_topics(sensor)


def test_the_snapshot_entity_is_an_image_of_its_own_topic():
    payload = dict(discovery_messages("ha", objects(chosen={"car_snapshot": True})))[
        "ha/image/visionstate_drive/car_snapshot/config"
    ]
    assert payload["unique_id"] == "visionstate_drive_car_snapshot"
    assert payload["name"] == "Car snapshot" and payload["image_topic"] == "visionstate/drive/objects/car/snapshot"


def test_snapshot_covers_every_box_within_the_region():
    image = Image.new("RGB", (1000, 500))
    whole = (0.0, 0.0, 1.0, 1.0)
    one = imaging.snapshot(image, [[0.2, 0.2, 0.6, 0.8]], whole, 0.1, 0)
    assert one.size == (480, 180 + 120 + 60)  # the box plus 10 % of it on each side
    both = imaging.snapshot(image, [[0.1, 0.2, 0.2, 0.4], [0.7, 0.6, 0.8, 0.8]], whole, 0.0, 0)
    assert both.size == (700, 300)  # the union
    small = imaging.snapshot(image, [[0.5, 0.5, 0.51, 0.51]], whole, 0.0, 300)
    assert small.size == (300, 300)  # a far object is not a stamp
    edge = imaging.snapshot(image, [[0.0, 0.0, 0.01, 0.01]], whole, 0.0, 300)
    assert edge.size == (300, 300)  # at the edge the room goes to the other side
    region = (0.2, 0.2, 0.4, 0.6)
    assert imaging.snapshot(image, [[0.1, 0.1, 0.9, 0.9]], region, 0.1, 300).size == (200, 200)
    assert imaging.snapshot(image, [[0.25, 0.25, 0.26, 0.26]], region, 0.0, 300).size == (200, 200)


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(out, "JPEG")
    return out.getvalue()


class StillCamera:
    async def grab(self, source_type, source):
        return jpeg()

    async def close(self):
        pass


class OrderedMqtt(RecordingMqtt):
    def __init__(self, real):
        super().__init__(real)
        self.order: list[str] = []

    async def publish(self, topic, payload, retain=False):
        self.order.append(topic)
        await super().publish(topic, payload, retain)


def test_a_snapshot_follows_the_count_and_comes_before_the_state(tmp_path):
    settings = settings_in(tmp_path)
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = StillCamera()
        mqtt = OrderedMqtt(rt.mqtt)
        rt.mqtt = mqtt
        boxes: list[list[float]] = []

        async def detect(image, roi, objects, threshold, all_classes=False):
            return [{"key": "car", "score": 0.9, "box": box} for box in boxes]

        rt.detect_objects = detect
        body = {
            "name": "Drive",
            "kind": "objects",
            "source_type": "http",
            "source": "x",
            "objects": {"classes": ["car"], "clear_after_s": 0},
            "interval_s": 3600,
            "debounce": 1,
        }
        sensor = client.post("/api/v1/sensors", json=body).json()
        assert next(e for e in sensor["ha_entities"] if e["id"] == "car_snapshot")["on"] is False
        assert (
            client.patch(f"/api/v1/sensors/{sensor['id']}", json={"ha_entities": {"car_snapshot": True}}).status_code
            == 200
        )
        topic = "visionstate/drive/objects/car/snapshot"
        state = "visionstate/drive/objects/car/state"

        def check(n: int) -> list[str]:
            before = len(mqtt.order)
            boxes[:] = [[0.1 + 0.2 * i, 0.1, 0.2 + 0.2 * i, 0.5] for i in range(n)]
            done = rt.live_state(sensor["id"]).last_run
            assert client.post(f"/api/v1/sensors/{sensor['id']}/classify").status_code in (200, 202, 204)
            assert wait_for(lambda: rt.live_state(sensor["id"]).last_run != done, timeout=10)
            count = "visionstate/drive/objects/car/count"
            assert wait_for(lambda: mqtt.sent.get(count) == str(n), timeout=10)
            return mqtt.order[before:]

        first = check(1)
        assert mqtt.sent[topic][:2] == b"\xff\xd8"
        assert first.index(topic) < first.index(state)  # an automation it starts finds this picture
        assert Image.open(io.BytesIO(mqtt.sent[topic])).size == (
            PUBLISH["snapshot_min_px"],
            max(PUBLISH["snapshot_min_px"], 288),
        )
        assert topic not in check(1)  # the same count: Home Assistant stores every picture
        assert topic in check(2)  # one more
        one_kept = mqtt.sent[topic]
        assert topic not in check(0)  # gone: the last one stays
        assert mqtt.sent[topic] == one_kept
        assert topic in check(1)  # back


class ConfirmingClient:
    def __init__(self):
        self.published: list[tuple] = []

    async def publish(self, topic, payload, qos=0, retain=False, timeout=None):
        self.published.append((topic, payload, qos, retain))


def test_removing_needs_a_broker_that_confirms(tmp_path):
    with TestClient(create_app(settings_in(tmp_path))) as client:
        rt = client.app.state.runtime
        rt.grabber = StillCamera()
        body = {
            "name": "Meter",
            "kind": "reading",
            "source_type": "http",
            "source": "x",
            "reading": {"mode": "counter"},
        }
        sensor_id = client.post("/api/v1/sensors", json=body).json()["id"]
        url = f"/api/v1/sensors/{sensor_id}"
        rt.mqtt.config = MqttConfig("broker", 1883, "", "", "options")

        # Broker configured but not connected: nothing is stored, as nothing would send it again.
        refused = client.patch(url, json={"ha_entities": {"frame": False}})
        assert refused.status_code == 503 and "MQTT" in refused.json()["detail"]
        assert next(e for e in client.get(url).json()["ha_entities"] if e["id"] == "frame")["on"] is True
        assert client.patch(url, json={"in_ha": False}).status_code == 503
        assert client.get(url).json()["in_ha"] is True

        # Adding needs nothing: discovery announces it when MQTT is back.
        added = client.patch(url, json={"ha_entities": {"raw": True}})
        assert added.status_code == 200
        assert next(e for e in added.json()["ha_entities"] if e["id"] == "raw")["on"] is True

        broker = ConfirmingClient()
        rt.mqtt._client, rt.mqtt.connected = broker, True
        removed = client.patch(url, json={"ha_entities": {"frame": False, "raw": True}})
        assert removed.status_code == 200
        assert ("homeassistant/image/visionstate_meter/frame/config", "", 1, True) in broker.published
        assert ("visionstate/meter/image", "", 1, True) in broker.published
        assert ("visionstate/meter/state", "", 1, True) not in broker.published

        broker.published.clear()
        gone = client.patch(url, json={"in_ha": False})
        assert gone.status_code == 200 and gone.json()["in_ha"] is False and gone.json()["entity_ids"] == []
        topics = {published[0] for published in broker.published}
        assert "homeassistant/sensor/visionstate_meter/state/config" in topics
        assert {"visionstate/meter/state", "visionstate/meter/availability"} <= topics
        rt.mqtt._client, rt.mqtt.connected = None, False


def test_a_sensor_made_not_in_home_assistant_sends_nothing(tmp_path):
    with TestClient(create_app(settings_in(tmp_path))) as client:
        rt = client.app.state.runtime
        rt.grabber = StillCamera()
        mqtt = OrderedMqtt(rt.mqtt)
        rt.mqtt = mqtt
        checked = []

        async def detect(image, roi, objects, threshold, all_classes=False):
            checked.append(True)
            return [{"key": "car", "score": 0.9, "box": [0.1, 0.1, 0.5, 0.5]}]

        rt.detect_objects = detect
        body = {
            "name": "Drive",
            "kind": "objects",
            "source_type": "http",
            "source": "x",
            "objects": {"classes": ["car"]},
            "in_ha": False,
            "interval_s": 1,
            "debounce": 1,
        }
        sensor = client.post("/api/v1/sensors", json=body).json()
        assert sensor["in_ha"] is False and sensor["entity_ids"] == []
        assert wait_for(lambda: len(checked) >= 2, timeout=20)  # it runs
        ours = [t for t in mqtt.order if "drive" in t or t.endswith("/config")]
        assert ours == []  # and neither the broker nor Home Assistant hears of it

        # Into Home Assistant later: nothing to confirm, its entities appear.
        client.patch(f"/api/v1/sensors/{sensor['id']}", json={"in_ha": True})
        assert wait_for(lambda: "visionstate/drive/objects/car/state" in mqtt.sent, timeout=20)


def test_without_a_broker_a_choice_is_just_stored(tmp_path):
    with TestClient(create_app(settings_in(tmp_path))) as client:
        rt = client.app.state.runtime
        rt.grabber = StillCamera()
        rt.mqtt.config = None
        body = {
            "name": "Meter",
            "kind": "reading",
            "source_type": "http",
            "source": "x",
            "reading": {"mode": "counter"},
        }
        sensor_id = client.post("/api/v1/sensors", json=body).json()["id"]
        assert client.patch(f"/api/v1/sensors/{sensor_id}", json={"in_ha": False}).status_code == 200


def test_a_sensor_from_before_the_tab_settles_once_from_home_assistant(tmp_path):
    settings = settings_in(tmp_path)
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = StillCamera()
        body = {
            "name": "Meter",
            "kind": "reading",
            "source_type": "http",
            "source": "x",
            "reading": {"mode": "counter"},
        }
        sensor_id = client.post("/api/v1/sensors", json=body).json()["id"]
        url = f"/api/v1/sensors/{sensor_id}"
        with rt.db.session() as s:
            s.get(Sensor, sensor_id).ha_entities = None  # as an upgraded database has it

        # Until it is settled nothing it had goes away.
        before = {e["id"]: e["on"] for e in client.get(url).json()["ha_entities"]}
        assert before["raw"] and before["rate"] and before["reader_image"]
        assert {"raw", "rate"} <= {o["id"] for o in entity_options(counter())}

        # MQTT down: it waits.
        asyncio.run(rt.settle_entity_choices({"visionstate_meter_raw"}))
        with rt.db.session() as s:
            assert s.get(Sensor, sensor_id).ha_entities is None

        broker = ConfirmingClient()
        rt.mqtt._client, rt.mqtt.connected = broker, True
        asyncio.run(rt.settle_entity_choices({"visionstate_meter_raw", "visionstate_meter_state"}))
        after = {e["id"]: e["on"] for e in client.get(url).json()["ha_entities"]}
        assert after["raw"] is True  # on in Home Assistant: stays
        assert after["rate"] is False and after["problem"] is False  # off there: goes
        assert after["confidence"] is True and after["frame"] is True  # never in question
        removed = {published[0] for published in broker.published}
        assert "homeassistant/sensor/visionstate_meter/rate/config" in removed
        assert "homeassistant/sensor/visionstate_meter/raw/config" not in removed

        # Once: what the user chooses after that stands.
        broker.published.clear()
        asyncio.run(rt.settle_entity_choices(set()))
        assert broker.published == []
        rt.mqtt._client, rt.mqtt.connected = None, False


def test_what_was_ticked_is_switched_on_once(tmp_path, monkeypatch):
    """Home Assistant gives a removed entity that comes back its switched-off state again."""
    from visionstate.engine import runtime as runtime_module

    enabled: list[list[str]] = []

    async def enable(url, token, entity_ids):
        enabled.append(entity_ids)

    monkeypatch.setattr(runtime_module, "enable_entities", enable)
    with TestClient(create_app(settings_in(tmp_path))) as client:
        rt = client.app.state.runtime
        rt.grabber = StillCamera()
        rt.mqtt.config = None
        body = {
            "name": "Meter",
            "kind": "reading",
            "source_type": "http",
            "source": "x",
            "reading": {"mode": "counter"},
        }
        url = f"/api/v1/sensors/{client.post('/api/v1/sensors', json=body).json()['id']}"
        client.patch(url, json={"ha_entities": {"raw": True}})
        assert rt.enable_in_ha == {"visionstate_meter_raw"}
        raw = {"unique_id": "visionstate_meter_raw", "entity_id": "sensor.meter_raw", "disabled_by": "user"}

        asyncio.run(rt._enable_chosen([]))  # not there yet: it waits
        assert enabled == [] and rt.enable_in_ha == {"visionstate_meter_raw"}
        asyncio.run(rt._enable_chosen([raw]))
        assert enabled == [["sensor.meter_raw"]] and rt.enable_in_ha == set()
        asyncio.run(rt._enable_chosen([raw]))  # switched off there later: the user's own choice
        assert enabled == [["sensor.meter_raw"]]

        client.patch(url, json={"ha_entities": {"problem": True, "raw": True}})
        client.patch(url, json={"ha_entities": {"problem": False, "raw": True}})
        assert rt.enable_in_ha == set()  # ticked and unticked again: nothing to do


def test_our_enabled_leaves_out_what_is_off_and_what_is_not_ours():
    registry = [
        {"platform": "mqtt", "unique_id": "visionstate_a_raw", "entity_id": "sensor.a_raw", "disabled_by": None},
        {
            "platform": "mqtt",
            "unique_id": "visionstate_a_rate",
            "entity_id": "sensor.a_rate",
            "disabled_by": "integration",
        },
        {"platform": "mqtt", "unique_id": "visionstate_a_problem", "entity_id": "sensor.a_p", "disabled_by": "user"},
        {"platform": "mqtt", "unique_id": "other_thing", "entity_id": "sensor.other", "disabled_by": None},
    ]
    assert our_enabled(registry) == {"visionstate_a_raw"}


def test_migration_leaves_an_existing_sensor_in_home_assistant_and_unsettled(tmp_path):
    path = tmp_path / "old.db"
    Database(path).init()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE sensor DROP COLUMN in_ha")  # as a database from before the tab
    con.execute("ALTER TABLE sensor DROP COLUMN ha_entities")
    con.execute(
        "INSERT INTO sensor (slug, name, kind, source_type, source, interval_s, threshold, debounce, enabled, "
        "entity_prefix, publish, created_at) "
        "VALUES ('a', 'A', 'reading', 'http', 'x', 30, 0.7, 2, 1, 0, 1, '2026-10-01')"
    )
    con.execute("PRAGMA user_version = 16")
    con.commit()
    con.close()
    db = Database(path)
    db.init()
    with db.session() as s:
        assert [(row.in_ha, row.ha_entities) for row in s.query(Sensor)] == [(True, None)]
