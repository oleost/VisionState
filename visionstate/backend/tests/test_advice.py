"""Thresholds suggested from review answers, for every sensor kind (api/advice.py): what each kind's
answers are, the suggestion, and setting it."""

from fastapi.testclient import TestClient

from visionstate.db import Prediction, Sample, SampleLabel, State
from visionstate.db import Sensor as SensorRow
from visionstate.main import create_app

from .test_storage import STATES, settings  # noqa: F401 (fixture)


def add(rt, sensor_id: int, **fields) -> int:  # noqa: ANN001
    with rt.db.session() as s:
        row = Prediction(sensor_id=sensor_id, is_change=False, **fields)
        s.add(row)
        s.flush()
        return row.id


def answered_frame(rt, sensor_id: int, seen: str, taught: str, confidence: float) -> None:  # noqa: ANN001
    """A state sensor's frame asked about and answered: the sample the answer added, labelled."""
    with rt.db.session() as s:
        state = s.query(State).filter_by(sensor_id=sensor_id, key=taught).one()
        sample = Sample(sensor_id=sensor_id, filename="x.jpg", origin="review")
        s.add(sample)
        s.flush()
        s.add(SampleLabel(sample_id=sample.id, state_id=state.id))
        sample_id = sample.id
    add(
        rt,
        sensor_id,
        state_key=seen,
        published_key="unknown",
        confidence=confidence,
        review_reason="low_confidence",
        reviewed=True,
        sample_id=sample_id,
    )


def item(client, sensor_id: int, key: str) -> dict:  # noqa: ANN001
    items = client.get(f"/api/v1/sensors/{sensor_id}/advice").json()["items"]
    return next(i for i in items if i["key"] == key)


def test_a_state_sensor_is_told_to_lower_its_threshold_by_right_answers_below_it(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        base = {"source_type": "http", "source": "x", "enabled": False}
        sid = client.post("/api/v1/sensors", json={**base, "name": "Door", "states": STATES}).json()["id"]
        threshold = client.get(f"/api/v1/sensors/{sid}").json()["threshold"]
        keys = [s["key"] for s in client.get(f"/api/v1/sensors/{sid}").json()["states"]]
        assert item(client, sid, "threshold")["advice"]["yes"] == 0  # no answers yet
        # Right at 0.55 and 0.6 (below the threshold: reported unknown), wrong at 0.4.
        answered_frame(rt, sid, keys[0], keys[0], 0.55)
        answered_frame(rt, sid, keys[0], keys[0], 0.6)
        answered_frame(rt, sid, keys[0], keys[1], 0.4)
        found = item(client, sid, "threshold")
        assert found["verb"] == "published" and found["threshold"] == threshold
        assert (found["advice"]["yes"], found["advice"]["no"]) == (2, 1)
        assert found["advice"]["current"]["yes_passed"] == 0
        assert found["advice"]["suggested"] == {"threshold": 0.55, "yes_passed": 2, "no_passed": 0}
        resp = client.post(f"/api/v1/sensors/{sid}/advice/threshold", json={"threshold": 0.55})
        assert resp.status_code == 200, resp.text
        assert client.get(f"/api/v1/sensors/{sid}").json()["threshold"] == 0.55
        assert item(client, sid, "threshold")["advice"]["suggested"] is None  # it suits them now


def test_a_reading_sensor_is_told_to_raise_its_threshold_by_misreads_above_it(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        base = {"source_type": "http", "source": "x", "enabled": False}
        sid = client.post("/api/v1/sensors", json={**base, "name": "Meter", "kind": "reading"}).json()["id"]
        client.patch(f"/api/v1/sensors/{sid}", json={"threshold": 0.5})
        for confidence, right in ((0.9, True), (0.95, True), (0.55, False), (0.6, False)):
            add(rt, sid, state_key="", published_key=None, confidence=confidence, read_ok=right, reviewed=True)
        found = item(client, sid, "threshold")
        assert found["verb"] == "accepted" and found["advice"]["current"]["no_passed"] == 2
        assert found["advice"]["suggested"] == {"threshold": 0.61, "yes_passed": 2, "no_passed": 0}
        assert client.post(f"/api/v1/sensors/{sid}/advice/threshold", json={"threshold": 1.5}).status_code == 400
        assert client.post(f"/api/v1/sensors/{sid}/advice/nothing", json={"threshold": 0.6}).status_code == 404


def test_own_labels_are_told_from_the_answers_to_is_this_ours(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        body = {"source_type": "http", "source": "x", "enabled": False, "name": "Drive", "kind": "objects"}
        sid = client.post("/api/v1/sensors", json={**body, "objects": {"classes": ["car"]}}).json()["id"]
        assert client.get(f"/api/v1/sensors/{sid}/advice").json()["items"] == []  # no own label: nothing to suggest
        with rt.db.session() as s:  # an own label, as teaching one makes it
            row = s.get(SensorRow, sid)
            assert row is not None
            row.objects = {**(row.objects or {}), "custom": [{"key": "our_car", "name": "Our car", "parent": "car"}]}
        rt.taught.pop(sid, None)
        for similarity in (0.82, 0.84, 0.85):
            ask = {"label": "our_car", "similarity": similarity, "box": [0, 0, 1, 1], "detected": "car"}
            add(
                rt,
                sid,
                state_key="our_car",
                published_key="ask",
                confidence=0.9,
                probs={"ask": ask, "answer": "yes"},
                reviewed=True,
            )
        # An answer whose taught box has been deleted since (wrong, or no longer wanted) no longer counts.
        ask = {"label": "our_car", "similarity": 0.9, "box": [0, 0, 1, 1], "detected": "car"}
        add(
            rt,
            sid,
            state_key="our_car",
            published_key="ask",
            confidence=0.9,
            probs={"ask": ask, "answer": "no"},
            reviewed=True,
            sample_id=987654,
        )
        found = item(client, sid, "teach:match_similarity")
        assert (found["advice"]["yes"], found["advice"]["no"]) == (3, 0)
        assert found["advice"]["current"]["yes_passed"] == 0  # asked about: below "recognised"
        suggested = found["advice"]["suggested"]
        assert suggested is not None and suggested["threshold"] == 0.82 and suggested["yes_passed"] == 3
        assert found["limits"][0] >= 0.8  # never below asking or keeping
        assert (
            client.post(f"/api/v1/sensors/{sid}/advice/teach:match_similarity", json={"threshold": 0.82}).status_code
            == 200
        )
        assert client.get(f"/api/v1/sensors/{sid}").json()["objects"]["teach"]["match_similarity"] == 0.82
        # No faces on this sensor: no face threshold.
        keys = [i["key"] for i in client.get(f"/api/v1/sensors/{sid}/advice").json()["items"]]
        assert keys == ["teach:match_similarity"]
