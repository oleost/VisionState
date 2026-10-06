"""One check of a reading sensor: read the number, check it, publish it, keep history."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, datetime

from PIL import Image
from sqlalchemy import select

from .. import imaging, readers
from ..db import Prediction, ReadingStat
from ..mqtt import rate_unit, topics
from ..redact import redact
from ..settings import READING
from .base import RuntimeBase
from .logic import reading_verdict
from .models import ModelsMixin
from .publishing import PublishingMixin
from .state import SensorConfig

log = logging.getLogger(__name__)


class ReadingChecksMixin(ModelsMixin, PublishingMixin, RuntimeBase):
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
        reason, settling = reading_verdict(text, value, last, cfg.threshold, settings)
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
        await self._publish_reading(cfg, image, reader_jpeg, text, shown, reason, now)
        await self._keep_reading(cfg, image, text, shown, reason, changed)

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
    ) -> None:
        """Count the reading for the day, and keep it in the history when the value changed, it was
        rejected (it goes to review) or it was picked for a spot check."""
        live = self.live_state(cfg.id)
        await asyncio.to_thread(self._count_reading, cfg.id, reason)
        spot = reason is None and not changed and random.random() < float(cfg.reading["spot_rate"])
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

    def _last_reading(self, sensor_id: int) -> str | None:
        """The last published value of a reading sensor (so a restart keeps checking against it)."""
        with self.db.session() as s:
            return s.scalar(
                select(Prediction.published_key)
                .where(Prediction.sensor_id == sensor_id, Prediction.published_key.is_not(None))
                .order_by(Prediction.created_at.desc())
                .limit(1)
            )
