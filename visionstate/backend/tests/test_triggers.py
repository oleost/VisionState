"""Trigger logic: scheduling, change detection, HA event parsing and an end-to-end burst."""

import asyncio
import json
import sqlite3
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate import imaging
from visionstate.api.common import Triggers
from visionstate.db import SCHEMA_VERSION, Database
from visionstate.engine import LiveState, SensorConfig, next_check_at
from visionstate.ha_events import HaEventListener, parse_trigger_event, trigger_subscription
from visionstate.main import create_app
from visionstate.settings import TRIGGER_DEFAULTS, Settings, merge_triggers

from .conftest import requires_model
from .test_integration import FakeCamera, wait_for


def config(**triggers) -> SensorConfig:
    return SensorConfig(
        id=1,
        slug="s",
        name="S",
        source_type="http",
        source="x",
        roi=None,
        interval_s=60,
        threshold=0.7,
        debounce=2,
        enabled=True,
        states=[],
        triggers=merge_triggers(triggers),
    )


def test_schedule_uses_interval_burst_and_probe():
    live = LiveState()
    assert next_check_at(config(), live, 100) == (100, "full")  # never run -> now
    live.last_run = 100
    assert next_check_at(config(), live, 101) == (160, "full")
    live.burst_until = 130
    assert next_check_at(config(burst_interval_s=2), live, 101) == (102, "full")
    assert next_check_at(config(change_detection=True, change_interval_s=5), LiveState(last_run=100), 101) == (
        105,
        "probe",
    )


def test_merge_triggers_fills_defaults():
    merged = merge_triggers({"entities": ["binary_sensor.motion"]})
    assert merged["entities"] == ["binary_sensor.motion"]
    assert merged["burst_duration_s"] == TRIGGER_DEFAULTS["burst_duration_s"]


def test_trigger_model_validates_entities():
    assert Triggers(entities=["binary_sensor.a", " binary_sensor.a "]).entities == ["binary_sensor.a"]
    with pytest.raises(ValueError):
        Triggers(entities=["not an entity"])
    with pytest.raises(ValueError):
        Triggers(change_threshold=5)


def test_change_score():
    black = Image.new("RGB", (100, 100), (0, 0, 0))
    half = Image.new("RGB", (100, 100), (0, 0, 0))
    half.paste((255, 255, 255), (0, 0, 100, 50))
    a = imaging.region_signature(black, None)
    b = imaging.region_signature(half, None)
    assert imaging.change_score(None, a) == 0.0
    assert imaging.change_score(a, a) == 0.0
    assert imaging.change_score(a, b) == pytest.approx(0.5, abs=0.02)
    # Only the region counts: a change outside it scores 0.
    top = imaging.region_signature(half, {"x": 0, "y": 0.6, "w": 1, "h": 0.4})
    bottom = imaging.region_signature(black, {"x": 0, "y": 0.6, "w": 1, "h": 0.4})
    assert imaging.change_score(bottom, top) == 0.0
    assert isinstance(a, np.ndarray)


def event(entity, old, new):
    return {
        "id": 1,
        "type": "event",
        "event": {
            "variables": {"trigger": {"entity_id": entity, "from_state": {"state": old}, "to_state": {"state": new}}}
        },
    }


def test_parse_trigger_event_filters_noise():
    assert parse_trigger_event(event("binary_sensor.m", "off", "on")) == ("binary_sensor.m", "off", "on")
    assert parse_trigger_event(event("binary_sensor.m", "on", "unavailable")) is None
    assert parse_trigger_event({"type": "result", "success": True}) is None
    sub = trigger_subscription(7, ["binary_sensor.m"])
    assert sub["trigger"] == {"platform": "state", "entity_id": ["binary_sensor.m"], "to": None}


