"""Export readings (bundle.export_readings): what goes in first when not all fit, an export of only
the readings not exported before, and memory that does not grow with the readings a sensor keeps
(a Raspberry Pi has little of it)."""

import io
import json
import tracemalloc
import zipfile
from datetime import timedelta
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from visionstate import bundle
from visionstate.db import Database, Prediction, Sensor
from visionstate.settings import READING, Settings
from visionstate.storage import Storage

from .conftest import MODEL_DIR

FRAME = "frame.jpg"
HOUR = 3600


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=tmp_path / "none",
        bundled_models_dir=MODEL_DIR,
    )
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init()
    with db.session() as s:
        sensor = Sensor(
            name="Water",
            slug="water",
            kind="reading",
            source_type="http",
            source="http://camera",
            interval_s=5,
            threshold=0.7,
            debounce=1,
            roi={"x": 0.1, "y": 0.1, "w": 0.6, "h": 0.4},
        )
        s.add(sensor)
        s.flush()
        sid = sensor.id
        made = sensor.created_at
    # One frame every reading points at: an image that compresses badly, so each crop weighs the same.
    folder = settings.history_dir / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    noise = np.random.default_rng(1).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    Image.fromarray(noise).save(folder / FRAME, quality=90)
    yield db, Storage(settings), sid, made, tmp_path
    db.engine.dispose()


def add(db: Database, sid: int, made, rows: list[dict]) -> None:
    """Readings: each {"hour": hours after the sensor was made, "ok": the answer, "rejected": bool}."""
    with db.engine.begin() as conn:
        conn.execute(
            Prediction.__table__.insert(),
            [
                {
                    "sensor_id": sid,
                    "created_at": made + timedelta(hours=r["hour"]),
                    "state_key": "reading",
                    "published_key": None if r.get("rejected") else "1",
                    "confidence": 0.9,
                    "probs": {
                        "text": "1",
                        "value": str(r["hour"]),
                        "reason": "went down" if r.get("rejected") else None,
                    },
                    "frame": FRAME,
                    "is_change": True,
                    "reviewed": r.get("ok") is not None,
                    "read_ok": r.get("ok"),
                }
                for r in rows
            ],
        )


def export(db, storage, sid, tmp_path, only_new=False) -> tuple[dict, list]:
    target = tmp_path / "readings.zip"
    _, shared = bundle.export_readings(db, storage, sid, target, "reader", only_new=only_new)
    with zipfile.ZipFile(io.BytesIO(target.read_bytes())) as archive:
        manifest = json.loads(archive.read("readings.json"))
        assert all(r["file"] in archive.namelist() for r in manifest["readings"])
    return manifest, shared


def hours(manifest: dict) -> list[int]:
    return [round(r["seconds"] / HOUR) for r in manifest["readings"]]


def test_every_beginning_of_the_spread_order_covers_the_whole_time():
    for n in (0, 1, 2, 5, 64, 100, 1000):
        order = bundle._spread(n)
        assert sorted(order) == list(range(n))  # each once
    order = bundle._spread(100)
    assert order[0] == 0  # the newest first
    for k in (4, 10, 30):
        chosen = sorted(order[:k])
        # every gap between the chosen ones is at most about twice the even one
        assert max(b - a for a, b in zip([0, *chosen], [*chosen, 99], strict=True)) <= 2 * 100 / k + 1


