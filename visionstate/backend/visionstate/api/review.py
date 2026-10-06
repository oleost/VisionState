"""The review queue (frames waiting for an answer) and history frames."""

from __future__ import annotations

import asyncio
import logging
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import CursorResult, delete, func, select, update

from .. import readers
from ..db import Prediction, Sample, SampleLabel, Sensor
from ..settings import (
    KIND_READING,
    merge_reading,
)
from .common import (
    API_PREFIX,
    get_sensor,
    runtime,
    state_id_for,
)
from .sensors import prediction_view

log = logging.getLogger(__name__)
router = APIRouter(prefix=API_PREFIX, tags=["review"])


class ReviewIn(BaseModel):
    # State sensors: confirm | label (state_key) | skip. Reading sensors: read_ok | misread
    # (optionally the value that was right) | skip.
    action: str
    state_key: str | None = None
    value: str | None = Field(None, max_length=32)


@router.get("/review")
def review_queue(request: Request, limit: int = 50) -> dict:
    """The newest ``limit`` items waiting for review, and how many wait per sensor."""
    rt = runtime(request)
    with rt.db.session() as s:
        query = select(Prediction).where(Prediction.reviewed.is_(False))
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        rows = s.scalars(query.order_by(Prediction.created_at.desc()).limit(limit)).all()
        # Waiting items per sensor, most first (the list itself only holds the newest `limit`).
        waiting = s.execute(
            select(Sensor.id, Sensor.name, func.count(Prediction.id))
            .join(Prediction, Prediction.sensor_id == Sensor.id)
            .where(Prediction.reviewed.is_(False))
            .group_by(Sensor.id, Sensor.name)
            .order_by(func.count(Prediction.id).desc(), Sensor.name)
        ).all()
        sensors = {x.id: x for x in s.scalars(select(Sensor).where(Sensor.id.in_({r.sensor_id for r in rows})))}
        items = []
        for row in rows:
            sensor = sensors[row.sensor_id]
            items.append(
                {
                    **prediction_view(row),
                    "sensor": {
                        "id": sensor.id,
                        "name": sensor.name,
                        "kind": sensor.kind,
                        "roi": sensor.roi,
                        "states": [{"key": st.key, "name": st.name, "color": st.color} for st in sensor.states],
                        "reading": merge_reading(sensor.reading) if sensor.kind == KIND_READING else None,
                    },
                }
            )
        return {
            "total": total,
            "items": items,
            "sensors": [{"id": sid, "name": name, "count": n} for sid, name, n in waiting],
        }


@router.post("/review/sensors/{sensor_id}/dismiss")
async def review_dismiss_all(sensor_id: int, request: Request) -> dict:
    """Take every waiting item of one sensor out of the queue, as if each was skipped.

    Nothing is learned from them; answers already given and the reading counts stay.
    """
    rt = runtime(request)
    with rt.db.session() as s:
        get_sensor(s, sensor_id)
        result = s.execute(
            update(Prediction)
            .where(Prediction.sensor_id == sensor_id, Prediction.reviewed.is_(False))
            .values(reviewed=True)
        )
        dismissed = cast(CursorResult, result).rowcount
    await rt.publish_review_count()
    return {"dismissed": dismissed}


@router.post("/review/{prediction_id}")
async def review_answer(prediction_id: int, body: ReviewIn, request: Request) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        row = s.get(Prediction, prediction_id)
        if row is None:
            raise HTTPException(404, "Review item not found")
        sensor = get_sensor(s, row.sensor_id)
        if sensor.kind == KIND_READING:
            if body.action not in ("read_ok", "misread", "skip"):
                raise HTTPException(400, "Answer read_ok, misread or skip for a reading")
            if body.action != "skip":
                row.read_ok = body.action == "read_ok"
                row.correct_value = None
                if body.action == "misread" and body.value and body.value.strip():
                    settings = merge_reading(sensor.reading)
                    number = readers.right_value(body.value, settings)  # digits only: placed like the reader
                    if number is None:
                        raise HTTPException(400, f"Not a number: {body.value!r}")
                    row.correct_value = readers.format_value(number, settings)
            row.reviewed = True
        elif body.action not in ("confirm", "label", "skip"):
            raise HTTPException(400, "Answer confirm, label or skip")
    if sensor.kind == KIND_READING:
        await rt.publish_review_count()
        return {"ok": True}
    with rt.db.session() as s:
        row = s.get(Prediction, prediction_id)
        if row is None:  # removed in the meantime (history clean-up)
            raise HTTPException(404, "Review item not found")
        sensor = get_sensor(s, row.sensor_id)
        key = row.state_key if body.action == "confirm" else body.state_key
        state_id = state_id_for(sensor, key) if body.action in ("confirm", "label") else None
        sensor_id, frame = row.sensor_id, row.frame
        row.reviewed = True
        # Answered before: change (or, when skipped now, remove) the sample that answer added.
        sample = s.get(Sample, row.sample_id) if row.sample_id is not None else None
        if sample is not None and sample.sensor_id == sensor_id:
            if state_id is None:
                rt.storage.delete_sample(sensor_id, sample.id, sample.filename)
                s.delete(sample)
                row.sample_id = None
            else:
                s.execute(delete(SampleLabel).where(SampleLabel.sample_id == sample.id))
                s.add(SampleLabel(sample_id=sample.id, state_id=state_id))
                sample.verified = False
            rt.schedule_retrain(sensor_id)
            state_id = None  # nothing more to add
    if state_id is not None and frame:
        path = rt.storage.history_path(sensor_id, frame)
        if path.exists():
            image = await asyncio.to_thread(lambda: Image.open(path).convert("RGB"))
            sample_id = await asyncio.to_thread(rt.add_sample, sensor_id, image, "review", state_id)
            with rt.db.session() as s:
                answered = s.get(Prediction, prediction_id)
                if answered is not None:
                    answered.sample_id = sample_id
            rt.schedule_retrain(sensor_id)
    await rt.publish_review_count()
    return {"ok": True}


@router.get("/history/{prediction_id}/image")
def history_image(prediction_id: int, request: Request, size: str = "full") -> FileResponse:
    rt = runtime(request)
    with rt.db.session() as s:
        row = s.get(Prediction, prediction_id)
        if row is None or not row.frame:
            raise HTTPException(404, "Frame not found")
        path = rt.storage.history_path(row.sensor_id, row.frame)
    if not path.exists():
        raise HTTPException(404, "Frame file missing")
    if size == "thumb":
        path = rt.storage.thumbnail("history", prediction_id, path)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
