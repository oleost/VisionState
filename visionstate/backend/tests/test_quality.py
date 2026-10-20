"""Possibly mislabelled samples and the app-wide review queue entity."""

import pytest
from fastapi.testclient import TestClient

from visionstate import classifier
from visionstate.db import ModelInfo
from visionstate.main import create_app
from visionstate.mqtt import REVIEW_TOPICS, hub_discovery_messages
from visionstate.settings import Settings

from .conftest import requires_model
from .test_integration import FakeCamera, wait_for


def test_hub_discovery_has_review_queue_sensor():
    [(topic, payload)] = hub_discovery_messages("homeassistant")
    assert topic == "homeassistant/sensor/visionstate/review_queue/config"
    # Home Assistant names it sensor.visionstate_review_queue after the device and the entity.
    assert payload["device"]["name"] == "VisionState" and payload["name"] == "Review queue"
    assert "default_entity_id" not in payload
    assert payload["state_topic"] == REVIEW_TOPICS["count"]


@requires_model
def test_mislabelled_sample_is_flagged_and_can_be_verified(tmp_path, model_dir):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=model_dir
    )
    camera = FakeCamera()
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
                "enabled": False,
            },
        ).json()["id"]
        for state in ("open", "closed"):
            camera.state = state
            for _ in range(8):
                client.post(f"/api/v1/sensors/{sid}/capture", json={"state_key": state})
        camera.state = "open"
        wrong = client.post(f"/api/v1/sensors/{sid}/capture", json={"state_key": "closed"}).json()["id"]

        def suspects():
            return client.get(f"/api/v1/sensors/{sid}/quality").json()["suspects"]

        assert wait_for(lambda: any(s["sample_id"] == wrong for s in suspects()))
        item = next(s for s in suspects() if s["sample_id"] == wrong)
        assert item["label"] == "closed" and item["predicted"] == "open" and not item["verified"]
        quality = client.get(f"/api/v1/sensors/{sid}/quality").json()
        assert any(t["action"] == "suspects" for t in quality["tips"])
        # It is one of the samples counted in its cell of the confusion matrix.
        keys, matrix = quality["confusion"]["keys"], quality["confusion"]["matrix"]
        assert matrix[keys.index("closed")][keys.index("open")] >= 1

        # "The label is right" marks it without retraining: no longer a warning, still in its cell.
        client.post(f"/api/v1/sensors/{sid}/samples/verify", json={"sample_ids": [wrong]})
        assert next(s for s in suspects() if s["sample_id"] == wrong)["verified"]
        quality = client.get(f"/api/v1/sensors/{sid}/quality").json()
        assert all(t["action"] != "suspects" for t in quality["tips"]) or any(not s["verified"] for s in suspects())

        # Still listed after the next training, as long as its cell counts it (it used to be dropped).
        version = client.get(f"/api/v1/sensors/{sid}").json()["model"]["version"]
        client.post(f"/api/v1/sensors/{sid}/retrain")
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["model"]["version"] > version)
        quality = client.get(f"/api/v1/sensors/{sid}/quality").json()
        keys, matrix = quality["confusion"]["keys"], quality["confusion"]["matrix"]
        listed = [s for s in quality["suspects"] if s["label"] == "closed" and s["predicted"] == "open"]
        assert len(listed) == matrix[keys.index("closed")][keys.index("open")]
        assert next(s for s in listed if s["sample_id"] == wrong)["verified"]

        # Relabelled: it no longer carries the label it was guessed wrong for.
        client.post(f"/api/v1/sensors/{sid}/samples/label", json={"sample_ids": [wrong], "state_key": "open"})
        assert all(s["sample_id"] != wrong for s in suspects())