def test_migration_adds_triggers_column(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE sensor (id INTEGER PRIMARY KEY, slug VARCHAR(64), name VARCHAR(128), kind VARCHAR(32), "
        "source_type VARCHAR(32), source VARCHAR(1024), roi JSON, interval_s FLOAT, threshold FLOAT, "
        "debounce INTEGER, enabled BOOLEAN, created_at DATETIME)"
    )
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    Database(path).init()
    con = sqlite3.connect(path)
    columns = {row[1] for row in con.execute("PRAGMA table_info(sensor)")}
    assert {"triggers", "review"} <= columns
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


class FakeHomeAssistant:
    """Minimal HA WebSocket API: auth + subscribe_trigger, and a way to fire state changes."""

    def __init__(self):
        self.subscriptions: list[list[str]] = []
        self.clients = set()

    async def handler(self, ws):
        await ws.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(await ws.recv())
        if auth.get("access_token") != "token":
            await ws.send(json.dumps({"type": "auth_invalid", "message": "bad token"}))
            return
        await ws.send(json.dumps({"type": "auth_ok"}))
        sub = json.loads(await ws.recv())
        self.subscriptions.append(sub["trigger"]["entity_id"])
        await ws.send(json.dumps({"id": sub["id"], "type": "result", "success": True}))
        self.clients.add(ws)
        try:
            await ws.wait_closed()
        finally:
            self.clients.discard(ws)

    async def fire(self, entity, old, new):
        for ws in list(self.clients):
            await ws.send(json.dumps(event(entity, old, new)))


def test_listener_receives_state_changes(tmp_path):
    from websockets.asyncio.server import serve

    async def scenario():
        fake = FakeHomeAssistant()
        received = []

        async def on_state(*args):
            received.append(args)

        async with serve(fake.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            settings = Settings(
                data_dir=tmp_path,
                media_dir=tmp_path,
                frontend_dir=tmp_path,
                bundled_models_dir=tmp_path,
                ha_url=f"http://127.0.0.1:{port}",
                ha_token="token",
            )
            listener = HaEventListener(settings, on_state)
            listener.url = f"ws://127.0.0.1:{port}"
            listener.set_entities({"binary_sensor.motion"})
            listener.start()
            for _ in range(50):
                if listener.connected:
                    break
                await asyncio.sleep(0.05)
            assert listener.connected
            await fake.fire("binary_sensor.motion", "off", "on")
            await asyncio.sleep(0.2)
            listener.set_entities({"binary_sensor.motion", "cover.garage"})  # resubscribes
            for _ in range(50):
                if len(fake.subscriptions) == 2 and listener.connected:
                    break
                await asyncio.sleep(0.05)
            await listener.stop()
            return received, fake.subscriptions

    received, subscriptions = asyncio.run(scenario())
    assert received == [("binary_sensor.motion", "off", "on")]
    assert subscriptions == [["binary_sensor.motion"], ["binary_sensor.motion", "cover.garage"]]


@requires_model
def test_change_detection_triggers_classification(tmp_path, model_dir):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=model_dir
    )
    camera = FakeCamera()
    camera.fixed = True
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        sid = client.post(
            "/api/v1/sensors",
            json={
                "name": "Door",
                "source_type": "http",
                "source": "x",
                "states": [{"name": "Open"}, {"name": "Closed"}],
                "roi": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.7},
                "interval_s": 3600,  # the interval alone would never catch the change
                "debounce": 1,
                "triggers": {"change_detection": True, "change_interval_s": 0.5, "burst_interval_s": 0.5},
            },
        ).json()["id"]
        for state in ("open", "closed"):
            camera.state = state
            for _ in range(5):
                client.post(f"/api/v1/sensors/{sid}/capture", json={"state_key": state})
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["live"]["published"] == "closed")
        started = time.time()
        camera.state = "open"
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["live"]["published"] == "open", 15)
        assert time.time() - started < 15
        live = client.get(f"/api/v1/sensors/{sid}").json()["live"]
        assert live["last_trigger"]["source"] == "change"
        assert client.get(f"/api/v1/sensors/{sid}").json()["triggers"]["change_detection"] is True

        # Moving the region resets the baseline; change detection must keep working afterwards.
        client.patch(f"/api/v1/sensors/{sid}", json={"roi": {"x": 0.21, "y": 0.2, "w": 0.6, "h": 0.7}})
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["status"] == "ok", 15)
        camera.state = "closed"
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["live"]["published"] == "closed", 20)
