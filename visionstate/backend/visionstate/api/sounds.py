"""Sound sensors: trying a stream before a sensor is made, and teaching own sounds from clips (a
clip in the history, an uploaded recording, or one recorded from the sensor's stream and trimmed)."""

from __future__ import annotations

import asyncio
import time
from functools import partial

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import advice, sounds
from ..db import Prediction, Sample, Sensor
from ..engine import Runtime
from ..engine.sound import RecordingError
from ..engine.state import SoundRecording
from ..settings import KIND_SOUND, SOUND, SOUND_LIMITS, UPLOAD_LIMITS, merge_sound
from ..sources import SOUND_SOURCE_TYPES, SourceError, check_source, open_audio
from ..storage import CLIP_SUFFIX, is_clip
from .advice import sound_answers
from .common import API_PREFIX, get_sensor, iso, runtime, slugify

router = APIRouter(prefix=API_PREFIX, tags=["sounds"])
PREVIEW_S = 6.0  # how long a stream is listened to before a sound sensor is made
CLIP_S = 4.0  # what is kept of an uploaded recording: its loudest seconds
PEAKS_PER_S = 20  # the waveform of a recording from the stream, drawn to trim it


class PreviewIn(BaseModel):
    source_type: str = "rtsp"  # sources.SOUND_SOURCE_TYPES
    source: str = Field(min_length=1, max_length=1024)


@router.post("/sounds/preview")
async def preview(body: PreviewIn, request: Request) -> dict:
    """Listen to a stream for a few seconds (the wizard): whether it has sound, what kind (codec,
    sample rate), its level, and the model's sounds that scored best."""
    rt = runtime(request)
    if body.source_type not in SOUND_SOURCE_TYPES:
        raise HTTPException(400, "Unknown source type")
    try:
        check_source(body.source_type, body.source)
    except SourceError as err:
        raise HTTPException(400, str(err)) from err
    stream = open_audio(rt.ha, body.source_type, body.source)
    try:
        # A Home Assistant camera's stream takes longer to open.
        deadline = time.monotonic() + PREVIEW_S + (SOUND["ha_open_s"] if body.source_type == "ha_camera" else 0)
        while time.monotonic() < deadline and stream.total < PREVIEW_S * stream.rate * 0.6:
            await asyncio.sleep(0.25)
            if stream.error and not stream.total:
                break
        samples = stream.tail(PREVIEW_S)
        info, error = dict(stream.info), stream.error
    finally:
        stream.close()
    if not len(samples):
        return {"ok": False, "error": error or "No sound arrived", "stream": info or None}
    hop = int(SOUND["hop_s"] * stream.rate)
    levels = [sounds.level_db(samples[i : i + hop]) for i in range(0, len(samples), hop)]
    try:
        model = await rt.ensure_sound_model()
    except Exception as err:  # noqa: BLE001 - the stream works; the model is the problem
        return {"ok": True, "stream": info, "levels": levels, "sounds": [], "model_error": str(err)}
    loudest = sounds.loudest_window(samples, stream.rate, SOUND["window_s"])
    async with rt._sem:
        scores, _ = await asyncio.to_thread(model.run, loudest[None, :])
    heard = model.sound_scores(scores[0], list(sounds.SOUNDS))
    return {
        "ok": True,
        "stream": info,
        "seconds": round(len(samples) / stream.rate, 1),
        "levels": levels,
        "sounds": [{"key": k, "score": v} for k, v in sorted(heard.items(), key=lambda kv: -kv[1])],
    }


# --- own sounds ------------------------------------------------------------------------------------


class TeachClipIn(BaseModel):
    """A clip in the history taught as one of the sensor's own sounds (``label``), as a new own
    sound (``new_name``), as "not any of them" (``label``: sounds.OTHER), or as one of the built-in
    sounds it listens for, or not that one (``label``: its key, or sounds.not_label of it)."""

    history_id: int
    label: str | None = Field(default=None, max_length=64)
    new_name: str | None = Field(default=None, min_length=1, max_length=64)


def own_sound(sound: dict, name: str) -> dict:
    """A new own sound: its key never clashes with the model's sounds or another own sound."""
    if len(sound["custom"]) >= SOUND["max_custom"]:
        raise HTTPException(400, f"A sensor can have at most {SOUND['max_custom']} own sounds")
    name = name.strip()
    if any(c["name"].lower() == name.lower() for c in sound["custom"]):
        raise HTTPException(400, f"There is already a sound called {name}")
    taken = set(sounds.SOUNDS) | {c["key"] for c in sound["custom"]} | {sounds.OTHER}
    base = slugify(name, "sound")
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return {"key": key, "name": name}