@requires_model
def test_relabel_and_delete_retrain_without_errors(tmp_path, model_dir):
    """Sync endpoints (label/delete) run in a worker thread and must still schedule retraining."""
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=model_dir
    )
    camera = FakeCamera()
    with TestClient(create_app(settings), raise_server_exceptions=True) as client:
        client.app.state.runtime.grabber = camera
        sid = client.post(
            "/api/v1/sensors",
            json={
                "name": "Door",
                "source_type": "http",
                "source": "x",
                "states": [{"name": "Open"}, {"name": "Closed"}],
            },
        ).json()["id"]
        ids = []
        for state in ("open", "closed"):
            camera.state = state
            for _ in range(3):
                ids.append(client.post(f"/api/v1/sensors/{sid}/capture", json={"state_key": state}).json()["id"])
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["model"] is not None)
        version = client.get(f"/api/v1/sensors/{sid}").json()["model"]["version"]

        resp = client.post(f"/api/v1/sensors/{sid}/samples/label", json={"sample_ids": [ids[0]], "state_key": "closed"})
        assert resp.status_code == 200
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["model"]["version"] > version)

        resp = client.post(f"/api/v1/sensors/{sid}/samples/delete", json={"sample_ids": [ids[1]]})
        assert resp.status_code == 200
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["model"]["n_samples"] == 5)


@requires_model
@pytest.mark.parametrize("outdated", ["legacy", "format", "older version trained"])
def test_outdated_heads_are_retrained_on_startup(tmp_path, model_dir, outdated):
    """A head from before HEAD_FORMAT (a scikit-learn pickle, <id>.joblib), of another format, or
    older than the sensor's model (an older version the app was taken back to trained it since) is
    retrained automatically at startup. The .joblib then holds the marker that makes an older
    version retrain too."""
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=model_dir
    )
    camera = FakeCamera()
    body = {"name": "Door", "source_type": "http", "source": "x", "states": [{"name": "Open"}, {"name": "Closed"}]}
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        for state in ("open", "closed"):
            camera.state = state
            for _ in range(3):
                client.post(f"/api/v1/sensors/{sid}/capture", json={"state_key": state})
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["model"] is not None)
        version = client.get(f"/api/v1/sensors/{sid}").json()["model"]["version"]
        legacy = classifier.legacy_path(settings.heads_dir, sid)
        assert legacy.read_bytes() == classifier.LEGACY_MARKER
        if outdated == "older version trained":
            with client.app.state.runtime.db.session() as s:
                s.get(ModelInfo, sid).version = version = version + 1

    path = classifier.head_path(settings.heads_dir, sid)
    if outdated == "legacy":
        path.replace(legacy)  # no .npz: what 0.10.0b3 left
    elif outdated == "format":
        head = classifier.load(path)
        assert head is not None
        head.format = 0
        classifier.save(head, path)

    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["model"]["version"] > version)
        assert client.get(f"/api/v1/sensors/{sid}").json()["status"] == "ok"
    head = classifier.load(path)
    assert head is not None and head.format == classifier.HEAD_FORMAT
    assert legacy.read_bytes() == classifier.LEGACY_MARKER


@requires_model
def test_a_head_without_the_marker_gets_it_at_startup(tmp_path, model_dir):
    """A head trained before the .joblib marker existed is kept (no retrain); the marker is added so an
    older version the app is taken back to retrains instead of finding no head or an old one."""
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=model_dir
    )
    camera = FakeCamera()
    body = {"name": "Door", "source_type": "http", "source": "x", "states": [{"name": "Open"}, {"name": "Closed"}]}
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        for state in ("open", "closed"):
            camera.state = state
            for _ in range(3):
                client.post(f"/api/v1/sensors/{sid}/capture", json={"state_key": state})
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["model"] is not None)
        version = client.get(f"/api/v1/sensors/{sid}").json()["model"]["version"]
    legacy = classifier.legacy_path(settings.heads_dir, sid)
    legacy.write_bytes(b"an old scikit-learn pickle")

    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        assert legacy.read_bytes() == classifier.LEGACY_MARKER
        assert client.get(f"/api/v1/sensors/{sid}").json()["model"]["version"] == version
