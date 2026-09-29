"""Possibly mislabelled samples and the app-wide review queue entity."""

from fastapi.testclient import TestClient

from visionstate.main import create_app
from visionstate.mqtt import REVIEW_TOPICS, hub_discovery_messages
from visionstate.settings import Settings

from .conftest import requires_model
from .test_integration import FakeCamera, wait_for


def test_hub_discovery_has_review_queue_sensor():
    [(topic, payload)] = hub_discovery_messages("homeassistant")
    assert topic == "homeassistant/sensor/visionstate/review_queue/config"
    assert payload["default_entity_id"] == "sensor.visionstate_review_queue"
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
        assert item["label"] == "closed" and item["predicted"] == "open"
        tips = client.get(f"/api/v1/sensors/{sid}/quality").json()["tips"]
        assert any(t["action"] == "suspects" for t in tips)

        # "The label is right" hides it without retraining.
        client.post(f"/api/v1/sensors/{sid}/samples/verify", json={"sample_ids": [wrong]})
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
