"""The history API: filters, paging, what arrived since, facets, validation and its index."""

from datetime import UTC, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from visionstate.api.history import EVENT_KIND, EVENT_ROWS
from visionstate.db import Database, Prediction, utcnow
from visionstate.main import create_app
from visionstate.settings import HISTORY, HISTORY_EVENTS, SENSOR_KINDS

from .conftest import requires_model
from .test_storage import STATES, settings  # noqa: F401 (fixture)

NOW = utcnow()


def make_sensors(client) -> dict[str, int]:
    """One paused sensor of each kind (they never check, so only the rows added here exist)."""
    base = {"source_type": "http", "source": "x", "enabled": False}
    return {
        "door": client.post("/api/v1/sensors", json={**base, "name": "Door", "states": STATES}).json()["id"],
        "drive": client.post(
            "/api/v1/sensors",
            json={**base, "name": "Drive", "kind": "objects", "objects": {"classes": ["car", "person"]}},
        ).json()["id"],
        "meter": client.post("/api/v1/sensors", json={**base, "name": "Meter", "kind": "reading"}).json()["id"],
    }


def add(rt, sensor_id: int, minutes_ago: float, state_key: str, published_key: str | None, **fields) -> int:
    with rt.db.session() as s:
        row = Prediction(
            sensor_id=sensor_id,
            created_at=fields.pop("created_at", NOW - timedelta(minutes=minutes_ago)),
            state_key=state_key,
            published_key=published_key,
            confidence=0.9,
            probs={},
            frame=None,
            is_change=fields.pop("is_change", False),
            review_reason=fields.pop("review_reason", None),
            reviewed=fields.pop("reviewed", True),
            **fields,
        )
        s.add(row)
        s.flush()
        return row.id


@pytest.fixture
def seeded(settings):  # noqa: F811
    """Rows of all three kinds; ``ids`` names each one."""
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sid = make_sensors(client)
        ids = {
            "door_open": add(rt, sid["door"], 50, "open", "open", is_change=True),
            "door_unsure": add(rt, sid["door"], 40, "closed", "open", review_reason="low_confidence", reviewed=False),
            "car_on": add(rt, sid["drive"], 30, "car", "on"),
            "our_car_on": add(rt, sid["drive"], 25, "our_car", "on"),
            "car_off": add(rt, sid["drive"], 20, "car", "off"),
            "person_filtered": add(rt, sid["drive"], 15, "person", "filtered"),
            "ask": add(rt, sid["drive"], 12, "our_car", "ask", review_reason="ask", reviewed=False),
            "value": add(rt, sid["meter"], 10, "reading", "1234", is_change=True),
            "rejected": add(rt, sid["meter"], 5, "reading", None, review_reason="rejected", reviewed=False),
            "verified": add(rt, sid["meter"], 2, "reading", "1235", read_ok=True),
        }
        yield client, sid, ids


def found(client, **params) -> list[int]:
    resp = client.get("/api/v1/history", params={"limit": HISTORY["max_page_size"], **params})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == len(body["items"])
    return [item["id"] for item in body["items"]]


