"""Object sensors: detector output handling, filtering, on/off tracking, discovery and the API."""

import re

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate import detectors, imaging
from visionstate.detectors import Detection, postprocess
from visionstate.engine import ObjectTrack, filter_detections, update_tracks
from visionstate.main import create_app
from visionstate.mqtt import SensorDescriptor, discovery_messages
from visionstate.settings import KIND_OBJECTS, OBJECT_MAX_CLASSES, merge_objects

from .conftest import ASSETS, MODEL_DIR, requires_detector
from .test_integration import wait_for

LABELS = detectors.LABELS.labels
PERSON, CAR = 0, 2


def logit(p: float) -> float:
    return float(np.log(p / (1 - p)))


def outputs(*queries: tuple[int, float, tuple[float, float, float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Fake detector outputs: one query per (class, probability, cx/cy/w/h box), rest background."""
    logits = np.full((10, len(LABELS)), -10.0, dtype=np.float32)
    boxes = np.zeros((10, 4), dtype=np.float32)
    for i, (cls, p, box) in enumerate(queries):
        logits[i, cls] = logit(p)
        boxes[i] = box
    return logits, boxes


# --- registry --------------------------------------------------------------------------------


def test_registry_labels_are_complete_and_safe():
    keys = [label.key for label in LABELS]
    assert len(keys) == 80 and len(set(keys)) == 80
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in keys)  # safe in entity ids
    assert set(detectors.LABELS.popular) <= set(keys)
    default = detectors.DETECTORS[detectors.DEFAULT_DETECTOR]
    assert default.bundled and default.license == "Apache-2.0"
    for spec in detectors.DETECTORS.values():
        assert "/resolve/" in spec.url and re.fullmatch(r"[0-9a-f]{64}", spec.sha256)  # pinned + checked


# --- postprocessing ----------------------------------------------------------------------------


def test_postprocess_converts_boxes_and_drops_weak():
    logits, boxes = outputs((PERSON, 0.9, (0.5, 0.5, 0.2, 0.4)), (CAR, 0.3, (0.2, 0.2, 0.1, 0.1)))
    [det] = postprocess(logits, boxes, LABELS, 0.5)
    assert det.key == "person" and det.score == pytest.approx(0.9, abs=1e-4)
    assert det.box == pytest.approx((0.4, 0.3, 0.6, 0.7), abs=1e-4)


def test_postprocess_merges_duplicates_of_one_class_only():
    same = (0.5, 0.5, 0.2, 0.4)
    logits, boxes = outputs(
        (PERSON, 0.9, same),
        (PERSON, 0.8, (0.505, 0.5, 0.2, 0.4)),  # the same person again
        (PERSON, 0.7, (0.1, 0.5, 0.1, 0.2)),  # another person
        (CAR, 0.85, same),  # a car at the same spot is not merged with the person
    )
    found = postprocess(logits, boxes, LABELS, 0.5)
    assert sorted((d.key, round(d.score, 1)) for d in found) == [("car", 0.8), ("person", 0.7), ("person", 0.9)]


# --- region filtering --------------------------------------------------------------------------


FULL_FRAME = (0.0, 0.0, 1.0, 1.0)


def test_filter_maps_boxes_from_the_analysed_crop_to_the_frame():
    roi = {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}
    analysed = imaging.region_box(roi, margin=0.25)  # grown by 0.125 on each side, clamped
    assert analysed == pytest.approx((0.375, 0.375, 1.0, 1.0))
    found = [Detection("person", 0.9, (0.2, 0.2, 0.6, 1.0))]
    [det] = filter_detections(found, analysed, roi, merge_objects({"classes": ["person"]}))
    assert det["box"] == pytest.approx([0.5, 0.5, 0.75, 1.0])


def test_filter_ignores_objects_standing_in_the_margin():
    roi = {"x": 0.4, "y": 0.0, "w": 0.2, "h": 1.0}
    analysed = imaging.region_box(roi, margin=0.25)  # x 0.35-0.65
    beside = Detection("dog", 0.9, (0.8, 0.5, 1.0, 0.9))  # feet at x = 0.62: in the margin only
    assert filter_detections([beside], analysed, roi, merge_objects({"classes": ["dog"]})) == []


def test_filter_uses_the_bottom_centre_the_class_and_the_size():
    # Triangle covering the lower-left half of the frame.
    roi = {"x": 0, "y": 0, "w": 1, "h": 1, "points": [[0, 0], [0, 1], [1, 1]]}
    inside = Detection("person", 0.9, (0.05, 0.3, 0.25, 0.9))  # feet at (0.15, 0.9): inside
    head_in_feet_out = Detection("person", 0.9, (0.6, 0.65, 0.8, 0.7))  # feet at (0.7, 0.7): outside
    tiny = Detection("person", 0.9, (0.1, 0.94, 0.12, 0.96))
    dog = Detection("dog", 0.9, (0.05, 0.3, 0.25, 0.9))
    objects = merge_objects({"classes": ["person"], "min_size": 0.01})
    kept = filter_detections([inside, head_in_feet_out, tiny, dog], FULL_FRAME, roi, objects)
    assert [d["box"][0] for d in kept] == [0.05]
    assert len(filter_detections([inside, dog], FULL_FRAME, roi, objects, all_classes=True)) == 2


def test_in_region_polygon():
    roi = {"x": 0, "y": 0, "w": 1, "h": 1, "points": [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]]}
    assert imaging.in_region(0.5, 0.3, roi)
    assert not imaging.in_region(0.15, 0.8, roi)
    assert imaging.in_region(0.99, 0.99, None)


# --- on/off tracking ---------------------------------------------------------------------------


def seen(n: int, key: str = "person") -> list[dict]:
    return [{"key": key, "score": 0.9, "box": [0, 0, 0.1, 0.1]}] * n


def test_tracks_switch_on_after_required_checks_and_hold_until_cleared():
    tracks: dict[str, ObjectTrack] = {}
    classes = ["person", "car"]
    assert update_tracks(tracks, seen(2), classes, required=2, clear_after_s=30, now=0) == []
    assert update_tracks(tracks, seen(2), classes, required=2, clear_after_s=30, now=5) == ["person"]
    assert (tracks["person"].on, tracks["person"].count, tracks["car"].on) == (True, 2, False)
    # Gone for less than clear_after_s: still on, last count kept.
    assert update_tracks(tracks, [], classes, required=2, clear_after_s=30, now=20) == []
    assert tracks["person"].on and tracks["person"].count == 2
    # Seen again resets the timer; then gone long enough clears it.
    update_tracks(tracks, seen(1), classes, required=2, clear_after_s=30, now=25)
    assert update_tracks(tracks, [], classes, required=2, clear_after_s=30, now=50) == []
    assert update_tracks(tracks, [], classes, required=2, clear_after_s=30, now=56) == ["person"]
    assert (tracks["person"].on, tracks["person"].count) == (False, 0)


def test_tracks_forget_deselected_classes():
    tracks: dict[str, ObjectTrack] = {}
    update_tracks(tracks, seen(1), ["person", "car"], 1, 30, 0)
    update_tracks(tracks, [], ["car"], 1, 30, 1)
    assert set(tracks) == {"car"}


# --- Home Assistant discovery ------------------------------------------------------------------


def test_discovery_for_object_sensor():
    sensor = SensorDescriptor("drive", "Drive", [], KIND_OBJECTS, [("person", "Person"), ("car", "Car")])
    messages = dict(discovery_messages("homeassistant", sensor))
    ids = {payload["default_entity_id"] for payload in messages.values()}
    assert {
        "binary_sensor.visionstate_drive_person",
        "sensor.visionstate_drive_person_count",
        "binary_sensor.visionstate_drive_car",
        "sensor.visionstate_drive_car_count",
        "image.visionstate_drive_frame",
        "button.visionstate_drive_classify",
        "switch.visionstate_drive_enabled",
    } == ids
    person = messages["homeassistant/binary_sensor/visionstate_drive/person/config"]
    assert person["device_class"] == "occupancy" and person["state_topic"] == "visionstate/drive/objects/person/state"
    assert messages["homeassistant/button/visionstate_drive/classify/config"]["name"] == "Detect now"


# --- with the real detector ----------------------------------------------------------------------


def counts(found) -> dict[str, int]:
    result: dict[str, int] = {}
    for det in found:
        result[det.key] = result.get(det.key, 0) + 1
    return result


@requires_detector
def test_detector_finds_objects_in_real_photos():
    """Guards against a broken model file (a bad conversion finds nonsense) and preprocessing bugs."""
    spec = detectors.DETECTORS[detectors.DEFAULT_DETECTOR]
    detector = detectors.Detector(spec, MODEL_DIR / spec.filename)
    driveway = counts(detector.detect(Image.open(ASSETS / "driveway.jpg"), 0.6))
    beach = counts(detector.detect(Image.open(ASSETS / "beach.jpg"), 0.6))
    cat = counts(detector.detect(Image.open(ASSETS / "cat.jpg"), 0.6))
    assert driveway.get("car", 0) >= 3 and driveway.get("person") == 1
    assert beach.get("dog", 0) >= 4 and beach.get("person") == 1
    assert cat == {"cat": 1}


class PhotoCamera:
    def __init__(self, name: str):
        self.name = name

    async def grab(self, source_type, source):
        return (ASSETS / f"{self.name}.jpg").read_bytes()

    async def close(self):
        pass


@pytest.fixture
def settings(tmp_path):
    from visionstate.settings import Settings

    return Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=tmp_path / "none",
        bundled_models_dir=MODEL_DIR,
    )


def create(client, **fields):
    body = {"name": "Beach", "kind": "objects", "source_type": "http", "source": "http://fake", **fields}
    return client.post("/api/v1/sensors", json=body)


@requires_detector
def test_object_sensor_flow(settings):
    camera = PhotoCamera("beach")
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        assert rt.detector is None  # not loaded without object sensors

        cfg = client.get("/api/v1/config").json()
        assert len(cfg["object_labels"]) == 80 and cfg["object_max_classes"] == OBJECT_MAX_CLASSES

        # Validation.
        assert create(client, objects={"classes": ["unicorn"]}).status_code == 422
        assert create(client, objects={"classes": []}).status_code == 422
        assert create(client, states=[{"name": "A"}, {"name": "B"}]).status_code == 400

        resp = create(client, objects={"classes": ["dog", "person"], "clear_after_s": 0}, interval_s=1)
        assert resp.status_code == 201, resp.text
        sensor = resp.json()
        sid = sensor["id"]
        assert sensor["kind"] == "objects" and sensor["threshold"] == 0.6 and sensor["debounce"] == 1
        assert sensor["entity_ids"][:2] == ["binary_sensor.visionstate_beach_dog", "sensor.visionstate_beach_dog_count"]

        def live():
            return {o["key"]: o for o in client.get(f"/api/v1/sensors/{sid}").json()["objects"]["live"]}

        assert wait_for(lambda: live()["dog"]["on"], timeout=60)
        assert live()["dog"]["count"] >= 4 and live()["person"]["count"] == 1
        view = client.get(f"/api/v1/sensors/{sid}").json()
        assert view["status"] == "ok"
        assert (
            client.get(f"/api/v1/sensors/{sid}/frame", params={"frame_id": view["live"]["frame_id"]}).status_code == 200
        )

        # Each class that appeared is in the history, with the frame's detections.
        history = client.get(f"/api/v1/sensors/{sid}/history").json()
        assert {(h["state_key"], h["published_key"]) for h in history} >= {("dog", "on"), ("person", "on")}
        assert all(h["detections"] and h["has_frame"] for h in history)

        # A region around the person only: the dogs outside stop counting and clear.
        resp = client.patch(f"/api/v1/sensors/{sid}", json={"roi": {"x": 0.45, "y": 0.1, "w": 0.2, "h": 0.4}})
        assert resp.status_code == 200
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: not live()["dog"]["on"] and live()["person"]["on"], timeout=30)

        # Training endpoints do not apply.
        assert client.post(f"/api/v1/sensors/{sid}/capture", json={"state_key": "x"}).status_code == 400
        assert client.get(f"/api/v1/sensors/{sid}/quality").status_code == 400
        assert client.post(f"/api/v1/sensors/{sid}/retrain").status_code == 400
        assert (
            client.patch(f"/api/v1/sensors/{sid}", json={"states": [{"name": "A"}, {"name": "B"}]}).status_code == 400
        )

        # The wizard preview returns the frame and everything found in the region.
        preview = client.post(
            "/api/v1/preview/detect", json={"source_type": "http", "source": "http://fake", "roi": None}
        ).json()
        assert preview["image"].startswith("data:image/jpeg;base64,")
        assert {d["key"] for d in preview["detections"]} >= {"dog", "person"}

        # Export and import keep the kind and the objects.
        exported = client.get(f"/api/v1/sensors/{sid}/export")
        imported = client.post("/api/v1/import", files={"file": ("b.zip", exported.content, "application/zip")})
        assert imported.status_code == 201, imported.text
        copy = client.get(f"/api/v1/sensors/{imported.json()['id']}").json()
        assert copy["kind"] == "objects" and copy["objects"]["classes"] == ["dog", "person"]

        # The detector is released once the last object sensor is gone.
        for sensor_id in (sid, copy["id"]):
            assert client.delete(f"/api/v1/sensors/{sensor_id}").status_code == 204
        assert rt.detector is None


@requires_detector
def test_detector_choice_is_stored_and_switchable(settings):
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        current = client.get("/api/v1/settings").json()
        assert current["detector"] == detectors.DEFAULT_DETECTOR
        assert rt.db.get_setting("detector") == detectors.DEFAULT_DETECTOR  # pinned at first start
        body = {
            "backbone": current["backbone"],
            "execution_provider": current["execution_provider"],
            "detector": "nope",
        }
        assert client.put("/api/v1/settings", json=body).status_code == 400
