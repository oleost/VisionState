"""Runtime engine: runs every sensor, trains heads and publishes results."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import delete, func, select

from . import backbones, classifier, detectors, imaging, readers, teach
from .db import Database, Embedding, ModelInfo, Prediction, ReadingStat, Sample, SampleLabel, Sensor, utcnow
from .ha_events import HaEventListener, fetch_entity_ids
from .lights import Lights
from .mqtt import REVIEW_TOPICS, MqttBridge, SensorDescriptor, object_topics, rate_unit, topics
from .redact import redact
from .settings import (
    DETECTION,
    KIND_OBJECTS,
    KIND_READING,
    KIND_STATES,
    READING,
    RUNTIME,
    STORAGE_DEFAULTS,
    TEACH,
    UNKNOWN_STATE,
    Settings,
    active_custom,
    merge_objects,
    merge_reading,
    merge_review,
    merge_triggers,
)
from .sources import FrameGrabber, HomeAssistant, SourceError
from .storage import Storage

log = logging.getLogger(__name__)

EMBED_BATCH = 16
OBJECT_BOX_COLOR = "#7ee2b8"  # boxes drawn on the Home Assistant image of an object sensor


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


def review_reason(
    rules: dict,
    confidence: float,
    threshold: float,
    recent_changes: int,
    seconds_since_flag: float,
    roll: float,
) -> str | None:
    """Decide whether a frame should go to the review queue, and why (rules: see merge_review)."""
    if not rules["enabled"] or seconds_since_flag < rules["cooldown_s"]:
        return None
    # Frames below the reporting threshold (reported as unknown) always qualify.
    if confidence < max(rules["below"], threshold):
        return "low_confidence"
    if recent_changes >= rules["flip_limit"]:
        return "flip"
    if roll < rules["spot_rate"]:
        return "spot_check"
    return None


@dataclass
class ObjectTrack:
    """Published state of one object class of an object sensor."""

    on: bool = False
    count: int = 0  # published count; kept while "on" until the object has cleared
    streak: int = 0  # checks in a row with the object present
    last_seen: float = 0.0
    score: float = 0.0  # best confidence in the last check


def update_tracks(
    tracks: dict[str, ObjectTrack],
    detections: list[dict],
    classes: list[str],
    required: int,
    clear_after_s: float,
    now: float,
) -> list[str]:
    """Advance each class after one check; returns the classes that switched on or off.

    An object switches on after ``required`` checks in a row with it present, and off once it
    has not been seen for ``clear_after_s`` seconds, so a person turning around does not flicker.
    ``classes`` may hold own labels too; filtered boxes count for nothing (see teach.counts_for).
    """
    changed = []
    for key in classes:
        track = tracks.setdefault(key, ObjectTrack())
        found = [d for d in detections if teach.counts_for(d, key)]
        track.score = max((d["score"] for d in found), default=0.0)
        if found:
            track.streak += 1
            track.last_seen = now
            if track.on or track.streak >= required:
                if not track.on:
                    changed.append(key)
                track.on, track.count = True, len(found)
        else:
            track.streak = 0
            if track.on and now - track.last_seen >= clear_after_s:
                track.on, track.count = False, 0
                changed.append(key)
    for key in [k for k in tracks if k not in classes]:
        del tracks[key]
    return changed


def filter_detections(
    found: list[detectors.Detection],
    analysed: imaging.Box,
    roi: dict | None,
    objects: dict,
    all_classes: bool = False,
) -> list[dict]:
    """Map detections from the analysed box to the whole frame and keep the ones that count.

    An object counts when its bottom centre (where a person or car stands) lies inside the
    region, it is big enough (share of the region's area) and its class is selected.
    """
    x1, y1, x2, y2 = analysed
    aw, ah = x2 - x1, y2 - y1
    rx1, ry1, rx2, ry2 = imaging.region_box(roi)
    region_area = (rx2 - rx1) * (ry2 - ry1)
    classes = set(objects["classes"])
    result = []
    for det in found:
        if not all_classes and det.key not in classes:
            continue
        bx1, by1, bx2, by2 = det.box
        box = (x1 + bx1 * aw, y1 + by1 * ah, x1 + bx2 * aw, y1 + by2 * ah)
        if (box[2] - box[0]) * (box[3] - box[1]) < objects["min_size"] * region_area:
            continue
        if not imaging.in_region((box[0] + box[2]) / 2, box[3], roi):
            continue
        result.append({"key": det.key, "score": round(det.score, 4), "box": [round(v, 4) for v in box]})
    return result


def entity_triggers(triggers: dict, entity_id: str, new: str | None) -> bool:
    """Whether this state change of a trigger entity starts a check (see TRIGGER_DEFAULTS["only_states"])."""
    wanted = triggers["only_states"].get(entity_id)
    return not wanted or (new or "").strip().lower() == wanted.strip().lower()


def next_check_at(cfg: SensorConfig, live: LiveState, now: float) -> tuple[float, str]:
    """When the sensor loop should wake up next, and whether that is a full check or a cheap probe."""
    if live.last_run is None:
        return now, "full"
    in_burst = now < live.burst_until
    if in_burst:
        full_at = live.last_run + cfg.triggers["burst_interval_s"]
    elif cfg.triggers["regular"]:
        # Counted from the last check, whatever caused it: frequent triggers postpone it.
        full_at = live.last_run + cfg.interval_s
    else:
        full_at = math.inf  # only triggers
    if cfg.triggers["change_detection"]:
        probe_at = max(live.last_run, live.last_probe) + cfg.triggers["change_interval_s"]
        if probe_at < full_at:
            return probe_at, "probe"
    return full_at, "full"


@dataclass
class LiveState:
    frames: deque = field(default_factory=lambda: deque(maxlen=RUNTIME["frame_cache_size"]))
    debouncer: Debouncer = field(default_factory=Debouncer)
    probs: dict[str, float] = field(default_factory=dict)
    top: str | None = None
    confidence: float = 0.0
    available: bool | None = None
    error: str = ""
    last_run: float | None = None
    last_flag: float = 0.0
    changes: deque = field(default_factory=lambda: deque(maxlen=50))
    force: bool = False
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


class Runtime:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.storage = Storage(settings)
        self.ha = HomeAssistant(settings)
        self.lights = Lights(lambda: self.ha)  # sensors' lights, shared by checks and open views
        self.grabber = FrameGrabber(self.ha)
        self.mqtt = MqttBridge(settings, self._on_command, self._on_mqtt_connect)
        self.ha_events = HaEventListener(settings, self._on_ha_state)
        self._entity_index: dict[str, set[int]] = {}  # trigger entity -> sensor ids
        # unique ID -> entity ID of our entities in Home Assistant's entity registry (empty outside HA)
        self.ha_entity_ids: dict[str, str] = {}
        self._registry_wanted = asyncio.Event()  # discovery was published: read the registry again
        self.global_review: dict = {}  # global review rules (DB setting "review")
        self.storage_rules: dict = {}  # history limits (DB setting "storage"); see storage_limits()
        self.history_trimmed = False  # frames were removed to stay under the size limit (until limits change)
        self.embedder: backbones.Embedder | None = None
        self.embedder_error = ""
        self.detector: detectors.Detector | None = None  # loaded on first use by an object sensor
        self.detector_error = ""
        self._detector_lock = asyncio.Lock()
        self._published_classes: dict[int, set[str]] = {}  # object sensor -> classes in discovery
        self.reader: readers.Reader | None = None  # loaded on first use by a reading sensor
        self.reader_error = ""
        self._reader_lock = asyncio.Lock()
        self.heads: dict[int, classifier.Head] = {}
        self.taught: dict[int, teach.TaughtIndex] = {}  # object sensors; built on use, dropped on change
        self._taught_generation: dict[int, int] = {}  # counts changes to each sensor's taught boxes
        self.live: dict[int, LiveState] = {}
        self.training: set[int] = set()
        self._tasks: dict[int, asyncio.Task] = {}
        self._wake: dict[int, asyncio.Event] = {}
        self._retrain_handles: dict[int, asyncio.TimerHandle] = {}
        self._background: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(RUNTIME["max_concurrent_inferences"])
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.global_review = await asyncio.to_thread(self.db.get_setting, "review", {}) or {}
        self.storage_rules = await asyncio.to_thread(self.db.get_setting, "storage", {}) or {}
        # Keep the models this installation uses, even when a later release recommends others.
        await asyncio.to_thread(self._pin_setting, "backbone", backbones.DEFAULT_BACKBONE)
        await asyncio.to_thread(self._pin_setting, "detector", detectors.DEFAULT_DETECTOR)
        await asyncio.to_thread(self._pin_setting, "reader", readers.DEFAULT_READER)
        backbone_id = self.db.get_setting("backbone", backbones.DEFAULT_BACKBONE)
        try:
            await self.load_embedder(backbone_id)
        except Exception as err:  # noqa: BLE001
            log.exception("Could not load backbone %s", backbone_id)
            self.embedder_error = str(err)
        for sensor_id in await asyncio.to_thread(self._sensor_ids, KIND_STATES):
            if self.heads_load(sensor_id):
                log.info("Sensor %s: model from another version, retraining from stored images", sensor_id)
                self.schedule_retrain(sensor_id, delay=0)
        for sensor_id in await asyncio.to_thread(self._sensor_ids):
            self._start_loop(sensor_id)
        await self.refresh_trigger_entities()
        self.ha_events.start()
        self.mqtt.start()
        self._spawn(self._cleanup_loop())
        self._spawn(self._light_sweep_loop())
        if self.ha_events.enabled:
            self._spawn(self._entity_registry_loop())

    async def stop(self) -> None:
        for task in [*self._tasks.values(), *self._background]:
            task.cancel()
        await self.mqtt.stop()
        await self.ha_events.stop()
        await self.grabber.close()
        await self.ha.close()

    async def _entity_registry_loop(self) -> None:
        """Keeps ha_entity_ids up to date: now and then, and shortly after discovery was published."""
        last_error = ""
        while True:
            try:
                self.ha_entity_ids = await fetch_entity_ids(self.ha_events.url, self.ha_events.token)
                last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the last known IDs and try again later
                error = str(err) or type(err).__name__
                if error != last_error:
                    log.warning("Could not read the entity IDs from Home Assistant: %s", error)
                last_error = error
            self._registry_wanted.clear()
            try:
                await asyncio.wait_for(self._registry_wanted.wait(), timeout=RUNTIME["entity_registry_refresh_s"])
            except TimeoutError:
                continue
            await asyncio.sleep(RUNTIME["entity_registry_delay_s"])  # let Home Assistant create the entities

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    # --- backbone ------------------------------------------------------------

    def model_path(self, spec: backbones.BackboneSpec) -> Path | None:
        return backbones.locate(spec, [self.settings.bundled_models_dir, self.settings.models_dir])

    async def load_embedder(self, backbone_id: str) -> None:
        spec = backbones.BACKBONES.get(backbone_id) or backbones.BACKBONES[backbones.DEFAULT_BACKBONE]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.embedder = await asyncio.to_thread(backbones.Embedder, spec, path)
        self.embedder_error = ""
        log.info("Backbone %s loaded", spec.id)

    async def set_backbone(self, backbone_id: str) -> None:
        await self.load_embedder(backbone_id)
        self.db.set_setting("backbone", backbone_id)
        for sensor_id in await asyncio.to_thread(self._sensor_ids, KIND_STATES):
            self.schedule_retrain(sensor_id, delay=0)

    def _pin_setting(self, key: str, default: str) -> None:
        if self.db.get_setting(key) is None:
            self.db.set_setting(key, default)

    # --- detector (object sensors) --------------------------------------------------

    async def load_detector(self, detector_id: str) -> None:
        spec = detectors.DETECTORS.get(detector_id) or detectors.DETECTORS[detectors.DEFAULT_DETECTOR]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.detector = await asyncio.to_thread(detectors.Detector, spec, path)
        self.detector_error = ""
        log.info("Detector %s loaded", spec.id)

    async def ensure_detector(self) -> detectors.Detector:
        """The detector, loaded on first use so installations without object sensors never pay for it."""
        async with self._detector_lock:
            if self.detector is None:
                detector_id = await asyncio.to_thread(self.db.get_setting, "detector", detectors.DEFAULT_DETECTOR)
                try:
                    await self.load_detector(detector_id)
                except Exception as err:
                    self.detector_error = redact(str(err))
                    log.exception("Could not load detector %s", detector_id)
                    raise
            return self.detector

    async def set_detector(self, detector_id: str) -> None:
        object_sensors = await asyncio.to_thread(self._sensor_ids, KIND_OBJECTS)
        if self.detector is not None or object_sensors:
            async with self._detector_lock:
                await self.load_detector(detector_id)
        await asyncio.to_thread(self.db.set_setting, "detector", detector_id)
        for sensor_id in object_sensors:
            self.wake(sensor_id, force=True)

    # --- reader (reading sensors) --------------------------------------------------------

    async def load_reader(self, reader_id: str) -> None:
        spec = readers.READERS.get(reader_id) or readers.READERS[readers.DEFAULT_READER]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.reader = await asyncio.to_thread(readers.Reader, spec, path)
        self.reader_error = ""
        log.info("Reader %s loaded", spec.id)

    async def ensure_reader(self) -> readers.Reader:
        """The number reader, loaded on first use so installations without reading sensors never pay for it."""
        async with self._reader_lock:
            if self.reader is None:
                reader_id = await asyncio.to_thread(self.db.get_setting, "reader", readers.DEFAULT_READER)
                try:
                    await self.load_reader(reader_id)
                except Exception as err:
                    self.reader_error = redact(str(err))
                    log.exception("Could not load reader %s", reader_id)
                    raise
            return self.reader

    async def set_reader(self, reader_id: str) -> None:
        reading_sensors = await asyncio.to_thread(self._sensor_ids, KIND_READING)
        if self.reader is not None or reading_sensors:
            async with self._reader_lock:
                await self.load_reader(reader_id)
        await asyncio.to_thread(self.db.set_setting, "reader", reader_id)
        for sensor_id in reading_sensors:
            self.wake(sensor_id, force=True)

    async def read_number(
        self, image: Image.Image, roi: dict | None, reading: dict
    ) -> tuple[readers.Text, Image.Image]:
        """Read the region of ``image``; returns the text and the image the reader used."""
        reader = await self.ensure_reader()
        region = imaging.crop_box(image, imaging.region_box(roi))
        async with self._sem:
            return await asyncio.to_thread(reader.read_display, region, reading["display"], int(reading["digits"]))

    async def detect_objects(
        self, image: Image.Image, roi: dict | None, objects: dict, threshold: float, all_classes: bool = False
    ) -> list[dict]:
        """Objects in the region of ``image`` (boxes normalised to the whole frame).

        The detector sees a margin around the region, so an object at the edge is seen whole and
        its box (and bottom centre) is not cut off by the crop.
        """
        detector = await self.ensure_detector()
        analysed = imaging.region_box(roi, DETECTION["context_margin"])
        region = imaging.crop_box(image, analysed)
        async with self._sem:
            found = await asyncio.to_thread(detector.detect, region, threshold)
        return filter_detections(found, analysed, roi, objects, all_classes)

    # --- teaching object sensors (see teach.py) ------------------------------------

    async def taught_index(self, cfg: SensorConfig) -> teach.TaughtIndex | None:
        """What the sensor was taught, ready to compare with; None when there is nothing to compare."""
        if not cfg.objects["use_taught"] or self.embedder is None:
            return None
        index = self.taught.get(cfg.id)
        generation = self._taught_generation.get(cfg.id, 0)
        # Rebuilt when the backbone, the own labels in use or the taught boxes changed. A check
        # may have loaded its settings before an own label was added, so the stale index it
        # builds is replaced on a later check instead of being kept.
        if (
            index is None
            or index.backbone != self.embedder.spec.id
            or index.parents != cfg.parents
            or index.generation != generation
        ):
            index = await asyncio.to_thread(self._build_taught, cfg, generation)
            self.taught[cfg.id] = index
        return index if index.ids else None

    def _build_taught(self, cfg: SensorConfig, generation: int = 0) -> teach.TaughtIndex:
        parents = cfg.parents
        with self.db.session() as s:
            examples = [
                x
                for x in s.scalars(
                    select(Sample)
                    .where(Sample.sensor_id == cfg.id, Sample.object_label.is_not(None))
                    .order_by(Sample.id)
                )
                if teach.usable(x.object_label, parents)
            ]
        vectors = self._taught_vectors(cfg, examples)
        return teach.build(
            self.embedder.spec.id,
            [x.id for x in examples],
            [x.object_label for x in examples],
            [x.detected for x in examples],
            vectors,
            parents,
            generation,
        )

    def _taught_vectors(self, cfg: SensorConfig, examples: list[Sample]) -> np.ndarray:
        """Embeddings of taught boxes, one image at a time (see teach.embed), cached in the DB."""
        embedder = self.embedder
        if embedder is None or not examples:
            return np.zeros((0, 0), dtype=np.float32)
        backbone_id = embedder.spec.id
        with self.db.session() as s:
            rows = s.scalars(
                select(Embedding).where(
                    Embedding.sample_id.in_([x.id for x in examples]),
                    Embedding.backbone == backbone_id,
                    Embedding.roi_key == teach.EMBEDDING_KEY,
                )
            ).all()
            cached = {r.sample_id: np.frombuffer(r.vector, dtype=np.float32) for r in rows}
        for x in examples:
            if x.id in cached:
                continue
            image = Image.open(self.storage.sample_path(cfg.id, x.filename)).convert("RGB")
            vector = teach.embed(embedder, [image])[0]
            with self.db.session() as s:
                s.merge(
                    Embedding(
                        sample_id=x.id, backbone=backbone_id, roi_key=teach.EMBEDDING_KEY, vector=vector.tobytes()
                    )
                )
            cached[x.id] = vector
        return np.stack([cached[x.id] for x in examples])

    async def apply_taught(
        self, cfg: SensorConfig, image: Image.Image, index: teach.TaughtIndex, found: list[dict], candidates: list[dict]
    ) -> list[dict]:
        checked, rescue = teach.plan(found, candidates, index)
        crops = [teach.crop(image, found[i]["box"]) for i in checked] + [teach.crop(image, d["box"]) for d in rescue]
        embedder = self.embedder
        if not crops or embedder is None:
            return found
        async with self._sem:
            vectors = await asyncio.to_thread(teach.embed, embedder, crops)
        n = len(checked)
        return teach.apply(found, checked, vectors[:n], rescue, vectors[n:], index, cfg.objects["classes"], cfg.parents)

    def taught_changed(self, sensor_id: int) -> None:
        """Taught boxes were added or removed: compare with the new set from the next check on."""
        self._taught_generation[sensor_id] = self._taught_generation.get(sensor_id, 0) + 1
        self.taught.pop(sensor_id, None)
        self.wake(sensor_id, force=True)

    def add_object_example(
        self,
        sensor_id: int,
        image: Image.Image,
        box: list[float],
        label: str,
        detected: str | None,
        score: float | None,
        origin: str,
        cropped: bool = False,
    ) -> int:
        """Store a taught box (its crop) as a training image of an object sensor.

        ``image`` is the whole frame, or with ``cropped`` already the crop (an imported sensor).
        """
        crop = image if cropped else teach.crop(image, box)
        filename = self.storage.save_sample(sensor_id, crop)
        with self.db.session() as s:
            sample = Sample(
                sensor_id=sensor_id,
                filename=filename,
                origin=origin,
                use_roi=False,
                is_night=imaging.is_night(crop),
                width=crop.width,
                height=crop.height,
                object_label=label,
                detected=detected,
                box=[round(float(v), 4) for v in box],
                score=round(float(score), 4) if score is not None else None,
            )
            s.add(sample)
            s.flush()
            return sample.id

    async def seen_faintly(self, cfg: SensorConfig, image: Image.Image, box: list[float]) -> bool:
        """Whether the detector sees anything at all where the user drew a box it missed."""
        found = await self.detect_objects(image, cfg.roi, cfg.objects, TEACH["seen_floor"], all_classes=True)
        return teach.seen_faintly(found, box)

    # --- sensors -------------------------------------------------------------

    def _sensor_ids(self, kind: str | None = None) -> list[int]:
        with self.db.session() as s:
            query = select(Sensor.id)
            if kind is not None:
                query = query.where(Sensor.kind == kind)
            return list(s.scalars(query))

    def load_sensor(self, sensor_id: int) -> SensorConfig | None:
        with self.db.session() as s:
            row = s.get(Sensor, sensor_id)
            return SensorConfig.from_row(row) if row else None

    def live_state(self, sensor_id: int) -> LiveState:
        return self.live.setdefault(sensor_id, LiveState())

    def _start_loop(self, sensor_id: int) -> None:
        self._wake.setdefault(sensor_id, asyncio.Event())
        if sensor_id not in self._tasks or self._tasks[sensor_id].done():
            self._tasks[sensor_id] = asyncio.create_task(self._sensor_loop(sensor_id), name=f"sensor-{sensor_id}")

    async def sensor_created(self, sensor_id: int) -> None:
        cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
        if cfg:
            await self.publish_discovery(cfg)
        self._start_loop(sensor_id)
        await self.refresh_trigger_entities()

    async def sensor_updated(self, sensor_id: int, retrain: bool) -> None:
        cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
        if cfg is None:
            return
        await self.publish_discovery(cfg)
        if retrain:
            self.schedule_retrain(sensor_id, delay=0)
        self.taught.pop(sensor_id, None)  # own labels or classes may have changed
        self.live_state(sensor_id).signature = None  # the region may have moved
        await self.refresh_trigger_entities()
        self.wake(sensor_id)

    async def sensor_deleted(self, sensor_id: int, descriptor: SensorDescriptor) -> None:
        task = self._tasks.pop(sensor_id, None)
        if task:
            task.cancel()
        self.live.pop(sensor_id, None)
        self.heads.pop(sensor_id, None)
        self.taught.pop(sensor_id, None)
        self._published_classes.pop(sensor_id, None)
        (self.settings.heads_dir / f"{sensor_id}.joblib").unlink(missing_ok=True)
        await self.mqtt.remove_discovery(descriptor)
        await asyncio.to_thread(self.storage.delete_sensor, sensor_id)
        await self.publish_review_count()
        await self.refresh_trigger_entities()
        if descriptor.kind == KIND_OBJECTS and not await asyncio.to_thread(self._sensor_ids, KIND_OBJECTS):
            self.detector = None  # the last object sensor is gone: free the memory
        if descriptor.kind == KIND_READING and not await asyncio.to_thread(self._sensor_ids, KIND_READING):
            self.reader = None

    def _on_loop(self, func, *args) -> bool:
        """Run ``func`` on the event loop. Returns True when called from another thread (deferred).

        Sync API endpoints and ``asyncio.to_thread`` workers run in threads; asyncio objects
        (events, timers) may only be touched from the loop thread.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is not None and running is not self._loop:
            self._loop.call_soon_threadsafe(func, *args)
            return True
        return False

    def wake(self, sensor_id: int, force: bool = False) -> None:
        if self._on_loop(self.wake, sensor_id, force):
            return
        if force:
            self.live_state(sensor_id).force = True
        event = self._wake.get(sensor_id)
        if event:
            event.set()

    async def _sensor_loop(self, sensor_id: int) -> None:
        event = self._wake[sensor_id]
        while True:
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg is None:
                return
            live = self.live_state(sensor_id)
            due_at, kind = next_check_at(cfg, live, time.time())
            if live.force or (cfg.enabled and due_at <= time.time()):
                forced, live.force = live.force, False
                try:
                    if forced or kind == "full":
                        await self.run_once(cfg)
                    else:
                        await self.probe(cfg)
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001
                    live.error = redact(str(err))
                    log.error("Sensor %s failed: %s", cfg.slug, live.error)
                due_at, _ = next_check_at(cfg, live, time.time())
            timeout = max(0.05, due_at - time.time()) if cfg.enabled else cfg.interval_s
            if math.isinf(timeout):
                timeout = None  # no regular check: sleep until something wakes the sensor
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except TimeoutError:
                pass
            event.clear()

    # --- review rules -----------------------------------------------------------

    def review_rules(self, cfg: SensorConfig) -> dict:
        return merge_review(self.global_review, cfg.review)

    def set_global_review(self, rules: dict) -> None:
        self.global_review = dict(rules)
        self.db.set_setting("review", self.global_review)

    # --- triggers --------------------------------------------------------------

    def _trigger_index(self) -> dict[str, set[int]]:
        index: dict[str, set[int]] = {}
        with self.db.session() as s:
            for row in s.scalars(select(Sensor)):
                for entity in merge_triggers(row.triggers)["entities"]:
                    index.setdefault(entity, set()).add(row.id)
        return index

    async def refresh_trigger_entities(self) -> None:
        self._entity_index = await asyncio.to_thread(self._trigger_index)
        self.ha_events.set_entities(set(self._entity_index))

    def start_burst(self, sensor_id: int, source: str, detail: str) -> None:
        """Check now, then keep checking at the burst pace for the configured duration."""
        cfg = self.load_sensor(sensor_id)
        if cfg is None or not cfg.enabled:
            return
        live = self.live_state(sensor_id)
        now = time.time()
        live.burst_until = now + cfg.triggers["burst_duration_s"]
        live.last_trigger = {"source": source, "detail": detail, "at": now}
        self.wake(sensor_id, force=True)

    def _entity_changed(self, sensor_id: int, entity_id: str, old: str | None, new: str | None) -> None:
        cfg = self.load_sensor(sensor_id)
        if cfg is None or not entity_triggers(cfg.triggers, entity_id, new):
            return
        log.debug("Sensor %s triggered by %s: %s -> %s", sensor_id, entity_id, old, new)
        self.start_burst(sensor_id, "entity", f"{entity_id}: {old} → {new}")

    async def _on_ha_state(self, entity_id: str, old: str | None, new: str | None) -> None:
        for sensor_id in self._entity_index.get(entity_id, set()):
            await asyncio.to_thread(self._entity_changed, sensor_id, entity_id, old, new)

    # --- light ---------------------------------------------------------------------

    async def _light_before(self, cfg: SensorConfig) -> float | None:
        """Hold the sensor's light on for a check (see lights.py).

        Returns the seconds until it is bright, or None without a light (or when it could not be
        switched on: the check goes on without it).
        """
        entity = cfg.triggers["light_entity"]
        live = self.live_state(cfg.id)
        if not entity:
            return None
        if live.light_off_task:
            live.light_off_task.cancel()  # still held from the previous check of a burst
            live.light_off_task = None
        light = await self.lights.hold(entity, f"check:{cfg.id}")
        if light.error:  # the check goes on without the light
            if light.error != live.light_error:
                log.warning("Sensor %s: could not switch on %s: %s", cfg.slug, entity, light.error)
            live.light_error = light.error
            return None
        live.light_error = ""
        return self.lights.wait_s(entity, cfg.triggers["light_delay_s"]) or 0.0

    async def _grab_in_light(self, cfg: SensorConfig, wait: float) -> tuple[str, bytes]:
        """A frame taken in the light, ``wait`` seconds after it came on.

        Many cameras hand out a picture they took before (an ESP32 camera keeps one ready, taken
        right after the previous one was fetched — up to 10 s earlier by default), and only
        adjust their exposure between pictures. So while the light warms up, frames are fetched
        and thrown away: the stale one goes, and the camera adjusts to the light. The frame after
        that is the one checked. An RTSP stream is live already: it just waits.
        """
        if cfg.source_type == "rtsp":
            if wait:
                await asyncio.sleep(wait)
            return await self.grab(cfg)
        deadline = time.time() + wait
        while True:
            await self.grabber.grab(cfg.source_type, cfg.source)  # thrown away
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(RUNTIME["light_warmup_interval_s"], remaining))
            if deadline - time.time() <= 0:
                break
        return await self.grab(cfg)

    def _light_after(self, cfg: SensorConfig) -> None:
        """Let go of the light — after the burst, so it does not flash for every check of it."""
        entity = cfg.triggers["light_entity"]
        live = self.live_state(cfg.id)
        if not entity or live.light_off_task or not self.lights.held(entity, f"check:{cfg.id}"):
            return
        wait = max(0.0, live.burst_until - time.time())

        async def off() -> None:
            await asyncio.sleep(wait)
            live.light_off_task = None
            await self.lights.release(entity, f"check:{cfg.id}")

        live.light_off_task = self._spawn(off())

    async def _light_sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(RUNTIME["light_sweep_s"])
            await self.lights.sweep()

    def frame_for_view(self, sensor_id: int) -> tuple[str, bytes] | None:
        """For a sensor with a light: the frame of its last check, unless the light is on right now.

        The UI must not take frames in the dark; while a view holds the light (and it had time to
        get bright), it shows fresh frames like any other sensor.
        """
        cfg = self.load_sensor(sensor_id)
        live = self.live_state(sensor_id)
        if cfg is None or not cfg.triggers["light_entity"]:
            return None
        if self.lights.wait_s(cfg.triggers["light_entity"], cfg.triggers["light_delay_s"]) == 0.0:
            return None
        return live.frames[-1] if live.frames else None

    async def probe(self, cfg: SensorConfig) -> None:
        """Cheap check: compare the region with the last classified frame; classify only if it changed."""
        live = self.live_state(cfg.id)
        live.last_probe = time.time()
        entity = cfg.triggers["light_entity"]
        if entity and self.lights.wait_s(entity, 0.0) is not None:
            # The light is on (a burst of checks, or someone looking): a frame now is lit and the
            # baseline is not, so every comparison would look like a change. Compare later.
            return
        try:
            frame_id, data = await self.grab(cfg)
            image = await asyncio.to_thread(imaging.decode, data)
        except (SourceError, OSError) as err:
            live.available, live.error = False, redact(str(err))
            return
        # With a light, the probe frame (taken without it) is only for comparing: the check
        # itself takes a new frame in the light.
        lit = bool(cfg.triggers["light_entity"])
        if live.signature is None:
            # No baseline (first run or the region changed). With a light this frame is the
            # baseline (checks in the light never set one); without, check it, which sets one.
            if lit:
                live.signature = await asyncio.to_thread(imaging.region_signature, image, cfg.roi)
            else:
                await self.run_once(cfg, data=data, image=image, frame_id=frame_id)
            return
        signature = await asyncio.to_thread(imaging.region_signature, image, cfg.roi)
        score = imaging.change_score(live.signature, signature)
        live.change_score = score
        if score >= cfg.triggers["change_threshold"]:
            now = time.time()
            live.burst_until = now + cfg.triggers["burst_duration_s"]
            live.last_trigger = {"source": "change", "detail": f"{score:.1%} of the region changed", "at": now}
            if lit:
                live.signature = signature
                await self.run_once(cfg)
            else:
                await self.run_once(cfg, data=data, image=image, frame_id=frame_id)

    async def grab(self, cfg: SensorConfig) -> tuple[str, bytes]:
        data = await self.grabber.grab(cfg.source_type, cfg.source)
        return self.live_state(cfg.id).remember(data), data

    async def run_once(
        self,
        cfg: SensorConfig,
        data: bytes | None = None,
        image: Image.Image | None = None,
        frame_id: str | None = None,
    ) -> None:
        live = self.live_state(cfg.id)
        t = topics(cfg.slug)
        live.last_run = time.time()
        lit = False  # the frame was taken with the sensor's light on
        try:
            if data is None:
                wait = await self._light_before(cfg)
                try:
                    if wait is None:
                        frame_id, data = await self.grab(cfg)
                    else:
                        frame_id, data = await self._grab_in_light(cfg, wait)
                        lit = True
                finally:
                    self._light_after(cfg)
            if image is None:
                image = await asyncio.to_thread(imaging.decode, data)
        except (SourceError, OSError) as err:
            if live.available is not False:
                log.warning("Sensor %s: camera unavailable: %s", cfg.slug, err)
            live.available, live.error = False, redact(str(err))
            await self._send(cfg, t["availability"], "offline", retain=True)
            return
        live.available, live.error = True, ""
        # Change detection compares frames without the light (probe), so a frame taken in the
        # light is no baseline for it: every probe would look like a change.
        if not lit:
            signature = await asyncio.to_thread(imaging.region_signature, image, cfg.roi)
            if live.signature is not None:
                live.change_score = imaging.change_score(live.signature, signature)
            live.signature = signature
        live.frame_id = frame_id
        await self._send(cfg, t["availability"], "online", retain=True)
        if cfg.is_objects:
            live.analysed.append((frame_id, data))
            await self._run_objects(cfg, image)
            return
        if cfg.is_reading:
            await self._run_reading(cfg, image)
            return

        probs = await self.classify_image(cfg, image)
        if probs:
            top, confidence = max(probs.items(), key=lambda kv: kv[1])
        else:
            top, confidence = UNKNOWN_STATE, 0.0
        live.probs, live.top, live.confidence = probs, top, confidence
        value = top if probs and confidence >= cfg.threshold else UNKNOWN_STATE
        changed = live.debouncer.update(value, cfg.debounce)
        now = time.time()
        if changed:
            live.changes.append(now)

        crop = imaging.crop(image, cfg.roi)
        await self._send(cfg, t["state"], live.debouncer.published or UNKNOWN_STATE, retain=True)
        await self._send(cfg, t["confidence"], f"{confidence * 100:.1f}", retain=True)
        await self._send(
            cfg,
            t["attributes"],
            {
                "probabilities": {k: round(v, 4) for k, v in probs.items()},
                "top_state": top,
                "last_update": datetime.now(UTC).isoformat(),
                "trained": bool(probs),
                "last_trigger": live.last_trigger,
            },
            retain=True,
        )
        await self._send(cfg, t["image"], await asyncio.to_thread(imaging.encode_jpeg, crop, 80), retain=True)

        reason = None
        if probs:
            rules = self.review_rules(cfg)
            recent = sum(1 for ts in live.changes if now - ts <= rules["flip_window_s"])
            reason = review_reason(rules, confidence, cfg.threshold, recent, now - live.last_flag, random.random())
            if reason:
                live.last_flag = now
        if changed or reason:
            await asyncio.to_thread(
                self._record_prediction,
                cfg.id,
                image,
                top,
                live.debouncer.published,
                confidence,
                probs,
                changed,
                reason,
            )
            if reason:
                await self.publish_review_count()

    async def _run_objects(self, cfg: SensorConfig, image: Image.Image) -> None:
        """One check of an object sensor: detect, compare with what was taught, update and publish."""
        live = self.live_state(cfg.id)
        classes = cfg.objects["classes"]
        index = await self.taught_index(cfg)
        rescue = bool(index and index.rescue)
        # Once a box the detector missed was taught, its unsure boxes are looked at too.
        floor = min(cfg.threshold, TEACH["rescue_floor"]) if rescue else cfg.threshold
        try:
            found = await self.detect_objects(image, cfg.roi, cfg.objects, floor, all_classes=rescue)
        except Exception as err:  # noqa: BLE001 - detector missing or failed to load
            live.error = f"Object detector unavailable: {redact(str(err))}"
            return
        candidates: list[dict] = []
        if rescue:
            candidates = [d for d in found if d["score"] < cfg.threshold]
            found = [d for d in found if d["score"] >= cfg.threshold and d["key"] in classes]
        if index is not None:
            found = await self.apply_taught(cfg, image, index, found, candidates)
        counted = [d for d in found if not d.get("filtered")]
        now = time.time()
        live.detections = found
        best = max(counted, key=lambda d: d["score"], default=None)
        live.top, live.confidence = (best.get("label") or best["key"], best["score"]) if best else (None, 0.0)
        keys = cfg.object_keys
        changed = update_tracks(live.tracks, found, keys, cfg.debounce, cfg.objects["clear_after_s"], now)
        live.changes.extend([now] * len(changed))

        for key in keys:
            track = live.tracks[key]
            ot = object_topics(cfg.slug, key)
            attributes = {
                "confidence": round(track.score, 4),
                "boxes": [d["box"] for d in found if teach.counts_for(d, key)],
                "last_seen": datetime.fromtimestamp(track.last_seen, UTC).isoformat() if track.last_seen else None,
                "last_trigger": live.last_trigger,
            }
            if index is not None:
                attributes["filtered"] = sum(1 for d in found if d.get("filtered") and d["key"] == key)
            await self._send(cfg, ot["state"], "ON" if track.on else "OFF", retain=True)
            await self._send(cfg, ot["count"], str(track.count), retain=True)
            await self._send(cfg, ot["attributes"], attributes, retain=True)
        names = {key: label.name for key, label in detectors.LABELS.by_key.items()}
        names.update({c["key"]: c["name"] for c in cfg.custom_labels})
        drawn = [{**d, "key": d.get("label") or d["key"]} for d in counted]
        annotated = await asyncio.to_thread(
            lambda: imaging.crop_box(
                imaging.draw_detections(image, drawn, names, OBJECT_BOX_COLOR), imaging.region_box(cfg.roi)
            )
        )
        jpeg = await asyncio.to_thread(imaging.encode_jpeg, annotated, 80)
        await self._send(cfg, topics(cfg.slug)["image"], jpeg, retain=True)
        for key in changed:
            track = live.tracks[key]
            published = "on" if track.on else "off"
            await asyncio.to_thread(self._record_detection, cfg.id, image, key, published, track.score, found)
        await self._record_filtered(cfg, image, found, now)

    async def _record_filtered(self, cfg: SensorConfig, image: Image.Image, found: list[dict], now: float) -> None:
        """Keep a frame in the history when a class starts being filtered away (not every check)."""
        live = self.live_state(cfg.id)
        filtered = {d["key"] for d in found if d.get("filtered")}
        for key in sorted(filtered - live.filtered_keys):
            if now - live.filtered_logged.get(key, 0.0) < TEACH["filtered_record_cooldown_s"]:
                continue
            live.filtered_logged[key] = now
            score = max(d["score"] for d in found if d.get("filtered") and d["key"] == key)
            await asyncio.to_thread(self._record_detection, cfg.id, image, key, "filtered", score, found)
        live.filtered_keys = filtered

    def _record_detection(self, sensor_id, image, key, published, score, found) -> None:
        """A history row of an object sensor: a class or own label went "on" or "off", or was "filtered"."""
        frame = self.storage.save_history(sensor_id, image)
        with self.db.session() as s:
            s.add(
                Prediction(
                    sensor_id=sensor_id,
                    state_key=key,
                    published_key=published,
                    confidence=score,
                    probs={},
                    frame=frame,
                    is_change=True,
                    reviewed=True,  # nothing to review: the detector is not trained here
                    detections=found,
                )
            )

    async def _run_reading(self, cfg: SensorConfig, image: Image.Image) -> None:
        """One check of a reading sensor: read the number, check it and publish it.

        A value is published after ``debounce`` equal readings in a row. Unsure, empty and
        implausible readings (a counter going down, a jump above ``max_step``, a mechanical
        counter read with another number of digits than it has wheels) are rejected:
        the last value stays and the rejected reading is kept in the history and sent to the
        review queue, every one of them. Every reading is counted per day (Quality tab).
        """
        live = self.live_state(cfg.id)
        settings = cfg.reading
        if not live.reading_restored:
            live.reading_restored = True
            if live.debouncer.published is None:
                live.debouncer.published = await asyncio.to_thread(self._last_reading, cfg.id)
        try:
            text, used = await self.read_number(image, cfg.roi, settings)
        except Exception as err:  # noqa: BLE001 - reader missing or failed to load
            live.error = f"Number reader unavailable: {redact(str(err))}"
            return
        now = time.time()
        value = readers.parse(text.text, settings)
        last = float(live.debouncer.published) if live.debouncer.published is not None else None
        if value is None:
            reason = "nothing read"
        elif readers.wrong_digit_count(text.text, settings):
            reason = "wrong digit count"
        elif text.score < cfg.threshold:
            reason = "unsure"
        else:
            reason = readers.implausible(value, last, settings)
        # One step below the value: the last wheel turning (see readers.settling). The value stays,
        # but that is no rejection.
        settling = reason == "went down" and readers.settling(value, last, settings)
        if settling:
            reason = None
        shown = readers.format_value(value, settings)
        live.reading = {
            "text": text.text,
            "score": round(text.score, 4),
            "value": shown,
            "reason": reason,
            "settling": settling,
            "at": now,
        }
        live.reading_image = await asyncio.to_thread(imaging.encode_jpeg, used, 85)
        live.top, live.confidence = shown, text.score

        t = topics(cfg.slug)
        changed = False
        if reason is None and not settling:  # never let a lower reading through the debounce
            changed = live.debouncer.update(shown, cfg.debounce)
            if changed:
                live.changes.append(now)
        if live.debouncer.published is not None:
            await self._send(cfg, t["state"], live.debouncer.published, retain=True)
        await self._send(cfg, t["confidence"], f"{text.score * 100:.1f}", retain=True)
        await self._send(
            cfg,
            t["attributes"],
            {
                "read_text": text.text,
                "last_update": datetime.now(UTC).isoformat(),
                "rejected": reason,
                "last_trigger": live.last_trigger,
            },
            retain=True,
        )
        crop = imaging.crop_box(image, imaging.region_box(cfg.roi))
        await self._send(cfg, t["image"], await asyncio.to_thread(imaging.encode_jpeg, crop, 80), retain=True)
        await self._send_reading_diagnostics(cfg, text.text, shown, reason, now)

        await asyncio.to_thread(self._count_reading, cfg.id, reason)
        spot = reason is None and not changed and random.random() < float(settings["spot_rate"])
        if changed or reason is not None or spot:
            published = live.debouncer.published if reason is None else None
            details = {"text": text.text, "value": shown, "reason": reason}
            review = "rejected" if reason is not None else "spot_check" if spot else None
            await asyncio.to_thread(
                self._record_reading, cfg.id, image, published, text.score, details, changed, review
            )
            if review:
                await self.publish_review_count()

    async def _send_reading_diagnostics(
        self, cfg: SensorConfig, text: str, shown: str | None, reason: str | None, now: float
    ) -> None:
        """The diagnostic entities of a reading sensor: raw reading, problem, accepted share, rate."""
        live = self.live_state(cfg.id)
        settings = cfg.reading
        t = topics(cfg.slug)
        await self._send(cfg, t["raw"], shown or text or "-", retain=True)
        await self._send(cfg, t["problem"], readers.problem_key(reason), retain=True)
        live.reads.append((now, reason is None))
        share = readers.accepted_share(live.reads, now, READING["accepted_window_s"])
        if share is not None:
            await self._send(cfg, t["accepted"], f"{share:g}", retain=True)
        if settings["mode"] != "counter":
            return
        if reason is None and live.debouncer.published is not None:
            live.rate_samples.append((now, float(live.debouncer.published)))
        window = float(settings["rate_window_min"]) * 60
        per_hour = readers.counter_rate(live.rate_samples, now, window, READING["rate_min_span_s"])
        if per_hour is not None:
            _, _, factor = rate_unit(settings)
            await self._send(cfg, t["rate"], f"{per_hour * factor:.4g}", retain=True)

    def _count_reading(self, sensor_id: int, reason: str | None) -> None:
        day = datetime.now().date().isoformat()  # local time: "today" as the user sees it
        with self.db.session() as s:
            row = s.get(ReadingStat, (sensor_id, day))
            if row is None:
                row = ReadingStat(sensor_id=sensor_id, day=day, reads=0, accepted=0, rejected={})
                s.add(row)
            row.reads += 1
            if reason is None:
                row.accepted += 1
            else:
                row.rejected = {**row.rejected, reason: row.rejected.get(reason, 0) + 1}

    def _record_reading(self, sensor_id, image, published, score, details, changed, review=None) -> None:
        frame = self.storage.save_history(sensor_id, image)
        with self.db.session() as s:
            s.add(
                Prediction(
                    sensor_id=sensor_id,
                    state_key="reading",
                    published_key=published,
                    confidence=score,
                    probs=details,
                    frame=frame,
                    is_change=changed,
                    review_reason=review,
                    reviewed=review is None,
                )
            )

    def _last_reading(self, sensor_id: int) -> str | None:
        """The last published value of a reading sensor (so a restart keeps checking against it)."""
        with self.db.session() as s:
            return s.scalar(
                select(Prediction.published_key)
                .where(Prediction.sensor_id == sensor_id, Prediction.published_key.is_not(None))
                .order_by(Prediction.created_at.desc())
                .limit(1)
            )

    def _record_prediction(self, sensor_id, image, top, published, confidence, probs, changed, reason) -> None:
        frame = self.storage.save_history(sensor_id, image)
        with self.db.session() as s:
            s.add(
                Prediction(
                    sensor_id=sensor_id,
                    state_key=top,
                    published_key=published,
                    confidence=confidence,
                    probs={k: round(v, 4) for k, v in probs.items()},
                    frame=frame,
                    is_change=changed,
                    review_reason=reason,
                    reviewed=reason is None,
                )
            )

    async def classify_image(self, cfg: SensorConfig, image: Image.Image) -> dict[str, float]:
        head = self.heads.get(cfg.id)
        if head is None or self.embedder is None or head.backbone != self.embedder.spec.id:
            return {}
        crop = imaging.crop(image, cfg.roi)
        async with self._sem:
            vector = await asyncio.to_thread(self.embedder.embed, [crop])
        return head.predict(vector, cfg.state_keys)[0]

    async def _send(self, cfg: SensorConfig, topic: str, payload, retain: bool = False) -> None:
        """Publish one of a sensor's values — unless it is not to be sent to Home Assistant."""
        if cfg.publish:
            await self.mqtt.publish(topic, payload, retain=retain)

    async def publish_discovery(self, cfg: SensorConfig) -> None:
        t = topics(cfg.slug)
        if cfg.is_objects:
            current = set(cfg.object_keys)
            for key in self._published_classes.get(cfg.id, set()) - current:
                await self.mqtt.remove_object_class(cfg.slug, key)  # deselected: remove its entities
            self._published_classes[cfg.id] = current
        if cfg.is_reading and cfg.reading["mode"] != "counter":
            await self.mqtt.remove_rate(cfg.slug)  # only counters have a rate
        await self.mqtt.publish_discovery(cfg.descriptor)
        self._registry_wanted.set()
        await self.mqtt.publish(t["enabled"], "ON" if cfg.enabled else "OFF", retain=True)
        live = self.live.get(cfg.id)
        # Not sent to Home Assistant: its entities are unavailable (no values, no statistics). Sent
        # again: available right away when the camera was, not only after the next check.
        if not cfg.publish:
            await self.mqtt.publish(t["availability"], "offline", retain=True)
        elif live and live.available:
            await self.mqtt.publish(t["availability"], "online", retain=True)
        if cfg.is_objects:
            for key, track in (live.tracks if live else {}).items():
                await self._send(cfg, object_topics(cfg.slug, key)["state"], "ON" if track.on else "OFF", retain=True)
        elif live and live.debouncer.published is not None:
            # Re-send the last state so it is not lost when discovery is (re)published.
            await self._send(cfg, t["state"], live.debouncer.published, retain=True)

    def _review_counts(self) -> tuple[int, dict[str, int]]:
        with self.db.session() as s:
            rows = s.execute(
                select(Sensor.name, func.count(Prediction.id))
                .join(Prediction, Prediction.sensor_id == Sensor.id)
                .where(Prediction.reviewed.is_(False))
                .group_by(Sensor.name)
            ).all()
        per_sensor = {name: count for name, count in rows}
        return sum(per_sensor.values()), per_sensor

    async def publish_review_count(self) -> None:
        total, per_sensor = await asyncio.to_thread(self._review_counts)
        await self.mqtt.publish(REVIEW_TOPICS["count"], str(total), retain=True)
        await self.mqtt.publish(REVIEW_TOPICS["attributes"], {"per_sensor": per_sensor}, retain=True)

    async def _on_mqtt_connect(self) -> None:
        await self.mqtt.publish_hub_discovery()
        await self.publish_review_count()
        for sensor_id in await asyncio.to_thread(self._sensor_ids):
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg:
                await self.publish_discovery(cfg)
                self.wake(sensor_id)

    async def _on_command(self, slug: str, command: str, payload: str) -> None:
        with self.db.session() as s:
            row = s.scalar(select(Sensor).where(Sensor.slug == slug))
            if row is None:
                return
            sensor_id = row.id
            if command == "enabled":
                row.enabled = payload.upper() == "ON"
        if command == "classify":
            self.wake(sensor_id, force=True)
        elif command == "enabled":
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg:
                await self.publish_discovery(cfg)
            self.wake(sensor_id)

    # --- training ------------------------------------------------------------

    def heads_load(self, sensor_id: int) -> bool:
        """Loads a sensor's head. Returns True when it must be retrained (missing, unreadable or outdated)."""
        path = self.settings.heads_dir / f"{sensor_id}.joblib"
        head = classifier.load(path)
        if head is not None:
            self.heads[sensor_id] = head  # keep using it until the retrain finishes
        backbone = self.embedder.spec.id if self.embedder else None
        return path.exists() and (head is None or not classifier.is_current(head, backbone))

    def schedule_retrain(self, sensor_id: int, delay: float | None = None) -> None:
        """Retrain soon; repeated calls within the delay are coalesced into one run."""
        if self._on_loop(self.schedule_retrain, sensor_id, delay):
            self.training.add(sensor_id)  # show "training" right away
            return
        loop = asyncio.get_running_loop()
        handle = self._retrain_handles.pop(sensor_id, None)
        if handle:
            handle.cancel()
        wait = RUNTIME["retrain_delay_s"] if delay is None else delay
        self.training.add(sensor_id)
        self._retrain_handles[sensor_id] = loop.call_later(wait, lambda: self._spawn(self.retrain(sensor_id)))

    async def retrain(self, sensor_id: int) -> None:
        self._retrain_handles.pop(sensor_id, None)
        self.training.add(sensor_id)
        try:
            async with self._sem:
                await asyncio.to_thread(self._retrain_sync, sensor_id)
        except Exception:  # noqa: BLE001
            log.exception("Training sensor %s failed", sensor_id)
        finally:
            if sensor_id not in self._retrain_handles:
                self.training.discard(sensor_id)
        self.wake(sensor_id, force=True)

    def _labelled_samples(self, sensor_id: int) -> tuple[SensorConfig | None, list[Sample], list[str]]:
        with self.db.session() as s:
            row = s.get(Sensor, sensor_id)
            if row is None:
                return None, [], []
            cfg = SensorConfig.from_row(row)
            key_by_state = {st["id"]: st["key"] for st in cfg.states}
            samples, labels = [], []
            for sample in s.scalars(select(Sample).where(Sample.sensor_id == sensor_id).order_by(Sample.id)):
                keys = [key_by_state[lab.state_id] for lab in sample.labels if lab.state_id in key_by_state]
                if keys:
                    samples.append(sample)
                    labels.append(keys[0])
            return cfg, samples, labels

    def _retrain_sync(self, sensor_id: int) -> None:
        if self.embedder is None:
            return
        cfg, samples, labels = self._labelled_samples(sensor_id)
        if cfg is None or cfg.kind != KIND_STATES:
            return
        vectors = self.vectors_for(cfg, samples)
        with self.db.session() as s:
            info = s.get(ModelInfo, sensor_id)
            version = (info.version if info else 0) + 1
        result = classifier.train(vectors, labels, self.embedder.spec.id, version, cfg.state_keys)
        suspects = [
            {"sample_id": samples[item["index"]].id, **{k: v for k, v in item.items() if k != "index"}}
            for item in result.suspects
            if not samples[item["index"]].verified
        ]
        head_path = self.settings.heads_dir / f"{sensor_id}.joblib"
        if result.head is None:
            self.heads.pop(sensor_id, None)
            head_path.unlink(missing_ok=True)
        else:
            classifier.save(result.head, head_path)
            self.heads[sensor_id] = result.head
        with self.db.session() as s:
            info = s.get(ModelInfo, sensor_id) or ModelInfo(sensor_id=sensor_id)
            info.backbone = self.embedder.spec.id
            info.version = version
            info.trained_at = utcnow()
            info.n_samples = result.n_samples
            info.accuracy = result.accuracy
            info.confusion = result.confusion
            info.suspects = suspects
            info.train_seconds = result.seconds
            s.merge(info)
        log.info(
            "Sensor %s trained on %d samples in %.2fs (accuracy %s)",
            cfg.slug,
            result.n_samples,
            result.seconds,
            f"{result.accuracy:.1%}" if result.accuracy is not None else "n/a",
        )

    def vectors_for(self, cfg: SensorConfig, samples: list[Sample]) -> np.ndarray:
        """Embeddings for samples, computing and caching the missing ones."""
        embedder = self.embedder
        if embedder is None or not samples:
            return np.zeros((0, 0), dtype=np.float32)
        backbone_id = embedder.spec.id
        keys = {s.id: imaging.roi_key(cfg.roi if s.use_roi else None) for s in samples}
        with self.db.session() as s:
            rows = s.scalars(
                select(Embedding).where(Embedding.sample_id.in_(list(keys)), Embedding.backbone == backbone_id)
            ).all()
            cached = {
                r.sample_id: np.frombuffer(r.vector, dtype=np.float32) for r in rows if r.roi_key == keys[r.sample_id]
            }
        missing = [sample for sample in samples if sample.id not in cached]
        for start in range(0, len(missing), EMBED_BATCH):
            batch = missing[start : start + EMBED_BATCH]
            images = []
            for sample in batch:
                image = Image.open(self.storage.sample_path(cfg.id, sample.filename)).convert("RGB")
                images.append(imaging.crop(image, cfg.roi) if sample.use_roi else image)
            vectors = embedder.embed(images)
            with self.db.session() as s:
                for sample, vector in zip(batch, vectors, strict=False):
                    s.execute(
                        delete(Embedding).where(Embedding.sample_id == sample.id, Embedding.backbone == backbone_id)
                    )
                    s.add(
                        Embedding(
                            sample_id=sample.id, backbone=backbone_id, roi_key=keys[sample.id], vector=vector.tobytes()
                        )
                    )
                    cached[sample.id] = vector
        return np.stack([cached[sample.id] for sample in samples])

    def suggest(self, cfg: SensorConfig, samples: list[Sample]) -> dict[int, tuple[str, float]]:
        """Predicted state for (unlabelled) samples, used as upload suggestions."""
        head = self.heads.get(cfg.id)
        if not samples or head is None or self.embedder is None or head.backbone != self.embedder.spec.id:
            return {}
        vectors = self.vectors_for(cfg, samples)
        result = {}
        for sample, probs in zip(samples, head.predict(vectors, cfg.state_keys), strict=False):
            key, p = max(probs.items(), key=lambda kv: kv[1])
            result[sample.id] = (key, p)
        return result

    # --- samples ----------------------------------------------------------------

    def add_sample(
        self, sensor_id: int, image: Image.Image, origin: str, state_id: int | None, use_roi: bool = True
    ) -> int:
        filename = self.storage.save_sample(sensor_id, image)
        with self.db.session() as s:
            sample = Sample(
                sensor_id=sensor_id,
                filename=filename,
                origin=origin,
                use_roi=use_roi,
                is_night=imaging.is_night(image),
                width=image.width,
                height=image.height,
            )
            if state_id is not None:
                sample.labels.append(SampleLabel(state_id=state_id))
            s.add(sample)
            s.flush()
            return sample.id

    # --- housekeeping ------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.cleanup_history)
                await self.publish_review_count()
            except Exception:  # noqa: BLE001
                log.exception("Cleanup failed")
            await asyncio.sleep(RUNTIME["cleanup_interval_s"])

    # --- history limits --------------------------------------------------------------

    def storage_limits(self) -> dict:
        """Effective history limits (Settings → Storage), with the defaults for what is not set."""
        return {
            "history_days": int(self.storage_rules.get("history_days") or STORAGE_DEFAULTS["history_days"]),
            "history_max_gb": float(self.storage_rules.get("history_max_gb", STORAGE_DEFAULTS["history_max_gb"])),
        }

    def set_storage_limits(self, rules: dict) -> None:
        self.storage_rules = dict(rules)
        self.history_trimmed = False  # the next clean-up tells whether the new size limit still bites
        self.db.set_setting("storage", self.storage_rules)

    def _history_file_size(self, row: Prediction) -> int:
        size = 0
        for path in (
            self.storage.history_path(row.sensor_id, row.frame) if row.frame else None,
            self.storage.thumb_path("history", row.id),
        ):
            if path is not None and path.exists():
                size += path.stat().st_size
        return size

    def cleanup_history(self) -> None:
        limits = self.storage_limits()
        days = limits["history_days"]
        cutoff = utcnow() - timedelta(days=days)
        review_cutoff = utcnow() - timedelta(days=days * 2)
        with self.db.session() as s:
            old = s.scalars(
                select(Prediction).where(
                    Prediction.created_at < cutoff,
                    (Prediction.reviewed.is_(True)) | (Prediction.created_at < review_cutoff),
                    Prediction.read_ok.is_(None),  # verified readings are kept, like training images
                )
            ).all()
            for row in old:
                self.storage.delete_history(row.sensor_id, row.id, row.frame)
                s.delete(row)
        if old:
            log.info("Removed %d old history frames", len(old))
        trimmed = self._trim_history(limits["history_max_gb"])
        if trimmed:
            self.history_trimmed = True
            log.info("Removed %d history frames to stay under %.1f GB", trimmed, limits["history_max_gb"])

    def _trim_history(self, max_gb: float) -> int:
        """Remove the oldest history frames until all of them fit in ``max_gb`` (0 = no limit).

        Reviewed frames go first, frames still waiting for review only when that is not enough;
        verified readings are never removed. Returns how many rows were removed.
        """
        if max_gb <= 0:
            return 0
        budget = int(max_gb * 1024**3)
        used = self.storage.history_bytes()
        if used <= budget:
            return 0
        removed = 0
        with self.db.session() as s:
            for waiting in (False, True):
                rows = s.scalars(
                    select(Prediction)
                    .where(Prediction.reviewed.is_(not waiting), Prediction.read_ok.is_(None))
                    .order_by(Prediction.created_at)
                ).all()
                for row in rows:
                    if used <= budget:
                        return removed
                    used -= self._history_file_size(row)
                    self.storage.delete_history(row.sensor_id, row.id, row.frame)
                    s.delete(row)
                    removed += 1
                s.flush()
        return removed

    def storage_usage(self) -> dict:
        """Disk use for the Settings page: history, training images and free space."""
        import shutil

        with self.db.session() as s:
            frames = s.scalar(select(func.count()).select_from(Prediction).where(Prediction.frame.is_not(None))) or 0
            oldest = s.scalar(select(func.min(Prediction.created_at)).where(Prediction.frame.is_not(None)))
            images = s.scalar(select(func.count()).select_from(Sample)) or 0
        media = self.settings.media_dir
        media.mkdir(parents=True, exist_ok=True)
        return {
            "history_bytes": self.storage.history_bytes(),
            "history_frames": frames,
            "oldest_history": oldest,
            "training_bytes": self.storage.samples_bytes(),
            "training_images": images,
            "free_bytes": shutil.disk_usage(media).free,
            "limited_by_size": self.history_trimmed,
            **self.storage_limits(),
        }
