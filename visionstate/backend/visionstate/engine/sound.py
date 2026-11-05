"""Sound sensors: listening to a stream's audio (docs/SOUND_PLAN.md).

A sound sensor's loop comes here every SOUND["hop_s"] instead of checking a frame: it takes what the
stream (sources.AudioStream, a thread of its own) received since, keeps the level of each hop,
and analyses the window that ends with each hop when it is loud enough — above the line
(``sounds.line_db``); a stream that delivers seconds at once has them all analysed. Each sound is
then decided over the last windows (logic.hear) and published when that changes. A sound
that begins to be heard writes a history row; with clips on, the seconds around it are cut from
the stream's buffer once they arrived, speech in them silenced, and kept with the row.

Own sounds are taught from clips (samples whose ``object_label`` is the own sound's key, or
sounds.OTHER for "not this"): a small head per sensor on the model's embeddings, trained again
when they change. With "learn the room" on, loud windows that matched no own sound are kept as
OTHER too (in the heads folder, never exported). An own sound can also be recorded from the
stream itself (Start, Stop and some seconds more for the stream's delay, then trimmed in the UI):
the seconds come from the same buffer, speech silenced like a clip's.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import uuid
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import backbones, sounds
from ..db import Embedding, Prediction, Sample
from ..mqtt import sound_topics, topics
from ..redact import redact
from ..settings import KIND_SOUND, SOUND
from ..sources import AudioStream, open_audio
from ..storage import is_clip
from .base import RuntimeBase
from .logic import hear
from .models import ModelsMixin
from .publishing import PublishingMixin
from .state import SensorConfig, SoundLive, SoundRecording, SoundTrack

log = logging.getLogger(__name__)

# Embedding.roi_key of a taught clip's embeddings: one per loud window (sounds.loud_windows), as an
# .npy array. Rows of the first key held one, of the loudest window only: replaced when met.
EMBEDDING_KEY = "sound-windows"
OLD_EMBEDDING_KEY = "sound"


class RecordingError(Exception):
    """A recording from a sensor's stream that cannot be made (said to the user as it is)."""


