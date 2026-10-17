"""One check of a reading sensor: read the number, check it, publish it, keep history."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, date, datetime, timedelta

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import change_limit, imaging, readers
from ..db import Prediction, ReadingStat, ReadingWatch, Sensor
from ..mqtt import rate_unit, topics
from ..redact import redact
from ..settings import COUNTER_LIMIT, READING, REMINDER, VERSION
from .base import RuntimeBase
from .logic import reading_verdict
from .models import ModelsMixin
from .publishing import PublishingMixin
from .state import SensorConfig

log = logging.getLogger(__name__)

CAMERA_CHANGED = "camera changed"  # the rejection reason while a camera's picture has another size


class ReadingChecksMixin(ModelsMixin, PublishingMixin, RuntimeBase):
    async def _run_reading(self, cfg: SensorConfig, image: Image.Image, lit: bool = False) -> None:
        """One check of a reading sensor: read the number, check it and publish it.

        A value is published after ``debounce`` equal readings in a row. Unsure, empty and
        implausible readings (a counter going down or up faster than its limit, change_limit.py;
        another value changing more than ``max_step``; a mechanical counter read with another
        number of digits than it has wheels) are rejected: the last value stays and the rejected
        reading is kept in the history and sent to the review queue — once while the same one waits
        there (same reason and value: _repeat_rejected). A
        counter's readings over its limit are accepted after all when enough of them agree.
        Every reading is counted per day (Quality tab).
        ``lit``: the frame was taken with the sensor's light switched on (kept with the reading).
        """
        live = self.live_state(cfg.id)
        settings = cfg.reading
        counter = settings["mode"] == "counter"
        if not live.reading_restored:
            live.reading_restored = True
            restored = await asyncio.to_thread(self._restore_reading, cfg.id, live.watch)
            if live.debouncer.published is None:
                live.debouncer.published = restored
        if await self._camera_changed(cfg, image, lit):
            return
        try:
            text, used = await self.read_number(image, cfg.roi, settings)
        except Exception as err:  # noqa: BLE001 - reader missing or failed to load
            live.error = f"Number reader unavailable: {redact(str(err))}"
            return
        now = time.time()
        value = readers.parse(text.text, settings)
        last = float(live.debouncer.published) if live.debouncer.published is not None else None
        watch = live.watch
        limit = None
        if counter:
            change_limit.advance(watch, now, cfg.interval_s)
            await asyncio.to_thread(self._learn_limit, cfg.id, watch, now)
            limit = change_limit.limit(settings, watch.learned, watch.budget_s)
        reason, settling = reading_verdict(text, value, last, cfg.threshold, settings, limit)
        # Over the limit stays rejected, however many readings agree: only an answer in Review moves it on.
        if counter and reason in change_limit.IMPLAUSIBLE and value is not None and last is not None:
            change_limit.hold(watch)
        shown = readers.format_value(value, settings)
        live.reading = {
            "text": text.text,
            "score": round(text.score, 4),
            "value": shown,
            "reason": reason,
            "settling": settling,
            "at": now,
        }
        live.reading_image = reader_jpeg = await asyncio.to_thread(imaging.encode_jpeg, used, 85)
        live.top, live.confidence = shown, text.score

        changed = False
        if reason is None and not settling and shown is not None:  # never let a lower reading through
            changed = live.debouncer.update(shown, cfg.debounce)
        if changed:
            live.changes.append(now)
        # The limit starts again when a reading confirms the published value — not with one that
        # still waits for its debounce: until then the published value is the older one.
        learned = None
        published = live.debouncer.published
        if counter and reason is None and value is not None:
            if published is not None and (changed or settling or shown == published):
                change_limit.accept(watch, now)
            learned = change_limit.observe(watch, now, value, cfg.interval_s)
        await self._publish_reading(cfg, image, reader_jpeg, text, shown, reason, now)
        await self._keep_reading(cfg, image, text, shown, reason, changed, lit, learned)

    async def _camera_changed(self, cfg: SensorConfig, image: Image.Image, lit: bool) -> bool:
        """Hold the readings while the camera's picture has another size than the region was drawn on.

        The region is stored as a share of the picture, so a camera that comes back from a restart
        with another size (an ESP32 camera falling back to its default, which also crops another
        part of its sensor) makes the region show something else, and the reader reads that. The
        first frame after the region or source changed sets the size (db.Sensor.frame_size). A
        frame of another size is not read: the last value stays, the problem is "camera_changed",
        the frame is kept in the history once and Home Assistant gets a notification. It ends by
        itself when the size is back, or when the user accepts the new picture or changes the
        region (both clear the stored size). Returns whether the readings are held.
        """
        live = self.live_state(cfg.id)
        size = list(image.size)
        known = cfg.frame_size
        if known is None:
            await asyncio.to_thread(self._set_frame_size, cfg.id, size)
        if known is None or known == size or cfg.roi is None:  # the whole picture: nothing moves
            if live.camera_changed is not None:
                log.info("%s: reading again, with the camera's picture at %dx%d", cfg.name, *size)
                live.camera_changed = None
                await self.camera_notice(cfg, None)
            return False
        now = time.time()
        held = live.camera_changed
        if held is None or held["to"] != size:
            log.warning(
                "%s: the camera's picture changed from %dx%d to %dx%d: readings are held until the region is checked",
                cfg.name,
                *known,
                *size,
            )
            live.camera_changed = held = {"from": known, "to": size, "since": now}
            details = {"text": "", "value": None, "reason": CAMERA_CHANGED, "roi": cfg.roi, "app": VERSION, "lamp": lit}
            await asyncio.to_thread(self._record_reading, cfg.id, image, None, 0.0, details, False)
            await self.camera_notice(cfg, held)
        live.reading = {"text": "", "score": 0.0, "value": None, "reason": CAMERA_CHANGED, "settling": False, "at": now}
        crop = imaging.crop_box(image, imaging.region_box(cfg.roi))
        live.reading_image = await asyncio.to_thread(imaging.encode_jpeg, crop, 85)
        await asyncio.to_thread(self._count_reading, cfg.id, CAMERA_CHANGED)
        await self._publish_reading(cfg, image, live.reading_image, readers.Text("", 0.0), None, CAMERA_CHANGED, now)
        return True

    def _set_frame_size(self, sensor_id: int, size: list[int]) -> None:
        with self.db.session() as s:
            sensor = s.get(Sensor, sensor_id)
            if sensor is not None:
                sensor.frame_size = size

    async def camera_notice(self, cfg: SensorConfig, held: dict | None) -> None:
        """The notification in Home Assistant that a camera's picture changed (None: take it away)."""
        if not self.ha.enabled:
            return
        notification_id = READING["camera_notification_id"] + cfg.slug
        try:
            if held is None:
                await self.ha.call_service("persistent_notification", "dismiss", {"notification_id": notification_id})
                return
            text = (
                f"{cfg.name}: the camera's picture changed from {held['from'][0]}×{held['from'][1]} to "
                f"{held['to'][0]}×{held['to'][1]} pixels, so the region may show something else. "
                "Readings are held until the size is back, or until you check the region in VisionState."
            )
            try:
                link = await self.ha.app_path()
            except Exception:  # noqa: BLE001 - a notification without a link is still one
                link = None
            message = f"{text}\n\n[Open VisionState]({link})" if link else text
            data = {"title": REMINDER["title"], "message": message, "notification_id": notification_id}
            await self.ha.call_service("persistent_notification", "create", data)
        except Exception as err:  # noqa: BLE001 - Home Assistant restarting: the problem entity still says it
            log.warning("Could not send the notification about %s's camera: %s", cfg.name, redact(str(err)))

    async def _publish_reading(
        self,
        cfg: SensorConfig,
        image: Image.Image,
        reader_jpeg: bytes,
        text: readers.Text,
        shown: str | None,
        reason: str | None,
        now: float,
    ) -> None:
        """Send a reading sensor's value (the last accepted one), confidence, images and diagnostics.

        ``reader_jpeg`` is what the reader saw (the region after display processing)."""
        live = self.live_state(cfg.id)
        t = topics(cfg.slug)
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
        await self._send(cfg, t["reader_image"], reader_jpeg, retain=True)
        await self._send_reading_diagnostics(cfg, text.text, shown, reason, now)

    async def _keep_reading(
        self,
        cfg: SensorConfig,
        image: Image.Image,
        text: readers.Text,
        shown: str | None,
        reason: str | None,
        changed: bool,
        lit: bool = False,
        learned: tuple[float, float] | None = None,
    ) -> None:
        """Count the reading for the day, and keep it in the history when the value changed, it was
        rejected (it goes to review, unless the same one already waits there: _repeat_rejected) or
        it was picked for a spot check.

        With it go what the reader told about it (which reader, the wheels) and the app version,
        so an export says how each reading was made. Counters: ``learned`` is what the reading
        adds to the day's limit (change_limit.observe), and their Watch is stored."""
        live = self.live_state(cfg.id)
        watch = live.watch if cfg.reading["mode"] == "counter" else None
        await asyncio.to_thread(self._count_reading, cfg.id, reason, learned, watch, live.debouncer.published)
        spot = reason is None and not changed and random.random() < float(cfg.reading["spot_rate"])
        if changed or reason is not None or spot:
            published = live.debouncer.published if reason is None else None
            # The region it was read in: the frame is whole, and the region may be moved later.
            details = {
                "text": text.text,
                "value": shown,
                "reason": reason,
                "roi": cfg.roi,
                **(text.details or {}),
                "app": VERSION,
                "lamp": lit,
            }
            review = "rejected" if reason is not None else "spot_check" if spot else None
            if reason is not None and await asyncio.to_thread(self._repeat_rejected, cfg.id, reason, shown):
                return
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

    def _count_reading(
        self,
        sensor_id: int,
        reason: str | None,
        learned: tuple[float, float] | None = None,
        watch: change_limit.Watch | None = None,
        published: str | None = None,
    ) -> None:
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
            if learned is not None:
                row.peak = max(row.peak or 0.0, learned[0])
                row.total = (row.total or 0.0) + learned[1]
            if watch is not None:
                _store_watch(s, sensor_id, watch, published)

    def _learn_limit(self, sensor_id: int, watch: change_limit.Watch, now: float) -> None:
        """Work out what a counter's limit learned from its days: once an hour, and on a new day."""
        today = date.today()
        if now - watch.learned_at < COUNTER_LIMIT["relearn_s"] and watch.learned_day == today.isoformat():
            return
        first = (today - timedelta(days=COUNTER_LIMIT["learn_days"] - 1)).isoformat()
        with self.db.session() as s:
            rows = s.scalars(
                select(ReadingStat).where(
                    ReadingStat.sensor_id == sensor_id, ReadingStat.day >= first, ReadingStat.peak.is_not(None)
                )
            ).all()
            days = [(row.peak or 0.0, row.total or 0.0) for row in rows]
        watch.learned, watch.days = change_limit.learn(days), len(days)
        watch.learned_at, watch.learned_day = now, today.isoformat()

    def _restore_reading(self, sensor_id: int, watch: change_limit.Watch) -> str | None:
        """After a restart: the last published value (so the checks go on from it) and a counter's
        Watch as it was stored.

        A value stored with the Watch is newer than the history's when it was accepted later (the
        history keeps changes only; a value confirmed in Review is only stored there). Without a
        stored Watch (the first start since there is one), the time of the last published value
        stands in for when the counter was last seen."""
        with self.db.session() as s:
            last = s.execute(
                select(Prediction.published_key, Prediction.created_at)
                .where(Prediction.sensor_id == sensor_id, Prediction.published_key.is_not(None))
                .order_by(Prediction.created_at.desc())
                .limit(1)
            ).first()
            stored = s.get(ReadingWatch, sensor_id)
            value, at = (last.published_key, _epoch(last.created_at)) if last is not None else (None, None)
            if stored is not None:
                watch.seen_at, watch.accepted_at = stored.seen_at, stored.accepted_at
                watch.budget_s, watch.held = stored.budget_s, stored.held
                if stored.value is not None and (at is None or (stored.accepted_at or 0.0) >= at):
                    value = stored.value
            else:
                watch.seen_at = watch.accepted_at = at
        return value

    async def anchor_reading(self, sensor_id: int, value: str, at: datetime) -> None:
        """Make ``value``, read at ``at``, the last accepted value: a rejected reading confirmed in Review.

        Later readings are checked against it (a counter stuck on a value read too high, or held
        by its limit, goes on from there), and Home Assistant gets it."""
        live = self.live_state(sensor_id)
        live.debouncer.published, live.debouncer.candidate, live.debouncer.count = value, None, 0
        now = time.time()
        watch = live.watch
        watch.accepted_at = _epoch(at)
        watch.budget_s, watch.held = max(0.0, now - watch.accepted_at), False
        watch.seen_at, watch.sample = now, None  # nothing learned across it
        await asyncio.to_thread(self._save_watch, sensor_id, watch, value)
        cfg = self.load_sensor(sensor_id)
        if cfg is not None:
            await self._send(cfg, topics(cfg.slug)["state"], value, retain=True)

    def _save_watch(self, sensor_id: int, watch: change_limit.Watch, value: str) -> None:
        with self.db.session() as s:
            _store_watch(s, sensor_id, watch, value)

    def _repeat_rejected(self, sensor_id: int, reason: str, value: str | None) -> bool:
        """Count a rejected reading on the one waiting in Review with the same reason and value.

        A meter that stands still on a rejected value (a night without water used) would otherwise
        queue one frame per check, all with the same answer. The waiting item keeps its first frame
        and time (a value confirmed in Review counts from when it was first read) and gets
        ``repeats`` and ``last_seen``. Returns whether there was one; a skipped item is no longer
        waiting, so the next reading is queued again."""
        with self.db.session() as s:
            waiting = s.scalars(
                select(Prediction).where(
                    Prediction.sensor_id == sensor_id,
                    Prediction.review_reason == "rejected",
                    Prediction.reviewed.is_(False),
                )
            )
            for row in waiting:
                probs = row.probs or {}
                if probs.get("reason") == reason and probs.get("value") == value:
                    seen = {"repeats": int(probs.get("repeats", 0)) + 1, "last_seen": datetime.now(UTC).isoformat()}
                    row.probs = {**probs, **seen}
                    return True
        return False

    def _record_reading(
        self,
        sensor_id: int,
        image: Image.Image,
        published: str | None,
        score: float,
        details: dict,
        changed: bool,
        review: str | None = None,
    ) -> None:
        """A history row of a reading sensor; ``review`` ("rejected", "spot_check") queues it."""
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


def _store_watch(s: Session, sensor_id: int, watch: change_limit.Watch, value: str | None) -> None:
    row = s.get(ReadingWatch, sensor_id)
    if row is None:
        row = ReadingWatch(sensor_id=sensor_id)
        s.add(row)
    row.value = value
    row.seen_at, row.accepted_at, row.budget_s, row.held = watch.seen_at, watch.accepted_at, watch.budget_s, watch.held


def _epoch(at: datetime) -> float:
    """Epoch seconds of a time from the database (SQLite keeps no time zone: they are UTC)."""
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp()
