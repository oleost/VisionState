"""Trigger logic: scheduling, change detection, HA event parsing and an end-to-end burst."""

import asyncio
import io
import json
import math
import sqlite3
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate import imaging
from visionstate.api.common import Triggers
from visionstate.db import SCHEMA_VERSION, Database
from visionstate.engine import LiveState, SensorConfig, entity_triggers, next_check_at
from visionstate.ha_events import HaEventListener, parse_trigger_event, trigger_subscription
from visionstate.main import create_app
from visionstate.settings import TRIGGER_DEFAULTS, Settings, merge_triggers

from .conftest import requires_model, requires_reader
from .displays import render
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
    version = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    assert {"triggers", "review"} <= columns
    assert version == SCHEMA_VERSION


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


# --- regular check off, entity states and the light --------------------------------------------------


def test_schedule_without_regular_check():
    live = LiveState(last_run=100)
    assert next_check_at(config(regular=False), live, 101) == (math.inf, "full")  # nothing until a trigger
    live.burst_until = 130  # a trigger still starts its burst
    assert next_check_at(config(regular=False, burst_interval_s=2), live, 101) == (102, "full")
    # Change detection keeps probing: it is a trigger of its own.
    assert next_check_at(
        config(regular=False, change_detection=True, change_interval_s=5), LiveState(last_run=100), 101
    ) == (
        105,
        "probe",
    )
    # A check caused by a trigger postpones the regular one: it is counted from the last check.
    assert next_check_at(config(), LiveState(last_run=500), 501) == (560, "full")


def test_trigger_model_validates_states_and_light():
    model = Triggers(
        entities=["sensor.status", "binary_sensor.motion"],
        only_states={"sensor.status": " Flow finished ", "binary_sensor.motion": " ", "sensor.gone": "x"},
        light_entity=" light.flash ",
    )
    assert model.only_states == {"sensor.status": "Flow finished"}  # trimmed; empty and unknown entities dropped
    assert model.light_entity == "light.flash"
    with pytest.raises(ValueError):
        Triggers(light_entity="camera.meter")  # not something to switch on
    with pytest.raises(ValueError):
        Triggers(light_entity="not an entity")
    assert Triggers().regular is True and merge_triggers(None)["only_states"] == {}


def test_entity_only_triggers_on_its_state():
    triggers = merge_triggers(
        {"entities": ["sensor.status", "cover.door"], "only_states": {"sensor.status": "Flow finished"}}
    )
    assert entity_triggers(triggers, "sensor.status", "flow finished")  # case does not matter
    assert not entity_triggers(triggers, "sensor.status", "Take image")
    assert entity_triggers(triggers, "cover.door", "open")  # no state set: any change


class FakeHa:
    """Home Assistant REST stand-in: remembers entity states and what was switched."""

    enabled = True

    def __init__(self, log: list[str]):
        self.log = log
        self.states = {"light.flash": "off"}
        self.fail = False

    async def state(self, entity_id):
        return self.states.get(entity_id)

    async def switch(self, entity_id, on):
        if self.fail:
            raise RuntimeError("no connection")
        self.states[entity_id] = "on" if on else "off"
        self.log.append("on" if on else "off")

    async def ping(self):
        return True

    async def entities(self):
        return []

    async def cameras(self):
        return []

    async def close(self):
        pass


class LoggingCamera:
    def __init__(self, log: list[str]):
        self.log = log

    async def grab(self, source_type, source):
        self.log.append("grab")
        buf = io.BytesIO()
        render("00500").save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    async def close(self):
        pass


@requires_reader
def test_only_triggered_checks_with_a_light(tmp_path, model_dir):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=model_dir
    )
    log: list[str] = []
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber, rt.ha = LoggingCamera(log), FakeHa(log)
        created = client.post(
            "/api/v1/sensors",
            json={
                "name": "Meter",
                "kind": "reading",
                "source_type": "http",
                "source": "x",
                "interval_s": 1,
                "debounce": 1,
                "triggers": {
                    "regular": False,
                    "entities": ["sensor.status"],
                    "only_states": {"sensor.status": "Flow finished"},
                    "burst_duration_s": 0,
                    "light_entity": "light.flash",
                    "light_delay_s": 0.2,
                },
            },
        )
        assert created.status_code == 201, created.text
        sid = created.json()["id"]

        def view():
            return client.get(f"/api/v1/sensors/{sid}").json()

        def fire(old, new):
            asyncio.run_coroutine_threadsafe(rt._on_ha_state("sensor.status", old, new), rt._loop).result(10)

        # One check after start-up, with the light on for it: a frame is thrown away while it warms
        # up (a camera may hand out one it took before), the next one is checked.
        lit_check = ["on", "grab", "grab", "off"]
        assert wait_for(lambda: view()["reading"]["value"] == "500", timeout=60)
        assert wait_for(lambda: log == lit_check, timeout=10), log
        assert view()["triggers"]["regular"] is False

        # No regular check although the interval is one second, and the UI does not take frames either.
        time.sleep(2.5)
        assert client.get(f"/api/v1/sensors/{sid}/frame").status_code == 200
        assert log == lit_check, log

        # Only the chosen state of the entity starts a check.
        fire("Flow finished", "Take image")
        time.sleep(1)
        assert log.count("grab") == 2
        fire("Digitalization", "Flow finished")
        assert wait_for(lambda: log == lit_check * 2, timeout=10), log
        assert view()["live"]["last_trigger"]["source"] == "entity"

        # A light that is already on is somebody else's: used, but not switched. (Not right after
        # VisionState switched it off: then Home Assistant may just not have caught up yet.)
        rt.lights._switched_off.clear()
        rt.ha.states["light.flash"] = "on"
        fire("x", "Flow finished")
        assert wait_for(lambda: log.count("grab") == 6, timeout=10)
        time.sleep(0.5)
        assert log[-1] == "grab" and rt.ha.states["light.flash"] == "on"

        # When the light can not be switched the check still runs (one plain frame), and the
        # settings say why.
        rt.ha.states["light.flash"], rt.ha.fail = "off", True
        fire("x", "Flow finished")
        assert wait_for(lambda: log.count("grab") == 7, timeout=10)
        assert view()["live"]["light_error"] == "no connection"

        # During the burst after a trigger the light stays on: one on, several frames, one off.
        rt.ha.fail = False
        triggers = {**view()["triggers"], "burst_duration_s": 2, "burst_interval_s": 0.5}
        assert client.patch(f"/api/v1/sensors/{sid}", json={"triggers": triggers}).status_code == 200
        time.sleep(0.5)
        log.clear()
        fire("x", "Flow finished")
        assert wait_for(lambda: "off" in log, timeout=15), log
        assert log[0] == "on" and log[-1] == "off" and log.count("on") == 1 and log.count("grab") >= 3, log


