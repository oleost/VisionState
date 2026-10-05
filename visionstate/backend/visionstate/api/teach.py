"""Teaching object sensors: correct a box, give it an own label, or draw one the detector missed."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from PIL import Image
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from .. import detectors, imaging
from ..db import Prediction, Sample
from ..settings import KIND_OBJECTS, NONE_LABEL, TEACH, active_custom, merge_objects
from .common import API_PREFIX, get_sensor, iso, runtime, slugify
from .sensors import prediction_view

router = APIRouter(prefix=f"{API_PREFIX}/sensors", tags=["teach"])


class TeachIn(BaseModel):
    """One box to teach, from the frame a check analysed (``frame_id``) or a history frame."""

    frame_id: str | None = Field(None, max_length=64)
    history_id: int | None = None
    box: list[float] = Field(min_length=4, max_length=4)  # x1, y1, x2, y2 normalised to the frame
    detected: str | None = None  # the detector's class for the box; None: a box it missed (drawn)
    score: float | None = Field(None, ge=0, le=1)
    label: str | None = Field(None, max_length=64)  # "none", one of the sensor's classes or own labels
    new_label: str | None = Field(None, min_length=1, max_length=64)  # name of a new own label for the box
    parent: str | None = None  # the class a new own label is a kind of (default: ``detected``)

    @field_validator("box")
    @classmethod
    def _valid_box(cls, value: list[float]) -> list[float]:
        x1, y1, x2, y2 = (min(max(float(v), 0.0), 1.0) for v in value)
        if x2 - x1 < TEACH["min_box"] or y2 - y1 < TEACH["min_box"]:
            raise ValueError("The box is too small")
        return [x1, y1, x2, y2]


def _own_label(objects: dict, name: str, parent: str) -> dict:
    """A new own label: its key never clashes with an object VisionState knows or another own label."""
    if parent not in objects["classes"]:
        raise HTTPException(400, "An own label belongs to one of the sensor's objects")
    if len(objects["custom"]) >= TEACH["max_labels"]:
        raise HTTPException(400, f"A sensor can have at most {TEACH['max_labels']} own labels")
    name = name.strip()
    if any(label["name"].lower() == name.lower() for label in objects["custom"]):
        raise HTTPException(400, f"There is already a label called {name}")
    taken = set(detectors.LABELS.by_key) | {label["key"] for label in objects["custom"]} | {NONE_LABEL}
    base = slugify(name, "label")
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return {"key": key, "name": name, "parent": parent}


def _example_view(x: Sample) -> dict:
    return {
        "id": x.id,
        "label": x.object_label,
        "detected": x.detected,
        "score": x.score,
        "origin": x.origin,
        "created_at": iso(x.created_at),
    }


@router.get("/{sensor_id}/taught")
def taught(sensor_id: int, request: Request) -> dict:
    """What an object sensor was taught, and the boxes it filtered away lately."""
    rt = runtime(request)
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id, KIND_OBJECTS)
        objects = merge_objects(sensor.objects)
        examples = s.scalars(
            select(Sample)
            .where(Sample.sensor_id == sensor_id, Sample.object_label.is_not(None))
            .order_by(Sample.created_at.desc())
        ).all()
        filtered = s.scalars(
            select(Prediction)
            .where(Prediction.sensor_id == sensor_id, Prediction.published_key == "filtered")
            .order_by(Prediction.created_at.desc())
            .limit(TEACH["list_limit"])
        ).all()
        active = {label["key"] for label in active_custom(objects)}
        return {
            "use_taught": objects["use_taught"],
            "labels": [{**label, "active": label["key"] in active} for label in objects["custom"]],
            "examples": [_example_view(x) for x in examples],
            "filtered": [prediction_view(p) for p in filtered],
        }


async def _frame_image(rt, sensor_id: int, body: TeachIn) -> tuple[Image.Image, str]:
    """The frame the box was drawn on, and where it came from ("live" or "history")."""
    if body.history_id is not None:
        with rt.db.session() as s:
            row = s.get(Prediction, body.history_id)
            if row is None or row.sensor_id != sensor_id or not row.frame:
                raise HTTPException(404, "History frame not found")
            path = rt.storage.history_path(sensor_id, row.frame)
        if not path.exists():
            raise HTTPException(404, "History frame file missing")
        return await asyncio.to_thread(lambda: Image.open(path).convert("RGB")), "history"
    if body.frame_id:
        data = rt.live_state(sensor_id).analysed_frame(body.frame_id)
        if data is None:
            raise HTTPException(409, "That frame is no longer kept. Tap the box again on the newest frame.")
        return await asyncio.to_thread(imaging.decode, data), "live"
    raise HTTPException(400, "Give the frame (frame_id or history_id) the box is on")


@router.post("/{sensor_id}/taught", status_code=201)
async def teach_box(sensor_id: int, body: TeachIn, request: Request) -> dict:
    """Teach one box: what it really is ("none" = not what the detector said), or a new own label.

    ``seen`` (boxes the detector missed): whether it sees anything there at all, however unsure;
    if not, it cannot be found there later either.
    """
    rt = runtime(request)
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id, KIND_OBJECTS)
        objects = merge_objects(sensor.objects)
        count = s.scalar(
            select(func.count())
            .select_from(Sample)
            .where(Sample.sensor_id == sensor_id, Sample.object_label.is_not(None))
        )
        if (count or 0) >= TEACH["max_examples"]:
            raise HTTPException(400, f"A sensor can be taught at most {TEACH['max_examples']} boxes")
        if body.detected is not None and body.detected not in detectors.LABELS.by_key:
            raise HTTPException(400, f"Unknown object {body.detected!r}")
        new = None
        if body.new_label:
            new = _own_label(objects, body.new_label, body.parent or body.detected or "")
            label = new["key"]
        else:
            active = {c["key"] for c in active_custom(objects)}
            label = body.label or ""
            if label == NONE_LABEL and body.detected is None:
                raise HTTPException(400, "A box the detector missed needs a label")
            if label != NONE_LABEL and label not in objects["classes"] and label not in active:
                raise HTTPException(400, f"Not one of the sensor's objects or labels: {label!r}")
        roi = sensor.roi
    x1, _, x2, y2 = body.box
    if not imaging.in_region((x1 + x2) / 2, y2, roi):
        raise HTTPException(400, "That box is outside the sensor's region, so it never counts")
    image, origin = await _frame_image(rt, sensor_id, body)
    if new is not None:
        with rt.db.session() as s:
            row = get_sensor(s, sensor_id)
            stored = merge_objects(row.objects)
            row.objects = {**(row.objects or {}), "custom": [*stored["custom"], new]}
    example_id = await asyncio.to_thread(
        rt.add_object_example, sensor_id, image, body.box, label, body.detected, body.score, origin
    )
    seen = None
    if body.detected is None:
        cfg = await asyncio.to_thread(rt.load_sensor, sensor_id)
        try:
            seen = await rt.seen_faintly(cfg, image, body.box)
        except Exception:  # noqa: BLE001 - detector unavailable: the box is taught anyway
            seen = None
    if new is not None:
        await rt.sensor_updated(sensor_id, retrain=False)  # the new label's entities
    rt.taught_changed(sensor_id)
    return {"id": example_id, "label": label, "seen": seen}


def _delete_examples(rt, s, sensor_id: int, where) -> int:
    rows = s.scalars(
        select(Sample).where(Sample.sensor_id == sensor_id, Sample.object_label.is_not(None), *where)
    ).all()
    for row in rows:
        rt.storage.delete_sample(sensor_id, row.id, row.filename)
        s.delete(row)
    return len(rows)


@router.delete("/{sensor_id}/taught/{example_id}", status_code=204)
def forget_box(sensor_id: int, example_id: int, request: Request) -> Response:
    rt = runtime(request)
    with rt.db.session() as s:
        get_sensor(s, sensor_id, KIND_OBJECTS)
        if not _delete_examples(rt, s, sensor_id, [Sample.id == example_id]):
            raise HTTPException(404, "Taught box not found")
    rt.taught_changed(sensor_id)
    return Response(status_code=204)


async def _remove_labels(rt, sensor_id: int, keys: set[str] | None) -> None:
    """Remove own labels (``None``: all of them, and every taught box) with their entities."""
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id, KIND_OBJECTS)
        objects = merge_objects(sensor.objects)
        gone = [c["key"] for c in objects["custom"] if keys is None or c["key"] in keys]
        if keys is not None and not gone:
            raise HTTPException(404, "Label not found")
        _delete_examples(rt, s, sensor_id, [] if keys is None else [Sample.object_label.in_(gone)])
        sensor.objects = {**(sensor.objects or {}), "custom": [c for c in objects["custom"] if c["key"] not in gone]}
        slug = sensor.slug
    for key in gone:
        await rt.mqtt.remove_object_class(slug, key)
    await rt.sensor_updated(sensor_id, retrain=False)
    rt.taught_changed(sensor_id)


@router.delete("/{sensor_id}/taught", status_code=204)
async def forget_all(sensor_id: int, request: Request) -> Response:
    """Forget everything an object sensor was taught: every box and every own label."""
    await _remove_labels(runtime(request), sensor_id, None)
    return Response(status_code=204)


@router.delete("/{sensor_id}/labels/{key}", status_code=204)
async def remove_label(sensor_id: int, key: str, request: Request) -> Response:
    """Remove an own label, the boxes taught with it and its entities in Home Assistant."""
    await _remove_labels(runtime(request), sensor_id, {key})
    return Response(status_code=204)


def import_custom(stored) -> list[dict]:
    """Own labels from an exported sensor, checked like new ones."""
    labels: list[dict] = []
    for item in stored if isinstance(stored, list) else []:
        try:
            key, name, parent = str(item["key"]), str(item["name"]).strip(), str(item["parent"])
        except KeyError, TypeError:
            continue
        taken = set(detectors.LABELS.by_key) | {label["key"] for label in labels} | {NONE_LABEL}
        if slugify(key, "") != key or key in taken or not name or parent not in detectors.LABELS.by_key:
            continue
        labels.append({"key": key, "name": name[:64], "parent": parent})
        if len(labels) >= TEACH["max_labels"]:
            break
    return labels


def importable_label(label, own: set[str]) -> bool:
    """Whether a taught box from an exported sensor has a label this sensor knows."""
    return isinstance(label, str) and (label == NONE_LABEL or label in detectors.LABELS.by_key or label in own)
