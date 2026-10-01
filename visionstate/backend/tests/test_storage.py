"""History limits: removal by age and by total size (oldest first, review queue last), and the API."""

from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate.db import Prediction, utcnow
from visionstate.main import create_app
from visionstate.settings import STORAGE_DEFAULTS

from .conftest import MODEL_DIR, requires_model

STATES = [{"name": "Open"}, {"name": "Closed"}]


@pytest.fixture
def settings(tmp_path):
    from visionstate.settings import Settings

    return Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=tmp_path / "none",
        bundled_models_dir=MODEL_DIR,
    )


def noise(seed: int) -> Image.Image:
    """An image that compresses badly, so every frame has a similar, real size."""
    return Image.fromarray(np.random.default_rng(seed).integers(0, 255, (120, 160, 3), dtype=np.uint8))


def add_frames(rt, sensor_id: int, frames: list[tuple[float, bool]]) -> list[int]:
    """History rows with frames: (age in days, reviewed) each. Returns their ids, oldest first."""
    ids = []
    with rt.db.session() as s:
        for i, (age_days, reviewed) in enumerate(frames):
            row = Prediction(
                sensor_id=sensor_id,
                created_at=utcnow() - timedelta(days=age_days),
                state_key="open",
                published_key="open",
                confidence=0.5,
                probs={},
                frame=rt.storage.save_history(sensor_id, noise(i)),
                is_change=True,
                review_reason=None if reviewed else "low_confidence",
                reviewed=reviewed,
            )
            s.add(row)
            s.flush()
            ids.append(row.id)
    return ids


def remaining(rt) -> set[int]:
    with rt.db.session() as s:
        return {row.id for row in s.query(Prediction).all()}


@requires_model
def test_size_limit_removes_oldest_reviewed_first_then_waiting(settings):
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sid = client.post(
            "/api/v1/sensors", json={"name": "Door", "source_type": "http", "source": "x", "states": STATES}
        ).json()["id"]
        # Two frames still waiting for review are the oldest; three reviewed ones are newer.
        waiting_old, waiting_new, *reviewed = add_frames(
            rt, sid, [(5, False), (4, False), (3, True), (2, True), (1, True)]
        )
        per_frame = rt.storage.history_bytes() / 5
        two_frames_gb = per_frame * 2.5 / 1024**3

        resp = client.put("/api/v1/storage", json={"history_days": 30, "history_max_gb": two_frames_gb})
        assert resp.status_code == 200, resp.text
        assert remaining(rt) == {waiting_old, waiting_new}  # reviewed frames went first, even though newer
        assert resp.json()["limited_by_size"] is True
        assert rt.storage.history_bytes() <= two_frames_gb * 1024**3

        # Still too much: now the oldest frame waiting for review goes.
        client.put("/api/v1/storage", json={"history_days": 30, "history_max_gb": per_frame * 1.5 / 1024**3})
        assert remaining(rt) == {waiting_new}

        # 0 = no size limit.
        add_frames(rt, sid, [(0, True)] * 3)
        assert (
            client.put("/api/v1/storage", json={"history_days": 30, "history_max_gb": 0}).json()["history_frames"] == 4
        )


@requires_model
def test_age_limit_and_usage(settings):
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sid = client.post(
            "/api/v1/sensors", json={"name": "Door", "source_type": "http", "source": "x", "states": STATES}
        ).json()["id"]
        usage = client.get("/api/v1/storage").json()
        # Days start from the app option, the size limit from the default.
        assert usage["history_days"] == settings.history_retention_days
        assert usage["history_max_gb"] == STORAGE_DEFAULTS["history_max_gb"]
        assert usage["free_bytes"] > 0 and usage["history_frames"] == 0

        old_reviewed, waiting_2d, waiting_3d, fresh = add_frames(
            rt, sid, [(2, True), (1.5, False), (3, False), (0, True)]
        )
        rt.add_sample(sid, noise(99), "upload", None)  # training images are never removed
        client.put("/api/v1/storage", json={"history_days": 1, "history_max_gb": 2})
        # Older than 1 day goes; frames waiting for review get twice as long (2 days).
        assert remaining(rt) == {waiting_2d, fresh}
        usage = client.get("/api/v1/storage").json()
        assert usage["history_frames"] == 2 and usage["training_images"] == 1 and usage["training_bytes"] > 0
        assert usage["limited_by_size"] is False
        assert usage["oldest_history"] is not None

        assert client.put("/api/v1/storage", json={"history_days": 0, "history_max_gb": 2}).status_code == 422
        assert client.put("/api/v1/storage", json={"history_days": 7, "history_max_gb": -1}).status_code == 422
