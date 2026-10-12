"""Teaching object sensors: correct a box, give it an own label, or draw one the detector missed."""

from __future__ import annotations

import asyncio
from datetime import UTC, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from PIL import Image
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from .. import detectors, imaging, teach
from ..db import Person, Prediction, Sample
from ..engine import Runtime
from ..settings import FACE_UNKNOWN_KEY, HISTORY, KIND_OBJECTS, NONE_LABEL, TEACH, active_custom, merge_objects
from .common import API_PREFIX, get_sensor, iso, runtime, slugify
from .sensors import prediction_view

router = APIRouter(prefix=f"{API_PREFIX}/sensors", tags=["teach"])
RESERVED_KEYS = {NONE_LABEL, FACE_UNKNOWN_KEY}  # never the key of an own label


class TeachIn(BaseModel):
    """One box to teach, from the frame a check analysed (``frame_id``) or a history frame."""

    frame_id: str | None = Field(default=None, max_length=64)
    history_id: int | None = None
    box: list[float] = Field(min_length=4, max_length=4)  # x1, y1, x2, y2 normalised to the frame
    detected: str | None = None  # the detector's class for the box; None: a box it missed (drawn)
    score: float | None = Field(default=None, ge=0, le=1)
    label: str | None = Field(default=None, max_length=64)  # "none", one of the sensor's classes or own labels
    new_label: str | None = Field(default=None, min_length=1, max_length=64)  # name of a new own label for the box
    parent: str | None = None  # the class a new own label is a kind of (default: ``detected``)

    @field_validator("box")
    @classmethod
    def _valid_box(cls, value: list[float]) -> list[float]:
        x1, y1, x2, y2 = (min(max(float(v), 0.0), 1.0) for v in value)
        if x2 - x1 < TEACH["min_box"] or y2 - y1 < TEACH["min_box"]:
            raise ValueError("The box is too small")
        return [x1, y1, x2, y2]


def own_label(objects: dict, name: str, parent: str, people: set[str]) -> dict:
    """A new own label: its key never clashes with an object VisionState knows, another own label
    or a person (``people``: their keys; they are entities of the sensor too)."""
    if parent not in objects["classes"]:
        raise HTTPException(400, "An own label belongs to one of the sensor's objects")
    if len(objects["custom"]) >= TEACH["max_labels"]:
        raise HTTPException(400, f"A sensor can have at most {TEACH['max_labels']} own labels")
    name = name.strip()
    if any(label["name"].lower() == name.lower() for label in objects["custom"]):
        raise HTTPException(400, f"There is already a label called {name}")
    taken = set(detectors.LABELS.by_key) | {label["key"] for label in objects["custom"]} | RESERVED_KEYS | people
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


async def frame_image(
    rt: Runtime, sensor_id: int, frame_id: str | None, history_id: int | None
) -> tuple[Image.Image, str]:
    """The frame a box was drawn on, and where it came from ("live" or "history")."""
    if history_id is not None:
        with rt.db.session() as s:
            row = s.get(Prediction, history_id)
            if row is None or row.sensor_id != sensor_id or not row.frame:
                raise HTTPException(404, "History frame not found")
            path = rt.storage.history_path(sensor_id, row.frame)
        if not path.exists():
            raise HTTPException(404, "History frame file missing")
        return await asyncio.to_thread(imaging.load, path), "history"
    if frame_id:
        data = rt.live_state(sensor_id).analysed_frame(frame_id)
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
            new = own_label(
                objects, body.new_label, body.parent or body.detected or "", set(s.scalars(select(Person.key)))
            )
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
    image, origin = await frame_image(rt, sensor_id, body.frame_id, body.history_id)
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
            seen = await rt.seen_faintly(cfg, image, body.box) if cfg else None
        except Exception:  # noqa: BLE001 - detector unavailable: the box is taught anyway
            seen = None
    if new is not None:
        await rt.sensor_updated(sensor_id, retrain=False)  # the new label's entities
    rt.taught_changed(sensor_id)
    if body.history_id is not None:
        await asyncio.to_thread(remember_taught, rt, sensor_id, body.history_id, body.box, label, body.detected)
    return {"id": example_id, "label": label, "seen": seen}


