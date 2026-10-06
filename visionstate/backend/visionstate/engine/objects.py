"""One check of an object sensor: detect, compare with what was taught, publish, keep history."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from PIL import Image

from .. import detectors, imaging, teach
from ..db import Prediction
from ..mqtt import object_topics, topics
from ..redact import redact
from ..settings import TEACH
from .base import RuntimeBase
from .logic import update_tracks
from .publishing import PublishingMixin
from .state import SensorConfig
from .teaching import TeachingMixin

log = logging.getLogger(__name__)
OBJECT_BOX_COLOR = "#7ee2b8"  # boxes drawn on the Home Assistant image of an object sensor


class ObjectChecksMixin(TeachingMixin, PublishingMixin, RuntimeBase):
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
            found = await self.apply_taught(cfg, image, index, found, candidates, live.detections)
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

    def _record_detection(
        self, sensor_id: int, image: Image.Image, key: str, published: str, score: float, found: list[dict]
    ) -> None:
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
