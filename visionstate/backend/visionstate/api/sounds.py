"""Sound sensors: trying a stream before a sensor is made, and teaching own sounds from clips (a
clip in the history, or an uploaded recording)."""

from __future__ import annotations

import asyncio
import time
from functools import partial

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import sounds
from ..db import Prediction, Sample, Sensor
from ..engine import Runtime
from ..settings import KIND_SOUND, SOUND, UPLOAD_LIMITS, merge_sound
from ..sources import AudioStream, SourceError, check_source
from ..storage import CLIP_SUFFIX, is_clip
from .common import API_PREFIX, get_sensor, iso, runtime, slugify

router = APIRouter(prefix=API_PREFIX, tags=["sounds"])
PREVIEW_S = 6.0  # how long a stream is listened to before a sound sensor is made
CLIP_S = 4.0  # what is kept of an uploaded recording: its loudest seconds


class PreviewIn(BaseModel):
    source: str = Field(min_length=1, max_length=1024)


@router.post("/sounds/preview")
async def preview(body: PreviewIn, request: Request) -> dict:
    """Listen to a stream for a few seconds (the wizard): whether it has sound, what kind (codec,
    sample rate), its level, and the model's sounds that scored best."""
    rt = runtime(request)
    try:
        check_source("rtsp", body.source)
    except SourceError as err:
        raise HTTPException(400, str(err)) from err
    stream = AudioStream(body.source)
    try:
        deadline = time.monotonic() + PREVIEW_S
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
    sound (``new_name``), or as "not any of them" (``label``: sounds.OTHER)."""

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


def _label(sensor: Sensor, label: str | None, new_name: str | None) -> tuple[str, bool]:
    """The label to teach (an own sound's key or OTHER), and whether a new own sound was made."""
    sound = merge_sound(sensor.sound)
    if new_name:
        made = own_sound(sound, new_name)
        sensor.sound = {**(sensor.sound or {}), "custom": [*sound["custom"], made]}
        return made["key"], True
    if label is not None and (label == sounds.OTHER or label in {c["key"] for c in sound["custom"]}):
        return label, False
    raise HTTPException(400, "Teach a clip as one of the sensor's own sounds, a new one, or as another sound")


def _add_clip(rt: Runtime, sensor_id: int, data: bytes, label: str | None, new_name: str | None, origin: str) -> dict:
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id, KIND_SOUND)
        key, made = _label(sensor, label, new_name)
        taught = s.scalars(select(Sample.id).where(Sample.sensor_id == sensor_id, Sample.object_label == key)).all()
        if key != sounds.OTHER and len(taught) >= SOUND["max_taught"]:
            raise HTTPException(400, f"An own sound can have at most {SOUND['max_taught']} clips")
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
        own = merge_sound(sensor.sound)["custom"]
        room = rt.sound_room.get(sensor_id)
        return {
            "clips": [
                {"id": x.id, "label": x.object_label, "origin": x.origin, "created_at": iso(x.created_at)} for x in rows
            ],
            "counts": counts,
            "sounds": [{**c, "clips": counts.get(c["key"], 0)} for c in own],
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
