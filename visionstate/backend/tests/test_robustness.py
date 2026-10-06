"""Hardening: source addresses, size limits, log redaction and loops that survive unexpected errors."""

import asyncio
import io
import json
import logging
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate import imaging
from visionstate import settings as vs_settings
from visionstate.engine import Runtime
from visionstate.main import create_app
from visionstate.mqtt import MqttBridge
from visionstate.redact import RedactingFormatter
from visionstate.settings import Settings
from visionstate.sources import FrameGrabber, HomeAssistant, SourceError, check_source


def make_settings(tmp_path, model_dir=None) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=tmp_path / "frontend",
        bundled_models_dir=model_dir or tmp_path / "models",
    )


def jpeg(size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 90, 90)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("source_type", "source"),
    [
        ("rtsp", "rtsp://user:pw@10.0.0.5:554/s1"),
        ("rtsp", "rtsps://cam/stream"),
        ("rtsp", "http://cam/video.mjpg"),
        ("http", "http://cam/snapshot.jpg"),
        ("http", "HTTPS://cam/snapshot.jpg"),
        ("ha_camera", "camera.garage_door"),
    ],
)
def test_valid_sources(source_type, source):
    check_source(source_type, source)


@pytest.mark.parametrize(
    ("source_type", "source"),
    [
        ("rtsp", "/data/options.json"),  # FFmpeg would open a local file
        ("rtsp", "file:///data/options.json"),
        ("rtsp", "concat:/a.mp4|/b.mp4"),
        ("http", "ftp://cam/snapshot.jpg"),
        ("http", "file:///etc/passwd"),
        ("ha_camera", "camera.x/../../states"),
        ("ha_camera", "Camera.Garage"),
        ("file", "x"),
    ],
)
def test_invalid_sources(source_type, source):
    with pytest.raises(SourceError):
        check_source(source_type, source)


def grabber_with(tmp_path, handler) -> FrameGrabber:
    grabber = FrameGrabber(HomeAssistant(make_settings(tmp_path)))
    grabber._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return grabber


def test_snapshot_size_limit(tmp_path, monkeypatch):
    monkeypatch.setitem(vs_settings.RUNTIME, "max_frame_mb", 0.001)  # 1 kB
    small = grabber_with(tmp_path, lambda request: httpx.Response(200, content=b"x" * 500))
    assert asyncio.run(small.grab("http", "http://cam/snap.jpg")) == b"x" * 500
    big = grabber_with(tmp_path, lambda request: httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(SourceError, match="larger than"):
        asyncio.run(big.grab("http", "http://cam/snap.jpg"))
    missing = grabber_with(tmp_path, lambda request: httpx.Response(404))
    with pytest.raises(SourceError, match="HTTP 404"):
        asyncio.run(missing.grab("http", "http://cam/snap.jpg"))


def test_image_pixel_limit(monkeypatch):
    data = jpeg((200, 100))
    assert imaging.decode(data).size == (200, 100)
    monkeypatch.setitem(vs_settings.RUNTIME, "max_image_megapixels", 0.01)  # 10,000 pixels
    with pytest.raises(imaging.ImageTooLarge):
        imaging.decode(data)
    with pytest.raises(OSError):  # handled wherever an unreadable image is
        imaging.decode(data)


def test_ha_client_without_api_raises_a_clear_error(tmp_path):
    ha = HomeAssistant(make_settings(tmp_path))
    assert not ha.enabled
    for call in (ha.state("light.x"), ha.switch("light.x", True)):
        with pytest.raises(SourceError, match="not configured"):
            asyncio.run(call)


def test_log_lines_are_redacted(caplog):
    formatter = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError("cannot open rtsp://admin:secret@10.0.0.5/s1")
    except RuntimeError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "Grab of %s failed", ("rtsp://u:pw@cam",), True)
        import sys

        record.exc_info = sys.exc_info()
    text = formatter.format(record)
    assert "secret" not in text and "pw@" not in text
    assert "rtsp://***@cam" in text and "Traceback" in text


