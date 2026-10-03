"""Home Assistant entity IDs: older sensors keep theirs, newer ones are named by Home Assistant."""

import asyncio
import io
import json
import sqlite3
import zipfile

from fastapi.testclient import TestClient

from visionstate.db import Database, Sensor
from visionstate.ha_events import fetch_entity_ids, our_entity_ids
from visionstate.main import create_app
from visionstate.mqtt import ha_slug
from visionstate.settings import Settings


def test_migration_keeps_the_prefix_of_existing_sensors(tmp_path):
    path = tmp_path / "old.db"
    Database(path).init()
    con = sqlite3.connect(path)
    # As a database of 0.6.3b5 and older (neither column yet).
    con.execute("ALTER TABLE sensor DROP COLUMN entity_prefix")
    con.execute("ALTER TABLE sensor DROP COLUMN publish")
    con.execute(
        "INSERT INTO sensor (slug, name, kind, source_type, source, interval_s, threshold, debounce, enabled, created_at) "
        "VALUES ('garage', 'Garage', 'single_state', 'http', 'http://cam', 30, 0.7, 2, 1, '2026-10-01 12:00:00')"
    )
    con.execute("PRAGMA user_version = 7")
    con.commit()
    con.close()
    db = Database(path)
    db.init()
    with db.session() as s:
        s.add(
            Sensor(
                slug="new",
                name="New",
                source_type="http",
                source="http://cam",
                interval_s=30,
                threshold=0.7,
                debounce=2,
            )
        )
    with db.session() as s:
        assert {row.slug: row.entity_prefix for row in s.query(Sensor)} == {"garage": True, "new": False}


def test_ha_slug_is_close_to_home_assistant():
    assert ha_slug("Garage door") == "garage_door"
    assert ha_slug("Vannmåler") == "vannmaler"
    assert ha_slug("Bløtkake Ærlig") == "blotkake_aerlig"
    assert ha_slug("Water meter 2 Person count") == "water_meter_2_person_count"
    assert ha_slug("???") == "unknown"


def test_registry_list_is_filtered_to_our_entities():
    registry = [
        {"platform": "mqtt", "unique_id": "visionstate_garage_state", "entity_id": "sensor.garage"},
        {"platform": "mqtt", "unique_id": "zigbee_1", "entity_id": "sensor.other"},
        {"platform": "hue", "unique_id": "visionstate_x", "entity_id": "light.x"},
        {"platform": "mqtt", "unique_id": None, "entity_id": "sensor.none"},
    ]
    assert our_entity_ids(registry) == {"visionstate_garage_state": "sensor.garage"}


def test_fetch_entity_ids_from_home_assistant():
    from websockets.asyncio.server import serve

    async def handler(ws):
        await ws.send(json.dumps({"type": "auth_required"}))
        assert json.loads(await ws.recv())["access_token"] == "token"
        await ws.send(json.dumps({"type": "auth_ok"}))
        request = json.loads(await ws.recv())
        assert request["type"] == "config/entity_registry/list"
        result = [{"platform": "mqtt", "unique_id": "visionstate_a_state", "entity_id": "sensor.my_a"}]
        await ws.send(json.dumps({"id": request["id"], "type": "result", "success": True, "result": result}))

    async def scenario():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await fetch_entity_ids(f"ws://127.0.0.1:{port}", "token")

    assert asyncio.run(scenario()) == {"visionstate_a_state": "sensor.my_a"}


def _bundle(sensor: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"schema": 1, "sensor": sensor, "samples": []}))
    return buf.getvalue()


def test_export_and_import_keep_the_entity_id_style(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=tmp_path
    )
    body = {
        "name": "Door",
        "source_type": "http",
        "source": "http://cam",
        "states": [{"key": "open", "name": "Open"}, {"key": "closed", "name": "Closed"}],
        "enabled": False,
    }
    with TestClient(create_app(settings)) as client:

        def imported(content: bytes) -> dict:
            resp = client.post("/api/v1/import", files={"file": ("b.zip", content)})
            assert resp.status_code == 201, resp.text
            return client.get(f"/api/v1/sensors/{resp.json()['id']}").json()

        new = client.post("/api/v1/sensors", json=body).json()
        assert new["entity_id"] == "sensor.door"
        # A bundle from an older version (no field) is an older sensor: it keeps its prefix.
        assert imported(_bundle({**body, "interval_s": 30}))["entity_id"] == "sensor.visionstate_door_2"
        # A new sensor exported and imported stays new.
        assert imported(client.get(f"/api/v1/sensors/{new['id']}/export").content)["entity_id"] == "sensor.door_3"
