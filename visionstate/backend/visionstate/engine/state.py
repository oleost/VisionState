"""What the engine knows about a sensor: its settings (a snapshot of the row) and its live state."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from .. import detectors, sounds
from ..change_limit import Watch
from ..db import Person, Sensor
from ..mqtt import SensorDescriptor
from ..settings import (
    FACE_UNKNOWN_KEY,
    FACES,
    KIND_OBJECTS,
    KIND_READING,
    KIND_SOUND,
    KIND_STATES,
    RUNTIME,
    SOUND,
    TEACH,
    active_custom,
    faces_on,
    merge_objects,
    merge_reading,
    merge_sound,
    merge_triggers,
)
from ..sources import AudioStream


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
    sound: dict = field(default_factory=lambda: merge_sound(None))  # sound sensors only
    entity_prefix: bool = False  # see db.Sensor.entity_prefix
    publish: bool = True  # see db.Sensor.publish
    frame_size: list | None = None  # see db.Sensor.frame_size

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
    def is_sound(self) -> bool:
        return self.kind == KIND_SOUND

    @property
    def sound_keys(self) -> list[str]:
        """What a sound sensor reports: the model's sounds it listens for, then its own sounds."""
        if not self.is_sound:
            return []
        own = [c["key"] for c in self.sound["custom"]]
        return [*(k for k in self.sound["sounds"] if k in sounds.SOUNDS), *own]

    @property
    def sound_names(self) -> dict[str, str]:
        """Sound key -> its name (the model's, or the own sound's)."""
        return {**{k: s.name for k, s in sounds.SOUNDS.items()}, **{c["key"]: c["name"] for c in self.sound["custom"]}}

    @property
    def custom_labels(self) -> list[dict]:
        """Own labels in use (object sensors): {key, name, parent}."""
        return active_custom(self.objects) if self.is_objects else []

    @property
    def parents(self) -> dict[str, str]:
        """Own label -> the class it is a kind of."""
        return {label["key"]: label["parent"] for label in self.custom_labels}

    @property
    def faces_on(self) -> bool:
        """An object sensor that recognises people by their face."""
        return self.is_objects and faces_on(self.objects)

    @property
    def object_keys(self) -> list[str]:
        """What an object sensor reports: its classes, then its own labels (and, recognising
        faces, the unknown person)."""
        unknown = [FACE_UNKNOWN_KEY] if self.faces_on else []
        return [*self.objects["classes"], *(label["key"] for label in self.custom_labels), *unknown]

    @property
    def descriptor(self) -> SensorDescriptor:
        names = detectors.LABELS.by_key
        objects = (
            [(k, names[k].name, names[k].icon) for k in self.objects["classes"] if k in names]
            + [(c["key"], c["name"], names[c["parent"]].icon) for c in self.custom_labels if c["parent"] in names]
            + ([(FACE_UNKNOWN_KEY, "Unknown person", "mdi:account-question")] if self.faces_on else [])
            if self.is_objects
            else []
        )
        reading = self.reading if self.is_reading else None
        heard = []
        for key in self.sound_keys:
            spec = sounds.SOUNDS.get(key)
            name = self.sound_names[key]
            heard.append((key, name, spec.icon, spec.device_class) if spec else (key, name, "mdi:ear-hearing", "sound"))
        return SensorDescriptor(
            self.slug, self.name, self.state_keys, self.kind, objects, reading, self.entity_prefix, heard
        )

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
            objects=with_people(merge_objects(row.objects), object_session(row)),
            reading=merge_reading(row.reading),
            sound=merge_sound(row.sound),
            entity_prefix=bool(row.entity_prefix),
            publish=bool(row.publish),
            frame_size=row.frame_size,
        )