def test_mqtt_dispatch_survives_a_bad_payload(tmp_path):
    seen = []

    async def on_command(slug, command, payload):
        seen.append(payload)

    async def on_connect():
        pass

    bridge = MqttBridge(make_settings(tmp_path), on_command, on_connect)
    asyncio.run(bridge._dispatch("visionstate/door/classify/set", b"\xff\xfe"))
    assert seen == ["��"]


def test_sensor_loop_survives_an_unexpected_error(monkeypatch, caplog):
    """A failing pass (e.g. the database is busy) is logged once and tried again, not the end of the sensor."""
    monkeypatch.setitem(vs_settings.RUNTIME, "loop_retry_s", 0.01)
    rt = Runtime.__new__(Runtime)
    passes = []

    async def step(sensor_id):
        passes.append(sensor_id)
        if len(passes) < 4:
            raise RuntimeError("database is locked")
        return False  # the sensor was deleted: the loop ends

    async def run():
        rt._wake = {1: asyncio.Event()}
        rt._sensor_step = step
        await asyncio.wait_for(rt._sensor_loop(1), timeout=5)

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    assert len(passes) == 4
    assert sum("check loop failed" in r.message for r in caplog.records) == 1


@pytest.fixture
def client(tmp_path, model_dir):
    with TestClient(create_app(make_settings(tmp_path, model_dir))) as c:
        yield c


SENSOR = {
    "name": "Door",
    "source_type": "http",
    "source": "http://cam/snap.jpg",
    "states": [{"key": "open", "name": "Open"}, {"key": "closed", "name": "Closed"}],
    "enabled": False,
}


def bundle(manifest, files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", manifest if isinstance(manifest, str) else json.dumps(manifest))
        for name, data in (files or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def test_import_skips_broken_samples(client):
    samples = [
        {"file": "good.jpg", "labels": ["open"]},
        {"file": "broken.jpg", "labels": ["closed"]},  # not an image
        {"file": "missing.jpg", "labels": ["open"]},  # not in the archive
        "not a sample",
    ]
    data = bundle(
        {"schema": 1, "sensor": SENSOR, "samples": samples},
        {"samples/good.jpg": jpeg(), "samples/broken.jpg": b"no image"},
    )
    resp = client.post("/api/v1/import", files={"file": ("b.zip", data)})
    assert resp.status_code == 201, resp.text
    assert resp.json()["skipped"] == 3
    sid = resp.json()["id"]
    assert client.get(f"/api/v1/sensors/{sid}").status_code == 200
    rt = client.app.state.runtime
    assert sid in rt._tasks  # started like any new sensor


@pytest.mark.parametrize(
    "manifest",
    [
        "not json",
        "[1, 2]",
        {"schema": 1},  # no sensor
        {"schema": "1", "sensor": SENSOR},
        {"schema": 99, "sensor": SENSOR},
    ],
)
def test_import_rejects_bad_manifests(client, manifest):
    resp = client.post("/api/v1/import", files={"file": ("b.zip", bundle(manifest))})
    assert resp.status_code == 400, resp.text
    assert client.get("/api/v1/sensors").json() == []


def test_import_rejects_a_huge_manifest(client, monkeypatch):
    monkeypatch.setitem(vs_settings.UPLOAD_LIMITS, "max_manifest_mb", 0.001)  # 1 kB
    manifest = {"schema": 1, "sensor": SENSOR, "samples": [{"file": f"{i}.jpg"} for i in range(200)]}
    resp = client.post("/api/v1/import", files={"file": ("b.zip", bundle(manifest))})
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"]


def test_import_rejects_a_file_that_is_no_zip(client):
    resp = client.post("/api/v1/import", files={"file": ("b.zip", b"not a zip")})
    assert resp.status_code == 400