def builtin_keys(sound: dict) -> list[str]:
    """The built-in sounds a sensor listens for that can be taught with clips (not speech: it never
    keeps a clip)."""
    return [k for k in sound["sounds"] if k in sounds.SOUNDS and k != SOUND["speech_key"]]


def _label(sensor: Sensor, label: str | None, new_name: str | None) -> tuple[str, bool]:
    """The label to teach (an own sound's key, OTHER, or a built-in sound's key or "not" it), and
    whether a new own sound was made."""
    sound = merge_sound(sensor.sound)
    if new_name:
        made = own_sound(sound, new_name)
        sensor.sound = {**(sensor.sound or {}), "custom": [*sound["custom"], made]}
        return made["key"], True
    builtin = builtin_keys(sound)
    allowed = {sounds.OTHER, *(c["key"] for c in sound["custom"]), *builtin, *map(sounds.not_label, builtin)}
    if label is not None and label in allowed:
        return label, False
    raise HTTPException(
        400,
        "Teach a clip as one of the sensor's sounds (its own or a built-in one it listens for), a new one, or another",
    )


def _add_clip(rt: Runtime, sensor_id: int, data: bytes, label: str | None, new_name: str | None, origin: str) -> dict:
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id, KIND_SOUND)
        key, made = _label(sensor, label, new_name)
        taught = s.scalars(select(Sample.id).where(Sample.sensor_id == sensor_id, Sample.object_label == key)).all()
        if key != sounds.OTHER and len(taught) >= SOUND["max_taught"]:
            raise HTTPException(400, f"A sound can have at most {SOUND['max_taught']} clips of each kind")
        filename = rt.storage.save_sample_clip(sensor_id, data)
        sample = Sample(sensor_id=sensor_id, filename=filename, origin=origin, object_label=key)
        s.add(sample)
        s.flush()
        return {"id": sample.id, "label": key, "made": made}


async def _taught(rt: Runtime, sensor_id: int) -> None:
    """The sensor's own sounds changed: its entities, and its head trained again."""
    await rt.sensor_updated(sensor_id, retrain=False)


@router.post("/sensors/{sensor_id}/sounds/teach", status_code=201)
async def teach_clip(sensor_id: int, body: TeachClipIn, request: Request) -> dict:
    """Teach a clip of the sensor's history (a sound it heard, kept with clips on)."""
    return await teach_history_clip(runtime(request), sensor_id, body)


async def teach_history_clip(rt: Runtime, sensor_id: int, body: TeachClipIn) -> dict:
    """Teach a history row's clip (also what a review answer teaches)."""

    def clip() -> bytes:
        with rt.db.session() as s:
            row = s.get(Prediction, body.history_id)
            if row is None or row.sensor_id != sensor_id or not is_clip(row.frame):
                raise HTTPException(404, "No clip in this history row")
            path = rt.storage.history_path(sensor_id, row.frame or "")
            # Taught as the built-in sound it was heard as, or as not that one: an answer about it,
            # as in Review (what the threshold advice counts).
            if row.state_key in sounds.SOUNDS and body.label in (row.state_key, sounds.not_label(row.state_key)):
                row.probs = {**(row.probs or {}), "answer": "yes" if body.label == row.state_key else "no"}
        try:
            data = path.read_bytes()
        except OSError as err:
            raise HTTPException(404, "The clip is gone") from err
        if path.suffix == CLIP_SUFFIX:
            return data
        # A clip with video: only its sound is taught.
        rate = sounds.MODELS[sounds.DEFAULT_MODEL].rate
        return sounds.encode_clip(sounds.decode_audio(data, rate), rate)

    data = await asyncio.to_thread(clip)
    result = await asyncio.to_thread(_add_clip, rt, sensor_id, data, body.label, body.new_name, "history")
    await _taught(rt, sensor_id)
    return result


@router.post("/sensors/{sensor_id}/sounds/upload", status_code=201)
async def upload_clips(
    sensor_id: int,
    request: Request,
    files: list[UploadFile] = File(...),  # noqa: B008 - FastAPI's way
    label: str | None = Form(None),
    new_name: str | None = Form(None),
) -> dict:
    """Teach recordings (any format FFmpeg reads, a phone's voice memo, a video): the loudest
    seconds of each become a clip of the own sound (``label`` or a new one, ``new_name``)."""
    rt = runtime(request)
    model = await rt.ensure_sound_model()
    limit = UPLOAD_LIMITS["max_file_mb"] * 1_000_000
    added = []
    for upload in files:
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"{upload.filename}: larger than {UPLOAD_LIMITS['max_file_mb']} MB")
        try:
            samples = await asyncio.to_thread(sounds.decode_audio, data, model.spec.rate)
        except Exception as err:  # noqa: BLE001 - not a recording FFmpeg reads
            raise HTTPException(400, f"{upload.filename}: no sound could be read ({err})") from err
        if len(samples) < model.spec.rate * 0.3:
            raise HTTPException(400, f"{upload.filename}: too short")
        clip = sounds.loudest_window(samples, model.spec.rate, CLIP_S)
        encoded = await asyncio.to_thread(sounds.encode_clip, clip, model.spec.rate)
        result = await asyncio.to_thread(_add_clip, rt, sensor_id, encoded, label, new_name, "upload")
        added.append(result)
        if result["made"]:  # the next files go to the sound the first one made
            label, new_name = result["label"], None
    await _taught(rt, sensor_id)
    return {"added": added}


