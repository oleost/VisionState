"""The runtime engine: lifecycle, sensors and triggers; the rest comes from the mixins."""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select

from .. import backbones, detectors, readers
from ..db import Database, Sensor
from ..ha_events import HaEventListener, fetch_entity_ids
from ..mqtt import MqttBridge, SensorDescriptor
from ..settings import KIND_OBJECTS, KIND_READING, KIND_STATES, RUNTIME, Settings, merge_triggers
from .checks import ChecksMixin
from .history import HistoryMixin
from .logic import entity_triggers

log = logging.getLogger(__name__)


class Runtime(ChecksMixin, HistoryMixin):
    """Runs every sensor, trains heads and publishes results; one instance per app.

    The parts live in mixins, one file each, on top of ``RuntimeBase`` (base.py: the shared state).
    Each part inherits the parts it uses, so ChecksMixin brings in objects, reading, teaching,
    models, training and publishing; HistoryMixin the clean-up.
    """

    def __init__(self, settings: Settings, db: Database):
        super().__init__(settings, db)
        self.mqtt = MqttBridge(settings, self._on_command, self._on_mqtt_connect)
        self.ha_events = HaEventListener(settings, self._on_ha_state)

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
        for handle in self._retrain_handles.values():
            handle.cancel()
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

    def set_global_review(self, rules: dict) -> None:
        self.global_review = dict(rules)
        self.db.set_setting("review", self.global_review)

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