def room_path(heads_dir: Path, sensor_id: int) -> Path:
    """The windows of the room a sensor kept as "other" (embeddings; never exported)."""
    return heads_dir / f"{sensor_id}{sounds.ROOM_SUFFIX}"


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
        recording = self.sound_recordings.get(sensor_id)
        if recording is not None and recording.samples is None:  # its seconds are gone with the stream
            del self.sound_recordings[sensor_id]

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
            await self._nothing_heard(cfg, heard)
            return None
        started = time.monotonic()
        stream = heard.stream
        video = bool(cfg.sound["clips"] and cfg.sound["video"])  # keep the camera's video for clips
        if stream is None or stream.url != cfg.source or stream.closed or stream.video != video:
            if stream is not None:
                stream.close()
            stream = heard.stream = open_audio(self.ha, cfg.source_type, cfg.source, video)
            heard.consumed = 0
            for clip in heard.clips:  # cut by the old stream's sample numbers
                heard.clip_pending.pop(clip["row"], None)
            heard.clips = []
            heard.near.clear()
        try:
            model = await self.ensure_sound_model()
        except Exception:  # noqa: BLE001 - shown on the sensor; tried again after a while
            live.available, live.error = False, f"Sound model: {self.sound_model_error}"
            return SOUND["reconnect_max_s"]
        new, heard.consumed = stream.since(heard.consumed)
        await self._sound_availability(cfg, stream, heard)
        if len(new):
            await self._hear(cfg, model, stream, heard, new, heard.consumed)
        await self._cut_clips(cfg, model, stream, heard)
        return max(0.05, SOUND["hop_s"] - (time.monotonic() - started))

    async def _nothing_heard(self, cfg: SensorConfig, heard: SoundLive) -> None:
        """Paused: nothing is heard now, also a sound that was (no question about it: no clip comes)."""
        heard.near.clear()
        for key, track in heard.tracks.items():
            track.recent.clear()  # those windows must not count towards the next time it listens
            if track.on:
                track.on, track.row = False, None
                await self._sound_changed(cfg, None, heard, key, track, sounds.SILENCE_DB, 0)

    async def _sound_availability(self, cfg: SensorConfig, stream: AudioStream, heard: SoundLive) -> None:
        live = self.live_state(cfg.id)
        available = stream.delivering
        if not available and live.available is None and not stream.error:
            return  # still opening the stream for the first time
        live.error = "" if available else stream.error
        if available == live.available:
            return
        if available:
            heard.reported = False
        elif not heard.reported:  # once: a stream that keeps coming and going is not news every time
            heard.reported = True
            log.warning("Sensor %s: no sound: %s", cfg.slug, stream.error or "no sound arrived")
        live.available = available
        live.last_run = time.time()
        await self._send(cfg, topics(cfg.slug)["availability"], "online" if available else "offline", retain=True)

    async def _hear(
        self,
        cfg: SensorConfig,
        model: sounds.SoundModel,
        stream: AudioStream,
        heard: SoundLive,
        new: np.ndarray,
        end: int,
    ) -> None:
        """Levels of the new hops (``new``: the samples up to sample number ``end``), and each sound
        decided on each hop: the window that ends with it is analysed when it is loud enough. A stream
        may deliver many hops at once (a Home Assistant camera's stream: a segment of several seconds
        at a time); each is analysed, not only the newest, or a sound in the middle would be missed."""
        rate = stream.rate
        hop, size = int(SOUND["hop_s"] * rate), int(SOUND["window_s"] * rate)
        hearing = any(track.on for track in heard.tracks.values())
        ends: list[int] = []  # the sample number each hop ends at
        levels: list[float] = []  # the loudest hop of the window that ends with each
        for i in range(0, len(new), hop):
            level = sounds.level_db(new[i : i + hop])
            heard.levels.append(level)
            if not hearing:  # a siren that goes on for minutes must not become the background
                heard.background.append(level)
            ends.append(end - len(new) + min(i + hop, len(new)))
            levels.append(max(list(heard.levels)[-int(SOUND["window_s"] / SOUND["hop_s"]) :]))
        heard.line = sounds.line_db(cfg.sound, sounds.background_db(list(heard.background)))
        loud = [j for j, level in enumerate(levels) if heard.line is not None and level >= heard.line]
        keys = cfg.sound_keys
        custom = {c["key"] for c in cfg.sound["custom"]}
        head = self.sound_heads.get(cfg.id)
        # hop -> its scores, embedding, and the built-in sounds held back by a clip that is not them
        analysed: dict[int, tuple[dict[str, float], np.ndarray, set[str]]] = {}
        if loud:
            # The new samples and a window's length before them (fewer when the buffer has no more).
            audio = stream.segment(max(0, end - len(new) - size), end)
            if audio is None:
                audio = new
            first = end - len(audio)
            windows = np.zeros((len(loud), size), dtype=np.float32)
            for n, j in enumerate(loud):
                part = audio[max(0, ends[j] - size - first) : ends[j] - first]
                windows[n, size - len(part) :] = part
            async with self._sem:
                raw, embeddings = await asyncio.to_thread(model.run, windows)
            own = head.predict(embeddings) if head is not None else None
            for n, j in enumerate(loud):
                when = time.time() - (end - ends[j]) / rate
                scores = dict.fromkeys(keys, 0.0)
                scores.update(model.sound_scores(raw[n], keys))
                probs = own[n] if own is not None else {}
                held: set[str] = set()
                for key in keys:
                    if key in custom:
                        scores[key] = probs.get(key, 0.0)
                    elif key in probs or sounds.not_label(key) in probs:  # taught with the household's clips
                        yes, no = probs.get(key), probs.get(sounds.not_label(key))
                        threshold = self.sound_threshold(cfg, key)
                        # Held back a moment ago: still, through its pauses (as a sound stays heard).
                        track = heard.tracks.setdefault(key, SoundTrack())
                        holding = when - track.held_at <= cfg.sound["clear_after_s"]
                        scores[key], was_held = sounds.correct(scores[key], yes, no, threshold, holding)
                        if was_held:
                            held.add(key)
                            track.held_at = when
                            if not holding:  # once per sound, as it is heard once
                                track.held_count += 1
                analysed[j] = (scores, embeddings[n], held)
        newest = len(ends) - 1
        heard.analysed = newest in analysed
        heard.scores = analysed[newest][0] if heard.analysed else {}
        now = time.time()
        for j, at in enumerate(ends):
            scores = analysed[j][0] if j in analysed else dict.fromkeys(keys, 0.0)
            held = analysed[j][2] if j in analysed else set()
            when = now - (end - at) / rate  # a hop that came in a burst: as long ago as it is in the stream
            for key in keys:
                track = heard.tracks.setdefault(key, SoundTrack())
                track.held = key in held
                threshold = self.sound_threshold(cfg, key)
                if hear(track, scores[key], threshold, when, cfg.sound["clear_after_s"]):
                    await self._sound_changed(cfg, stream, heard, key, track, levels[j], at)
                await self._near(cfg, stream, heard, key, track, scores[key], threshold, when, at, levels[j])
        for key in [k for k in heard.tracks if k not in keys]:
            del heard.tracks[key]  # no longer listened for
        if loud and (custom or head is not None) and cfg.sound["learn_room"]:
            scores, embedding, _ = analysed[loud[-1]]
            self._keep_room(cfg, heard, embedding, scores)

    async def _near(
        self,
        cfg: SensorConfig,
        stream: AudioStream,
        heard: SoundLive,
        key: str,
        track: SoundTrack,
        score: float,
        threshold: float,
        when: float,
        at: int,
        level: float,
    ) -> None:
        """A sound almost heard: the same windows decided at SOUND["near_share"] of its threshold.
        When that ends and the sound itself was not heard meanwhile, it is asked about (with its
        clip: only with clips on, and as the review rules allow)."""
        if not cfg.sound["clips"] or key == SOUND["speech_key"]:
            return
        near = heard.near.setdefault(key, {"track": SoundTrack(), "start": 0, "heard": False})
        shadow: SoundTrack = near["track"]
        if not hear(shadow, score, threshold * SOUND["near_share"], when, cfg.sound["clear_after_s"]):
            near["heard"] = near["heard"] or track.on
            return
        if shadow.on:  # it began
            near["start"], near["heard"] = at, track.on
            return
        if near["heard"] or track.on:
            return
        rules = self.review_rules(cfg)
        live = self.live_state(cfg.id)
        now = time.time()
        if not rules["enabled"] or now - live.last_flag < rules["cooldown_s"]:
            return
        if await asyncio.to_thread(self._near_answered_no, cfg.id, key):
            return
        live.last_flag = now
        best = shadow.best
        row = await asyncio.to_thread(self._record_sound, cfg.id, key, False, best, level, "near")
        onset = near["start"] - int(SOUND["window_s"] * stream.rate)
        heard.clip_pending[row] = best
        heard.near_rows.add(row)
        heard.clips.append(
            {
                "row": row,
                "key": key,
                "start": max(0, onset - int(SOUND["clip_before_s"] * stream.rate)),
                "end": near["start"] + int(SOUND["clip_after_s"] * stream.rate),
            }
        )

    def _near_answered_no(self, sensor_id: int, key: str) -> bool:
        """Whether the last SOUND["near_quiet_after"] answers about ``key`` almost heard were all
        that it was not this sound: then it is not asked about almost heard any more."""
        wanted = SOUND["near_quiet_after"]
        with self.db.session() as s:
            rows = s.scalars(
                select(Prediction)
                .where(
                    Prediction.sensor_id == sensor_id, Prediction.state_key == key, Prediction.published_key == "near"
                )
                .order_by(Prediction.id.desc())
                .limit(SOUND["near_quiet_rows"])
            )
            answers = [a for r in rows if (a := (r.probs or {}).get("answer")) is not None][:wanted]
        return len(answers) >= wanted and all(a != "yes" for a in answers)

    def sound_threshold(self, cfg: SensorConfig, key: str) -> float:
        """The score a sound must reach: the sensor's own, else the model's (own sounds: SOUND)."""
        own = cfg.sound["thresholds"].get(key)
        if own is not None:
            return own
        spec = sounds.SOUNDS.get(key)
        return spec.threshold if spec else SOUND["own_threshold"]

    async def _sound_changed(
        self,
        cfg: SensorConfig,
        stream: AudioStream | None,
        heard: SoundLive,
        key: str,
        track: SoundTrack,
        level: float,
        at: int,
    ) -> None:
        """A sound began or stopped to be heard in the window that ends at sample number ``at``:
        publish it, and write a history row (with a clip to cut once its seconds after arrived)."""
        await self._send(cfg, sound_topics(cfg.slug, key)["state"], "ON" if track.on else "OFF", retain=True)
        score = track.best if not track.on else track.score
        row = await asyncio.to_thread(self._record_sound, cfg.id, key, track.on, score, level)
        if not track.on and track.row is not None:
            await self._maybe_ask(cfg, key, track, heard)
        track.row = row if track.on else None
        if stream is not None and track.on and cfg.sound["clips"] and key != SOUND["speech_key"]:
            onset = at - int(SOUND["window_s"] * stream.rate)
            heard.clip_pending[row] = None
            heard.clips.append(
                {
                    "row": row,
                    "key": key,
                    "start": max(0, onset - int(SOUND["clip_before_s"] * stream.rate)),
                    "end": at + int(SOUND["clip_after_s"] * stream.rate),
                }
            )
        if not track.on:
            track.best = 0.0

    async def _maybe_ask(self, cfg: SensorConfig, key: str, track: SoundTrack, heard: SoundLive) -> None:
        """A sound that is no longer heard, and never scored well above its threshold: ask in the
        review queue whether it was that sound (only with its clip kept, and as the sensor's review
        rules allow), once the clip is stored: it may still be on its way. The answer teaches the
        clip (api/review.py)."""
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
        if track.row in heard.clip_pending:
            heard.clip_pending[track.row] = track.best
            return
        await self._ask_sound(track.row, track.best)

    async def _ask_sound(self, row_id: int | None, best: float, reason: str = "unsure") -> None:
        await asyncio.to_thread(self._store_question, row_id, best, reason)
        await self.publish_review_count()

    def _store_question(self, row_id: int | None, best: float, reason: str) -> None:
        """Only with its clip: a question is listened to, and its answer teaches the clip. ``reason``:
        "unsure" (only just heard) or "near" (almost heard)."""
        with self.db.session() as s:
            row = s.get(Prediction, row_id) if row_id is not None else None
            if row is not None and is_clip(row.frame):
                row.review_reason, row.reviewed, row.confidence = reason, False, round(best, 4)

    def _record_sound(
        self, sensor_id: int, key: str, on: bool, score: float, level: float, event: str | None = None
    ) -> int:
        """A history row: the sound began ("on") or stopped ("off") to be heard, or ``event``
        ("near": almost heard, not published)."""
        with self.db.session() as s:
            row = Prediction(
                sensor_id=sensor_id,
                state_key=key,
                published_key=event or ("on" if on else "off"),
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
            if samples is not None:
                self._spawn(self._finish_clip(cfg, model, stream, heard, clip, samples))
            else:  # the stream was opened again meanwhile: the seconds are gone
                heard.clip_pending.pop(clip["row"], None)

    async def _finish_clip(
        self,
        cfg: SensorConfig,
        model: sounds.SoundModel,
        stream: AudioStream,
        heard: SoundLive,
        clip: dict,
        samples: np.ndarray,
    ) -> None:
        """Speech silenced, the video waited for and the clip stored, beside the listening: a sound
        heard meanwhile must not be missed."""
        try:
            async with self._sem:
                quiet, muted = await asyncio.to_thread(model.silence_speech, samples, SOUND["speech_mute"])
            # With the camera's video when the stream has one (made by the stream's thread), else sound only.
            future = stream.video_clip(quiet, clip["start"])
            try:
                with_video = await asyncio.to_thread(future.result, SOUND["video_wait_s"])
            except TimeoutError:
                with_video = None
            await asyncio.to_thread(self._store_clip, cfg.id, clip["row"], quiet, muted, stream.rate, with_video)
            asked = heard.clip_pending.pop(clip["row"], None)
            near = clip["row"] in heard.near_rows
            heard.near_rows.discard(clip["row"])
            if asked is not None:
                await self._ask_sound(clip["row"], asked, "near" if near else "unsure")
        except Exception:  # noqa: BLE001 - the sound was heard all the same; only its clip is lost
            heard.clip_pending.pop(clip["row"], None)
            heard.near_rows.discard(clip["row"])
            log.exception("Sensor %s: could not keep a clip", cfg.slug)

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

    # --- Recording an own sound from the stream --------------------------------------------------

    def start_recording(self, sensor_id: int) -> SoundRecording:
        """Start pressed: the stream's sample number now (one recording per sensor; a new one
        replaces it). Only while the sensor hears its stream."""
        self._forget_old_recordings()
        live = self.live.get(sensor_id)
        stream = live.sound.stream if live is not None and live.sound is not None else None
        if stream is None or stream.closed or not stream.delivering:
            raise RecordingError("The sensor does not hear its stream now: it must be listening, with sound coming")
        recording = SoundRecording(id=uuid.uuid4().hex, stream=stream, start=stream.total)
        self.sound_recordings[sensor_id] = recording
        return recording

    def get_recording(self, sensor_id: int, recording_id: str) -> SoundRecording | None:
        self._forget_old_recordings()
        recording = self.sound_recordings.get(sensor_id)
        return recording if recording is not None and recording.id == recording_id else None

    def _forget_old_recordings(self) -> None:
        now = time.monotonic()
        for sensor_id, recording in list(self.sound_recordings.items()):
            if now - recording.started > SOUND["record_keep_s"]:
                del self.sound_recordings[sensor_id]

    async def stop_recording(self, recording: SoundRecording) -> SoundRecording:
        """Stop pressed: the recording goes on for SOUND["record_after_s"] (the stream comes late),
        then its seconds are taken from the stream's buffer and speech in them silenced. At most
        SOUND["record_max_s"] count up to Stop, measured in time held."""
        if recording.samples is not None:
            return recording  # stopped already (Stop sent twice)
        stream, rate = recording.stream, recording.stream.rate
        # As long as it was held, not what arrived meanwhile: a stream that sends seconds at once
        # (a Home Assistant camera's) may have sent nothing between Start and Stop.
        held = min(time.monotonic() - recording.started, SOUND["record_max_s"])
        if held < SOUND["record_min_s"]:
            raise RecordingError("Too short: record for a moment longer")
        stop = recording.start + int(held * rate)
        end = stop + int(SOUND["record_after_s"] * rate)
        deadline = time.monotonic() + SOUND["record_after_s"] + SOUND["stall_s"]
        while stream.total < end and not stream.closed and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        samples = stream.segment(recording.start, min(end, stream.total))
        if samples is None:
            raise RecordingError("The stream was opened again while recording: record it again")
        if len(samples) < SOUND["record_min_s"] * rate:
            raise RecordingError("No sound came after Start: record it again")
        model = await self.ensure_sound_model()
        async with self._sem:
            quiet, muted = await asyncio.to_thread(model.silence_speech, samples, SOUND["speech_mute"])
        recording.samples, recording.muted_s = quiet, muted
        recording.stop_s = round((stop - recording.start) / rate, 2)
        return recording

    # --- Own sounds -----------------------------------------------------------------------------

    def _keep_room(self, cfg: SensorConfig, heard: SoundLive, embedding: np.ndarray, scores: dict) -> None:
        """A loud window that matched none of the sensor's sounds (its own, and the built-in ones it
        listens for), kept as "other" (at most one per SOUND["room_every_s"], the newest
        SOUND["room_kept"])."""
        now = time.time()
        if now - heard.room_at < SOUND["room_every_s"]:
            return
        if any(scores.get(key, 0.0) >= self.sound_threshold(cfg, key) * SOUND["off_share"] for key in cfg.sound_keys):
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
            room = np.load(path) if path.exists() else None
        except OSError, ValueError:
            room = None
        return room if isinstance(room, np.ndarray) and room.ndim == 2 else np.zeros((0, 0), dtype=np.float32)

    def _save_room(self, sensor_id: int, room: np.ndarray) -> None:
        path = room_path(self.settings.heads_dir, sensor_id)
        part = path.with_name(f"{path.name}.part.npy")
        np.save(part, room)
        part.replace(path)

    def clip_embeddings(self, model: sounds.SoundModel, data: bytes) -> np.ndarray:
        """The embeddings of a taught clip's loud windows (what the head learns it from): N × 576."""
        samples = sounds.decode_audio(data, model.spec.rate)
        return model.run(sounds.loud_windows(samples, model.spec.rate))[1].astype(np.float32)

    def _sound_vectors(self, sensor_id: int, model: sounds.SoundModel) -> tuple[list[np.ndarray], list[str]]:
        """The embeddings and labels of a sound sensor's taught clips, one per loud window of each
        (embedded once, then cached)."""
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
                embedded = self.clip_embeddings(model, data)
                buf = io.BytesIO()
                np.save(buf, embedded)
                try:
                    with self.db.session() as s:
                        s.merge(
                            Embedding(
                                sample_id=sample_id, backbone=model.id, roi_key=EMBEDDING_KEY, vector=buf.getvalue()
                            )
                        )
                        old = s.get(Embedding, (sample_id, model.id, OLD_EMBEDDING_KEY))
                        if old is not None:
                            s.delete(old)
                except IntegrityError:
                    continue  # the clip was forgotten meanwhile (its head is trained again for that)
            else:
                embedded = np.load(io.BytesIO(vector))
            vectors += list(embedded)
            labels += [label] * len(embedded)
        return vectors, labels

    async def train_sound_head(self, cfg: SensorConfig) -> None:
        """Train a sound sensor's head again: its own sounds, and the built-in sounds it listens for
        that were taught with the household's clips (theirs and those that are not them), from the
        taught clips, "No" answers and the room's windows when it learns them; none without any of
        those. A failure (the model cannot be loaded) is logged: the sensor goes on with the model's
        sounds."""
        try:
            await self._train_sound_head(cfg)
        except Exception:  # noqa: BLE001
            log.exception("Sensor %s: could not train its own sounds", cfg.slug)

    async def _train_sound_head(self, cfg: SensorConfig) -> None:
        own = {c["key"] for c in cfg.sound["custom"]}
        builtin = [k for k in cfg.sound["sounds"] if k in sounds.SOUNDS]
        wanted = own | set(builtin) | {sounds.not_label(k) for k in builtin} | {sounds.OTHER}
        if not own and not await asyncio.to_thread(self._has_taught, cfg.id, wanted - {sounds.OTHER}):
            self.sound_heads.pop(cfg.id, None)
            return
        model = await self.ensure_sound_model()
        async with self._sem:
            vectors, labels = await asyncio.to_thread(self._sound_vectors, cfg.id, model)
        keep = [i for i, label in enumerate(labels) if label in wanted]
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

    def _has_taught(self, sensor_id: int, labels: set[str]) -> bool:
        with self.db.session() as s:
            found = s.scalar(
                select(Sample.id).where(Sample.sensor_id == sensor_id, Sample.object_label.in_(labels)).limit(1)
            )
            return found is not None

    async def sound_settled(self) -> None:
        """At start-up: the heads of the sound sensors that have own sounds or taught built-in ones."""
        for sensor_id in await asyncio.to_thread(self._sensor_ids, KIND_SOUND):
            cfg = await asyncio.to_thread(self.load_sensor, sensor_id)
            if cfg is not None:
                await self.train_sound_head(cfg)
