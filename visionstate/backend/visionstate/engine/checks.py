"""Running the checks: each sensor's loop, change detection, the light, and the state sensors' check."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import UTC, datetime

from PIL import Image

from .. import imaging
from ..db import Prediction
from ..mqtt import topics
from ..redact import redact
from ..settings import RUNTIME, UNKNOWN_STATE
from ..sources import SourceError
from .logic import next_check_at, review_reason
from .state import SensorConfig

log = logging.getLogger(__name__)


class ChecksMixin:
    async def _sensor_loop(self, sensor_id: int) -> None:
        event = self._wake[sensor_id]
        last_error = ""
        while True:
            try:
                timeout = await self._sensor_step(sensor_id)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - e.g. the database is busy: keep the sensor alive
                error = redact(str(err)) or type(err).__name__
                if error != last_error:
                    log.exception("Sensor %s: check loop failed", sensor_id)
                last_error = error
                timeout = RUNTIME["loop_retry_s"]
            else:
                last_error = ""
            if timeout is False:
                return  # the sensor was deleted
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            except TimeoutError:
                pass
            event.clear()

    async def _sensor_step(self, sensor_id: int) -> float | None | bool:
        """One pass of a sensor's loop: check when due. Returns how long to sleep (None: until
        woken), or False when the sensor no longer exists."""
        cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
        if cfg is None:
            return False
        live = self.live_state(sensor_id)
        due_at, kind = next_check_at(cfg, live, time.time())
        if not cfg.enabled and not live.force_paused:
            live.force = False  # paused: no check after a retrain or a model change, only when asked
        if live.force or (cfg.enabled and due_at <= time.time()):
            forced, live.force, live.force_paused = live.force, False, False
            try:
                if forced or kind == "full":
                    await self.run_once(cfg)
                else:
                    await self.probe(cfg)
                live.failure = ""
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                live.error = redact(str(err)) or type(err).__name__
                if live.error != live.failure:
                    # Camera problems are handled in run_once: this is unexpected, keep the traceback
                    # (once, not for every check while it keeps failing).
                    log.exception("Sensor %s failed: %s", cfg.slug, live.error)
                live.failure = live.error
            due_at, _ = next_check_at(cfg, live, time.time())
        timeout = max(0.05, due_at - time.time()) if cfg.enabled else cfg.interval_s
        return None if math.isinf(timeout) else timeout  # no regular check: sleep until woken

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
        await self._run_states(cfg, image)

    async def _run_states(self, cfg: SensorConfig, image: Image.Image) -> None:
        """One check of a state sensor: classify the region, debounce, publish, keep history."""
        live = self.live_state(cfg.id)
        t = topics(cfg.slug)
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
