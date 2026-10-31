"""Shared helpers for the REST API."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import change_limit, detectors, imaging, readers, sounds, teach
from ..db import Face, ModelInfo, Sample, SampleLabel, Sensor
from ..engine import LiveState, ObjectTrack, Runtime, SensorConfig, with_people
from ..mqtt import entity_options, main_entities
from ..settings import (
    COUNTER_LIMIT,
    COUNTER_READERS,
    FACE_DEFAULTS,
    FACE_LIMITS,
    FACE_UNKNOWN_KEY,
    FACES,
    KIND_OBJECTS,
    KIND_READING,
    KIND_SOUND,
    LIGHT_DOMAINS,
    MAX_STATES,
    OBJECT_DEFAULTS,
    OBJECT_LIMITS,
    OBJECT_MAX_CLASSES,
    READING,
    READING_CHANGE_LIMITS,
    READING_DEFAULTS,
    READING_DEVICE_CLASSES,
    READING_DISPLAYS,
    READING_LIMITS,
    READING_MODES,
    READING_NEW_SENSOR,
    REVIEW_DEFAULTS,
    REVIEW_LIMITS,
    ROI_MAX_POINTS,
    SOUND,
    SOUND_DEFAULTS,
    SOUND_LEVELS,
    SOUND_LIMITS,
    TEACH_TUNABLE_LIMITS,
    TRIGGER_DEFAULTS,
    TRIGGER_LIMITS,
    TRIGGER_MAX_ENTITIES,
    TRIGGER_STATE_MAX_LENGTH,
    UNKNOWN_STATE,
    active_custom,
    faces_on,
    merge_objects,
    merge_reading,
    merge_review,
    merge_sound,
    merge_triggers,
)

API_PREFIX = "/api/v1"


def runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def slugify(text: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:48] or fallback


def unique_slug(session: Session, name: str) -> str:
    base = slugify(name, "sensor")
    slug, n = base, 2
    while session.scalar(select(Sensor.id).where(Sensor.slug == slug)) is not None:
        slug, n = f"{base}_{n}", n + 1
    return slug


def unique_name(session: Session, name: str) -> str:
    """``name``, or "name (2)", "name (3)"… when a sensor already has it (an imported copy)."""
    candidate, n = name, 2
    while session.scalar(select(Sensor.id).where(Sensor.name == candidate)) is not None:
        candidate, n = f"{name} ({n})", n + 1
    return candidate


def get_sensor(session: Session, sensor_id: int, kind: str | None = None) -> Sensor:
    """The sensor, or 404. With ``kind``, a sensor of another kind is a 400 (e.g. training an object sensor)."""
    sensor = session.get(Sensor, sensor_id)
    if sensor is None:
        raise HTTPException(404, "Sensor not found")
    if kind is not None and sensor.kind != kind:
        what = {KIND_OBJECTS: "Object sensors", KIND_READING: "Reading sensors", KIND_SOUND: "Sound sensors"}.get(
            sensor.kind, "State sensors"
        )
        raise HTTPException(400, f"{what} do not support this")
    return sensor


def check_sensor(rt: Runtime, sensor_id: int, kind: str | None = None) -> None:
    """404 (or 400, see get_sensor) unless the sensor exists; for a worker thread (asyncio.to_thread)."""
    with rt.db.session() as s:
        get_sensor(s, sensor_id, kind)


def state_id_for(sensor: Sensor, key: str | None) -> int | None:
    if key is None:
        return None
    for state in sensor.states:
        if state.key == key:
            return state.id
    raise HTTPException(400, f"Unknown state {key!r}")


class Roi(BaseModel):
    """Rectangle (normalised 0-1), optionally a polygon given by ``points`` (bounding box = x/y/w/h)."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    points: list[tuple[float, float]] | None = Field(None, min_length=3, max_length=ROI_MAX_POINTS)

    def normalised(self) -> dict | None:
        return imaging.normalise_roi(self.model_dump(exclude_none=True))


ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_tlo = {k: v[0] for k, v in TRIGGER_LIMITS.items()}
_thi = {k: v[1] for k, v in TRIGGER_LIMITS.items()}


def valid_light(value: str) -> str:
    """A light, switch or helper to switch on for a sensor ("" = none)."""
    value = value.strip()
    if value and not ENTITY_ID.match(value):
        raise ValueError(f"Not an entity id: {value!r}")
    if value and value.split(".", 1)[0] not in LIGHT_DOMAINS:
        raise ValueError(f"The light must be one of: {', '.join(LIGHT_DOMAINS)}")
    return value


