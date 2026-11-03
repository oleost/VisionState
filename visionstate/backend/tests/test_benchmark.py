"""The speed test: models measured on a sensor's camera, the sensors paused meanwhile."""

import io
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate import backbones
from visionstate.main import create_app
from visionstate.settings import BENCHMARK, Settings
from visionstate.sources import SourceError

from .conftest import MODEL_DIR, requires_reader
from .test_integration import wait_for

original = backbones.locate


class Camera:
    def __init__(self):
        buf = io.BytesIO()
        Image.new("RGB", (320, 240), (90, 110, 130)).save(buf, "JPEG")
        self.jpeg, self.grabs, self.error = buf.getvalue(), 0, None

    async def grab(self, source_type, source):
        self.grabs += 1
        if self.error:
            raise self.error
        return self.jpeg

    async def close(self):
        pass


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "d", media_dir=tmp_path / "m", frontend_dir=tmp_path, bundled_models_dir=MODEL_DIR
    )


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setitem(BENCHMARK, "rounds", 2)


def _state_sensor(client) -> int:
    body = {
        "name": "Door",
        "source_type": "http",
        "source": "http://cam:secret@cam/a.jpg",
        "interval_s": 1,
        "states": [{"name": "A"}, {"name": "B"}],
    }
    return client.post("/api/v1/sensors", json=body).json()["id"]


def _finished(client) -> dict:
    assert wait_for(lambda: client.get("/api/v1/benchmark/run").json()["run"]["state"] != "running", timeout=60)
    return client.get("/api/v1/benchmark/run").json()["run"]


@requires_reader
def test_a_model_is_measured_with_the_sensors_paused_and_they_go_on_afterwards(settings):
    camera = Camera()
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        sid = _state_sensor(client)
        assert wait_for(lambda: rt.embedder is not None and rt.live_state(sid).last_run, timeout=30)
        info = client.get("/api/v1/benchmark").json()
        sensor = next(s for s in info["sensors"] if s["id"] == sid)
        assert "secret" not in json.dumps(info)  # never the address
        model = next(m for m in sensor["models"] if m["current"])
        assert model["installed"] and model["role"] == "backbones"

        started = client.post(
            "/api/v1/benchmark", json={"sensor_id": sid, "models": [{"role": model["role"], "id": model["id"]}]}
        )
        assert started.status_code == 202
        assert (
            client.post(
                "/api/v1/benchmark", json={"sensor_id": sid, "models": [{"role": model["role"], "id": model["id"]}]}
            ).status_code
            == 409
        )
        assert rt.benchmark_paused and client.get("/api/v1/status").json()["benchmark_running"]
        assert client.get(f"/api/v1/sensors/{sid}").json()["status"] == "speed_test"  # not "needs training"
        run = _finished(client)
        assert run["state"] == "finished"
        [result] = run["results"]
        assert result["status"] == "done" and result["current"] and result["rounds"] == 2
        assert result["total"]["median"] >= result["ai"]["median"] > 0
        assert result["picture"] == "320×240"
        assert "secret" not in json.dumps(run)
        report = client.get("/api/v1/benchmark/report")
        assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
        assert f"\n{model['id']} | " in report.text
        assert "secret" not in report.text and "Door" not in report.text  # no addresses, no names

        # The camera was asked for the warm-up too; the sensor checks again, with its model back.
        assert wait_for(lambda: not rt.benchmark_paused and rt.embedder is not None, timeout=30)
        last = rt.live_state(sid).last_run
        assert wait_for(lambda: rt.live_state(sid).last_run > last, timeout=15)


@requires_reader
def test_no_check_during_the_test(settings):
    camera = Camera()
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        sid = _state_sensor(client)
        assert wait_for(lambda: rt.embedder is not None and rt.live_state(sid).last_run, timeout=30)
        rt.benchmark_paused = True
        assert wait_for(lambda: rt._steps_running == 0, timeout=15)
        before, last = camera.grabs, rt.live_state(sid).last_run
        rt.wake(sid, force=True)
        time.sleep(1.5)
        assert camera.grabs == before and rt.live_state(sid).last_run == last
        rt.schedule_retrain(sid)
        assert wait_for(lambda: sid in rt._retrain_after_benchmark, timeout=5)  # on the app's loop
        rt.benchmark_paused = False
        rt._retrain_after_benchmark.clear()
        rt.training.discard(sid)


