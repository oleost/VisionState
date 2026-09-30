"""Security and privacy behaviour: credential redaction, import validation, limits, static files."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from visionstate import settings as vs_settings
from visionstate.main import create_app
from visionstate.redact import has_credentials, redact
from visionstate.settings import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("rtsp://admin:secret@10.0.0.5:554/s1", "rtsp://***@10.0.0.5:554/s1"),
        (
            "http://cam/cgi-bin/api.cgi?cmd=Snap&user=admin&password=secret",
            "http://cam/cgi-bin/api.cgi?cmd=Snap&user=***&password=***",
        ),
        ("Connection to rtsp://u:p@cam failed", "Connection to rtsp://***@cam failed"),
        ("http://cam/snapshot.jpg", "http://cam/snapshot.jpg"),
    ],
)
def test_redact(raw, expected):
    assert redact(raw) == expected
    assert has_credentials(raw) == (raw != expected)


@pytest.fixture
def client(tmp_path, model_dir):
    frontend = tmp_path / "frontend"
    (frontend / "assets").mkdir(parents=True)
    (frontend / "index.html").write_text("<html>app</html>")
    (tmp_path / "secret.txt").write_text("top secret")
    settings = Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=frontend,
        bundled_models_dir=model_dir,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def sensor_body(**overrides):
    body = {
        "name": "Cam",
        "source_type": "rtsp",
        "source": "rtsp://admin:secret@10.0.0.5/s1",
        "states": [{"name": "Open"}, {"name": "Closed"}],
        "enabled": False,
    }
    body.update(overrides)
    return body


def bundle(sensor: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"schema": 1, "sensor": sensor, "samples": []}))
    return buf.getvalue()


def test_export_strips_camera_credentials(client):
    sid = client.post("/api/v1/sensors", json=sensor_body()).json()["id"]
    exported = client.get(f"/api/v1/sensors/{sid}/export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
    assert "secret" not in json.dumps(manifest)
    assert manifest["sensor"]["source_redacted"] is True


def test_import_is_validated_like_the_api(client):
    ok = sensor_body(interval_s=30, states=[{"key": "open", "name": "Open"}, {"key": "closed", "name": "Closed"}])
    assert client.post("/api/v1/import", files={"file": ("b.zip", bundle(ok))}).status_code == 201
    for bad in (
        sensor_body(interval_s=0),  # would hammer the camera
        sensor_body(source_type="file"),
        sensor_body(states=[{"name": "Only one"}]),
        sensor_body(triggers={"entities": ["not valid"]}),
    ):
        resp = client.post("/api/v1/import", files={"file": ("b.zip", bundle(bad))})
        assert resp.status_code == 400, bad


def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setitem(vs_settings.UPLOAD_LIMITS, "max_file_mb", 0.001)  # 1 kB
    sid = client.post("/api/v1/sensors", json=sensor_body()).json()["id"]
    resp = client.post(f"/api/v1/sensors/{sid}/uploads", files=[("files", ("big.jpg", b"x" * 5000, "image/jpeg"))])
    assert resp.json()["created"] == 0
    assert "larger than" in resp.json()["errors"][0]


def test_static_files_cannot_escape_frontend_dir(client):
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2Fsecret.txt"):
        resp = client.get(path)
        assert "top secret" not in resp.text


def test_review_rules_global_and_per_sensor(client):
    assert client.get("/api/v1/review-rules").json()["spot_rate"] == 0.0
    rules = client.put("/api/v1/review-rules", json={"below": 0.8, "cooldown_s": 600}).json()
    assert rules["below"] == 0.8 and rules["cooldown_s"] == 600
    sid = client.post("/api/v1/sensors", json=sensor_body(review={"below": 0.6})).json()["id"]
    sensor = client.get(f"/api/v1/sensors/{sid}").json()
    assert sensor["review"]["below"] == 0.6 and sensor["review"]["cooldown_s"] is None
    assert sensor["review_effective"]["below"] == 0.6
    assert sensor["review_effective"]["cooldown_s"] == 600  # inherited from the global rules
    cleared = client.patch(f"/api/v1/sensors/{sid}", json={"review": {}}).json()
    assert cleared["review_effective"]["below"] == 0.8
    assert client.put("/api/v1/review-rules", json={"below": 2}).status_code == 422


def test_polygon_region_round_trip(client):
    points = [[0.1, 0.2], [0.8, 0.25], [0.7, 0.9], [0.2, 0.8], [0.05, 0.5]]
    body = sensor_body(roi={"x": 0, "y": 0, "w": 1, "h": 1, "points": points})
    sensor = client.post("/api/v1/sensors", json=body).json()
    assert sensor["roi"]["points"] == points
    assert sensor["roi"]["x"] == 0.05 and sensor["roi"]["h"] == 0.7  # bounding box recomputed
    too_many = [[i / 40, (i % 2) / 2] for i in range(40)]
    resp = client.patch(
        f"/api/v1/sensors/{sensor['id']}", json={"roi": {"x": 0, "y": 0, "w": 1, "h": 1, "points": too_many}}
    )
    assert resp.status_code == 422
