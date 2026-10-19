"""Backup of everything and restoring it (backup.py, api/backup.py)."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from visionstate import backup
from visionstate.db import SCHEMA_VERSION, Base, Database, Face, Prediction, Sample, Sensor
from visionstate.main import create_app
from visionstate.mqtt import MqttBridge
from visionstate.settings import BACKUP, BACKUP_LEFT_OUT, BACKUP_PARTS, Settings
from visionstate.storage import Storage

from .backup_fixture import BACKUPS, make, populate
from .conftest import MODEL_DIR


def make_settings(root: Path) -> Settings:
    return Settings(
        data_dir=root / "data", media_dir=root / "media", frontend_dir=root / "none", bundled_models_dir=MODEL_DIR
    )


@pytest.fixture
def removed(monkeypatch):
    """The sensors (slugs) and people (keys) whose entities a restore took away."""
    gone: dict[str, list[str]] = {"sensors": [], "people": [], "classes": []}

    async def remove_discovery(self, sensor):
        gone["sensors"].append(sensor.slug)

    async def remove_person(self, key):
        gone["people"].append(key)

    async def remove_object_class(self, slug, key, discovery=True):
        gone["classes"].append(f"{slug}/{key}")

    monkeypatch.setattr(MqttBridge, "remove_discovery", remove_discovery)
    monkeypatch.setattr(MqttBridge, "remove_object_class", remove_object_class)
    monkeypatch.setattr(MqttBridge, "remove_person", remove_person)
    return gone


def files_listed(settings: Settings) -> dict[str, int]:
    """Rows of each kind whose file is there (a restore that loses a file shows up here)."""
    storage = Storage(settings)
    db = Database(settings.db_path)
    try:
        with db.session() as s:
            samples = s.scalars(select(Sample)).all()
            faces = s.scalars(select(Face)).all()
            frames = s.scalars(select(Prediction).where(Prediction.frame.is_not(None))).all()
            return {
                "samples": sum(storage.sample_path(r.sensor_id, r.filename).exists() for r in samples),
                "samples_missing": sum(not storage.sample_path(r.sensor_id, r.filename).exists() for r in samples),
                "faces": sum(storage.face_path(r.filename).exists() for r in faces),
                "faces_missing": sum(not storage.face_path(r.filename).exists() for r in faces),
                "history": sum(storage.history_path(r.sensor_id, r.frame).exists() for r in frames if r.frame),
                "history_missing": sum(
                    not storage.history_path(r.sensor_id, r.frame).exists() for r in frames if r.frame
                ),
            }
    finally:
        db.engine.dispose()


# --- Nothing added later is missing from a backup -------------------------------------------


def test_every_table_belongs_to_one_part():
    """A new table in db.py must be put in a part of settings.BACKUP_PARTS (the database goes
    along whole; the part says when its rows are left out)."""
    listed = [table for part in BACKUP_PARTS.values() for table in part["tables"]]
    assert sorted(listed) == sorted(set(listed)), "a table is in two parts"
    assert set(listed) == set(Base.metadata.tables), (
        "every table of db.py belongs to exactly one part of settings.BACKUP_PARTS"
    )


def test_every_folder_belongs_to_one_part(tmp_path):
    """A new folder in Settings (under the data or media folder) must be in a part of
    settings.BACKUP_PARTS, or in BACKUP_LEFT_OUT when it is made again by itself."""
    settings = make_settings(tmp_path)
    roots = (settings.data_dir, settings.media_dir)
    names = [
        name
        for name in dir(Settings)
        if isinstance(getattr(Settings, name), property)
        and isinstance(path := getattr(settings, name), Path)
        and any(root in path.parents for root in roots)
        and name != "db_path"
    ]
    listed = [name for part in BACKUP_PARTS.values() for name in part["folders"]] + list(BACKUP_LEFT_OUT)
    assert sorted(listed) == sorted(set(listed)), "a folder is listed twice"
    assert set(names) == set(listed), "every folder of Settings is in settings.BACKUP_PARTS or BACKUP_LEFT_OUT"


def test_after_populating_every_file_is_in_a_known_folder(tmp_path):
    """Files written anywhere else in the data or media folder would not be in a backup."""
    settings = make_settings(tmp_path)
    populate(settings)
    known = [p.resolve() for p in backup.folders(settings).values()]
    db_files = {p.name for p in (settings.db_path, settings.db_path.with_suffix(".db-wal"))}
    for root in (settings.data_dir, settings.media_dir):
        for path in root.rglob("*"):
            if path.is_file() and path.name not in db_files and not path.name.endswith("-shm"):
                assert any(folder in path.resolve().parents for folder in known), path


# --- Export ---------------------------------------------------------------------------------


def test_snapshot_leaves_out_parts_and_masks_passwords(tmp_path):
    settings = make_settings(tmp_path)
    populate(settings)
    out = io.BytesIO()
    backup.write(settings, out, {"sensors", "samples"}, credentials=False)
    archive = zipfile.ZipFile(out)
    manifest = json.loads(archive.read("backup.json"))
    assert manifest["parts"] == ["samples", "sensors"]
    assert manifest["schema"] == SCHEMA_VERSION and manifest["format"] == BACKUP["format"]
    assert sorted(manifest["masked"]) == ["Garage door", "Water meter"]
    names = archive.namelist()
    assert any(n.startswith("files/samples_dir/") for n in names)
    assert any(n.startswith("files/heads_dir/") for n in names)  # the sensors' part: always
    assert not any(n.startswith(("files/history_dir/", "files/faces_dir/", "files/models_dir/")) for n in names)
    db_copy = tmp_path / "copy.db"
    db_copy.write_bytes(archive.read("visionstate.db"))
    con = sqlite3.connect(db_copy)
    try:
        assert con.execute("SELECT count(*) FROM prediction").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM person").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM sample").fetchone()[0] == 5
        sources = {r[0] for r in con.execute("SELECT source FROM sensor")}
        assert "rtsp://***@10.0.0.9/live" in sources and not any("secret" in s for s in sources)
    finally:
        con.close()
    # With the passwords when asked for.
    out = io.BytesIO()
    backup.write(settings, out, set(BACKUP_PARTS), credentials=True)
    archive = zipfile.ZipFile(out)
    assert json.loads(archive.read("backup.json"))["masked"] == []
    assert any(n.startswith("files/models_dir/") for n in archive.namelist())


def test_training_images_moved_already_are_not_in_twice(tmp_path):
    settings = make_settings(tmp_path)
    populate(settings)
    moved = next(settings.samples_dir.rglob("*.jpg"))
    old = settings.old_samples_dir / moved.relative_to(settings.samples_dir)
    old.parent.mkdir(parents=True)
    old.write_bytes(moved.read_bytes())  # the copy older versions still find
    (settings.old_samples_dir / "9").mkdir()
    (settings.old_samples_dir / "9" / "only-here.jpg").write_bytes(b"x")
    out = io.BytesIO()
    backup.write(settings, out, set(BACKUP_PARTS), credentials=False)
    names = zipfile.ZipFile(out).namelist()
    assert "files/old_samples_dir/9/only-here.jpg" in names
    assert not any(n.startswith("files/old_samples_dir/") and n.endswith(moved.name) for n in names)


def test_export_is_streamed_and_checks_its_parts(tmp_path):
    settings = make_settings(tmp_path)
    populate(settings)
    with TestClient(create_app(settings)) as client:
        info = client.get("/api/v1/backup").json()
        assert set(info["parts"]) == set(BACKUP_PARTS) and info["parts"]["samples"] > 0 and info["free"] > 0
        cfg = client.get("/api/v1/config").json()["backup"]
        assert [p["key"] for p in cfg["parts"]] == list(BACKUP_PARTS) and cfg["credentials"] is False
        assert client.get("/api/v1/backup/export?leave_out=sensors").status_code == 422
        assert client.get("/api/v1/backup/export?leave_out=nonsense").status_code == 422
        resp = client.get("/api/v1/backup/export?leave_out=history,models")
        assert resp.status_code == 200
        assert "visionstate-backup-" in resp.headers["content-disposition"]
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("backup.json"))
        assert manifest["parts"] == ["faces", "samples", "sensors"]
        assert [s["name"] for s in manifest["sensors"]] == ["Garage door", "Driveway", "Water meter"]


def test_a_stopped_download_ends_the_writer(tmp_path):
    settings = make_settings(tmp_path)
    populate(settings)
    pieces = backup.stream(settings, set(BACKUP_PARTS), False)
    next(pieces)
    pieces.close()  # the reader went away: the thread stops instead of waiting forever


# --- Restore --------------------------------------------------------------------------------


def restore(client, data: bytes):
    return client.post("/api/v1/backup/restore", content=data, headers={"content-type": "application/zip"})


def test_restore_replaces_everything(tmp_path, removed):
    source = make_settings(tmp_path / "a")
    populate(source)
    with TestClient(create_app(source)) as client:
        data = client.get("/api/v1/backup/export").content  # the passwords are masked
    target = make_settings(tmp_path / "b")
    with TestClient(create_app(target)) as client:
        made = client.post(
            "/api/v1/sensors",
            json={
                "name": "Old one",
                "source_type": "http",
                "source": "http://x",
                "states": [{"name": "A"}, {"name": "B"}],
            },
        )
        assert made.status_code == 201, made.text
        # The same sensor (slug) as in the backup, looking for something else: only that goes.
        same = client.post(
            "/api/v1/sensors",
            json={
                "name": "Driveway",
                "kind": "objects",
                "source_type": "http",
                "source": "http://x",
                "objects": {"classes": ["car", "dog"]},
            },
        )
        assert same.status_code == 201, same.text
        (target.models_dir / "here-already.onnx").write_bytes(b"kept")
        (target.sample_thumbs_dir / "1.jpg").write_bytes(b"stale thumbnail")
        before = client.app.state.runtime
        assert before.embedder is not None
        resp = restore(client, data)
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["sensors"] == 3 and result["people"] == 1
        assert [m["name"] for m in result["masked"]] == ["Garage door", "Water meter"]
        assert removed["sensors"] == ["old_one"]  # its entities go; the others keep theirs
        assert removed["classes"] == ["driveway/dog"]
        sensors = client.get("/api/v1/sensors").json()
        assert sorted(s["name"] for s in sensors) == ["Driveway", "Garage door", "Water meter"]
        assert client.get("/api/v1/status").status_code == 200
        assert client.get("/api/v1/people").json()["people"][0]["name"] == "Emma"
        rt = client.app.state.runtime
        assert set(rt._tasks) == {s["id"] for s in sensors}  # the restored sensors run
        # The old runtime let go of its models: two sets at once ran a small machine out of memory.
        assert rt is not before and rt.embedder is not None
        assert before.embedder is None and before.detector is None and not before.heads
        assert not any(live.frames for live in before.live.values())
    assert files_listed(target) == {
        "samples": 5,
        "samples_missing": 0,
        "faces": 1,
        "faces_missing": 0,
        "history": 3,
        "history_missing": 0,
    }
    assert (target.models_dir / "here-already.onnx").exists()  # models are added, never removed
    assert not (target.models_dir / "downloaded.onnx").exists()  # not a model of the registries
    assert not (target.sample_thumbs_dir / "1.jpg").exists()
    assert (target.heads_dir / "1.joblib").exists()  # restored (and retrained here: the test file is no model)
    assert not backup.stage_dir(target).exists() and not (target.media_dir / backup.STAGE).exists()
    assert not list(target.data_dir.glob("*.restore-old")) and not list(target.media_dir.glob("*.restore-old"))


def test_restore_without_history_removes_the_history_here(tmp_path, removed):
    source = make_settings(tmp_path / "a")
    populate(source)
    with TestClient(create_app(source)) as client:
        data = client.get("/api/v1/backup/export?leave_out=history,faces").content
        # Into the same app: its own history is gone with the rows that listed it.
        assert restore(client, data).status_code == 200
        assert client.get("/api/v1/people").json()["people"] == []
    assert removed["people"] == ["emma"]
    assert files_listed(source)["history"] == 0
    assert not any(source.history_dir.rglob("*.jpg"))
    assert files_listed(source)["samples"] == 5


def test_restore_refuses_what_it_cannot_restore(tmp_path):
    settings = make_settings(tmp_path)
    populate(settings)
    good = make(tmp_path / "good.zip").read_bytes()

    def changed(**fields) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(good)) as src, zipfile.ZipFile(out, "w") as dst:
            for item in src.infolist():
                body = src.read(item)
                if item.filename == "backup.json":
                    body = json.dumps({**json.loads(body), **fields}).encode()
                dst.writestr(item, body)
        return out.getvalue()

    with TestClient(create_app(settings)) as client:
        before = sorted(s["name"] for s in client.get("/api/v1/sensors").json())
        newer_db = restore(client, changed(schema=SCHEMA_VERSION + 1, app_version="9.9.9"))
        assert newer_db.status_code == 400 and "newer VisionState (9.9.9" in newer_db.json()["detail"]
        newer = restore(client, changed(format=BACKUP["format"] + 1))
        assert newer.status_code == 400 and "newer" in newer.json()["detail"]
        assert restore(client, b"not a zip").status_code == 400
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as z:
            z.writestr("manifest.json", "{}")  # a sensor bundle, not a backup
        assert (
            "export of one sensor: add it with Import a sensor" in restore(client, bundle.getvalue()).json()["detail"]
        )
        # A setting a newer version added (no new database version for it) is caught like in a
        # sensor import, before anything is stopped.
        future = with_db(
            good, tmp_path, "UPDATE sensor SET reading = json_set(reading, '$.mode', 'hologram') WHERE kind = 'reading'"
        )
        refused = restore(client, future).json()["detail"]
        assert "'Water meter'" in refused and "hologram" in refused and "update this app first" in refused
        # A database with something VisionState never makes.
        trigger = with_db(good, tmp_path, "CREATE TRIGGER t AFTER INSERT ON prediction BEGIN DELETE FROM sensor; END")
        assert "triggers or views" in restore(client, trigger).json()["detail"]
        assert sorted(s["name"] for s in client.get("/api/v1/sensors").json()) == before
        assert not backup.stage_dir(settings).exists()


def with_db(data: bytes, tmp_path: Path, sql: str) -> bytes:
    """The backup ``data`` with ``sql`` run on its database."""
    db = tmp_path / "edit.db"
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            body = src.read(item)
            if item.filename == "visionstate.db":
                db.write_bytes(body)
                con = sqlite3.connect(db)
                con.execute(sql)
                con.commit()
                con.close()
                body = db.read_bytes()
            dst.writestr(item, body)
    return out.getvalue()


def test_a_sensor_export_and_a_backup_say_where_they_go(tmp_path):
    settings = make_settings(tmp_path)
    data = make(tmp_path / "b.zip").read_bytes()
    with TestClient(create_app(settings)) as client:
        made = client.post(
            "/api/v1/sensors",
            json={
                "name": "Door",
                "source_type": "http",
                "source": "http://x",
                "states": [{"name": "A"}, {"name": "B"}],
            },
        )
        resp = client.post("/api/v1/import", files={"file": ("b.zip", data, "application/zip")})
        assert resp.status_code == 400 and "restore it under Settings → Backup" in resp.json()["detail"]
        exported = client.get(f"/api/v1/sensors/{made.json()['id']}/export").content
        assert "add it with Import a sensor" in restore(client, exported).json()["detail"]


def test_only_known_models_come_back(tmp_path):
    """The app loads a model file it finds without checking it again: a restore takes only the
    files of the registries, unchanged (SHA-256), like a download."""
    from visionstate.backbones import all_specs

    spec = next((s for s in sorted(all_specs(), key=lambda s: s.size) if (MODEL_DIR / s.filename).exists()), None)
    if spec is None:
        pytest.skip("no model downloaded")
    source = make_settings(tmp_path / "a")
    populate(source)
    (source.models_dir / spec.filename).write_bytes((MODEL_DIR / spec.filename).read_bytes())
    changed = next(s for s in all_specs() if s.filename != spec.filename)
    (source.models_dir / changed.filename).write_bytes(b"not the model")
    out = io.BytesIO()
    backup.write(source, out, set(BACKUP_PARTS), credentials=False)
    staged = backup.stage(make_settings(tmp_path / "b"), _saved(tmp_path, out.getvalue()))
    kept = sorted(p.name for p in (staged.root / "models_dir").iterdir())
    assert kept == [spec.filename]


def _saved(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "upload.zip"
    path.write_bytes(data)
    return path


def test_restore_ignores_paths_outside_its_folders(tmp_path):
    settings = make_settings(tmp_path)
    data = make(tmp_path / "b.zip").read_bytes()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item))
        dst.writestr("files/samples_dir/../../../escaped.txt", "x")
        dst.writestr("files/thumbs_dir/1.jpg", "x")  # never in a backup
        dst.writestr("files/somewhere/x.txt", "x")
        dst.writestr("/etc/x", "x")
    with TestClient(create_app(settings)) as client:
        assert restore(client, out.getvalue()).status_code == 200
    assert not (tmp_path / "escaped.txt").exists() and not list(tmp_path.rglob("escaped.txt"))
    assert not (settings.thumbs_dir / "1.jpg").exists()


def test_a_failed_start_puts_the_old_data_back(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    data = make(tmp_path / "b.zip").read_bytes()
    from visionstate.engine import Runtime

    original = Runtime.start
    calls = {"n": 0}

    async def start(self):
        calls["n"] += 1
        if calls["n"] == 2:  # the first start after the swap
            raise RuntimeError("broken")
        await original(self)

    monkeypatch.setattr(Runtime, "start", start)
    with TestClient(create_app(settings)) as client:
        client.post(
            "/api/v1/sensors",
            json={
                "name": "Mine",
                "source_type": "http",
                "source": "http://x",
                "states": [{"name": "A"}, {"name": "B"}],
            },
        )
        resp = restore(client, data)
        assert resp.status_code == 500 and "broken" in resp.json()["detail"]
        assert [s["name"] for s in client.get("/api/v1/sensors").json()] == ["Mine"]
    assert not list(settings.data_dir.glob("*.restore-old"))


def test_a_restore_cut_off_is_undone_at_start(tmp_path):
    settings = make_settings(tmp_path)
    populate(settings)
    staged = backup.stage(settings, make(tmp_path / "b.zip"))
    # Cut off halfway: the old database is aside, the new one not yet in place.
    settings.db_path.rename(settings.db_path.with_name("visionstate.db.restore-old"))
    settings.history_dir.rename(settings.history_dir.with_name("history.restore-old"))
    assert staged.root.exists()
    backup.clean_leftovers(settings)
    assert settings.db_path.exists() and any(settings.history_dir.rglob("*.jpg"))
    assert not backup.stage_dir(settings).exists()
    # Cut off after the restored data started: what was kept aside goes.
    aside = settings.history_dir.with_name("history.restore-old")
    aside.mkdir()
    (backup.stage_dir(settings)).mkdir()
    (backup.stage_dir(settings) / backup.DONE).touch()
    backup.clean_leftovers(settings)
    assert not aside.exists() and any(settings.history_dir.rglob("*.jpg"))


def test_each_folder_is_staged_on_the_disk_it_goes_to(tmp_path):
    """The media folder may be network storage: the history is extracted there, so the swap (with
    the app stopped) only renames instead of copying it."""
    settings = make_settings(tmp_path)
    populate(settings)
    staged = backup.stage(settings, make(tmp_path / "b.zip"))
    media_stage = settings.media_dir / backup.STAGE / "new"
    assert any((media_stage / "history_dir").rglob("*.jpg"))
    assert not (staged.root / "history_dir").exists()
    assert any((staged.root / "samples_dir").rglob("*.jpg")) and (staged.root / backup.DATABASE).exists()
    moves = []
    real_move = backup.shutil.move

    def move(src, dst):
        moves.append((Path(src), Path(dst)))
        return real_move(src, dst)

    backup.shutil.move = move
    try:
        backup.swap(settings, staged)
    finally:
        backup.shutil.move = real_move
    for src, dst in moves:  # never from one disk's folder to the other's
        assert (settings.media_dir in src.parents) == (settings.media_dir in dst.parents), (src, dst)
    assert any(settings.history_dir.rglob("*.jpg"))


def test_a_restore_cut_off_before_the_database_moved_keeps_it(tmp_path):
    """The database is moved aside first; cut off before that, the live database is the one from
    before and must be left alone (an early version deleted it at the next start)."""
    settings = make_settings(tmp_path)
    populate(settings)
    order = backup._replaced(settings)
    assert order[:3] == backup._db_files(settings.db_path)
    settings.heads_dir.rename(settings.heads_dir.with_name("heads.restore-old"))  # one folder, then cut off
    size = settings.db_path.stat().st_size
    backup.clean_leftovers(settings)
    assert settings.db_path.exists() and settings.db_path.stat().st_size == size
    assert (settings.heads_dir / "1.joblib").exists()


def test_a_newer_database_keeps_its_version(tmp_path):
    """An older version on a newer database (a rollback) must not lower its version: the next
    update would run its data migrations again."""
    path = tmp_path / "x.db"
    Database(path).init()
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 3}")
    con.commit()
    con.close()
    Database(path).init()
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 3
    con.close()


# --- Every released backup format restores in this version ----------------------------------

KEPT = sorted(BACKUPS.glob("*.zip"))


def test_a_backup_of_this_version_is_kept():
    """When the database version or the backup format goes up, a backup made by it is added, so
    every later version is tested against it: run `python -m tests.backup_fixture`."""
    made = {(m["format"], m["schema"]) for m in (json.loads(zipfile.ZipFile(p).read("backup.json")) for p in KEPT)}
    assert (BACKUP["format"], SCHEMA_VERSION) in made, "run `python -m tests.backup_fixture` and commit the file"


@pytest.mark.parametrize("path", KEPT, ids=[p.name for p in KEPT])
def test_every_kept_backup_restores(tmp_path, path, removed):
    manifest = json.loads(zipfile.ZipFile(path).read("backup.json"))
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        resp = restore(client, path.read_bytes())
        assert resp.status_code == 200, resp.text
        sensors = client.get("/api/v1/sensors").json()
        assert sorted(s["name"] for s in sensors) == sorted(s["name"] for s in manifest["sensors"])
        for sensor in sensors:
            assert client.get(f"/api/v1/sensors/{sensor['id']}").status_code == 200
        assert client.get("/api/v1/history").status_code == 200
    listed = files_listed(settings)
    assert listed["samples_missing"] == listed["faces_missing"] == listed["history_missing"] == 0
    assert listed["samples"] > 0 and listed["faces"] > 0 and listed["history"] > 0
    con = sqlite3.connect(settings.db_path)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert con.execute("SELECT count(*) FROM sensor").fetchone()[0] == len(manifest["sensors"])
    finally:
        con.close()
    with Database(settings.db_path).session() as s:
        assert len(s.scalars(select(Sensor)).all()) == len(manifest["sensors"])


def test_requests_wait_while_the_data_is_swapped(tmp_path):
    """The UI keeps polling during a restore: its requests wait for the new runtime instead of failing."""
    import threading
    import time

    with TestClient(create_app(make_settings(tmp_path))) as client:
        ready = client.app.state.ready
        assert client.portal is not None
        client.portal.call(ready.clear)
        answers = []
        asking = threading.Thread(target=lambda: answers.append(client.get("/api/v1/status").status_code))
        asking.start()
        time.sleep(0.5)
        assert answers == []  # still waiting
        client.portal.call(ready.set)
        asking.join(10)
        assert answers == [200]
