"""History limits: removal by age and by total size (oldest first, review queue last), and the API;
files written whole and removed with their rows; frames no row lists."""

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
        # The warning stays while the limit applies, also after a clean-up with nothing to remove.
        rt.cleanup_history()
        assert client.get("/api/v1/storage").json()["limited_by_size"] is True

        # 0 = no size limit.
        add_frames(rt, sid, [(0, True)] * 3)
        unlimited = client.put("/api/v1/storage", json={"history_days": 30, "history_max_gb": 0}).json()
        assert unlimited["history_frames"] == 4 and unlimited["limited_by_size"] is False


@requires_model
def test_age_limit_and_usage(settings):
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sid = client.post(
            "/api/v1/sensors", json={"name": "Door", "source_type": "http", "source": "x", "states": STATES}
        ).json()["id"]
        usage = client.get("/api/v1/storage").json()
        assert usage["history_days"] == STORAGE_DEFAULTS["history_days"]
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


# --- training images: kept in the app's storage, moved there from the media folder ------------


@pytest.fixture
def runtime(settings):
    from visionstate.db import Database
    from visionstate.engine import Runtime

    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init()
    yield Runtime(settings, db)
    db.engine.dispose()


def state_sensor(rt) -> int:
    from visionstate.db import Sensor, State

    with rt.db.session() as s:
        row = Sensor(slug="door", name="Door", source_type="http", source="x", interval_s=5, threshold=0.6,
                     debounce=1, states=[State(key="open", name="Open", color="#fff"),
                                         State(key="closed", name="Closed", color="#000")])  # fmt: skip
        s.add(row)
        s.flush()
        return row.id