# --- recording from the sensor's stream ------------------------------------------------------------


class TrimIn(BaseModel):
    """The part of a recording taught (seconds from Start), as an own sound, a new one or OTHER."""

    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    label: str | None = Field(default=None, max_length=64)
    new_name: str | None = Field(default=None, min_length=1, max_length=64)


def _recording(rt: Runtime, sensor_id: int, recording_id: str) -> SoundRecording:
    recording = rt.get_recording(sensor_id, recording_id)
    if recording is None:
        raise HTTPException(404, "This recording is gone: record it again")
    return recording


def _is_sound_sensor(rt: Runtime, sensor_id: int) -> None:
    with rt.db.session() as s:
        get_sensor(s, sensor_id, KIND_SOUND)


@router.post("/sensors/{sensor_id}/sounds/recording", status_code=201)
async def start_recording(sensor_id: int, request: Request) -> dict:
    """Start recording an own sound from the sensor's stream (it must be listening)."""
    rt = runtime(request)
    await asyncio.to_thread(_is_sound_sensor, rt, sensor_id)
    try:
        recording = rt.start_recording(sensor_id)
    except RecordingError as err:
        raise HTTPException(409, str(err)) from err
    return {"id": recording.id, "max_s": SOUND["record_max_s"], "after_s": SOUND["record_after_s"]}


@router.post("/sensors/{sensor_id}/sounds/recording/{recording_id}/stop")
async def stop_recording(sensor_id: int, recording_id: str, request: Request) -> dict:
    """Stop: answers once the seconds after Stop arrived (SOUND["record_after_s"]), with the
    recording's length, where Stop was, and its waveform."""
    rt = runtime(request)
    try:
        recording = await rt.stop_recording(_recording(rt, sensor_id, recording_id))
    except RecordingError as err:
        raise HTTPException(409, str(err)) from err
    samples = recording.samples
    assert samples is not None
    rate = recording.stream.rate
    return {
        "id": recording.id,
        "seconds": round(len(samples) / rate, 2),
        "stop_s": recording.stop_s,
        "muted_s": recording.muted_s,
        "peaks": await asyncio.to_thread(sounds.peaks, samples, rate, PEAKS_PER_S),
        "peaks_per_s": PEAKS_PER_S,
    }


@router.get("/sensors/{sensor_id}/sounds/recording/{recording_id}/audio")
async def recording_audio(sensor_id: int, recording_id: str, request: Request) -> Response:
    """A stopped recording as WAV (speech silenced), to listen to while trimming."""
    rt = runtime(request)
    recording = _recording(rt, sensor_id, recording_id)
    if recording.samples is None:
        raise HTTPException(409, "Still recording")
    data = await asyncio.to_thread(sounds.encode_wav, recording.samples, recording.stream.rate)
    return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/sensors/{sensor_id}/sounds/recording/{recording_id}/teach", status_code=201)
async def teach_recording(sensor_id: int, recording_id: str, body: TrimIn, request: Request) -> dict:
    """Teach the trimmed part of a stopped recording; the recording is then forgotten."""
    rt = runtime(request)
    recording = _recording(rt, sensor_id, recording_id)
    if recording.samples is None:
        raise HTTPException(409, "Still recording")
    rate = recording.stream.rate
    part = recording.samples[int(body.start_s * rate) : int(body.end_s * rate)]
    if len(part) < SOUND["record_min_s"] * rate:
        raise HTTPException(400, f"Keep at least {SOUND['record_min_s']:g} s of the recording")
    encoded = await asyncio.to_thread(sounds.encode_clip, part, rate)
    result = await asyncio.to_thread(_add_clip, rt, sensor_id, encoded, body.label, body.new_name, "recording")
    rt.sound_recordings.pop(sensor_id, None)
    await _taught(rt, sensor_id)
    return result


