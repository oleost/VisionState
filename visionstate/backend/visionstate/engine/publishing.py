"""Home Assistant over MQTT: discovery, values, the review queue entity and commands."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from PIL import Image
from sqlalchemy import func, select

from .. import imaging
from ..db import Prediction, Sensor
from ..mqtt import REVIEW_TOPICS, SensorDescriptor, entity_options, object_topics, topics
from ..settings import APP_SLUG, HA_ENTITIES_NOT_BY_DEFAULT, PUBLISH
from .base import RuntimeBase
from .state import SensorConfig

log = logging.getLogger(__name__)


class PublishingMixin(RuntimeBase):
    @staticmethod
    def _sends(cfg: SensorConfig, topic: str) -> bool:
        """Whether this value of a sensor goes to Home Assistant (the sensor's Home Assistant tab)."""
        return cfg.publish and cfg.in_ha and topic not in cfg.muted_topics

    async def _send(self, cfg: SensorConfig, topic: str, payload: str | bytes | dict, retain: bool = False) -> None:
        """Publish one of a sensor's values — unless it is not to be sent to Home Assistant, or it
        is what was sent last (sent again every PUBLISH["refresh_s"] all the same, and after MQTT
        (re)connects: publish_discovery forgets what was sent). Home Assistant would only throw an
        unchanged value away."""
        if not self._sends(cfg, topic):
            return
        live = self.live_state(cfg.id)
        now = time.time()
        sent = live.sent_on_change.get(topic)
        if sent is not None and sent[0] == payload and now - sent[1] < PUBLISH["refresh_s"]:
            return
        await self.mqtt.publish(topic, payload, retain=retain)
        live.sent_on_change[topic] = (payload, now)

    async def _send_on_change(
        self, cfg: SensorConfig, topic: str, shows: object, make: Callable[[], bytes | dict]
    ) -> None:
        """A picture, or attributes that change a little at every check (an object's boxes and
        when it was last seen): sent when what they are about changed (``shows``: the state, the
        objects, the value), otherwise only every PUBLISH["refresh_s"].

        Home Assistant stores the state again for every picture an image entity gets (its state
        is the time of the picture) and for attributes that changed, so sending them at every
        check filled its database. ``make`` builds the payload only when it is sent. After MQTT
        (re)connects, publish_discovery forgets what was sent, so the next check sends it again.
        """
        if not self._sends(cfg, topic):
            return
        live = self.live_state(cfg.id)
        now = time.time()
        sent = live.sent_on_change.get(topic)
        if sent is not None and sent[0] == shows and now - sent[1] < PUBLISH["refresh_s"]:
            return
        await self.mqtt.publish(topic, await asyncio.to_thread(make), retain=True)
        live.sent_on_change[topic] = (shows, now)

    async def _send_snapshot(self, cfg: SensorConfig, key: str, image: Image.Image, boxes: list, count: int) -> None:
        """An object's picture: sent when its count changed, before its state goes on (so an
        automation that starts finds this one), and kept when it has gone (the broker retains it)."""
        topic = object_topics(cfg.slug, key)["snapshot"]
        live = self.live_state(cfg.id)
        if not count:
            live.sent_on_change.pop(topic, None)
            return
        sent = live.sent_on_change.get(topic)
        if not boxes or (sent is not None and sent[0] == count) or not self._sends(cfg, topic):
            return

        def make() -> bytes:
            region = imaging.region_box(cfg.roi)
            picture = imaging.snapshot(image, boxes, region, PUBLISH["snapshot_margin"], PUBLISH["snapshot_min_px"])
            return imaging.encode_jpeg(picture, PUBLISH["snapshot_quality"])

        await self.mqtt.publish(topic, await asyncio.to_thread(make), retain=True)
        live.sent_on_change[topic] = (count, time.time())

    def _unsettled(self) -> list[tuple[int, SensorDescriptor]]:
        """The sensors from before their entities could be chosen."""
        with self.db.session() as s:
            rows = s.scalars(select(Sensor).where(Sensor.ha_entities.is_(None))).all()
            return [(row.id, SensorConfig.from_row(row).descriptor) for row in rows]

    async def settle_entity_choices(self, enabled: set[str]) -> None:
        """Once, for a sensor from before its entities could be chosen: those Home Assistant has
        switched on stay, the ones it has off go (they were not in use). From then on the sensor's
        Home Assistant tab alone decides. ``enabled``: the unique IDs that are on there."""
        unsettled = await asyncio.to_thread(self._unsettled)
        if not unsettled or not self.mqtt.connected:
            return
        for sensor_id, before in unsettled:
            uid = f"{APP_SLUG}_{before.slug}"
            chosen = {
                option["id"]: True
                for option in entity_options(before)
                if option["id"] in HA_ENTITIES_NOT_BY_DEFAULT and f"{uid}_{option['id']}" in enabled
            }
            await self.mqtt.remove_confirmed(self.mqtt.removals(before, replace(before, chosen=chosen)))
            await asyncio.to_thread(self._store_entity_choices, sensor_id, chosen)
            log.info("%s: its diagnostic entities in Home Assistant are settled: %s", before.name, sorted(chosen))

    def _store_entity_choices(self, sensor_id: int, chosen: dict[str, bool]) -> None:
        with self.db.session() as s:
            row = s.get(Sensor, sensor_id)
            if row is not None and row.ha_entities is None:  # the user may have chosen meanwhile
                row.ha_entities = chosen

    async def publish_discovery(self, cfg: SensorConfig) -> None:
        """Tell Home Assistant about a sensor's entities (again), and repeat what it last knew.

        Called when a sensor is made or changed and when MQTT (re)connects. Entities of object
        classes no longer selected are removed; the availability, the pause switch and the last
        value are sent again, so nothing waits for the next check.
        """
        if not cfg.in_ha:
            return  # nothing of it in Home Assistant; what it had went when that was chosen
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
        if cfg.id in self.live:
            self.live[cfg.id].sent_on_change.clear()  # sent again at the next check (e.g. publishing switched on)
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

    def _review_counts(self) -> tuple[int, dict[str, int], datetime | None]:
        """Frames waiting for review: in all, per sensor, and when the oldest of them came (UTC)."""
        with self.db.session() as s:
            rows = s.execute(
                select(Sensor.name, func.count(Prediction.id), func.min(Prediction.created_at))
                .join(Prediction, Prediction.sensor_id == Sensor.id)
                .where(Prediction.reviewed.is_(False))
                .group_by(Sensor.name)
            ).all()
        per_sensor = {name: count for name, count, _ in rows}
        oldest = min((first for _, _, first in rows), default=None)
        if oldest is not None and oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        return sum(per_sensor.values()), per_sensor, oldest

    async def publish_review_count(self) -> None:
        total, per_sensor, oldest = await asyncio.to_thread(self._review_counts)
        await self.mqtt.publish(REVIEW_TOPICS["count"], str(total), retain=True)
        attributes = {"per_sensor": per_sensor, "oldest_waiting_since": oldest.isoformat() if oldest else None}
        await self.mqtt.publish(REVIEW_TOPICS["attributes"], attributes, retain=True)
        self._reminder_wanted.set()  # the queue changed: the reminder may be due, or may go away

    async def _on_mqtt_connect(self) -> None:
        await self.mqtt.publish_hub_discovery()
        await self.publish_review_count()
        for sensor_id in await asyncio.to_thread(self._sensor_ids):
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg:
                await self.publish_discovery(cfg)
                self.wake(sensor_id)

    def _apply_command(self, slug: str, command: str, payload: str) -> int | None:
        """The sensor a command is for (None: no such sensor); stores "enabled"."""
        with self.db.session() as s:
            row = s.scalar(select(Sensor).where(Sensor.slug == slug))
            if row is None:
                return None
            if command == "enabled":
                value = payload.strip().upper()
                if value not in ("ON", "OFF"):  # anything else is not from Home Assistant's switch
                    log.warning("Sensor %s: ignored %r on its pause switch (expected ON or OFF)", slug, payload[:20])
                    return None
                row.enabled = value == "ON"
            return row.id

    async def _on_command(self, slug: str, command: str, payload: str) -> None:
        sensor_id = await asyncio.to_thread(self._apply_command, slug, command, payload)
        if sensor_id is None:
            return
        if command == "classify":
            self.wake(sensor_id, force=True, paused_too=True)
        elif command == "enabled":
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg:
                await self.publish_discovery(cfg)
            self.wake(sensor_id)
