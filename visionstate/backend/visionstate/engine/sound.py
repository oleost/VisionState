"""Sound sensors: listening to a stream's audio (docs/SOUND_PLAN.md).

A sound sensor's loop comes here every SOUND["hop_s"] instead of checking a frame: it takes what the
stream (sources.AudioStream, a thread of its own) received since, keeps the level of each hop,
and analyses the newest window when it is loud enough — above the line (``sounds.line_db``). Each
sound is then decided over the last windows (logic.hear) and published when that changes. A sound
that begins to be heard writes a history row; with clips on, the seconds around it are cut from
the stream's buffer once they arrived, speech in them silenced, and kept with the row.

Own sounds are taught from clips (samples whose ``object_label`` is the own sound's key, or
sounds.OTHER for "not this"): a small head per sensor on the model's embeddings, trained again
when they change. With "learn the room" on, loud windows that matched no own sound are kept as
OTHER too (in the heads folder, never exported).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np
from sqlalchemy import select

from .. import backbones, sounds
from ..db import Embedding, Prediction, Sample
from ..mqtt import sound_topics, topics
from ..redact import redact
from ..settings import KIND_SOUND, SOUND
from ..sources import AudioStream
from .base import RuntimeBase
from .logic import hear
from .models import ModelsMixin
from .publishing import PublishingMixin
from .state import SensorConfig, SoundLive, SoundTrack

log = logging.getLogger(__name__)

EMBEDDING_KEY = "sound"  # Embedding.roi_key of a taught clip's embedding


def room_path(heads_dir: Path, sensor_id: int) -> Path:
    """The windows of the room a sensor kept as "other" (embeddings; never exported)."""
    return heads_dir / f"{sensor_id}.room.npy"


class SoundMixin(ModelsMixin, PublishingMixin, RuntimeBase):
    # --- The model ------------------------------------------------------------------------------

    async def ensure_sound_model(self) -> sounds.SoundModel:
        """The sound model, downloaded and loaded on first use (no sound sensor: never)."""
        async with self._sound_lock:
            if self.sound_model is not None:
                return self.sound_model
            spec = sounds.MODELS[sounds.DEFAULT_MODEL]
            try:
                path = self.model_path(spec)
                if path is None:
                    if not spec.url:
                        raise RuntimeError(
                            f"The sound model {spec.filename} is not published yet: make it with "
                            f"tools/soundmodel/make_model.py and put it in {self.settings.models_dir}"
                        )
                    path = await asyncio.to_thread(backbones.download, spec, self.settings.models_dir)
                self.sound_model = await asyncio.to_thread(sounds.SoundModel, spec, path)
            except Exception as err:
                self.sound_model_error = redact(str(err)) or type(err).__name__
                raise
            self.sound_model_error = ""
            log.info("Sound model %s loaded", spec.id)
            return self.sound_model

    def stop_listening(self, sensor_id: int) -> None:
        """Close a sound sensor's stream (paused, deleted, its source changed, the app stops)."""
        live = self.live.get(sensor_id)
        if live is not None and live.sound is not None and live.sound.stream is not None:
            live.sound.stream.close()
            live.sound.stream = None

    # --- Listening ------------------------------------------------------------------------------

    async def _listen(self, cfg: SensorConfig) -> float | None:
        """One pass of a sound sensor's loop: what arrived since the last one. Returns how long to
        sleep (None: until woken, while paused)."""
        live = self.live_state(cfg.id)
        if live.sound is None:
            live.sound = SoundLive()
        heard = live.sound
        if not cfg.enabled:
            self.stop_listening(cfg.id)
            return None
        started = time.monotonic()
        stream = heard.stream
        video = bool(cfg.sound["clips"] and cfg.sound["video"])  # keep the camera's video for clips
        if stream is None or stream.url != cfg.source or stream.closed or stream.video != video:
            if stream is not None:
                stream.close()
            stream = heard.stream = AudioStream(cfg.source, video)
            heard.consumed = 0
        try:
            model = await self.ensure_sound_model()
        except Exception:  # noqa: BLE001 - shown on the sensor; tried again after a while
            live.available, live.error = False, f"Sound model: {self.sound_model_error}"
            return SOUND["reconnect_max_s"]
        new, heard.consumed = stream.since(heard.consumed)
        await self._sound_availability(cfg, stream)
        if len(new):
            await self._hear(cfg, model, stream, heard, new)
        await self._cut_clips(cfg, model, stream, heard)
        return max(0.05, SOUND["hop_s"] - (time.monotonic() - started))

    async def _sound_availability(self, cfg: SensorConfig, stream: AudioStream) -> None:
        live = self.live_state(cfg.id)
        available = stream.connected
        live.error = "" if available else stream.error
        if available == live.available:
            return
        if available is False and live.available is not False:
            log.warning("Sensor %s: no sound: %s", cfg.slug, stream.error or "connecting")
        live.available = available
        live.last_run = time.time()
        await self._send(cfg, topics(cfg.slug)["availability"], "online" if available else "offline", retain=True)

    async def _hear(
        self, cfg: SensorConfig, model: sounds.SoundModel, stream: AudioStream, heard: SoundLive, new: np.ndarray
    ) -> None:
        """Levels of the new hops; the newest window analysed when loud enough; each sound decided."""
        hop = int(SOUND["hop_s"] * stream.rate)
        hops = [new[i : i + hop] for i in range(0, len(new), hop)]
        hearing = any(track.on for track in heard.tracks.values())
        for part in hops:
            level = sounds.level_db(part)
            heard.levels.append(level)
            if not hearing:  # a siren that goes on for minutes must not become the background
                heard.background.append(level)
        heard.line = sounds.line_db(cfg.sound, sounds.background_db(list(heard.background)))
        recent = list(heard.levels)[-int(SOUND["window_s"] / SOUND["hop_s"]) :]
        loud = heard.line is not None and max(recent, default=sounds.SILENCE_DB) >= heard.line
        keys = cfg.sound_keys
        scores = dict.fromkeys(keys, 0.0)
        embedding = None
        if loud:
            window = stream.tail(SOUND["window_s"])
            async with self._sem:
                raw, embeddings = await asyncio.to_thread(model.run, window[None, :])
            scores.update(model.sound_scores(raw[0], keys))
            embedding = embeddings[0]
            head = self.sound_heads.get(cfg.id)
            if head is not None:
                for key, p in head.predict(embedding[None, :])[0].items():
                    if key in scores:
                        scores[key] = p
        heard.analysed, heard.scores = loud, scores if loud else {}
        now = time.time()
        level = max(recent, default=sounds.SILENCE_DB)
        for key in keys:
            track = heard.tracks.setdefault(key, SoundTrack())
            if hear(track, scores[key], self.sound_threshold(cfg, key), now, cfg.sound["clear_after_s"]):
                await self._sound_changed(cfg, stream, heard, key, track, level)
        for key in [k for k in heard.tracks if k not in keys]:
            del heard.tracks[key]  # no longer listened for
        if embedding is not None and cfg.sound["custom"] and cfg.sound["learn_room"]:
            self._keep_room(cfg, heard, embedding, scores)

    def sound_threshold(self, cfg: SensorConfig, key: str) -> float:
        """The score a sound must reach: the sensor's own, else the model's (own sounds: SOUND)."""
        own = cfg.sound["thresholds"].get(key)
        if own is not None:
            return own
        spec = sounds.SOUNDS.get(key)
        return spec.threshold if spec else SOUND["own_threshold"]

    async def _sound_changed(
        self, cfg: SensorConfig, stream: AudioStream, heard: SoundLive, key: str, track: SoundTrack, level: float
    ) -> None:
        """A sound began or stopped to be heard: publish it, and write a history row (with a clip
        to cut once its seconds after arrived)."""
        await self._send(cfg, sound_topics(cfg.slug, key)["state"], "ON" if track.on else "OFF", retain=True)
        score = track.best if not track.on else track.score
        row = await asyncio.to_thread(self._record_sound, cfg.id, key, track.on, score, level)
        if not track.on and track.row is not None:
            await self._maybe_ask(cfg, key, track)
        track.row = row if track.on else None
        if track.on and cfg.sound["clips"] and key != SOUND["speech_key"]:
            onset = stream.total - int(SOUND["window_s"] * stream.rate)
            heard.clips.append(
                {
                    "row": row,
                    "key": key,
                    "start": max(0, onset - int(SOUND["clip_before_s"] * stream.rate)),
                    "end": stream.total + int(SOUND["clip_after_s"] * stream.rate),
                }
            )
        if not track.on:
            track.best = 0.0

    async def _maybe_ask(self, cfg: SensorConfig, key: str, track: SoundTrack) -> None:
        """A sound that is no longer heard, and never scored well above its threshold: ask in the
        review queue whether it was that sound (only with its clip kept, and as the sensor's review
        rules allow). The answer teaches the clip (api/review.py)."""
        if not cfg.sound["clips"] or key == SOUND["speech_key"]:
            return
        threshold = self.sound_threshold(cfg, key)
        if track.best >= threshold + (1.0 - threshold) * SOUND["unsure_share"]:
            return
        rules = self.review_rules(cfg)
        live = self.live_state(cfg.id)
        now = time.time()
        if not rules["enabled"] or now - live.last_flag < rules["cooldown_s"]:
            return
        live.last_flag = now
        await asyncio.to_thread(self._ask_sound, track.row, track.best)
        await self.publish_review_count()

    def _ask_sound(self, row_id: int | None, best: float) -> None:
        with self.db.session() as s:
            row = s.get(Prediction, row_id) if row_id is not None else None
            if row is not None:
                row.review_reason, row.reviewed, row.confidence = "unsure", False, round(best, 4)

    def _record_sound(self, sensor_id: int, key: str, on: bool, score: float, level: float) -> int:
        with self.db.session() as s:
            row = Prediction(
                sensor_id=sensor_id,
                state_key=key,
                published_key="on" if on else "off",
                confidence=round(score, 4),
                probs={"level_db": round(level, 1)},
                is_change=True,
                reviewed=True,
            )
            s.add(row)
            s.flush()
            return row.id

    async def _cut_clips(
        self, cfg: SensorConfig, model: sounds.SoundModel, stream: AudioStream, heard: SoundLive
    ) -> None:
        """Clips whose seconds after arrived: cut, speech silenced, stored with their row."""
        due = [clip for clip in heard.clips if clip["end"] <= stream.total]
        if not due:
            return
        heard.clips = [clip for clip in heard.clips if clip["end"] > stream.total]
        for clip in due:
            samples = stream.segment(clip["start"], clip["end"])
            if samples is None:
                continue  # the stream was opened again meanwhile: the seconds are gone
            async with self._sem:
                quiet, muted = await asyncio.to_thread(model.silence_speech, samples, SOUND["speech_mute"])
            # With the camera's video when the stream has one (made by the stream's thread), else sound only.
            future = stream.video_clip(quiet, clip["start"])
            with_video = await asyncio.to_thread(future.result, SOUND["video_wait_s"])
            await asyncio.to_thread(self._store_clip, cfg.id, clip["row"], quiet, muted, stream.rate, with_video)

    def _store_clip(
        self, sensor_id: int, row_id: int, quiet: np.ndarray, muted: int, rate: int, with_video: bytes | None
    ) -> None:
        if with_video:
            filename = self.storage.save_clip(sensor_id, with_video, video=True)
        else:
            filename = self.storage.save_clip(sensor_id, sounds.encode_clip(quiet, rate))
        with self.db.session() as s:
            row = s.get(Prediction, row_id)
            if row is None:  # removed meanwhile (the sensor was deleted)
                self.storage.delete_history(sensor_id, row_id, filename)
                return
            row.frame = filename
            row.probs = {**(row.probs or {}), "muted_s": muted}

    # --- Own sounds -----------------------------------------------------------------------------

    def _keep_room(self, cfg: SensorConfig, heard: SoundLive, embedding: np.ndarray, scores: dict) -> None:
        """A loud window that matched none of the own sounds, kept as "other" (at most one per
        SOUND["room_every_s"], the newest SOUND["room_kept"])."""
        now = time.time()
        if now - heard.room_at < SOUND["room_every_s"]:
            return
        own = [c["key"] for c in cfg.sound["custom"]]
        if any(scores.get(key, 0.0) >= self.sound_threshold(cfg, key) * SOUND["off_share"] for key in own):
            return
        heard.room_at = now
        room = self.sound_room.get(cfg.id)
        if room is None:
            room = self._load_room(cfg.id)
        kept = embedding[None, :].astype(np.float32)
        room = np.concatenate([room, kept])[-SOUND["room_kept"] :] if room.size else kept
        self.sound_room[cfg.id] = room
        self._spawn(asyncio.to_thread(self._save_room, cfg.id, room))

    def _load_room(self, sensor_id: int) -> np.ndarray:
        path = room_path(self.settings.heads_dir, sensor_id)
        try:
            return np.load(path) if path.exists() else np.zeros((0, 0), dtype=np.float32)
        except OSError, ValueError:
            return np.zeros((0, 0), dtype=np.float32)

    def _save_room(self, sensor_id: int, room: np.ndarray) -> None:
        path = room_path(self.settings.heads_dir, sensor_id)
        part = path.with_name(f"{path.name}.part.npy")
        np.save(part, room)
        part.replace(path)

    def clip_embedding(self, model: sounds.SoundModel, data: bytes) -> np.ndarray:
        """The embedding of a clip's loudest window (what a taught clip is compared with)."""
        samples = sounds.decode_audio(data, model.spec.rate)
        window = sounds.loudest_window(samples, model.spec.rate, SOUND["window_s"])
        return model.run(window[None, :])[1][0]

    def _sound_vectors(self, sensor_id: int, model: sounds.SoundModel) -> tuple[list[np.ndarray], list[str]]:
        """The embeddings and labels of a sound sensor's taught clips (embedded once, then cached)."""
        with self.db.session() as s:
            rows = s.execute(
                select(Sample.id, Sample.filename, Sample.object_label).where(
                    Sample.sensor_id == sensor_id, Sample.object_label.is_not(None)
                )
            ).all()
            cached = {
                e.sample_id: e.vector
                for e in s.scalars(
                    select(Embedding).where(
                        Embedding.sample_id.in_([r.id for r in rows]),
                        Embedding.backbone == model.id,
                        Embedding.roi_key == EMBEDDING_KEY,
                    )
                )
            }
        vectors, labels = [], []
        for sample_id, filename, label in rows:
            vector = cached.get(sample_id)
            if vector is None:
                try:
                    data = self.storage.sample_path(sensor_id, filename).read_bytes()
                except OSError:
                    continue
                embedded = self.clip_embedding(model, data).astype(np.float32)
                with self.db.session() as s:
                    s.merge(
                        Embedding(
                            sample_id=sample_id, backbone=model.id, roi_key=EMBEDDING_KEY, vector=embedded.tobytes()
                        )
                    )
                vectors.append(embedded)
            else:
                vectors.append(np.frombuffer(vector, dtype=np.float32))
            labels.append(label)
        return vectors, labels

    async def train_sound_head(self, cfg: SensorConfig) -> None:
        """Train a sound sensor's head for its own sounds again (taught clips, "not this" answers,
        and the room's windows when it learns them); none without own sounds."""
        own = {c["key"] for c in cfg.sound["custom"]}
        if not own:
            self.sound_heads.pop(cfg.id, None)
            return
        model = await self.ensure_sound_model()
        async with self._sem:
            vectors, labels = await asyncio.to_thread(self._sound_vectors, cfg.id, model)
        keep = [i for i, label in enumerate(labels) if label in own or label == sounds.OTHER]
        x = [vectors[i] for i in keep]
        y = [labels[i] for i in keep]
        if cfg.sound["learn_room"]:
            room = self.sound_room.get(cfg.id)
            if room is None:
                room = self.sound_room[cfg.id] = await asyncio.to_thread(self._load_room, cfg.id)
            if room.size:
                x += list(room)
                y += [sounds.OTHER] * len(room)
        if not x:
            self.sound_heads.pop(cfg.id, None)
            return
        everyday = await asyncio.to_thread(sounds.other_sounds, model.id)
        x += list(everyday)
        y += [sounds.OTHER] * len(everyday)
        head = await asyncio.to_thread(sounds.train_head, model.id, np.stack(x), y)
        if head is None:
            self.sound_heads.pop(cfg.id, None)
        else:
            self.sound_heads[cfg.id] = head

    async def sound_settled(self) -> None:
        """At start-up: the heads of the sound sensors that have own sounds."""
        for sensor_id in await asyncio.to_thread(self._sensor_ids, KIND_SOUND):
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg is not None and cfg.sound["custom"]:
                try:
                    await self.train_sound_head(cfg)
                except Exception:  # noqa: BLE001 - the sensor listens for the model's sounds meanwhile
                    log.exception("Sensor %s: could not train its own sounds", cfg.slug)