def test_a_full_export_keeps_the_checked_and_rejected_ones_and_a_spread_of_the_accepted(setup):
    db, storage, sid, made, tmp_path = setup
    # 100 hours: one reading an hour; three checked, four rejected, the rest accepted nobody checked.
    checked = {10: True, 50: False, 90: True}
    rejected = {20, 40, 60, 80}
    add(db, sid, made, [{"hour": h, "ok": checked.get(h), "rejected": h in rejected} for h in range(100)])
    everything, _ = export(db, storage, sid, tmp_path)
    assert everything["format"] == 8 and everything["left_out"] == 0 and len(everything["readings"]) == 100
    weight = (tmp_path / "readings.zip").stat().st_size / 100
    # Room for about 25 readings: the 7 that matter most, then accepted ones from all through the time.
    with patch.dict(READING, {"export_max_mb": (100_000 + 25 * weight) / 1e6}):
        full, shared = export(db, storage, sid, tmp_path)
    got = hours(full)
    n = len(got)
    assert 15 <= n <= 30 and full["left_out"] == 100 - n and len(shared) == n
    answers = [r["answer"] for r in full["readings"][:3]]
    assert got[:3] == [90, 50, 10] and answers == ["read correctly", "misread", "read correctly"]
    assert got[3:7] == [80, 60, 40, 20] and all(r["rejected"] == "went down" for r in full["readings"][3:7])
    accepted = got[7:]
    assert all(r["answer"] == "unchecked" and not r["rejected"] for r in full["readings"][7:])
    assert accepted[0] == 99 and min(accepted) <= 8  # the newest and some of the oldest
    gaps = [b - a for a, b in zip(sorted(accepted), sorted(accepted)[1:], strict=False)]
    assert max(gaps) <= 2 * 100 / len(accepted) + 2  # no long stretch left out

    # The ones exported are noted; an export of the new ones holds exactly those left out.
    bundle.mark_shared(db, shared)
    assert bundle.readings_to_share(db, sid) == {"new": 100 - n, "shared": n}
    rest, rest_shared = export(db, storage, sid, tmp_path, only_new=True)
    assert rest["only_new"] is True and rest["left_out"] == 0
    assert sorted(hours(rest)) == sorted(set(range(100)) - set(got))
    bundle.mark_shared(db, rest_shared)
    assert bundle.readings_to_share(db, sid) == {"new": 0, "shared": 100}
    with pytest.raises(LookupError, match="No new readings"):
        export(db, storage, sid, tmp_path, only_new=True)

    # An answer given after the export makes it new; the same answer again does not.
    with db.session() as s:
        row = s.query(Prediction).filter(Prediction.sensor_id == sid).order_by(Prediction.created_at).all()
        row[30].read_ok, row[30].correct_value = False, "31"
        row[10].read_ok = True  # as it was
    again, _ = export(db, storage, sid, tmp_path, only_new=True)
    assert [(r["answer"], r["right_value"], round(r["seconds"] / HOUR)) for r in again["readings"]] == [
        ("misread", "31", 30)
    ]
    # Everything is still there to export, and the seconds are the same in every export.
    assert sorted(hours(export(db, storage, sid, tmp_path)[0])) == list(range(100))


def test_the_mark_keeps_the_rest_of_a_reading_and_marks_only_what_was_sent(setup):
    db, storage, sid, made, tmp_path = setup
    add(db, sid, made, [{"hour": h} for h in range(3)])
    with db.session() as s:
        first = s.query(Prediction).order_by(Prediction.created_at).first()
        assert first is not None
        first_id = first.id
        first.probs = None  # a row without details still gets its mark
    _, shared = export(db, storage, sid, tmp_path)
    bundle.mark_shared(db, [m for m in shared if m[0] != first_id][:1])
    with db.session() as s:
        rows = {r.id: r.probs for r in s.query(Prediction)}
    marked = [p for p in rows.values() if p and "shared" in p]
    assert len(marked) == 1 and marked[0]["shared"] == [None, None] and marked[0]["text"] == "1"
    bundle.mark_shared(db, [m for m in shared if m[0] == first_id])
    with db.session() as s:
        assert s.get(Prediction, first_id).probs == {"shared": [None, None]}  # type: ignore[union-attr]


def test_memory_does_not_grow_with_the_readings_a_sensor_keeps(setup):
    """Before, every row with all its details was read first: 200 MB for 20,000 readings."""
    db, storage, sid, made, tmp_path = setup
    wheels = {"per_wheel": [{"pos": 1.0, "p": 0.9, "top": [[1, 0.9], [2, 0.05], [0, 0.05]]}] * 8, "value": 1.5}
    with db.engine.begin() as conn:
        conn.execute(
            Prediction.__table__.insert(),
            [
                {
                    "sensor_id": sid,
                    "created_at": made + timedelta(minutes=i),
                    "state_key": "reading",
                    "published_key": "1",
                    "confidence": 0.9,
                    "probs": {"text": "1", "value": "1", "reason": None, "wheels": wheels, "note": "x" * 1500},
                    "frame": FRAME,
                    "is_change": True,
                    "reviewed": i % 5 == 0,
                    "read_ok": True if i % 5 == 0 else None,
                }
                for i in range(10_000)
            ],
        )
    with patch.dict(READING, {"export_max_mb": 0.3}):
        tracemalloc.start()
        try:
            manifest, _ = export(db, storage, sid, tmp_path)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    assert manifest["left_out"] > 9_000
    assert peak < 15_000_000, f"{peak / 1e6:.1f} MB"  # all rows loaded: about 100 MB
