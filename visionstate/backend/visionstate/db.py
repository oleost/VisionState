"""Database models and session handling (SQLite)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import QueuePool

log = logging.getLogger(__name__)

SCHEMA_VERSION = 15

# Schema upgrades for existing databases, keyed on the version they upgrade to.
# Each step is a list of (table, column, SQL type) columns to add.
MIGRATIONS: dict[int, list[tuple[str, str, str]]] = {
    2: [("sensor", "triggers", "JSON")],
    3: [("sensor", "review", "JSON")],
    4: [("model_info", "suspects", "JSON"), ("sample", "verified", "BOOLEAN NOT NULL DEFAULT 0")],
    5: [("sensor", "objects", "JSON"), ("prediction", "detections", "JSON")],
    6: [("sensor", "reading", "JSON")],
    7: [("prediction", "read_ok", "BOOLEAN"), ("prediction", "correct_value", "VARCHAR(64)")],
    # Sensors made before 0.6.3b6 keep their "visionstate_" entity IDs (see Sensor.entity_prefix).
    8: [("sensor", "entity_prefix", "BOOLEAN NOT NULL DEFAULT 1")],
    # Existing sensors keep sending to Home Assistant.
    9: [("sensor", "publish", "BOOLEAN NOT NULL DEFAULT 1")],
    10: [
        ("sample", "object_label", "VARCHAR(64)"),
        ("sample", "detected", "VARCHAR(64)"),
        ("sample", "box", "JSON"),
        ("sample", "score", "FLOAT"),
    ],
    11: [("prediction", "sample_id", "INTEGER")],
    # 13: the people and their faces (new tables: person, face, face_embedding).
    # 14: what a counter's limit learns per day (and a new table: reading_watch).
    14: [("reading_stat", "peak", "FLOAT"), ("reading_stat", "total", "FLOAT")],
    15: [("sensor", "frame_size", "JSON")],
}


def keep_text_reader(reading: dict | None) -> dict | None:
    """Reading settings of a counter made before the wheel reader, with its text reader kept.

    A mechanical counter without a stored ``counter_reader`` was set up with the text reader;
    it keeps it (switching is a choice on the Settings tab). Anything else is returned as it is.
    """
    if not isinstance(reading, dict) or reading.get("display") != "counter" or "counter_reader" in reading:
        return reading
    return {**reading, "counter_reader": "ocr"}


def _keep_text_readers(conn: Connection) -> None:
    for sensor_id, raw in conn.execute(text("SELECT id, reading FROM sensor WHERE reading IS NOT NULL")).all():
        reading = json.loads(raw) if isinstance(raw, str) else raw
        kept = keep_text_reader(reading)
        if kept is not reading:
            conn.execute(
                text("UPDATE sensor SET reading = :r WHERE id = :id"), {"r": json.dumps(kept), "id": sensor_id}
            )


def keep_change_step(reading: dict | None) -> dict | None:
    """Reading settings of a counter made before change_limit, with the limit it had kept.

    Its ``max_step`` (the change allowed between two readings) stays that ("step"); a counter
    without one gets no limit ("off"), as before. A counter that has ``change_limit`` and
    anything else are returned as they are.
    """
    if not isinstance(reading, dict) or reading.get("mode", "counter") != "counter" or "change_limit" in reading:
        return reading
    step = float(reading.get("max_step") or 0)
    return {**reading, "change_limit": "step" if step > 0 else "off"}


def _keep_change_steps(conn: Connection) -> None:
    rows = conn.execute(text("SELECT id, reading FROM sensor WHERE reading IS NOT NULL")).all()
    for sensor_id, raw in rows:
        reading = json.loads(raw) if isinstance(raw, str) else raw
        kept = keep_change_step(reading)
        if kept is not reading:
            conn.execute(
                text("UPDATE sensor SET reading = :r WHERE id = :id"), {"r": json.dumps(kept), "id": sensor_id}
            )


# Changes to the data of existing databases, keyed on the version they upgrade to (after the columns).
DATA_MIGRATIONS: dict[int, Callable[[Connection], None]] = {
    # Counters made before 0.7.1b3 keep the text reader; new ones get the wheel reader.
    12: _keep_text_readers,
    # Counters keep the change limit they had (see keep_change_step).
    14: _keep_change_steps,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Sensor(Base):
    __tablename__ = "sensor"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    # settings.SENSOR_KINDS: "single_state" (learned states), "objects" (detector) or "reading"
    # (number on a display, OCR). Fixed at creation.
    kind: Mapped[str] = mapped_column(String(32), default="single_state")
    source_type: Mapped[str] = mapped_column(String(32))  # see sources.SOURCE_TYPES
    source: Mapped[str] = mapped_column(String(1024))
    roi: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # normalised {x, y, w, h}
    interval_s: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    debounce: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # When to check the camera besides the interval; see settings.TRIGGER_DEFAULTS.
    triggers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Per-sensor overrides of the review rules; missing/None fields use the global rules.
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Object sensors: classes and filters; see settings.OBJECT_DEFAULTS.
    objects: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Reading sensors: mode, decimals, unit …; see settings.READING_DEFAULTS.
    reading: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Older sensors suggest "visionstate_<slug>…" entity IDs to Home Assistant, and keep doing so
    # (also when exported and imported), so entities that come back get the same ID. Newer sensors
    # let Home Assistant name them after the device and entity, like other integrations.
    entity_prefix: Mapped[bool] = mapped_column(Boolean, default=False)
    # Send values to Home Assistant (settings.SENSOR_PUBLISH_DEFAULT). Off: the entities stay unavailable.
    publish: Mapped[bool] = mapped_column(Boolean, default=True)
    # Reading sensors: the camera picture's size [width, height] the region was drawn on (taken
    # from the first frame after the region or source changed). A frame of another size holds the
    # readings (engine/reading.py, "camera changed"): the region would show something else.
    frame_size: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    states: Mapped[list[State]] = relationship(
        back_populates="sensor", cascade="all, delete-orphan", order_by="State.position"
    )


class State(Base):
    __tablename__ = "state"

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    color: Mapped[str] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, default=0)

    sensor: Mapped[Sensor] = relationship(back_populates="states")


class Sample(Base):
    __tablename__ = "sample"

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(256))
    origin: Mapped[str] = mapped_column(String(32))  # snapshot | upload | video | review | import
    use_roi: Mapped[bool] = mapped_column(Boolean, default=True)
    is_night: Mapped[bool] = mapped_column(Boolean, default=False)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    # The user confirmed this label is right; it is no longer listed as possibly mislabelled.
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Object sensors: a box the user taught (see settings.TEACH). The image is the box with a
    # margin; object_label is what it is ("none", a class or an own label), detected the class the
    # detector gave it (None: a box it missed, drawn by the user), box and score where and how sure.
    object_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detected: Mapped[str | None] = mapped_column(String(64), nullable=True)
    box: Mapped[list | None] = mapped_column(JSON, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Many-to-many on purpose: single-state sensors use one label, multi-label can use more.
    labels: Mapped[list[SampleLabel]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class SampleLabel(Base):
    __tablename__ = "sample_label"

    sample_id: Mapped[int] = mapped_column(ForeignKey("sample.id", ondelete="CASCADE"), primary_key=True)
    state_id: Mapped[int] = mapped_column(ForeignKey("state.id", ondelete="CASCADE"), primary_key=True)


class Embedding(Base):
    __tablename__ = "embedding"

    sample_id: Mapped[int] = mapped_column(ForeignKey("sample.id", ondelete="CASCADE"), primary_key=True)
    backbone: Mapped[str] = mapped_column(String(64), primary_key=True)
    roi_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class Prediction(Base):
    """A stored result: published state changes and frames flagged for review.

    Object sensors store one row per object class that appeared (published_key "on") or
    cleared ("off"), with state_key = the class and the frame's detections.
    Reading sensors store accepted new values (state_key "reading", published_key = the value),
    every rejected reading (published_key None, review_reason "rejected") and spot checks of
    accepted ones; probs holds {"text", "value", "reason", "roi"} ("roi": the region it was read
    in, None for the whole frame; missing in rows from before 0.7.1b2), and on a rejected one still
    waiting for review "repeats" and "last_seen" when the same reading came again (it is not stored
    again: engine.reading._repeat_rejected). ``read_ok`` is the user's verdict on
    what the reader read (``correct_value`` when it misread); verified readings are never removed
    automatically, as they may later teach the reader.
    """

    __tablename__ = "prediction"
    # The history of some sensors, newest first, paged by (time, id): see api/history.py.
    __table_args__ = (Index("ix_prediction_sensor_time", "sensor_id", "created_at", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    state_key: Mapped[str] = mapped_column(String(64))  # top prediction
    published_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    probs: Mapped[dict] = mapped_column(JSON, default=dict)
    frame: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_change: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    detections: Mapped[list | None] = mapped_column(JSON, nullable=True)  # object sensors, see detectors.Detection
    read_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # reading sensors, see above
    correct_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # State sensors: the dataset sample a review answer added, so a second answer changes it
    # instead of adding the frame again. Not a foreign key: the sample may be deleted later.
    sample_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReadingStat(Base):
    """How many readings a reading sensor made per day, and why the rejected ones were rejected."""

    __tablename__ = "reading_stat"

    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # local date, YYYY-MM-DD
    reads: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[dict] = mapped_column(JSON, default=dict)  # reason -> count
    # Counters, for the limit they learn (change_limit.learn): the largest rise over
    # COUNTER_LIMIT["window_s"] and the total rise that day, while the counter was seen.
    peak: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)


class ReadingWatch(Base):
    """A counter's limit between readings (change_limit.Watch), so a restart knows how long it was
    away, and the last accepted value of a reading sensor when it is newer than the history's
    (a counter's, or a value confirmed in Review)."""

    __tablename__ = "reading_watch"

    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(64), nullable=True)  # accepted at accepted_at
    seen_at: Mapped[float | None] = mapped_column(Float, nullable=True)  # epoch seconds
    accepted_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_s: Mapped[float] = mapped_column(Float, default=0.0)
    held: Mapped[bool] = mapped_column(Boolean, default=False)


