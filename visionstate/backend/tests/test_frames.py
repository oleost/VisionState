"""Frames for the UI: the frame a check analysed, or the latest one when it is gone already."""

from fastapi.testclient import TestClient

from visionstate.main import create_app
from visionstate.settings import Settings


def test_analysed_frame_falls_back_to_the_latest(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=tmp_path
    )
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        body = {
            "name": "Door",
            "source_type": "http",
            "source": "x",
            "states": [{"name": "A"}, {"name": "B"}],
            "enabled": False,
        }
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        assert (
            client.get(f"/api/v1/sensors/{sid}/frame", params={"frame_id": "gone"}).status_code == 404
        )  # nothing cached
        live = rt.live_state(sid)
        live.frames.append(("old", b"\xff\xd8old"))
        live.frames.append(("new", b"\xff\xd8new"))
        exact = client.get(f"/api/v1/sensors/{sid}/frame", params={"frame_id": "old"})
        assert exact.headers["x-frame-id"] == "old" and exact.content == b"\xff\xd8old"
        # A frame no longer cached (frequent checks): the latest one, saying which it is — not a 404.
        latest = client.get(f"/api/v1/sensors/{sid}/frame", params={"frame_id": "evicted"})
        assert latest.status_code == 200 and latest.headers["x-frame-id"] == "new"
        assert latest.headers["cache-control"] == "no-store"
