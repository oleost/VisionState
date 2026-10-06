"""Database models and session handling (SQLite)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

SCHEMA_VERSION = 11

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
    accepted ones; probs holds {"text", "value", "reason"}. ``read_ok`` is the user's verdict on
    what the reader read (``correct_value`` when it misread); verified readings are never removed
    automatically, as they may later teach the reader.
    """

    __tablename__ = "prediction"

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
            conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_setting(self, key: str, default=None):
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

    def set_setting(self, key: str, value) -> None:
        with self.session() as s:
            row = s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value))
            else:
                row.value = value