class ModelInfo(Base):
    __tablename__ = "model_info"

    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"), primary_key=True)
    backbone: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=0)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    n_samples: Mapped[int] = mapped_column(Integer, default=0)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    confusion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Samples whose cross-validated prediction disagrees with their label.
    suspects: Mapped[list | None] = mapped_column(JSON, nullable=True)
    train_seconds: Mapped[float] = mapped_column(Float, default=0.0)


class Person(Base):
    """Someone taught by their face, known to every object sensor that recognises faces. ``key``
    is the entity key on those sensors (``binary_sensor.<sensor>_<key>``) and of their own
    sensor (``sensor.visionstate_<key>``): never changed once made."""

    __tablename__ = "person"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Face(Base):
    """A taught face: the face with a margin, not aligned, in <data>/faces
    (storage.Storage.face_path), the face in that crop's pixels {box, points, score} (see
    faces.face_to_dict), and the camera it was taught on (null once that sensor is deleted: the
    face stays with the person)."""

    __tablename__ = "face"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    sensor_id: Mapped[int | None] = mapped_column(ForeignKey("sensor.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str] = mapped_column(String(256))
    origin: Mapped[str] = mapped_column(String(32))  # live | history | review | import
    is_night: Mapped[bool] = mapped_column(Boolean, default=False)
    face: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FaceEmbedding(Base):
    """A taught face's embedding by one face model (made again when the model changes)."""

    __tablename__ = "face_embedding"

    face_id: Mapped[int] = mapped_column(ForeignKey("face.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class Database:
    def __init__(self, path: Path):
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        self._factory = sessionmaker(self.engine, expire_on_commit=False)
        self.closed = False

    def close(self) -> Callable[[], int]:
        """No session after this (a backup is restored under it: a worker thread of the stopped
        runtime must not write into the restored database). Returns how many connections are
        still in use, as a function to wait on; dispose of the engine once it is 0. Not before:
        a pool disposed of counts nothing, and a connection handed back to it afterwards stays open."""
        self.closed = True
        pool = self.engine.pool
        return pool.checkedout if isinstance(pool, QueuePool) else lambda: 0

    def init(self) -> None:
        fresh = not inspect(self.engine).has_table("sensor")
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            version = conn.execute(text("PRAGMA user_version")).scalar() or 0
            if not fresh:
                for target in sorted(v for v in MIGRATIONS if v > version):
                    for table, column, sql_type in MIGRATIONS[target]:
                        columns = {c["name"] for c in inspect(conn).get_columns(table)}
                        if column not in columns:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                for target in sorted(v for v in DATA_MIGRATIONS if v > version):
                    DATA_MIGRATIONS[target](conn)
            # Indexes added later: create_all only makes those of new tables. Older versions ignore them.
            for table in Base.metadata.sorted_tables:
                for index in table.indexes:
                    index.create(conn, checkfirst=True)
            # Never lowered: an older version on a newer database (a rollback, a backup restored)
            # must not make the next update run that database's data migrations again.
            conn.execute(text(f"PRAGMA user_version = {max(version, SCHEMA_VERSION)}"))

    @staticmethod
    def after_commit(session: Session, action: Callable[[], None]) -> None:
        """Run ``action`` once ``session`` is committed, never when it is rolled back: a file is
        removed only together with the row that lists it. A file that cannot be removed then is
        logged: the row is gone already, so the request must not fail."""

        def run(_session: Session) -> None:
            try:
                action()
            except OSError as err:
                log.warning("Could not remove a file: %s", err)

        event.listen(session, "after_commit", run, once=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        if self.closed:
            raise RuntimeError("The database was closed (a backup is being restored)")
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_setting(self, key: str, default: Any = None) -> Any:
        """A global setting (stored as JSON), or ``default``; see get_text and get_dict for typed ones."""
        with self.session() as s:
            row = s.get(Setting, key)
            return default if row is None else row.value

    def get_text(self, key: str, default: str) -> str:
        """A setting that is a string (such as the chosen model), or ``default``."""
        value = self.get_setting(key, default)
        return value if isinstance(value, str) else default

    def get_dict(self, key: str) -> dict:
        """A setting that is an object (such as the global review rules), or an empty one."""
        value = self.get_setting(key)
        return value if isinstance(value, dict) else {}

    def set_setting(self, key: str, value: Any) -> None:
        with self.session() as s:
            row = s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value))
            else:
                row.value = value