def remember_taught(
    rt: Runtime,
    sensor_id: int,
    history_id: int,
    box: list[float],
    label: str,
    detected: str | None,
    person: bool = False,
) -> None:
    """A box taught from a history frame (or a review answer): the row shows it with its new label
    from then on, and an own label or person counts as seen at that frame's time (so the sensor no
    longer says "not seen yet" about what was just taught from it)."""
    with rt.db.session() as s:
        row = s.get(Prediction, history_id)
        if row is None or row.sensor_id != sensor_id:
            return
        key = _mark_taught(row, box, label, detected, add=True) or label
        # The same moment's other rows (a face seen, and the question about it) show it too.
        moment = timedelta(seconds=HISTORY["same_moment_s"])
        for other in s.scalars(
            select(Prediction).where(
                Prediction.sensor_id == sensor_id,
                Prediction.id != row.id,
                Prediction.created_at.between(row.created_at - moment, row.created_at + moment),
            )
        ):
            _mark_taught(other, box, label, detected, add=False)
        created = row.created_at
        at = (created if created.tzinfo else created.replace(tzinfo=UTC)).timestamp()  # stored naive, in UTC
    if label not in (NONE_LABEL, key):
        rt.seen_earlier(sensor_id, label, at, person)


def _mark_taught(row: Prediction, box: list[float], label: str, detected: str | None, add: bool) -> str | None:
    """Write a taught box into a history row: the box of the row that is this one (or, ``add``,
    a new one) gets the label; returns the class it was found as, or None (no such box)."""
    found = [dict(d) for d in row.detections or []]
    best = max(found, key=lambda d: teach.box_iou(d["box"], box), default=None)
    if best is None or teach.box_iou(best["box"], box) < TEACH["keep_iou"]:
        if not add:
            return None
        best = {"key": detected or label, "score": 0.0, "box": list(box)}
        found.append(best)
    best.pop("ask", None)
    best.pop("filtered", None)
    if label in (NONE_LABEL, best["key"]):
        best.pop("label", None)  # not it, or just the class it was found as
        if label == NONE_LABEL:
            best["filtered"] = True
    else:
        best["label"] = label
    best["taught"] = True
    row.detections = found
    return best["key"]


def delete_examples(rt: Runtime, s: Session, sensor_id: int, where: list[ColumnElement[bool]]) -> int:
    """Delete the taught boxes of a sensor that match ``where`` (all with an empty list); returns how many."""
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
        if not delete_examples(rt, s, sensor_id, [Sample.id == example_id]):
            raise HTTPException(404, "Taught box not found")
    rt.taught_changed(sensor_id)
    return Response(status_code=204)


async def remove_labels(rt: Runtime, sensor_id: int, keys: set[str] | None) -> None:
    """Remove own labels (``None``: all of them, and every taught box) with their entities."""
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id, KIND_OBJECTS)
        objects = merge_objects(sensor.objects)
        gone = [c["key"] for c in objects["custom"] if keys is None or c["key"] in keys]
        if keys is not None and not gone:
            raise HTTPException(404, "Label not found")
        delete_examples(rt, s, sensor_id, [] if keys is None else [Sample.object_label.in_(gone)])
        sensor.objects = {**(sensor.objects or {}), "custom": [c for c in objects["custom"] if c["key"] not in gone]}
        slug = sensor.slug
    for key in gone:
        await rt.mqtt.remove_object_class(slug, key)
    await rt.sensor_updated(sensor_id, retrain=False)
    rt.taught_changed(sensor_id)


@router.delete("/{sensor_id}/taught", status_code=204)
async def forget_all(sensor_id: int, request: Request) -> Response:
    """Forget everything an object sensor was taught: every box and every own label."""
    await remove_labels(runtime(request), sensor_id, None)
    return Response(status_code=204)


@router.delete("/{sensor_id}/labels/{key}", status_code=204)
async def remove_label(sensor_id: int, key: str, request: Request) -> Response:
    """Remove an own label, the boxes taught with it and its entities in Home Assistant."""
    await remove_labels(runtime(request), sensor_id, {key})
    return Response(status_code=204)


def import_custom(stored: object) -> list[dict]:
    """Own labels from an exported sensor, checked like new ones (people come with their faces,
    api/imports.py)."""
    labels: list[dict] = []
    for item in stored if isinstance(stored, list) else []:
        try:
            key, name, parent = str(item["key"]), str(item["name"]).strip(), str(item["parent"])
        except KeyError, TypeError:
            continue
        taken = set(detectors.LABELS.by_key) | {label["key"] for label in labels} | RESERVED_KEYS
        if item.get("face") or slugify(key, "") != key or key in taken or not name:
            continue
        if parent not in detectors.LABELS.by_key or len(labels) >= TEACH["max_labels"]:
            continue
        labels.append({"key": key, "name": name[:64], "parent": parent})
    return labels


def importable_label(label: object, own: set[str]) -> bool:
    """Whether a taught box from an exported sensor has a label this sensor knows."""
    return isinstance(label, str) and (label == NONE_LABEL or label in detectors.LABELS.by_key or label in own)