class EspCamera:
    """Like an ESP32 camera: it hands out the picture it keeps ready, taken right after the
    previous one was fetched — so the first picture after the light comes on is a dark one."""

    def __init__(self, ha: FakeHa):
        self.ha = ha
        self.ready = self.take()

    def take(self) -> bytes:
        buf = io.BytesIO()
        if self.ha.states.get("light.flash") == "on":
            render("00500").save(buf, format="JPEG", quality=92)
        else:
            Image.new("RGB", (400, 160), (6, 6, 8)).save(buf, format="JPEG")  # dark: nothing to read
        return buf.getvalue()

    async def grab(self, source_type, source):
        picture, self.ready = self.ready, self.take()
        return picture

    async def close(self):
        pass


class DimCamera:
    """A display that is only readable in the light; without it a dim copy (enough to see a change)."""

    def __init__(self, ha: FakeHa, log: list[str]):
        self.ha, self.log, self.text = ha, log, "00500"

    async def grab(self, source_type, source):
        lit = self.ha.states.get("light.flash") == "on"
        self.log.append("grab lit" if lit else "grab dark")
        image = render(self.text)
        if not lit:
            from PIL import ImageEnhance

            image = ImageEnhance.Brightness(image).enhance(0.5)
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    async def close(self):
        pass


@requires_reader
def test_change_detection_with_a_light_reads_a_new_frame_in_the_light(tmp_path, model_dir):
    """Changes are looked for without the light; the check itself takes a frame in the light, and
    a lit frame never counts as a change (that would keep the checks going for ever)."""
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=model_dir
    )
    log: list[str] = []
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.ha = FakeHa(log)
        camera = DimCamera(rt.ha, log)
        rt.grabber = camera
        triggers = {
            "regular": False,
            "change_detection": True,
            "change_interval_s": 0.5,
            "change_threshold": 0.005,  # the drawn display changes little (thin segments)
            "burst_duration_s": 0,
            "light_entity": "light.flash",
            "light_delay_s": 0.2,
        }
        body = {
            "name": "Meter",
            "kind": "reading",
            "source_type": "http",
            "source": "x",
            "interval_s": 3600,
            "debounce": 1,
            "threshold": 0.5,  # "88888" below is read at ~0.69
        }
        sid = client.post("/api/v1/sensors", json={**body, "triggers": triggers}).json()["id"]

        def view():
            return client.get(f"/api/v1/sensors/{sid}").json()

        assert wait_for(lambda: view()["reading"]["value"] == "500", timeout=60)  # read in the light
        time.sleep(2)  # nothing changes: dark probes only, no new check in the light
        checks = log.count("on")
        assert checks == 1 and log[-1] == "grab dark", log

        camera.text = "88888"  # the display changes: seen without the light, read with it
        assert wait_for(lambda: view()["reading"]["value"] == "88888", timeout=30), view()["reading"]["last"]
        assert view()["live"]["last_trigger"]["source"] == "change"
        time.sleep(2)
        assert log.count("on") == 2, log  # and no endless checks after it


@requires_reader
def test_a_camera_that_hands_out_an_older_picture_is_read_in_the_light(tmp_path, model_dir):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=model_dir
    )
    log: list[str] = []
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.ha = FakeHa(log)
        rt.grabber = EspCamera(rt.ha)
        body = {
            "name": "Meter",
            "kind": "reading",
            "source_type": "ha_camera",
            "source": "camera.esp",
            "interval_s": 3600,
            "debounce": 1,
            "triggers": {"regular": False, "burst_duration_s": 0, "light_entity": "light.flash", "light_delay_s": 0.2},
        }
        sid = client.post("/api/v1/sensors", json=body).json()["id"]

        def view():
            return client.get(f"/api/v1/sensors/{sid}").json()

        # The check after start-up: the dark picture kept from before is thrown away.
        assert wait_for(lambda: view()["reading"]["last"] is not None, timeout=60)
        assert view()["reading"]["last"]["reason"] is None and view()["reading"]["value"] == "500"
        for _ in range(3):  # and every check after it, the light going off in between
            before = view()["reading"]["last"]["at"]
            client.post(f"/api/v1/sensors/{sid}/classify")
            assert wait_for(lambda before=before: view()["reading"]["last"]["at"] != before, timeout=30)
            assert view()["reading"]["last"]["reason"] is None, view()["reading"]["last"]
        assert client.get(f"/api/v1/sensors/{sid}/reading-quality").json()["periods"][0]["rejected"] == 0
