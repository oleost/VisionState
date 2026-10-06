"""What the engine knows about a sensor: its settings (a snapshot of the row) and its live state."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .. import detectors
from ..db import Sensor
from ..mqtt import SensorDescriptor
from ..settings import (
    KIND_OBJECTS,
    KIND_READING,
    KIND_STATES,
    RUNTIME,
    TEACH,
    active_custom,
    merge_objects,
    merge_reading,
    merge_triggers,
)


@dataclass
class SensorConfig:
    """Plain snapshot of a sensor row, safe to use outside a DB session."""

    id: int
    slug: str
    name: str
    source_type: str
    source: str
    roi: dict | None
    interval_s: float
    threshold: float
    debounce: int
    enabled: bool
    states: list[dict]
    triggers: dict = field(default_factory=lambda: merge_triggers(None))
    review: dict = field(default_factory=dict)  # sensor overrides only; see Runtime.review_rules
    kind: str = KIND_STATES
    objects: dict = field(default_factory=lambda: merge_objects(None))  # object sensors only
    reading: dict = field(default_factory=lambda: merge_reading(None))  # reading sensors only
    entity_prefix: bool = False  # see db.Sensor.entity_prefix
    publish: bool = True  # see db.Sensor.publish

    @property
    def state_keys(self) -> list[str]:
        return [s["key"] for s in self.states]

    @property
    def is_objects(self) -> bool:
        return self.kind == KIND_OBJECTS

    @property
    def is_reading(self) -> bool:
        return self.kind == KIND_READING

    @property
    def custom_labels(self) -> list[dict]:
        """Own labels in use (object sensors): {key, name, parent}."""
        return active_custom(self.objects) if self.is_objects else []

    @property
    def parents(self) -> dict[str, str]:
        """Own label -> the class it is a kind of."""
        return {label["key"]: label["parent"] for label in self.custom_labels}

    @property
    def object_keys(self) -> list[str]:
        """What an object sensor reports: its classes, then its own labels."""
        return [*self.objects["classes"], *(label["key"] for label in self.custom_labels)]

    @property
    def descriptor(self) -> SensorDescriptor:
        names = detectors.LABELS.by_key
        objects = (
            [(k, names[k].name, names[k].icon) for k in self.objects["classes"] if k in names]
            + [(c["key"], c["name"], names[c["parent"]].icon) for c in self.custom_labels if c["parent"] in names]
            if self.is_objects
            else []
        )
        reading = self.reading if self.is_reading else None
        return SensorDescriptor(self.slug, self.name, self.state_keys, self.kind, objects, reading, self.entity_prefix)

    @classmethod
    def from_row(cls, row: Sensor) -> SensorConfig:
        """A snapshot of the row; settings stored as JSON are filled with their defaults."""
        return cls(
            id=row.id,
            slug=row.slug,
            name=row.name,
            source_type=row.source_type,
            source=row.source,
            roi=row.roi,
            interval_s=row.interval_s,
            threshold=row.threshold,
            debounce=row.debounce,
            enabled=row.enabled,
            states=[{"id": s.id, "key": s.key, "name": s.name, "color": s.color} for s in row.states],
            triggers=merge_triggers(row.triggers),
            review=dict(row.review or {}),
            kind=row.kind or KIND_STATES,
            objects=merge_objects(row.objects),
            reading=merge_reading(row.reading),
            entity_prefix=bool(row.entity_prefix),
            publish=bool(row.publish),
        )


class Debouncer:
    """Only changes the published value after ``required`` consecutive equal results."""

    def __init__(self) -> None:
        self.published: str | None = None
        self.candidate: str | None = None
        self.count = 0

    def update(self, value: str, required: int) -> bool:
        if value == self.candidate:
            self.count += 1
        else:
            self.candidate, self.count = value, 1
        if self.published is None or (self.count >= required and value != self.published):
            changed = value != self.published
            self.published = value
            return changed
        return False


@dataclass
class ObjectTrack:
    """Published state of one object class of an object sensor."""

    on: bool = False
    count: int = 0  # published count; kept while "on" until the object has cleared
    streak: int = 0  # checks in a row with the object present
    misses: int = 0  # checks in a row without it (see DETECTION["clear_misses"])
    last_seen: float = 0.0
    score: float = 0.0  # best confidence in the last check


@dataclass
class LiveState:
    frames: deque = field(default_factory=lambda: deque(maxlen=RUNTIME["frame_cache_size"]))
    debouncer: Debouncer = field(default_factory=Debouncer)
    probs: dict[str, float] = field(default_factory=dict)
    top: str | None = None
    confidence: float = 0.0
    available: bool | None = None
    error: str = ""
    failure: str = ""  # the unexpected error last logged by the sensor's loop (logged once)
    last_run: float | None = None
    last_flag: float = 0.0
    changes: deque = field(default_factory=lambda: deque(maxlen=50))
    force: bool = False
    force_paused: bool = False  # the forced check was asked for by the user: also when paused
    burst_until: float = 0.0
    last_probe: float = 0.0
    signature: np.ndarray | None = None  # region thumbnail of the last classified frame
    change_score: float | None = None  # last measured difference, shown in the UI for tuning
    last_trigger: dict | None = None  # {"source": ..., "detail": ..., "at": epoch seconds}
    light_off_task: asyncio.Task | None = None  # lets go of the light after a burst of checks
    light_error: str = ""  # why the light could not be switched (shown in the sensor's settings)
    frame_id: str | None = None  # the frame the last check analysed (still in ``frames`` for a while)
    tracks: dict[str, ObjectTrack] = field(default_factory=dict)  # object sensors, per class
    detections: list[dict] = field(default_factory=list)  # object sensors, last check
    # Object sensors: the frames the last checks analysed, so a box can be taught a while after
    # it was shown (``frames`` turns over quickly); classes filtered away in the last check and
    # when each was last stored in the history.
    analysed: deque = field(default_factory=lambda: deque(maxlen=TEACH["frames_kept"]))
    filtered_keys: set[str] = field(default_factory=set)
    filtered_logged: dict[str, float] = field(default_factory=dict)
    recheck_at: float = 0.0  # an object that was there went missing: check again then (0: no)
    asked: dict[str, float] = field(default_factory=dict)  # own label -> when the review last asked
    # Reading sensors: the last read {"text", "score", "value", "reason", "at"} and the image
    # the reader saw (JPEG), shown in the UI so a bad region or display setting is easy to spot.
    reading: dict | None = None
    reading_image: bytes | None = None
    reading_restored: bool = False  # last published value loaded from the history after a restart
    # Reading sensors, for the diagnostic entities: (time, accepted) of the last day's readings,
    # and (time, value) of accepted readings of a counter for its rate (see readers.counter_rate).
    reads: deque = field(default_factory=deque)
    rate_samples: deque = field(default_factory=deque)

    def remember(self, data: bytes) -> str:
        frame_id = f"{time.time_ns():x}"
        self.frames.append((frame_id, data))
        return frame_id

    def frame(self, frame_id: str | None) -> bytes | None:
        for fid, data in reversed(self.frames):
            if frame_id is None or fid == frame_id:
                return data
        return None

    def analysed_frame(self, frame_id: str) -> bytes | None:
        """A frame a recent check analysed (or any frame still cached)."""
        for fid, data in reversed(self.analysed):
            if fid == frame_id:
                return data
        return self.frame(frame_id)
