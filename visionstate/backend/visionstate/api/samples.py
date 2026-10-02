"""Sample endpoints: capture, upload, list, label and delete training images."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from .. import imaging, uploads
from ..db import Sample, SampleLabel
from ..engine import SensorConfig
from ..settings import KIND_STATES, UPLOAD_LIMITS, VIDEO
from .common import API_PREFIX, get_sensor, iso, runtime, state_id_for

router = APIRouter(prefix=API_PREFIX, tags=["samples"])


class CaptureIn(BaseModel):
    state_key: str
    frame_id: str | None = None


class LabelIn(BaseModel):
    sample_ids: list[int]
    state_key: str | None  # None removes the label


class IdsIn(BaseModel):
    sample_ids: list[int]


def copy_limited(source, target, max_bytes: int, chunk: int = 1 << 20) -> bool:
    """Copies at most ``max_bytes``; returns False if the source was larger."""
    total = 0
    while data := source.read(chunk):
        total += len(data)
        if total > max_bytes:
            return False
        target.write(data)
    return True


def _retrain(request: Request, sensor_id: int) -> None:
    runtime(request).schedule_retrain(sensor_id)


@router.post("/sensors/{sensor_id}/capture", status_code=201)
async def capture(sensor_id: int, body: CaptureIn, request: Request) -> dict:
    """Label the frame the user is looking at (by frame id), or a fresh one."""
    rt = runtime(request)
    with rt.db.session() as s:
        state_id = state_id_for(get_sensor(s, sensor_id, KIND_STATES), body.state_key)
    data = rt.live_state(sensor_id).frame(body.frame_id) if body.frame_id else None
    if data is None:
        shown = await asyncio.to_thread(rt.frame_for_view, sensor_id)
        if shown is not None:
            data = shown[1]
    if data is None:
        cfg = await asyncio.to_thread(rt.load_sensor, sensor_id)
        _, data = await rt.grab(cfg)
    image = await asyncio.to_thread(imaging.decode, data)
    sample_id = await asyncio.to_thread(rt.add_sample, sensor_id, image, "snapshot", state_id)
    _retrain(request, sensor_id)
    return {"id": sample_id}


@router.post("/sensors/{sensor_id}/uploads", status_code=201)
async def upload(
    sensor_id: int,
    request: Request,
    files: list[UploadFile] = File(...),
    state_key: str | None = Form(None),
    frame_interval_s: float = Form(VIDEO["frame_interval_s"]),
    use_roi: bool = Form(True),
) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        state_id = state_id_for(get_sensor(s, sensor_id, KIND_STATES), state_key or None)

    created: list[int] = []
    errors: list[str] = []
    for upload_file in files:
        name = upload_file.filename or "upload"
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(name).suffix) as tmp:
            tmp_path = Path(tmp.name)
            too_large = not copy_limited(upload_file.file, tmp, UPLOAD_LIMITS["max_file_mb"] * 1_000_000)
        if too_large:
            tmp_path.unlink(missing_ok=True)
            errors.append(f"{name}: larger than {UPLOAD_LIMITS['max_file_mb']} MB")
            continue
        try:
            created += await asyncio.to_thread(
                _ingest, rt, sensor_id, tmp_path, name, frame_interval_s, state_id, use_roi
            )
        except (uploads.UploadError, OSError) as err:
            errors.append(f"{name}: {err}")
        finally:
            tmp_path.unlink(missing_ok=True)
    if created and state_id is not None:
        _retrain(request, sensor_id)
    return {"created": len(created), "sample_ids": created, "errors": errors}


def _ingest(rt, sensor_id, path, name, interval, state_id, use_roi) -> list[int]:
    return [
        rt.add_sample(sensor_id, image, origin, state_id, use_roi)
        for image, origin in uploads.frames_from_file(path, name, interval)
    ]


def _sample_view(sample: Sample, key_by_state: dict[int, str], suggestion) -> dict:
    return {
        "id": sample.id,
        "labels": [key_by_state[lab.state_id] for lab in sample.labels if lab.state_id in key_by_state],
        "origin": sample.origin,
        "is_night": sample.is_night,
        "use_roi": sample.use_roi,
        "created_at": iso(sample.created_at),
        "suggestion": {"key": suggestion[0], "confidence": suggestion[1]} if suggestion else None,
    }


@router.get("/sensors/{sensor_id}/samples")
async def list_samples(
    sensor_id: int,
    request: Request,
    filter: str = "all",  # all | labelled | unlabelled
    state: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id)
        key_by_state = {st.id: st.key for st in sensor.states}
        labelled_ids = select(SampleLabel.sample_id)
        query = select(Sample).where(Sample.sensor_id == sensor_id)
        if filter == "labelled":
            query = query.where(Sample.id.in_(labelled_ids))
        elif filter == "unlabelled":
            query = query.where(Sample.id.not_in(labelled_ids))
        if state:
            query = query.where(
                Sample.id.in_(select(SampleLabel.sample_id).where(SampleLabel.state_id == state_id_for(sensor, state)))
            )
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        samples = s.scalars(query.order_by(Sample.created_at.desc()).limit(min(limit, 1000)).offset(offset)).all()
        cfg = SensorConfig.from_row(sensor)
    unlabelled = [x for x in samples if not x.labels]
    suggestions = await asyncio.to_thread(rt.suggest, cfg, unlabelled) if unlabelled else {}
    return {
        "total": total,
        "items": [_sample_view(x, key_by_state, suggestions.get(x.id)) for x in samples],
    }


@router.post("/sensors/{sensor_id}/samples/label")
def label_samples(sensor_id: int, body: LabelIn, request: Request) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id)
        state_id = state_id_for(sensor, body.state_key)
        ids = s.scalars(select(Sample.id).where(Sample.sensor_id == sensor_id, Sample.id.in_(body.sample_ids))).all()
        s.execute(delete(SampleLabel).where(SampleLabel.sample_id.in_(ids)))
        if state_id is not None:
            s.add_all(SampleLabel(sample_id=i, state_id=state_id) for i in ids)
    _retrain(request, sensor_id)
    return {"updated": len(ids)}


@router.post("/sensors/{sensor_id}/samples/accept-suggestions")
async def accept_suggestions(sensor_id: int, body: IdsIn, request: Request) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id)
        cfg = SensorConfig.from_row(sensor)
        samples = [
            x
            for x in s.scalars(select(Sample).where(Sample.sensor_id == sensor_id, Sample.id.in_(body.sample_ids)))
            if not x.labels
        ]
    suggestions = await asyncio.to_thread(rt.suggest, cfg, samples)
    state_ids = {st["key"]: st["id"] for st in cfg.states}
    with rt.db.session() as s:
        s.add_all(SampleLabel(sample_id=i, state_id=state_ids[key]) for i, (key, _) in suggestions.items())
    if suggestions:
        _retrain(request, sensor_id)
    return {"updated": len(suggestions)}


@router.post("/sensors/{sensor_id}/samples/verify")
def verify_samples(sensor_id: int, body: IdsIn, request: Request) -> dict:
    """The user confirms these labels are right (removes them from "possibly mislabelled")."""
    rt = runtime(request)
    with rt.db.session() as s:
        get_sensor(s, sensor_id)
        samples = s.scalars(select(Sample).where(Sample.sensor_id == sensor_id, Sample.id.in_(body.sample_ids))).all()
        for sample in samples:
            sample.verified = True
    return {"verified": len(samples)}


@router.post("/sensors/{sensor_id}/samples/delete")
def delete_samples(sensor_id: int, body: IdsIn, request: Request) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        get_sensor(s, sensor_id)
        samples = s.scalars(select(Sample).where(Sample.sensor_id == sensor_id, Sample.id.in_(body.sample_ids))).all()
        for sample in samples:
            rt.storage.delete_sample(sensor_id, sample.id, sample.filename)
            s.delete(sample)
    _retrain(request, sensor_id)
    return {"deleted": len(samples)}


@router.get("/samples/{sample_id}/image")
def sample_image(sample_id: int, request: Request, size: str = "thumb") -> FileResponse:
    rt = runtime(request)
    with rt.db.session() as s:
        sample = s.get(Sample, sample_id)
        if sample is None:
            raise HTTPException(404, "Sample not found")
        path = rt.storage.sample_path(sample.sensor_id, sample.filename)
    if not path.exists():
        raise HTTPException(404, "Image file missing")
    if size == "thumb":
        path = rt.storage.thumbnail("sample", sample_id, path)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