class Triggers(BaseModel):
    """When a sensor checks its camera besides the interval. Defaults and limits: settings.TRIGGER_*."""

    regular: bool = TRIGGER_DEFAULTS["regular"]
    entities: list[str] = Field(default_factory=list, max_length=TRIGGER_MAX_ENTITIES)
    only_states: dict[str, str] = Field(default_factory=dict)
    burst_interval_s: float = Field(
        TRIGGER_DEFAULTS["burst_interval_s"], ge=_tlo["burst_interval_s"], le=_thi["burst_interval_s"]
    )
    burst_duration_s: float = Field(
        TRIGGER_DEFAULTS["burst_duration_s"], ge=_tlo["burst_duration_s"], le=_thi["burst_duration_s"]
    )
    change_detection: bool = TRIGGER_DEFAULTS["change_detection"]
    change_interval_s: float = Field(
        TRIGGER_DEFAULTS["change_interval_s"], ge=_tlo["change_interval_s"], le=_thi["change_interval_s"]
    )
    change_threshold: float = Field(
        TRIGGER_DEFAULTS["change_threshold"], ge=_tlo["change_threshold"], le=_thi["change_threshold"]
    )
    light_entity: str = TRIGGER_DEFAULTS["light_entity"]
    light_delay_s: float = Field(TRIGGER_DEFAULTS["light_delay_s"], ge=_tlo["light_delay_s"], le=_thi["light_delay_s"])

    @field_validator("entities")
    @classmethod
    def _valid_entities(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(v.strip() for v in value if v.strip()))
        for entity in cleaned:
            if not ENTITY_ID.match(entity):
                raise ValueError(f"Not an entity id: {entity!r}")
        return cleaned

    @field_validator("light_entity")
    @classmethod
    def _valid_light(cls, value: str) -> str:
        return valid_light(value)

    @model_validator(mode="after")
    def _valid_only_states(self) -> Triggers:
        # Only for chosen entities; an empty state means "any change" and is dropped.
        cleaned = {}
        for entity, state in self.only_states.items():
            state = state.strip()
            if entity in self.entities and state:
                if len(state) > TRIGGER_STATE_MAX_LENGTH:
                    raise ValueError(f"State too long for {entity}")
                cleaned[entity] = state
        self.only_states = cleaned
        return self


_olo = {k: v[0] for k, v in OBJECT_LIMITS.items()}
_ohi = {k: v[1] for k, v in OBJECT_LIMITS.items()}
_tlo = {k: v[0] for k, v in TEACH_TUNABLE_LIMITS.items()}
_thi = {k: v[1] for k, v in TEACH_TUNABLE_LIMITS.items()}