@requires_model
def test_filters(seeded):
    client, sid, ids = seeded
    every = sorted(ids.values(), reverse=True)  # newest first: added oldest first
    assert found(client) == every
    assert set(found(client, sensor=sid["door"])) == {ids["door_open"], ids["door_unsure"]}
    assert len(found(client, sensor=[sid["door"], sid["meter"]])) == 5
    assert set(found(client, kind="reading")) == {ids["value"], ids["rejected"], ids["verified"]}

    # Keys: a class and an own label are told apart; a key never matches a reading.
    assert set(found(client, key="car")) == {ids["car_on"], ids["car_off"]}
    assert set(found(client, key=["our_car"])) == {ids["our_car_on"], ids["ask"]}
    assert found(client, key="reading") == []
    # The same key on all chosen sensors (a state named like a class matches on both).
    assert set(found(client, key=["open", "car"], sensor=[sid["door"], sid["drive"]])) == {
        ids["door_open"],
        ids["car_on"],
        ids["car_off"],
    }

    # Events, each among the rows of its kind.
    assert found(client, event="change") == [ids["door_open"]]
    assert found(client, event="flagged") == [ids["door_unsure"]]
    assert found(client, event="appeared", key="car") == [ids["car_on"]]
    assert set(found(client, event=["cleared", "filtered"])) == {ids["car_off"], ids["person_filtered"]}
    assert found(client, event="asked") == [ids["ask"]]
    assert set(found(client, event="value")) == {ids["value"], ids["verified"]}
    assert found(client, event="rejected") == [ids["rejected"]]
    assert found(client, event="verified") == [ids["verified"]]
    assert set(found(client, event=["change", "rejected"])) == {ids["door_open"], ids["rejected"]}
    # A sensor of another kind than the event has none.
    assert found(client, event="change", sensor=sid["meter"]) == []

    assert set(found(client, waiting=True)) == {ids["door_unsure"], ids["ask"], ids["rejected"]}
    assert len(found(client, waiting=False)) == len(ids) - 3


@requires_model
def test_time_window_in_any_time_zone(seeded):
    client, _sid, ids = seeded
    since = NOW - timedelta(minutes=21)
    until = NOW - timedelta(minutes=10)  # exclusive
    expected = [ids["ask"], ids["person_filtered"], ids["car_off"]]
    assert found(client, since=since.isoformat(), until=until.isoformat()) == expected
    # The same moments in another zone; a time without a zone is UTC.
    oslo = timezone(timedelta(hours=2))
    assert found(client, since=since.astimezone(oslo).isoformat(), until=until.astimezone(oslo).isoformat()) == expected
    naive = since.astimezone(UTC).replace(tzinfo=None)
    assert found(client, since=naive.isoformat(), until=until.isoformat()) == expected