@requires_reader
def test_a_camera_that_fails_is_shown_and_the_sensors_go_on(settings):
    camera = Camera()
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        sid = _state_sensor(client)
        assert wait_for(lambda: rt.embedder is not None, timeout=30)
        camera.error = SourceError("offline")
        model = next(m for m in client.get("/api/v1/benchmark").json()["sensors"][0]["models"] if m["current"])
        client.post("/api/v1/benchmark", json={"sensor_id": sid, "models": [{"role": "backbones", "id": model["id"]}]})
        run = _finished(client)
        assert run["state"] == "finished" and run["results"][0]["status"] == "failed"
        assert "offline" in run["results"][0]["error"]
        assert wait_for(lambda: not rt.benchmark_paused and rt.embedder is not None, timeout=30)


@requires_reader
def test_stopping_the_test_lets_the_sensors_go_on(settings, monkeypatch):
    camera = Camera()
    monkeypatch.setitem(BENCHMARK, "rounds", 100_000)
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        sid = _state_sensor(client)
        assert wait_for(lambda: rt.embedder is not None, timeout=30)
        model = next(m for m in client.get("/api/v1/benchmark").json()["sensors"][0]["models"] if m["current"])
        client.post("/api/v1/benchmark", json={"sensor_id": sid, "models": [{"role": "backbones", "id": model["id"]}]})
        assert wait_for(lambda: camera.grabs > 3, timeout=30)
        client.post("/api/v1/benchmark/cancel")
        assert _finished(client)["state"] == "cancelled"
        assert wait_for(lambda: not rt.benchmark_paused and rt.embedder is not None, timeout=30)


def test_stopped_during_a_download_nothing_comes_back_afterwards(settings, monkeypatch):
    """The download runs on a thread: it must have ended before the file is deleted."""
    camera = Camera()
    spec = backbones.BACKBONES["dinov2-base"]
    started = threading.Event()

    def slow_download(file, folder, progress):
        started.set()
        for done in range(200):
            time.sleep(0.02)
            progress(done)
        (folder / file.filename).write_bytes(b"finished")

    monkeypatch.setattr(backbones, "download", slow_download)
    monkeypatch.setattr(backbones, "locate", lambda s, dirs: None if s is spec else original(s, dirs))
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        sid = _state_sensor(client)
        settings.models_dir.mkdir(parents=True, exist_ok=True)
        pick = {"role": "backbones", "id": spec.id}
        assert client.post("/api/v1/benchmark", json={"sensor_id": sid, "models": [pick]}).status_code == 202
        assert started.wait(10)
        client.post("/api/v1/benchmark/cancel")
        run = _finished(client)
        assert run["state"] == "cancelled" and spec.filename in run["deleted"]
        time.sleep(0.5)
        assert not (settings.models_dir / spec.filename).exists()


def test_what_cannot_be_tested_is_refused(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = Camera()
        sid = _state_sensor(client)
        pick = {"role": "detectors", "id": "dfine-n-coco"}
        assert client.post("/api/v1/benchmark", json={"sensor_id": sid, "models": [pick]}).status_code == 400
        assert client.get("/api/v1/benchmark/report").status_code == 404  # nothing tested yet
        pick = {"role": "backbones", "id": "no-such-model"}
        assert client.post("/api/v1/benchmark", json={"sensor_id": sid, "models": [pick]}).status_code == 400
        pick = {"role": "backbones", "id": backbones.DEFAULT_BACKBONE}
        assert client.post("/api/v1/benchmark", json={"sensor_id": 999, "models": [pick]}).status_code == 404
        assert client.post("/api/v1/benchmark", json={"sensor_id": sid, "models": []}).status_code == 422


def test_what_was_downloaded_for_the_test_is_deleted_only_when_noted(settings):
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = Camera()
        folder = settings.models_dir
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "mine.onnx").write_bytes(b"chosen by the user")
        (folder / "test.onnx").write_bytes(b"only for the test")
        rt._benchmark_note_download("test.onnx")
        assert rt.benchmark_delete_downloads(keep=True) == []
        assert (folder / "test.onnx").exists()  # kept, and no longer noted
        assert rt.benchmark_delete_downloads() == []
        rt._benchmark_note_download("test.onnx")
        rt._benchmark_note_download("../../outside.onnx")
        (settings.models_dir.parent / "outside.onnx").write_bytes(b"not ours")
        assert rt.benchmark_delete_downloads() == ["test.onnx", "outside.onnx"]
        assert not (folder / "test.onnx").exists() and (folder / "mine.onnx").exists()
        assert (settings.models_dir.parent / "outside.onnx").exists()  # never out of the folder