def old_sample(rt, sensor_id: int, seed: int) -> tuple[int, str]:
    """A training image where versions before the move kept it (the media folder)."""
    from visionstate.db import Sample

    filename = rt.storage.new_filename()
    path = rt.storage.old_sample_path(sensor_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    noise(seed).save(path, format="JPEG")
    with rt.db.session() as s:
        row = Sample(sensor_id=sensor_id, filename=filename, origin="upload")
        s.add(row)
        s.flush()
        return row.id, filename


def test_training_images_are_saved_in_the_apps_storage(runtime):
    sid = state_sensor(runtime)
    runtime.add_sample(sid, noise(1), "upload", None)
    assert len(list((runtime.settings.data_dir / "samples" / str(sid)).iterdir())) == 1
    assert not runtime.settings.old_samples_dir.exists()  # a new installation never makes the old folder


def test_training_images_move_from_the_media_folder(runtime):
    from visionstate.settings import SAMPLES_MOVE

    rt = runtime
    sid = state_sensor(rt)
    (_, first), (second_id, second) = old_sample(rt, sid, 1), old_sample(rt, sid, 2)
    stranger = rt.settings.old_samples_dir / str(sid) / "not-ours.jpg"  # no row lists it
    noise(3).save(stranger, format="JPEG")
    old_thumb = rt.settings.thumbs_dir / "sample" / "1.jpg"
    old_thumb.parent.mkdir(parents=True)
    noise(4).save(old_thumb, format="JPEG")
    # Before the move they are read where they are.
    assert rt.storage.sample_path(sid, first) == rt.storage.old_sample_path(sid, first)

    rt.move_samples()
    for name in (first, second):
        new = rt.storage.new_sample_path(sid, name)
        assert rt.storage.sample_path(sid, name) == new
        assert new.read_bytes() == rt.storage.old_sample_path(sid, name).read_bytes()  # the old copy stays
    assert not list(rt.storage.new_sample_path(sid, "x").parent.glob("*.part"))
    assert not (rt.settings.data_dir / "samples" / str(sid) / "not-ours.jpg").exists()
    moved_at = rt.db.get_setting("samples_moved_at")
    assert moved_at

    # A deleted image goes from both places; a later run copies nothing back.
    rt.storage.delete_sample(sid, second_id, second)
    assert not rt.storage.old_sample_path(sid, second).exists()
    rt.move_samples()
    assert not rt.storage.new_sample_path(sid, second).exists()
    assert rt.db.get_setting("samples_moved_at") == moved_at

    # After the days the old copies are kept, they go; what no sensor knows stays.
    long_ago = utcnow() - timedelta(days=SAMPLES_MOVE["keep_old_days"] + 1)
    rt.db.set_setting("samples_moved_at", long_ago.isoformat())
    rt.move_samples()
    assert not rt.storage.old_sample_path(sid, first).exists()
    assert rt.storage.sample_path(sid, first).exists() and stranger.exists()
    assert not old_thumb.exists()
    stranger.unlink()
    rt.move_samples()
    assert not rt.settings.old_samples_dir.exists()  # empty folders are removed


def test_a_full_disk_leaves_the_images_where_they_are(runtime, monkeypatch):
    import shutil

    rt = runtime
    sid = state_sensor(rt)
    _, name = old_sample(rt, sid, 1)

    def full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copy2", full)
    rt.move_samples()
    assert rt.storage.sample_path(sid, name) == rt.storage.old_sample_path(sid, name)
    assert rt.db.get_setting("samples_moved_at") is None  # tried again later
    monkeypatch.undo()
    rt.move_samples()
    assert rt.storage.sample_path(sid, name) == rt.storage.new_sample_path(sid, name)


def test_training_leaves_out_images_that_are_gone(runtime):
    """A state sensor trains on the images it still has (a backup restored without them)."""
    from visionstate.db import Sample

    class FakeEmbedder:
        class spec:  # noqa: N801 - mimics BackboneSpec
            id = "fake"

        def embed(self, images):
            return np.ones((len(images), 4), dtype=np.float32)

    rt = runtime
    rt.embedder = FakeEmbedder()
    sid = state_sensor(rt)
    ids = [rt.add_sample(sid, noise(i), "upload", None) for i in range(3)]
    with rt.db.session() as s:
        samples = [s.get(Sample, i) for i in ids]
    rt.storage.sample_path(sid, samples[1].filename).unlink()
    rt.storage.sample_path(sid, samples[2].filename).write_bytes(b"broken")
    kept, vectors = rt.vectors_for(rt.load_sensor(sid), samples)
    assert [x.id for x in kept] == [ids[0]] and vectors.shape == (1, 4)


def test_a_file_is_removed_only_when_its_row_is(runtime):
    from visionstate.db import Sample

    rt = runtime
    sid = state_sensor(rt)
    sample_id = rt.add_sample(sid, noise(1), "upload", None)
    with rt.db.session() as s:
        filename = s.get(Sample, sample_id).filename
    path = rt.storage.sample_path(sid, filename)
    with pytest.raises(RuntimeError), rt.db.session() as s:
        sample = s.get(Sample, sample_id)
        rt.db.after_commit(s, lambda: rt.storage.delete_sample(sid, sample_id, filename))
        s.delete(sample)
        raise RuntimeError("the commit never happens")
    assert path.exists()
    with rt.db.session() as s:
        rt.db.after_commit(s, lambda: rt.storage.delete_sample(sid, sample_id, filename))
        s.delete(s.get(Sample, sample_id))
    assert not path.exists()


def test_a_media_folder_inside_the_apps_storage_keeps_its_images(tmp_path):
    """VISIONSTATE_MEDIA set to the data folder: old and new place are the same files."""
    from visionstate.db import Database
    from visionstate.engine import Runtime
    from visionstate.settings import SAMPLES_MOVE, Settings

    same = Settings(data_dir=tmp_path, media_dir=tmp_path, frontend_dir=tmp_path / "x", bundled_models_dir=MODEL_DIR)
    same.ensure_dirs()
    db = Database(same.db_path)
    db.init()
    rt = Runtime(same, db)
    sid = state_sensor(rt)
    rt.add_sample(sid, noise(1), "upload", None)
    rt.db.set_setting("samples_moved_at", (utcnow() - timedelta(days=SAMPLES_MOVE["keep_old_days"] + 1)).isoformat())
    rt.move_samples()
    assert len(list((tmp_path / "samples" / str(sid)).iterdir())) == 1
    db.engine.dispose()


def test_an_image_deleted_while_it_is_moved_is_not_brought_back(runtime, monkeypatch):
    import shutil

    rt = runtime
    sid = state_sensor(rt)
    (_, gone), (_, raced), (_, kept) = old_sample(rt, sid, 1), old_sample(rt, sid, 2), old_sample(rt, sid, 3)
    copy = shutil.copy2

    def deleted_meanwhile(src, dst):
        name = src.name
        if name == gone:  # removed before the copy starts
            src.unlink()
            raise FileNotFoundError(src)
        copy(src, dst)
        if name == raced:  # removed (Storage.delete_sample) while it was copied
            src.unlink()

    monkeypatch.setattr(shutil, "copy2", deleted_meanwhile)
    rt.move_samples()
    assert not rt.storage.new_sample_path(sid, gone).exists()
    assert not rt.storage.new_sample_path(sid, raced).exists()
    assert rt.storage.new_sample_path(sid, kept).exists()  # one gone does not stop the others
    assert rt.db.get_setting("samples_moved_at")


def test_a_file_that_cannot_be_removed_does_not_fail_the_request(runtime, caplog):
    from visionstate.db import Sample

    rt = runtime
    sid = state_sensor(rt)
    sample_id = rt.add_sample(sid, noise(1), "upload", None)

    def locked():
        raise PermissionError("in use")

    with rt.db.session() as s:
        rt.db.after_commit(s, locked)
        s.delete(s.get(Sample, sample_id))
    with rt.db.session() as s:
        assert s.get(Sample, sample_id) is None
    assert "Could not remove a file" in caplog.text


def test_history_files_go_only_when_the_rows_are_committed(runtime, monkeypatch):
    """The clean-up removes a frame together with its row: a commit that fails keeps both."""
    rt = runtime
    sid = state_sensor(rt)
    (row_id,) = add_frames(rt, sid, [(400, True)])
    with rt.db.session() as s:
        path = rt.storage.history_path(sid, s.get(Prediction, row_id).frame)

    real_session = rt.db.session

    def failing_session():
        from contextlib import contextmanager

        @contextmanager
        def session():
            with real_session() as s:
                yield s
                s.flush()
                raise OSError("database is locked")

        return session()

    monkeypatch.setattr(rt.db, "session", failing_session)
    with pytest.raises(OSError):
        rt._remove_history([(row_id, sid, path.name)])
    monkeypatch.undo()
    assert path.exists() and remaining(rt) == {row_id}
    rt.cleanup_history()
    assert not path.exists() and remaining(rt) == set()


def test_frames_no_row_lists_are_removed_once_they_are_old(runtime):
    import os
    import time

    from visionstate.settings import HISTORY_CLEANUP

    rt = runtime
    sid = state_sensor(rt)
    (row_id,) = add_frames(rt, sid, [(0, True)])
    with rt.db.session() as s:
        kept = rt.storage.history_path(sid, s.get(Prediction, row_id).frame)
    kept_thumb = rt.storage.thumbnail("history", row_id, kept)
    old_orphan = rt.storage.history_path(sid, rt.storage.save_history(sid, noise(2)))
    young_orphan = rt.storage.history_path(sid, rt.storage.save_history(sid, noise(3)))
    old_thumb = rt.storage.thumb_path("history", row_id + 100)
    old_thumb.write_bytes(kept_thumb.read_bytes())
    half = old_orphan.with_name("x.jpg.part")  # a write cut off by a power cut
    half.write_bytes(b"half")
    other = rt.settings.history_dir / "notes.txt"  # not ours: left alone
    other.write_text("x")
    long_ago = time.time() - HISTORY_CLEANUP["orphan_age_s"] - 60
    for path in (kept, kept_thumb, old_orphan, old_thumb, half, other):
        os.utime(path, (long_ago, long_ago))

    assert rt.remove_orphan_frames() == 3
    assert kept.exists() and kept_thumb.exists() and young_orphan.exists() and other.exists()
    assert not old_orphan.exists() and not old_thumb.exists() and not half.exists()


def test_image_files_are_written_whole_or_not_at_all(runtime, monkeypatch):
    from visionstate import imaging

    rt = runtime
    sid = state_sensor(rt)
    name = rt.storage.save_history(sid, noise(1))
    folder = rt.storage.history_path(sid, name).parent
    assert [p.name for p in folder.iterdir()] == [name]

    def cut_off(image, quality=90):  # noqa: ANN001
        raise OSError("No space left on device")

    monkeypatch.setattr(imaging, "encode_jpeg", cut_off)
    with pytest.raises(OSError):
        rt.storage.save_history(sid, noise(2))
    assert [p.name for p in folder.iterdir()] == [name]  # no half picture, no leftover


def test_the_review_queue_is_counted_from_an_index(runtime):
    """The count after every check that flags a frame reads the partial index, not the history."""
    from sqlalchemy import func, select, text

    rt = runtime
    query = select(Prediction.sensor_id, func.count(Prediction.id)).where(Prediction.reviewed.is_(False))
    sql = str(query.group_by(Prediction.sensor_id).compile(rt.db.engine, compile_kwargs={"literal_binds": True}))
    with rt.db.engine.connect() as conn:
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL (with WAL)
        plan = " ".join(str(row[-1]) for row in conn.execute(text("EXPLAIN QUERY PLAN " + sql)))
    assert "ix_prediction_waiting" in plan, plan


def test_an_existing_database_gets_the_index_at_start(settings):
    """Indexes added later are made when the app starts on an older database (Database.init)."""
    from sqlalchemy import text

    from visionstate.db import Database

    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init()
    with db.engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_prediction_waiting"))  # as a database of an older version
    db.init()
    with db.engine.connect() as conn:
        names = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'"))}
    db.engine.dispose()
    assert "ix_prediction_waiting" in names


def test_frames_are_decoded_small_where_that_is_enough(tmp_path):
    """Change detection and thumbnails decode a JPEG at 1/2, 1/4 or 1/8 of its size while decoding:
    the signature stays the same (to far below any threshold), the thumbnail its size."""
    import io

    from visionstate import imaging
    from visionstate.settings import CHANGE_SIGNATURE_DECODE, CHANGE_SIGNATURE_SIZE, THUMB_SIZE

    from .conftest import ASSETS

    photo = Image.open(ASSETS / "beach.jpg").convert("RGB").resize((3840, 2160))
    buf = io.BytesIO()
    photo.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    for roi in (None, {"x": 0.6, "y": 0.5, "w": 0.1, "h": 0.2}):
        small = imaging.decode_for_signature(data, roi)
        assert small.width < 3840  # decoded smaller …
        region = imaging.crop(small, roi)
        assert min(region.size) >= CHANGE_SIGNATURE_SIZE * CHANGE_SIGNATURE_DECODE  # … but large enough
        whole = imaging.region_signature(imaging.decode(data), roi)
        assert imaging.change_score(whole, imaging.region_signature(small, roi)) < 0.005
    path = tmp_path / "frame.jpg"
    path.write_bytes(data)
    assert imaging.load(path, at_least=(THUMB_SIZE, THUMB_SIZE)).width < 3840
    assert imaging.thumbnail(imaging.load(path, at_least=(THUMB_SIZE, THUMB_SIZE))).size == (THUMB_SIZE, 180)
    png = io.BytesIO()
    photo.resize((640, 360)).save(png, format="PNG")
    assert imaging.decode(png.getvalue(), at_least=(10, 10)).size == (640, 360)  # not a JPEG: whole