@requires_model
def test_pages_stay_stable_while_rows_arrive(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sid = make_sensors(client)["door"]
        same_time = NOW - timedelta(hours=1)
        # Several rows at the very same moment: the id keeps them in order.
        rows = [add(rt, sid, 0, "open", "open", created_at=same_time) for _ in range(3)]
        rows += [add(rt, sid, 50 - i, "open", "open") for i in range(5)]

        def walk(order: str, limit: int, arriving: list[int] | None = None) -> list[int]:
            """Every page in turn; ``arriving`` gets the id of a row added after the first page."""
            seen, cursor = [], None
            while True:
                params = {"sensor": sid, "limit": limit, "order": order}
                if cursor:
                    params["cursor"] = cursor
                page = client.get("/api/v1/history", params=params).json()
                seen += [item["id"] for item in page["items"]]
                if arriving is not None and not arriving:
                    arriving.append(add(rt, sid, 0, "open", "open"))  # new at the top: never shown twice
                cursor = page["next"]
                if cursor is None:
                    return seen

        arrived: list[int] = []
        assert walk("newest", 3, arrived) == sorted(rows, reverse=True)
        rows += arrived
        assert walk("oldest", 2) == sorted(rows)
        assert walk("oldest", len(rows)) == sorted(rows)  # one full page: no next
        assert client.get("/api/v1/history", params={"sensor": sid, "limit": 2}).json()["total"] == len(rows)

        # What arrived since a page was loaded (also when it lists the oldest first): a count only.
        page = client.get("/api/v1/history", params={"sensor": sid, "limit": 1, "order": "oldest"}).json()
        top = client.get("/api/v1/history", params={"sensor": sid, "limit": 1}).json()
        assert page["newest"] == top["newest"] == top["next"]
        assert top["items"][0]["id"] == arrived[0]
        add(rt, sid, 0, "open", "open")
        add(rt, sid, 0, "closed", "closed")
        newer = {"sensor": sid, "limit": 0, "newer_than": page["newest"]}
        answer = client.get("/api/v1/history", params=newer).json()
        assert {**answer, "newest": None} == {"items": [], "total": 2, "next": None, "newest": None}
        assert client.get("/api/v1/history", params={"sensor": 987654}).json()["newest"] is None
        assert client.get("/api/v1/history", params={**newer, "key": "closed"}).json()["total"] == 1


@requires_model
def test_facets_keep_offering_what_another_choice_would_find(seeded):
    client, sid, _ids = seeded

    def facets(**params) -> dict:
        resp = client.get("/api/v1/history/facets", params=params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return {
            "sensors": {x["id"]: x["count"] for x in body["sensors"]},
            "keys": {(x["sensor_id"], x["key"]): x["count"] for x in body["keys"]},
            "events": {x["event"]: x["count"] for x in body["events"]},
        }

    everything = facets()
    assert everything["sensors"] == {sid["door"]: 2, sid["drive"]: 5, sid["meter"]: 3}
    assert everything["keys"] == {
        (sid["door"], "open"): 1,
        (sid["door"], "closed"): 1,
        (sid["drive"], "car"): 2,
        (sid["drive"], "our_car"): 2,
        (sid["drive"], "person"): 1,
    }  # no readings
    assert everything["events"] == {
        "change": 1,
        "flagged": 1,
        "appeared": 2,
        "cleared": 1,
        "filtered": 1,
        "asked": 1,
        "value": 2,
        "rejected": 1,
        "verified": 1,
    }

    # One sensor chosen: the other sensors are still offered, keys and events are its own.
    door = facets(sensor=sid["door"])
    assert door["sensors"] == everything["sensors"]
    assert set(door["keys"]) == {(sid["door"], "open"), (sid["door"], "closed")}
    assert door["events"] == {"change": 1, "flagged": 1}
    # A key chosen: the other keys still offered, the sensors narrowed to those that have it.
    car = facets(key="car")
    assert car["keys"] == everything["keys"]
    assert car["sensors"] == {sid["drive"]: 2}
    assert car["events"] == {"appeared": 1, "cleared": 1}
    # An event chosen: the other events still offered.
    assert facets(event="asked")["events"] == everything["events"]
    assert facets(waiting=True)["events"] == {"flagged": 1, "asked": 1, "rejected": 1}


@requires_model
@pytest.mark.parametrize(
    "params",
    [
        {"kind": "multi_state"},
        {"event": "exploded"},
        {"cursor": "yesterday"},
        {"newer_than": "2026-01-01T00:00:00|x"},
        {"since": "soon"},
        {"limit": HISTORY["max_page_size"] + 1},
        {"limit": -1},
        {"order": "random"},
        {"sensor": list(range(HISTORY["max_filter_values"] + 1))},
    ],
)
def test_bad_filters_are_refused(settings, params):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/history", params=params).status_code == 422
        if "limit" not in params and "order" not in params and "cursor" not in params:
            assert client.get("/api/v1/history/facets", params=params).status_code == 422


@requires_model
def test_unknown_sensor_finds_nothing_and_config_names_the_events(seeded):
    client, _sid, _ids = seeded
    assert found(client, sensor=987654) == []
    config = client.get("/api/v1/config").json()
    assert config["history"]["page_size"] == HISTORY["page_size"]
    assert {kind: tuple(events) for kind, events in config["history_events"].items()} == HISTORY_EVENTS


def test_every_event_has_rows_and_a_kind():
    assert set(EVENT_ROWS) == set(EVENT_KIND)
    assert set(HISTORY_EVENTS) == set(SENSOR_KINDS)
    names = [event for events in HISTORY_EVENTS.values() for event in events]
    assert len(names) == len(set(names))  # a filter value names one event of one kind


def test_index_is_added_to_an_existing_database(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    db.init()
    with db.engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_prediction_sensor_time"))  # as made by an older version
    assert "ix_prediction_sensor_time" not in {i["name"] for i in inspect(db.engine).get_indexes("prediction")}
    db.engine.dispose()

    again = Database(path)
    again.init()
    indexes = {i["name"]: i["column_names"] for i in inspect(again.engine).get_indexes("prediction")}
    assert indexes["ix_prediction_sensor_time"] == ["sensor_id", "created_at", "id"]
    again.init()  # starting again changes nothing