@router.delete("/sensors/{sensor_id}/sounds/recording/{recording_id}", status_code=204)
async def discard_recording(sensor_id: int, recording_id: str, request: Request) -> Response:
    rt = runtime(request)
    if rt.get_recording(sensor_id, recording_id) is not None:
        rt.sound_recordings.pop(sensor_id, None)
    return Response(status_code=204)


@router.get("/sensors/{sensor_id}/sounds/taught")
def taught_clips(sensor_id: int, request: Request) -> dict:
    """The clips taught to a sound sensor, newest first, and how many per own sound."""
    rt = runtime(request)
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id, KIND_SOUND)
        rows = s.scalars(
            select(Sample)
            .where(Sample.sensor_id == sensor_id, Sample.object_label.is_not(None))
            .order_by(Sample.created_at.desc())
        ).all()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.object_label or ""] = counts.get(row.object_label or "", 0) + 1
        settings = merge_sound(sensor.sound)
        own = settings["custom"]
        room = rt.sound_room.get(sensor_id)
        builtin = builtin_keys(settings)
        answers = sound_answers(s, sensor_id, builtin)
        live = rt.live.get(sensor_id)
        tracks = live.sound.tracks if live is not None and live.sound is not None else {}
        lo_hi = SOUND_LIMITS["threshold"]
        return {
            "clips": [
                {"id": x.id, "label": x.object_label, "origin": x.origin, "created_at": iso(x.created_at)} for x in rows
            ],
            "counts": counts,
            "sounds": [{**c, "clips": counts.get(c["key"], 0)} for c in own],
            "builtin": [
                {
                    "key": key,
                    "name": sounds.SOUNDS[key].name,
                    "clips": counts.get(key, 0),
                    "not_clips": counts.get(sounds.not_label(key), 0),
                    "held": tracks[key].held_count if key in tracks else 0,
                    "threshold": (threshold := settings["thresholds"].get(key, sounds.SOUNDS[key].threshold)),
                    "model_threshold": sounds.SOUNDS[key].threshold,
                    "advice": advice.threshold_advice(*answers.get(key, ([], [])), threshold, lo_hi),
                }
                for key in builtin
            ],
            "room": int(len(room)) if isinstance(room, np.ndarray) and room.size else 0,
            "trained": sensor_id in rt.sound_heads,
        }


@router.get("/sound-clips/{sample_id}")
def taught_clip(sample_id: int, request: Request) -> Response:
    """A taught clip (AAC in MP4)."""
    rt = runtime(request)
    with rt.db.session() as s:
        sample = s.get(Sample, sample_id)
        if sample is None or not sample.filename.endswith(CLIP_SUFFIX):
            raise HTTPException(404, "Clip not found")
        path = rt.storage.sample_path(sample.sensor_id, sample.filename)
    if not path.exists():
        raise HTTPException(404, "Clip file missing")
    return FileResponse(path, media_type="audio/mp4", headers={"Cache-Control": "max-age=86400"})


@router.delete("/sound-clips/{sample_id}", status_code=204)
async def forget_clip(sample_id: int, request: Request) -> Response:
    rt = runtime(request)

    def delete() -> int:
        with rt.db.session() as s:
            sample = s.get(Sample, sample_id)
            if sample is None or not sample.filename.endswith(CLIP_SUFFIX):
                raise HTTPException(404, "Clip not found")
            sensor_id = sample.sensor_id
            rt.db.after_commit(s, partial(rt.storage.delete_sample, sensor_id, sample.id, sample.filename))
            s.delete(sample)
            return sensor_id

    sensor_id = await asyncio.to_thread(delete)
    await _taught(rt, sensor_id)
    return Response(status_code=204)


@router.delete("/sensors/{sensor_id}/sounds/{key}", status_code=204)
async def forget_sound(sensor_id: int, key: str, request: Request) -> Response:
    """Forget an own sound: its clips and its entity."""
    rt = runtime(request)

    def forget() -> None:
        with rt.db.session() as s:
            sensor = get_sensor(s, sensor_id, KIND_SOUND)
            sound = merge_sound(sensor.sound)
            if key not in {c["key"] for c in sound["custom"]}:
                raise HTTPException(404, "No such own sound")
            sensor.sound = {
                **(sensor.sound or {}),
                "custom": [c for c in sound["custom"] if c["key"] != key],
                "thresholds": {k: v for k, v in sound["thresholds"].items() if k != key},
            }
            for sample in s.scalars(select(Sample).where(Sample.sensor_id == sensor_id, Sample.object_label == key)):
                rt.db.after_commit(s, partial(rt.storage.delete_sample, sensor_id, sample.id, sample.filename))
                s.delete(sample)

    await asyncio.to_thread(forget)
    await _taught(rt, sensor_id)
    return Response(status_code=204)
