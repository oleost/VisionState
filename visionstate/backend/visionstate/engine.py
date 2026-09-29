"""Runtime engine: runs every sensor, trains heads and publishes results."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import delete, select

from . import backbones, classifier, imaging
from .db import Database, Embedding, ModelInfo, Prediction, Sample, SampleLabel, Sensor, utcnow
from .ha_events import HaEventListener
from .mqtt import MqttBridge, SensorDescriptor, topics
from .redact import redact
from .settings import RUNTIME, UNKNOWN_STATE, Settings, merge_review, merge_triggers
from .sources import FrameGrabber, HomeAssistant, SourceError
from .storage import Storage

log = logging.getLogger(__name__)

EMBED_BATCH = 16


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

    @property
    def state_keys(self) -> list[str]:
        return [s["key"] for s in self.states]

    @property
    def descriptor(self) -> SensorDescriptor:
        return SensorDescriptor(self.slug, self.name, self.state_keys)

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


def next_check_at(cfg: SensorConfig, live: LiveState, now: float) -> tuple[float, str]:
    """When the sensor loop should wake up next, and whether that is a full check or a cheap probe."""
    if live.last_run is None:
        return now, "full"
    in_burst = now < live.burst_until
    full_at = live.last_run + (cfg.triggers["burst_interval_s"] if in_burst else cfg.interval_s)
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

    def remember(self, data: bytes) -> str:
        frame_id = f"{time.time_ns():x}"
        self.frames.append((frame_id, data))
        return frame_id

    def frame(self, frame_id: str | None) -> bytes | None:
        for fid, data in reversed(self.frames):
            if frame_id is None or fid == frame_id:
                return data
        return None


class Runtime:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.storage = Storage(settings)
        self.ha = HomeAssistant(settings)
        self.grabber = FrameGrabber(self.ha)
        self.mqtt = MqttBridge(settings, self._on_command, self._on_mqtt_connect)
        self.ha_events = HaEventListener(settings, self._on_ha_state)
        self._entity_index: dict[str, set[int]] = {}  # trigger entity -> sensor ids
        self.global_review: dict = {}  # global review rules (DB setting "review")
        self.embedder: backbones.Embedder | None = None
        self.embedder_error = ""
        self.heads: dict[int, classifier.Head] = {}
        self.live: dict[int, LiveState] = {}
        self.training: set[int] = set()
        self._tasks: dict[int, asyncio.Task] = {}
        self._wake: dict[int, asyncio.Event] = {}
        self._retrain_handles: dict[int, asyncio.TimerHandle] = {}
        self._background: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(RUNTIME["max_concurrent_inferences"])

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self.global_review = await asyncio.to_thread(self.db.get_setting, "review", {}) or {}
        backbone_id = self.db.get_setting("backbone", backbones.DEFAULT_BACKBONE)
        provider = self.db.get_setting("execution_provider", "CPUExecutionProvider")
        try:
            await self.load_embedder(backbone_id, provider)
        except Exception as err:  # noqa: BLE001
            log.exception("Could not load backbone %s", backbone_id)
            self.embedder_error = str(err)
        for sensor_id in await asyncio.to_thread(self._sensor_ids):
            self.heads_load(sensor_id)
            self._start_loop(sensor_id)
        await self.refresh_trigger_entities()
        self.ha_events.start()
        self.mqtt.start()
        self._spawn(self._cleanup_loop())

    async def stop(self) -> None:
        for task in [*self._tasks.values(), *self._background]:
            task.cancel()
        await self.mqtt.stop()
        await self.ha_events.stop()
        await self.grabber.close()
        await self.ha.close()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    # --- backbone ------------------------------------------------------------

    def model_path(self, spec: backbones.BackboneSpec) -> Path | None:
        return backbones.locate(spec, [self.settings.bundled_models_dir, self.settings.models_dir])

    async def load_embedder(self, backbone_id: str, provider: str) -> None:
        spec = backbones.BACKBONES.get(backbone_id) or backbones.BACKBONES[backbones.DEFAULT_BACKBONE]
        path = self.model_path(spec)
        if path is None:
            path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
        self.embedder = await asyncio.to_thread(backbones.Embedder, spec, path, provider)
        self.embedder_error = ""
        log.info("Backbone %s loaded (%s)", spec.id, self.embedder.provider)

    async def set_backbone(self, backbone_id: str, provider: str) -> None:
        await self.load_embedder(backbone_id, provider)
        self.db.set_setting("backbone", backbone_id)
        self.db.set_setting("execution_provider", provider)
        for sensor_id in await asyncio.to_thread(self._sensor_ids):
            self.schedule_retrain(sensor_id, delay=0)

    # --- sensors -------------------------------------------------------------

    def _sensor_ids(self) -> list[int]:
        with self.db.session() as s:
            return list(s.scalars(select(Sensor.id)))

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
        self.live_state(sensor_id).signature = None  # the region may have moved
        await self.refresh_trigger_entities()
        self.wake(sensor_id)

    async def sensor_deleted(self, sensor_id: int, descriptor: SensorDescriptor) -> None:
        task = self._tasks.pop(sensor_id, None)
        if task:
            task.cancel()
        self.live.pop(sensor_id, None)
        self.heads.pop(sensor_id, None)
        (self.settings.heads_dir / f"{sensor_id}.joblib").unlink(missing_ok=True)
        await self.mqtt.remove_discovery(descriptor)
        await asyncio.to_thread(self.storage.delete_sensor, sensor_id)
        await self.refresh_trigger_entities()

    def wake(self, sensor_id: int, force: bool = False) -> None:
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

    async def _on_ha_state(self, entity_id: str, old: str | None, new: str | None) -> None:
        for sensor_id in self._entity_index.get(entity_id, set()):
            log.debug("Sensor %s triggered by %s: %s -> %s", sensor_id, entity_id, old, new)
            await asyncio.to_thread(self.start_burst, sensor_id, "entity", f"{entity_id}: {old} → {new}")

    async def probe(self, cfg: SensorConfig) -> None:
        """Cheap check: compare the region with the last classified frame; classify only if it changed."""
        live = self.live_state(cfg.id)
        live.last_probe = time.time()
        try:
            _, data = await self.grab(cfg)
            image = await asyncio.to_thread(imaging.decode, data)
        except (SourceError, OSError) as err:
            live.available, live.error = False, redact(str(err))
            return
        if live.signature is None:
            # No baseline (first run or the region changed): classify this frame, which sets one.
            await self.run_once(cfg, data=data, image=image)
            return
        signature = await asyncio.to_thread(imaging.region_signature, image, cfg.roi)
        score = imaging.change_score(live.signature, signature)
        live.change_score = score
        if score >= cfg.triggers["change_threshold"]:
            now = time.time()
            live.burst_until = now + cfg.triggers["burst_duration_s"]
            live.last_trigger = {"source": "change", "detail": f"{score:.1%} of the region changed", "at": now}
            await self.run_once(cfg, data=data, image=image)

    async def grab(self, cfg: SensorConfig) -> tuple[str, bytes]:
        data = await self.grabber.grab(cfg.source_type, cfg.source)
        return self.live_state(cfg.id).remember(data), data

    async def run_once(self, cfg: SensorConfig, data: bytes | None = None, image: Image.Image | None = None) -> None:
        live = self.live_state(cfg.id)
        t = topics(cfg.slug)
        live.last_run = time.time()
        try:
            if data is None:
                _, data = await self.grab(cfg)
            if image is None:
                image = await asyncio.to_thread(imaging.decode, data)
        except (SourceError, OSError) as err:
            if live.available is not False:
                log.warning("Sensor %s: camera unavailable: %s", cfg.slug, err)
            live.available, live.error = False, redact(str(err))
            await self.mqtt.publish(t["availability"], "offline", retain=True)
            return
        live.available, live.error = True, ""
        signature = await asyncio.to_thread(imaging.region_signature, image, cfg.roi)
        if live.signature is not None:
            live.change_score = imaging.change_score(live.signature, signature)
        live.signature = signature
        await self.mqtt.publish(t["availability"], "online", retain=True)

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
        await self.mqtt.publish(t["state"], live.debouncer.published or UNKNOWN_STATE, retain=True)
        await self.mqtt.publish(t["confidence"], f"{confidence * 100:.1f}", retain=True)
        await self.mqtt.publish(
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
        await self.mqtt.publish(t["image"], await asyncio.to_thread(imaging.encode_jpeg, crop, 80), retain=True)

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

    async def publish_discovery(self, cfg: SensorConfig) -> None:
        t = topics(cfg.slug)
        await self.mqtt.publish_discovery(cfg.descriptor)
        await self.mqtt.publish(t["enabled"], "ON" if cfg.enabled else "OFF", retain=True)

    async def _on_mqtt_connect(self) -> None:
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

    def heads_load(self, sensor_id: int) -> None:
        head = classifier.load(self.settings.heads_dir / f"{sensor_id}.joblib")
        if head is not None:
            self.heads[sensor_id] = head

    def schedule_retrain(self, sensor_id: int, delay: float | None = None) -> None:
        """Retrain soon; repeated calls within the delay are coalesced into one run."""
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
        if cfg is None:
            return
        vectors = self.vectors_for(cfg, samples)
        with self.db.session() as s:
            info = s.get(ModelInfo, sensor_id)
            version = (info.version if info else 0) + 1
        result = classifier.train(vectors, labels, self.embedder.spec.id, version, cfg.state_keys)
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
                await asyncio.to_thread(self._cleanup)
            except Exception:  # noqa: BLE001
                log.exception("Cleanup failed")
            await asyncio.sleep(RUNTIME["cleanup_interval_s"])

    def _cleanup(self) -> None:
        days = self.settings.history_retention_days
        cutoff = utcnow() - timedelta(days=days)
        review_cutoff = utcnow() - timedelta(days=days * 2)
        with self.db.session() as s:
            old = s.scalars(
                select(Prediction).where(
                    Prediction.created_at < cutoff,
                    (Prediction.reviewed.is_(True)) | (Prediction.created_at < review_cutoff),
                )
            ).all()
            for row in old:
                self.storage.delete_history(row.sensor_id, row.id, row.frame)
                s.delete(row)
        if old:
            log.info("Removed %d old history frames", len(old))
