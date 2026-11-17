"""Turning dials: counting turns between checks and the sensor that gives a flow (docs/TURNING_DIAL_PLAN.md)."""

import io

import pytest
from fastapi.testclient import TestClient

from visionstate import readers, turns
from visionstate.main import create_app
from visionstate.mqtt import SensorDescriptor, discovery_messages
from visionstate.settings import KIND_READING, TURNING, Settings, merge_reading

from .conftest import MODEL_DIR
from .displays import render_test_dial
from .test_integration import wait_for

WINDOW = 60.0


def counted(positions: list[float], every: float = 2.0) -> tuple[turns.Turns, list[str]]:
    """The turns counted from readings ``every`` seconds apart, and what each reading was."""
    tracker = turns.Turns()
    seen = [turns.observe(tracker, p, 1000.0 + k * every, WINDOW) for k, p in enumerate(positions)]
    return tracker, seen


def test_a_dial_that_turns_is_counted_a_reading_late():
    # 0.0 → 9.0 in steps of 1.5 (0.15 of a turn each), then on past 0 to 3.0: 1.3 turns.
    tracker, seen = counted([0.0, 1.5, 3.0, 4.5, 6.0, 7.5, 9.0, 0.5, 2.0, 3.0, 3.0, 3.0])
    assert seen[:3] == [turns.STARTED, turns.WAITING, turns.MOVED]
    assert tracker.total == pytest.approx(1.3)
    assert seen[-1] == turns.STILL  # the last move counted with the reading that confirmed it


def test_a_dial_that_stands_still_or_jitters_adds_nothing():
    tracker, seen = counted([5.0, 5.1, 4.9, 5.05, 4.95, 5.0])
    assert tracker.total == 0.0 and set(seen[1:]) == {turns.STILL}


def test_a_slow_leak_still_adds_up():
    # 0.01 of a turn per reading is under the deadband, but the counted position stays where it was.
    tracker, _ = counted([k * 0.1 for k in range(21)])
    assert 0.2 - 2 * TURNING["deadband"] <= tracker.total <= 0.2


def test_one_misread_needle_never_adds_a_turn():
    # Read a third of a turn ahead once, then where it was again: the waiting move is dropped.
    tracker, seen = counted([2.0, 2.0, 5.5, 2.1, 2.0, 2.0])
    assert seen[2:4] == [turns.WAITING, turns.JUMPED] and tracker.total == 0.0
    # Read half way round once (the counterweight taken for the tip): too far to count either way.
    tracker, seen = counted([2.0, 2.0, 7.0, 2.1, 2.0, 2.0])
    assert turns.TOO_FAST in seen and tracker.total == 0.0


def test_too_far_between_two_readings_starts_the_count_again():
    # More than max_turn between two readings: it may have gone round once more unseen.
    jump = (TURNING["max_turn"] + 0.1) * 10
    tracker, seen = counted([0.0, 0.0, jump, jump])
    assert turns.TOO_FAST in seen and tracker.total == 0.0


def test_a_gap_starts_the_count_again():
    tracker = turns.Turns()
    turns.observe(tracker, 1.0, 1000.0, WINDOW)
    assert turns.observe(tracker, 3.0, 1000.0 + TURNING["max_gap_s"] + 1, WINDOW) == turns.STARTED
    assert tracker.total == 0.0 and tracker.counted == pytest.approx(0.3)


def test_the_flow_is_the_turns_over_the_window():
    # A quarter of a turn every 15 s: one turn a minute; with 2 ft³ a turn, 2 ft³/min.
    tracker, _ = counted([k * 2.5 % 10 for k in range(9)], every=15.0)
    now = 1000.0 + 8 * 15.0
    per_hour = readers.counter_rate(tracker.samples, now, WINDOW, TURNING["min_span_s"])
    assert per_hour is not None and per_hour * 2 / 60 == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("per", "shown"), [(0.0, "0"), (2.0, "2.00"), (12.345, "12.3"), (1234.5, "1234"), (0.0123, "0.0123")]
)
def test_a_flow_has_three_significant_digits(per, shown):
    assert readers.format_flow(per) == shown
    assert readers.running(shown) is (per > 0)