def with_people(objects: dict, session: Session | None) -> dict:
    """An object sensor's settings with the people (shared by every sensor, db.Person) among its
    own labels while it recognises faces: {key, name, parent "person", face True}. Each one is an
    entity of the sensor and a name its faces can get."""
    if session is None or not faces_on(objects):
        return objects
    people = [
        {"key": p.key, "name": p.name, "parent": "person", "face": True}
        for p in session.scalars(select(Person).order_by(Person.created_at))
    ]
    return {**objects, "custom": [*(c for c in objects["custom"] if not c.get("face")), *people]}


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
class FaceBurst:
    """Checks every FACES["burst_interval_s"] while a person nobody is named for is there (logic.steer_face_burst)."""

    until: float = 0.0  # running until then (at most); 0: not running
    idle: int = 0  # checks in a row without a usable face or the person coming closer
    absent: int = 0  # checks in a row without a person
    best_height: float = 0.0  # the tallest person box (share of the frame) of this burst
    best_face: float = 0.0  # the widest face (pixels) of this burst
    spent: bool = False  # this visit had its burst: none again until the person has gone


@dataclass
class SoundTrack:
    """What a sound sensor publishes for one sound, and the windows that decide it (logic.hear)."""

    on: bool = False
    recent: deque = field(default_factory=lambda: deque(maxlen=SOUND["of_windows"]))  # passed its threshold
    last_heard: float = 0.0  # last window above the lower threshold while on
    score: float = 0.0  # the last window's score
    best: float = 0.0  # the best score since it was heard (for the history)


@dataclass
class SoundLive:
    """A sound sensor between two windows: its stream, levels, sounds and clips still to cut."""

    stream: AudioStream | None = None  # while the sensor listens
    consumed: int = 0  # the stream's samples looked at
    levels: deque = field(default_factory=lambda: deque(maxlen=SOUND["live_levels"]))  # dBFS per hop, newest last
    background: deque = field(
        default_factory=lambda: deque(maxlen=int(SOUND["background_s"] / SOUND["hop_s"]))
    )  # levels the background is learned from
    line: float | None = None  # the level a window must reach to be analysed
    analysed: bool = False  # the newest window was loud enough and analysed
    scores: dict[str, float] = field(default_factory=dict)  # the last analysed window's, per sound
    tracks: dict[str, SoundTrack] = field(default_factory=dict)
    clips: list[dict] = field(default_factory=list)  # {"row", "start", "end", "key"}: cut once received
    room_at: float = 0.0  # when a window of the room was last kept as "other" (own sounds)


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
    face_burst: FaceBurst = field(default_factory=FaceBurst)
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
    asked: dict[str, list[float]] = field(
        default_factory=dict
    )  # own label/person -> when the review asked (this cooldown)
    # Faces: what the face search gave for the last person boxes it looked at ("ok" or the
    # problem: "none", "cut", "small", "turned", "blurry", "unsure"), for "a usable face in 9 of 40".
    face_stats: deque = field(default_factory=lambda: deque(maxlen=FACES["stats_window"]))
    face_frame: tuple[int, int] | None = None  # the size of the last frame looked at for faces
    face_min_px: float | None = None  # the smallest face (its width) it was looked at with
    face_logged: tuple[float, float] = (0.0, 0.0)  # when a face nobody is named for was last kept, and its width
    # Reading sensors: the last read {"text", "score", "value", "reason", "at"} and the image
    # the reader saw (JPEG), shown in the UI so a bad region or display setting is easy to spot.
    reading: dict | None = None
    reading_image: bytes | None = None
    reading_restored: bool = False  # last published value loaded from the history after a restart
    # Reading sensors, for the diagnostic entities: (time, accepted) of the last day's readings,
    # and (time, value) of accepted readings of a counter for its rate (see readers.counter_rate).
    reads: deque = field(default_factory=deque)
    rate_samples: deque = field(default_factory=deque)
    watch: Watch = field(default_factory=Watch)  # counters: how much the value may go up next
    # Reading sensors: the camera's picture has another size than the region was drawn on
    # ({"from": [w, h], "to": [w, h], "since": epoch seconds}); readings are held until it is
    # back or the user accepts it (engine/reading.py).
    camera_changed: dict | None = None
    sound: SoundLive | None = None  # sound sensors, while they listen
    # Sent to Home Assistant when it changed: topic -> (what it showed, when), see PublishingMixin._send_on_change.
    sent_on_change: dict[str, tuple[object, float]] = field(default_factory=dict)

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
