"""A full installation drawn by our own code, and a backup of it (backup.write).

Used by tests/test_backup.py, and to add a backup made by this version to ``BACKUPS`` — run it
whenever ``db.SCHEMA_VERSION`` or ``settings.BACKUP["format"]`` goes up (the test
``test_a_backup_of_this_version_is_kept`` says so); every kept backup is restored by every later
version's tests:

    python -m tests.backup_fixture
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

BACKUPS = Path(__file__).parent / "assets" / "backups"


def picture(seed: int, size: tuple[int, int] = (160, 90)) -> Image.Image:
    image = Image.new("RGB", size, (40 + seed * 30 % 200, 90, 120))
    ImageDraw.Draw(image).rectangle((20 + seed * 7 % 60, 20, 80 + seed * 7 % 60, 70), fill=(220, 210, 60))
    return image


def populate(settings) -> dict:  # noqa: ANN001
    """One sensor of each kind with what each keeps (training images, a taught box, a person with
    a face, history, a counter's watch, a model file), the way the app stores them. Returns
    what is in it."""
    from visionstate.db import (
        Database,
        Face,
        ModelInfo,
        Person,
        Prediction,
        ReadingStat,
        ReadingWatch,
        Sample,
        SampleLabel,
        Sensor,
        State,
    )
    from visionstate.storage import Storage

    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init()
    storage = Storage(settings)
    common = {"interval_s": 60.0, "threshold": 0.7, "debounce": 2, "source_type": "http"}
    try:
        with db.session() as s:
            door = Sensor(slug="garage_door", name="Garage door", source="rtsp://admin:secret@10.0.0.9/live", **common)
            door.states = [
                State(key="open", name="Open", color="#2e7d32", position=0),
                State(key="closed", name="Closed", color="#c62828", position=1),
            ]
            drive = Sensor(
                slug="driveway",
                name="Driveway",
                kind="objects",
                source="http://cam.local/snap.jpg",
                objects={"classes": ["person", "car"], "faces": {"enabled": True}},
                **common,
            )
            meter = Sensor(
                slug="water_meter",
                name="Water meter",
                kind="reading",
                source="http://cam.local/meter.jpg?user=me&password=pw",
                reading={"mode": "counter", "display": "counter", "digits": 7, "decimals": 3},
                **common,
            )
            s.add_all([door, drive, meter])
            s.flush()
            for n in range(4):
                sample = Sample(sensor_id=door.id, filename=storage.save_sample(door.id, picture(n)), origin="snapshot")
                s.add(sample)
                s.flush()
                s.add(SampleLabel(sample_id=sample.id, state_id=door.states[n % 2].id))
            s.add(
                Sample(
                    sensor_id=drive.id,
                    filename=storage.save_sample(drive.id, picture(9)),
                    origin="live",
                    object_label="car",
                    detected="car",
                    box=[0.1, 0.2, 0.5, 0.6],
                    score=0.8,
                )
            )
            emma = Person(key="emma", name="Emma")
            s.add(emma)
            s.flush()
            face = {"box": [10.0, 10.0, 40.0, 40.0], "points": [[20.0, 20.0]] * 5, "score": 0.9}
            s.add(
                Face(
                    person_id=emma.id,
                    sensor_id=drive.id,
                    filename=storage.save_face(picture(5, (64, 64))),
                    origin="live",
                    face=face,
                )
            )
            for n, (sensor, key) in enumerate(((door, "open"), (drive, "car"), (meter, "reading"))):
                s.add(
                    Prediction(
                        sensor_id=sensor.id,
                        state_key=key,
                        published_key=key if key != "reading" else "89939.512",
                        confidence=0.9,
                        probs={},
                        frame=storage.save_history(sensor.id, picture(20 + n)),
                        is_change=True,
                    )
                )
            s.add(ReadingStat(sensor_id=meter.id, day="2026-10-01", reads=10, accepted=9, rejected={"too_high": 1}))
            s.add(ReadingWatch(sensor_id=meter.id, value="89939.512", seen_at=1.0, accepted_at=1.0))
            s.add(ModelInfo(sensor_id=door.id, backbone="test", n_samples=4))
            ids = {"door": door.id, "drive": drive.id, "meter": meter.id}
        db.set_setting("storage", {"history_days": 9})
        (settings.heads_dir / f"{ids['door']}.joblib").write_bytes(b"head")
        (settings.models_dir / "downloaded.onnx").write_bytes(b"model")
    finally:
        db.engine.dispose()
    return {"slugs": {"garage_door", "driveway", "water_meter"}, "people": {"emma"}, "ids": ids}


def make(target: Path) -> Path:
    """A backup of ``populate`` in a fresh folder, with everything and without camera passwords."""
    import tempfile

    from visionstate import backup
    from visionstate.settings import BACKUP_PARTS, Settings

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = Settings(
            data_dir=root / "data", media_dir=root / "media", frontend_dir=root, bundled_models_dir=root
        )
        populate(settings)
        with target.open("wb") as out:
            backup.write(settings, out, set(BACKUP_PARTS), credentials=False)
    return target


if __name__ == "__main__":
    from visionstate.db import SCHEMA_VERSION
    from visionstate.settings import BACKUP, VERSION

    BACKUPS.mkdir(parents=True, exist_ok=True)
    out = BACKUPS / f"backup-format{BACKUP['format']}-schema{SCHEMA_VERSION}.zip"
    if out.exists():
        sys.exit(f"{out.name} is kept already (made by an earlier version): never change a kept backup")
    make(out)
    print(f"{out} (VisionState {VERSION}, {out.stat().st_size} bytes)", file=sys.stderr)