def test_a_dial_is_announced_as_a_flow_with_running():
    reading = {"mode": "flow", "display": "dial", "unit": "ft³", "device_class": "gas", "per_turn": 2}
    sensor = SensorDescriptor("gas_dial", "Gas dial", [], KIND_READING, [], merge_reading(reading), chosen=None)
    configs = {topic.split("/")[-2]: payload for topic, payload in discovery_messages("homeassistant", sensor)}
    value = configs["state"]
    assert (value["unit_of_measurement"], value["device_class"]) == ("ft³/min", "volume_flow_rate")
    assert value["state_class"] == "measurement"
    assert configs["running"]["device_class"] == "running" and configs["running"]["state_topic"].endswith("/running")
    assert "rate" not in configs
    # "ft3" with no Home Assistant type is ft³ too, as the UI shows it (found on a real Home
    # Assistant: the flow went out as 120 ft3/h while the UI said ft³/min); a counter's rate keeps
    # its "m3/h" (test_reading).
    typed = merge_reading({"mode": "flow", "display": "dial", "unit": "ft3", "device_class": "", "per_turn": 2})
    sensor = SensorDescriptor("gas_dial", "Gas dial", [], KIND_READING, [], typed, chosen=None)
    value = {topic.split("/")[-2]: payload for topic, payload in discovery_messages("homeassistant", sensor)}["state"]
    assert (value["unit_of_measurement"], value["device_class"]) == ("ft³/min", "volume_flow_rate")


class DialCamera:
    """A camera on a drawn test dial whose needle moves ``step`` (0.0-9.9) at every picture."""

    def __init__(self, step: float):
        self.position, self.step = 0.0, step

    async def grab(self, source_type, source, camera=None):
        self.position = (self.position + self.step) % 10
        buf = io.BytesIO()
        render_test_dial(self.position).save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    async def close(self):
        pass


def test_a_turning_dial_sensor(tmp_path, monkeypatch):
    monkeypatch.setitem(TURNING, "min_span_s", 2.0)
    settings = Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=tmp_path / "none",
        bundled_models_dir=MODEL_DIR,
    )
    camera = DialCamera(0.7)
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        sent: dict[str, object] = {}

        async def publish(topic, payload, retain=False):
            sent[topic] = payload

        rt.mqtt.publish = publish
        reading = {"mode": "flow", "display": "dial", "unit": "ft³", "device_class": "gas", "per_turn": 2}
        preview = {"source_type": "http", "source": "http://fake", "reading": {**reading, "flow_window_s": 10}}
        read = client.post("/api/v1/preview/read", json=preview).json()
        assert read["value"] is None and read["needle"] == pytest.approx(camera.position, abs=0.2)
        # A dial gives a flow, and only a dial does.
        for wrong in ({**reading, "mode": "counter"}, {**reading, "display": "counter"}):
            assert client.post("/api/v1/preview/read", json={**preview, "reading": wrong}).status_code == 422
        body = {
            "name": "Gas dial",
            "kind": "reading",
            "source_type": "http",
            "source": "http://fake",
            "reading": {**reading, "flow_window_s": 10},
            "interval_s": 1,
            "triggers": {"burst_interval_s": 0.5},
        }
        resp = client.post("/api/v1/sensors", json=body)
        assert resp.status_code == 201, resp.text
        sid = resp.json()["id"]
        base = "visionstate/gas_dial"
        view = lambda: client.get(f"/api/v1/sensors/{sid}").json()  # noqa: E731
        # It turns: a flow (ft³/min) and running.
        assert wait_for(lambda: readers.running(view()["reading"]["value"]), timeout=30), view()["reading"]
        assert wait_for(lambda: sent.get(f"{base}/running") == "ON", timeout=10)
        assert float(sent[f"{base}/state"]) > 0
        history = f"/api/v1/history?sensor={sid}"
        assert wait_for(lambda: any(i["published_key"] for i in client.get(history).json()["items"]), timeout=10)
        # It stops: the flow goes to 0 once the window has passed, and running off.
        camera.step = 0.0
        assert wait_for(lambda: view()["reading"]["value"] == "0", timeout=30), view()["reading"]
        assert wait_for(lambda: sent.get(f"{base}/running") == "OFF", timeout=10)
        assert sent[f"{base}/state"] == "0"