class TeachLimits(BaseModel):
    """The tunable teaching limits (settings.TEACH_TUNABLE). None = inherit: globally the
    backbone's default, on a sensor the global value."""

    match_similarity: float | None = Field(None, ge=_tlo["match_similarity"], le=_thi["match_similarity"])
    keep_similarity: float | None = Field(None, ge=_tlo["keep_similarity"], le=_thi["keep_similarity"])
    ask_similarity: float | None = Field(None, ge=_tlo["ask_similarity"], le=_thi["ask_similarity"])
    rescue_similarity: float | None = Field(None, ge=_tlo["rescue_similarity"], le=_thi["rescue_similarity"])
    rescue_floor: float | None = Field(None, ge=_tlo["rescue_floor"], le=_thi["rescue_floor"])

    def stored(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


def check_teach(limits: dict) -> None:
    """Effective teaching limits must make sense together: asking and keeping start below a match."""
    if limits["ask_similarity"] > limits["match_similarity"]:
        raise HTTPException(400, "“Asks you in the review queue” must not be above “Recognised as a box you taught”")
    if limits["keep_similarity"] > limits["match_similarity"]:
        raise HTTPException(400, "“Keeps that answer” must not be above “Recognised as a box you taught”")


_flo = {k: v[0] for k, v in FACE_LIMITS.items()}
_fhi = {k: v[1] for k, v in FACE_LIMITS.items()}


class FaceLimits(BaseModel):
    """The tunable face limits (settings.FACE_TUNABLE). None = inherit: globally the default (the
    face model's similarities), on a sensor the global value."""

    min_face_px: float | None = Field(None, ge=_flo["min_face_px"], le=_fhi["min_face_px"])
    match_similarity: float | None = Field(None, ge=_flo["match_similarity"], le=_fhi["match_similarity"])
    sure_similarity: float | None = Field(None, ge=_flo["sure_similarity"], le=_fhi["sure_similarity"])
    ask_similarity: float | None = Field(None, ge=_flo["ask_similarity"], le=_fhi["ask_similarity"])
    keep_similarity: float | None = Field(None, ge=_flo["keep_similarity"], le=_fhi["keep_similarity"])

    def stored(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


def check_faces(limits: dict) -> None:
    """Effective face limits must make sense together: asking and keeping below a match, a match
    not above being sure at once."""
    if limits["ask_similarity"] > limits["match_similarity"]:
        raise HTTPException(400, "“Asks you in the review queue” must not be above “Recognised”")
    if limits["keep_similarity"] > limits["match_similarity"]:
        raise HTTPException(400, "“Keeps the name” must not be above “Recognised”")
    if limits["match_similarity"] > limits["sure_similarity"]:
        raise HTTPException(400, "“Recognised” must not be above “Sure at once”")


class FacesIn(FaceLimits):
    """Recognising people by their face on an object sensor: on or off, asking about strangers,
    and its own face limits (unset ones follow the global ones). Defaults: settings.FACE_*."""

    enabled: bool = FACE_DEFAULTS["enabled"]
    ask_unknown: bool = FACE_DEFAULTS["ask_unknown"]


class ObjectsIn(BaseModel):
    """What an object sensor looks for. Defaults and limits: settings.OBJECT_*."""

    classes: list[str] = Field(default_factory=lambda: list(OBJECT_DEFAULTS["classes"]), max_length=OBJECT_MAX_CLASSES)
    min_size: float = Field(default=OBJECT_DEFAULTS["min_size"], ge=_olo["min_size"], le=_ohi["min_size"])
    clear_after_s: float = Field(
        default=OBJECT_DEFAULTS["clear_after_s"], ge=_olo["clear_after_s"], le=_ohi["clear_after_s"]
    )
    use_taught: bool = OBJECT_DEFAULTS["use_taught"]
    teach: TeachLimits | None = None  # this sensor's own teaching limits; unset ones follow the global ones
    faces: FacesIn | None = None  # recognising people by their face; unset keeps what the sensor has
    # Own labels ("custom") are made by teaching a box (api/teach.py) or a face (api/faces.py), never set here.

    def stored(self, exclude_unset: bool = False) -> dict:
        data = self.model_dump(exclude_unset=exclude_unset, exclude={"teach", "faces"})
        if self.teach is not None or not exclude_unset:
            data["teach"] = self.teach.stored() if self.teach else {}
        if self.faces is not None:
            data["faces"] = self.faces.model_dump(exclude_none=True)
        return data

    @field_validator("classes")
    @classmethod
    def _known_classes(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value))
        if not cleaned:
            raise ValueError("Pick at least one object")
        unknown = [key for key in cleaned if key not in detectors.LABELS.by_key]
        if unknown:
            raise ValueError(f"Unknown object {unknown[0]!r}")
        return cleaned


_dlo = {k: v[0] for k, v in READING_LIMITS.items()}
_dhi = {k: v[1] for k, v in READING_LIMITS.items()}


class DialIn(BaseModel):
    """A pointer dial of a counter (settings.READING_DEFAULTS["dials"]): its circle in units of the
    region's height from its top-left corner, and whether it turns anticlockwise."""

    x: float = Field(ge=-READING["dial_reach"], le=READING_LIMITS["digits"][1] * 2 + READING["dial_reach"])
    y: float = Field(ge=-READING["dial_reach"], le=1 + READING["dial_reach"])
    r: float = Field(ge=READING["dial_radius"][0], le=READING["dial_radius"][1])
    ccw: bool = False


class ReadingIn(BaseModel):
    """How a reading sensor turns what it reads into a value. Defaults and limits: settings.READING_*."""

    mode: str = READING_DEFAULTS["mode"]
    decimals: int = Field(default=READING_DEFAULTS["decimals"], ge=_dlo["decimals"], le=_dhi["decimals"])
    unit: str = Field(default=READING_DEFAULTS["unit"], max_length=16)
    device_class: str = READING_DEFAULTS["device_class"]
    display: str = READING_DEFAULTS["display"]
    digits: int = Field(default=READING_DEFAULTS["digits"], ge=_dlo["digits"], le=_dhi["digits"])
    counter_reader: str = READING_DEFAULTS["counter_reader"]
    wheel_reader: str = Field(default=READING_DEFAULTS["wheel_reader"], validate_default=True)
    straighten: bool = READING_NEW_SENSOR["straighten"]
    dials: list[DialIn] = Field(default=[], max_length=READING["max_dials"])
    max_step: float = Field(default=READING_DEFAULTS["max_step"], ge=_dlo["max_step"], le=_dhi["max_step"])
    change_limit: str = READING_DEFAULTS["change_limit"]
    max_rate: float = Field(default=READING_DEFAULTS["max_rate"], ge=_dlo["max_rate"], le=_dhi["max_rate"])
    spot_rate: float = Field(default=READING_DEFAULTS["spot_rate"], ge=_dlo["spot_rate"], le=_dhi["spot_rate"])
    rate_window_min: float = Field(
        default=READING_DEFAULTS["rate_window_min"], ge=_dlo["rate_window_min"], le=_dhi["rate_window_min"]
    )

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in READING_MODES:
            raise ValueError(f"Unknown mode {value!r}")
        return value

    @field_validator("display")
    @classmethod
    def _display(cls, value: str) -> str:
        if value not in READING_DISPLAYS:
            raise ValueError(f"Unknown display {value!r}")
        return value

    @field_validator("change_limit")
    @classmethod
    def _change_limit(cls, value: str) -> str:
        if value not in READING_CHANGE_LIMITS:
            raise ValueError(f"Unknown change limit {value!r}")
        return value

    @field_validator("counter_reader")
    @classmethod
    def _counter_reader(cls, value: str) -> str:
        if value not in COUNTER_READERS:
            raise ValueError(f"Unknown counter reader {value!r}")
        return value

    @field_validator("wheel_reader")
    @classmethod
    def _wheel_reader(cls, value: str) -> str:
        if not value:
            return readers.DEFAULT_WHEEL_READER  # stored, so a later default never changes it
        if value not in readers.WHEEL_READERS:
            raise ValueError(f"Unknown wheel reader {value!r}")
        return value

    @field_validator("device_class")
    @classmethod
    def _device_class(cls, value: str) -> str:
        if value not in READING_DEVICE_CLASSES:
            raise ValueError(f"Unknown device class {value!r}")
        return value

    @field_validator("unit")
    @classmethod
    def _unit(cls, value: str) -> str:
        return value.strip()


_slo = {k: v[0] for k, v in SOUND_LIMITS.items()}
_shi = {k: v[1] for k, v in SOUND_LIMITS.items()}


class SoundIn(BaseModel):
    """What a sound sensor listens for and when. Defaults and limits: settings.SOUND_*."""

    sounds: list[str] = Field(default_factory=lambda: list(SOUND_DEFAULTS["sounds"]))
    level: str = SOUND_DEFAULTS["level"]
    above_db: float = Field(default=SOUND_DEFAULTS["above_db"], ge=_slo["above_db"], le=_shi["above_db"])
    fixed_db: float = Field(default=SOUND_DEFAULTS["fixed_db"], ge=_slo["fixed_db"], le=_shi["fixed_db"])
    clear_after_s: float = Field(
        default=SOUND_DEFAULTS["clear_after_s"], ge=_slo["clear_after_s"], le=_shi["clear_after_s"]
    )
    clips: bool = SOUND_DEFAULTS["clips"]
    video: bool = SOUND_DEFAULTS["video"]
    learn_room: bool = SOUND_DEFAULTS["learn_room"]
    # A sound's own threshold (None or missing: the model's, or SOUND["own_threshold"] for own sounds).
    thresholds: dict[str, float | None] = Field(default_factory=dict)
    # Own sounds ("custom") are made by teaching a clip (api/sounds.py), never set here.

    @field_validator("sounds")
    @classmethod
    def _known_sounds(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(value))
        unknown = [key for key in cleaned if key not in sounds.SOUNDS]
        if unknown:
            raise ValueError(f"Unknown sound {unknown[0]!r}")
        if len(cleaned) > SOUND["max_sounds"]:
            raise ValueError(f"At most {SOUND['max_sounds']} sounds per sensor")
        return cleaned

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        if value not in SOUND_LEVELS:
            raise ValueError(f"Unknown level {value!r}")
        return value

    @field_validator("thresholds")
    @classmethod
    def _thresholds(cls, value: dict[str, float | None]) -> dict[str, float | None]:
        lo, hi = SOUND_LIMITS["threshold"]
        for key, threshold in value.items():
            if threshold is not None and not lo <= threshold <= hi:
                raise ValueError(f"The threshold of {key!r} must be between {lo:g} and {hi:g}")
        return value

    def stored(self, exclude_unset: bool = False) -> dict:
        data = self.model_dump(exclude_unset=exclude_unset)
        if "thresholds" in data:
            data["thresholds"] = {k: v for k, v in self.thresholds.items() if v is not None}
        return data


_rlo = {k: v[0] for k, v in REVIEW_LIMITS.items()}
_rhi = {k: v[1] for k, v in REVIEW_LIMITS.items()}


class ReviewRules(BaseModel):
    """Global review rules. Defaults and limits: settings.REVIEW_*."""

    enabled: bool = REVIEW_DEFAULTS["enabled"]
    below: float = Field(REVIEW_DEFAULTS["below"], ge=_rlo["below"], le=_rhi["below"])
    cooldown_s: float = Field(REVIEW_DEFAULTS["cooldown_s"], ge=_rlo["cooldown_s"], le=_rhi["cooldown_s"])
    flip_limit: int = Field(REVIEW_DEFAULTS["flip_limit"], ge=_rlo["flip_limit"], le=_rhi["flip_limit"])
    flip_window_s: float = Field(REVIEW_DEFAULTS["flip_window_s"], ge=_rlo["flip_window_s"], le=_rhi["flip_window_s"])
    spot_rate: float = Field(REVIEW_DEFAULTS["spot_rate"], ge=_rlo["spot_rate"], le=_rhi["spot_rate"])


class ReviewOverrides(BaseModel):
    """Per-sensor overrides; None = use the global value."""

    enabled: bool | None = None
    below: float | None = Field(None, ge=_rlo["below"], le=_rhi["below"])
    cooldown_s: float | None = Field(None, ge=_rlo["cooldown_s"], le=_rhi["cooldown_s"])
    flip_limit: int | None = Field(None, ge=_rlo["flip_limit"], le=_rhi["flip_limit"])
    flip_window_s: float | None = Field(None, ge=_rlo["flip_window_s"], le=_rhi["flip_window_s"])
    spot_rate: float | None = Field(None, ge=_rlo["spot_rate"], le=_rhi["spot_rate"])

    def stored(self) -> dict | None:
        values = self.model_dump(exclude_none=True)
        return values or None


class StateIn(BaseModel):
    key: str | None = None
    name: str = Field(min_length=1, max_length=64)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


def validate_states(states: list[StateIn]) -> None:
    if not 2 <= len(states) <= MAX_STATES:
        raise HTTPException(400, f"A sensor needs between 2 and {MAX_STATES} states")
    keys = [s.key or slugify(s.name, "state") for s in states]
    if len(set(keys)) != len(keys):
        raise HTTPException(400, "State names must be unique")
    if UNKNOWN_STATE in keys:
        raise HTTPException(400, f"'{UNKNOWN_STATE}' is reserved")


def sample_counts(session: Session, sensor: Sensor) -> dict:
    """Labelled samples per state (split by day/night) and number of unlabelled samples."""
    rows = session.execute(
        select(SampleLabel.state_id, Sample.is_night, func.count())
        .join(Sample, Sample.id == SampleLabel.sample_id)
        .where(Sample.sensor_id == sensor.id)
        .group_by(SampleLabel.state_id, Sample.is_night)
    ).all()
    per_state = {st.key: {"day": 0, "night": 0} for st in sensor.states}
    key_by_id = {st.id: st.key for st in sensor.states}
    for state_id, night, count in rows:
        if state_id in key_by_id:
            per_state[key_by_id[state_id]]["night" if night else "day"] += count
    total = (
        session.scalar(
            select(func.count())
            .select_from(Sample)
            .where(Sample.sensor_id == sensor.id, Sample.object_label.is_(None))  # not boxes taught to an object sensor
        )
        or 0
    )
    labelled = sum(v["day"] + v["night"] for v in per_state.values())
    return {"per_state": per_state, "labelled": labelled, "unlabelled": total - labelled}


def entity_ids(rt: Runtime, sensor: Sensor) -> list[str]:
    """The sensor's main Home Assistant entities (one, or two per object class).

    As Home Assistant has them when known (its entity registry), else as it would name them.
    """
    known = rt.ha_entity_ids
    return [known.get(uid, expected) for uid, expected in main_entities(SensorConfig.from_row(sensor).descriptor)]


def ha_entities_view(sensor: Sensor) -> list[dict]:
    """The entities that can be chosen on the sensor's Home Assistant tab, and whether each is on."""
    descriptor = SensorConfig.from_row(sensor).descriptor
    return [
        {**option, "on": descriptor.entity_on(option["id"], option["default"])} for option in entity_options(descriptor)
    ]


def objects_view(session: Session, sensor: Sensor, live: LiveState | None) -> dict | None:
    """An object sensor's part of its view: each class and own label (with the people while it
    recognises faces), live, and what was taught."""
    if sensor.kind != KIND_OBJECTS:
        return None
    settings = with_people(merge_objects(sensor.objects), session)
    parents = {label["key"]: label["parent"] for label in active_custom(settings)}
    tracks = live.tracks if live else {}
    per_class = []
    unknown = [FACE_UNKNOWN_KEY] if faces_on(settings) else []  # recognising faces: "unknown person"
    for key in [*settings["classes"], *parents, *unknown]:
        track = tracks.get(key) or ObjectTrack()
        per_class.append(
            {
                "key": key,
                "parent": parents.get(key),  # own labels: the class they are a kind of
                "on": track.on,
                "count": track.count,
                "score": track.score,
                "last_seen": track.last_seen or None,
            }
        )
    taught = session.execute(
        select(Sample.object_label, Sample.detected).where(
            Sample.sensor_id == sensor.id, Sample.object_label.is_not(None)
        )
    ).all()
    usable = [(label, detected) for label, detected in taught if label is not None and teach.usable(label, parents)]
    taught_faces = session.scalar(select(func.count()).select_from(Face).where(Face.sensor_id == sensor.id)) or 0
    stats = list(live.face_stats) if live else []
    return {
        **settings,
        "live": per_class,
        "detections": live.detections if live else [],
        "taught": len(taught),  # boxes taught (Quality tab, first-time question)
        "taught_faces": taught_faces,  # faces taught on this camera (export)
        "taught_keys": sorted(teach.compared_keys(usable, parents)),  # classes compared with them
        # Faces: of the last person boxes looked at, how many had a usable face, and why not.
        "face_stats": {
            "looked": len(stats),
            "usable": stats.count("ok"),
            "problems": {p: stats.count(p) for p in sorted(set(stats) - {"ok"})},
            "frame": list(live.face_frame) if live and live.face_frame else None,  # width, height
            "min_face_px": live.face_min_px if live else None,  # the smallest face it was looked at with
        },
    }


def sound_view(sensor: Sensor, live: LiveState | None) -> dict | None:
    """A sound sensor's part of its view: its settings, and live what it hears — the level of each
    hop and the line a window must reach, each sound's score against its threshold, and the
    stream's audio (codec, sample rate)."""
    if sensor.kind != KIND_SOUND:
        return None
    settings = merge_sound(sensor.sound)
    heard = live.sound if live else None
    stream = heard.stream if heard else None
    own = {c["key"]: c["name"] for c in settings["custom"]}
    keys = [*(k for k in settings["sounds"] if k in sounds.SOUNDS), *own]
    per_sound = []
    for key in keys:
        spec = sounds.SOUNDS.get(key)
        track = heard.tracks.get(key) if heard else None
        threshold = settings["thresholds"].get(key, spec.threshold if spec else SOUND["own_threshold"])
        per_sound.append(
            {
                "key": key,
                "name": spec.name if spec else own[key],
                "own": spec is None,
                "threshold": threshold,
                "model_threshold": spec.threshold if spec else SOUND["own_threshold"],
                "on": bool(track and track.on),
                "score": track.score if track else 0.0,
            }
        )
    return {
        **settings,
        "live": per_sound,
        "levels": list(heard.levels) if heard else [],
        "line": heard.line if heard else None,
        "analysed": bool(heard and heard.analysed),
        "stream": getattr(stream, "info", None) or None,
    }


def reading_view(sensor: Sensor, live: LiveState | None) -> dict | None:
    """A reading sensor's part of its view: its settings, the published value and the last read."""
    if sensor.kind != KIND_READING:
        return None
    settings = merge_reading(sensor.reading)
    return {
        **settings,
        "value": live.debouncer.published if live else None,
        "last": live.reading if live else None,
        "has_image": bool(live and live.reading_image),
        "camera_changed": live.camera_changed if live else None,
        "limit": limit_view(settings, live.watch) if live and settings["mode"] == "counter" else None,
    }


def limit_view(settings: dict, watch: change_limit.Watch) -> dict:
    """What a counter's limit is now: learned (per hour and day, from how many days; ``needed``
    days before it is; ``rate``: what "auto" allows per hour once learned) and how much the next
    reading may go up (None: no limit)."""
    learned = watch.learned
    return {
        "days": watch.days,
        "needed": COUNTER_LIMIT["min_days"],
        "per_hour": learned.per_hour if learned else None,
        "per_day": learned.per_day if learned else None,
        "rate": change_limit.learned_rate(learned),
        "allowed": change_limit.limit(settings, learned, watch.budget_s),
        "held": watch.held,
    }


def sensor_view(rt: Runtime, session: Session, sensor: Sensor) -> dict:
    """A sensor as the UI shows it: settings, status, live state, model and the parts of its kind."""
    live = rt.live.get(sensor.id)
    info = session.get(ModelInfo, sensor.id)
    head = rt.heads.get(sensor.id)
    counts = sample_counts(session, sensor)
    if sensor.kind in (KIND_OBJECTS, KIND_READING, KIND_SOUND):
        trained = True  # pretrained models, no training
    else:
        trained = head is not None and rt.embedder is not None and head.backbone == rt.embedder.spec.id
    if not sensor.enabled:
        status = "disabled"
    elif live and live.available is False:
        status = "unavailable"
    elif not trained:
        status = "untrained"
    else:
        status = "ok"
    ids = entity_ids(rt, sensor)
    now = time.time()
    paces = [merge_triggers(sensor.triggers)["burst_interval_s"]] if live and live.burst_until > now else []
    if live and live.face_burst.until > now:
        paces.append(FACES["burst_interval_s"])
    pace = min(paces) if paces else None
    return {
        "id": sensor.id,
        "slug": sensor.slug,
        "name": sensor.name,
        "kind": sensor.kind,
        "source_type": sensor.source_type,
        "source": sensor.source,
        "roi": sensor.roi,
        "interval_s": sensor.interval_s,
        "threshold": sensor.threshold,
        "debounce": sensor.debounce,
        "enabled": sensor.enabled,
        "publish": sensor.publish,
        "in_ha": sensor.in_ha is not False,
        "ha_entities": ha_entities_view(sensor),
        "triggers": merge_triggers(sensor.triggers),
        "review": {key: (sensor.review or {}).get(key) for key in REVIEW_DEFAULTS},
        "review_effective": merge_review(rt.global_review, sensor.review),
        "entity_id": ids[0] if ids else None,
        "entity_ids": ids,
        "objects": objects_view(session, sensor, live),
        "reading": reading_view(sensor, live),
        "sound": sound_view(sensor, live),
        "states": [{"id": s.id, "key": s.key, "name": s.name, "color": s.color} for s in sensor.states],
        "status": status,
        "trained": trained,
        "training": sensor.id in rt.training,
        "live": {
            "available": live.available if live else None,
            "error": live.error if live else "",
            "published": live.debouncer.published if live else None,
            "top": live.top if live else None,
            "confidence": live.confidence if live else 0.0,
            "probs": live.probs if live else {},
            "last_run": live.last_run if live else None,
            # A burst of checks (a trigger's, or looking for a face while a person is there) and its pace.
            "in_burst": pace is not None,
            "burst_interval_s": pace,
            "change_score": live.change_score if live else None,
            "last_trigger": live.last_trigger if live else None,
            "light_error": live.light_error if live else "",
            "frame_id": live.frame_id if live else None,
        },
        "model": {
            "backbone": info.backbone,
            "version": info.version,
            "trained_at": iso(info.trained_at),
            "n_samples": info.n_samples,
            "accuracy": info.accuracy,
            "train_seconds": info.train_seconds,
        }
        if info
        else None,
        "counts": counts,
    }
