"""Sensor endpoints: CRUD, live frames, quality, history and export."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import bundle
from ..db import ModelInfo, Prediction, Sample, Sensor, State
from ..settings import (
    KIND_OBJECTS,
    KIND_STATES,
    OBJECT_SENSOR_DEFAULTS,
    QUALITY,
    SENSOR_DEFAULTS,
    SENSOR_KINDS,
    SENSOR_LIMITS,
    STATE_PALETTE,
)
from ..sources import SOURCE_TYPES, SourceError
from .common import (
    API_PREFIX,
    ObjectsIn,
    ReviewOverrides,
    Roi,
    StateIn,
    Triggers,
    get_sensor,
    iso,
    runtime,
    sample_counts,
    sensor_view,
    slugify,
    unique_slug,
    validate_states,
)

router = APIRouter(prefix=f"{API_PREFIX}/sensors", tags=["sensors"])

lo = {k: v[0] for k, v in SENSOR_LIMITS.items()}
hi = {k: v[1] for k, v in SENSOR_LIMITS.items()}


class SensorIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = KIND_STATES  # settings.SENSOR_KINDS; fixed after creation
    source_type: str
    source: str = Field(min_length=1, max_length=1024)
    roi: Roi | None = None
    states: list[StateIn] = Field(default_factory=list)  # state sensors
    objects: ObjectsIn | None = None  # object sensors
    # Unset: the defaults of the sensor's kind (SENSOR_DEFAULTS / OBJECT_SENSOR_DEFAULTS).
    interval_s: float | None = Field(None, ge=lo["interval_s"], le=hi["interval_s"])
    threshold: float | None = Field(None, ge=lo["threshold"], le=hi["threshold"])
    debounce: int | None = Field(None, ge=lo["debounce"], le=hi["debounce"])
    enabled: bool = True
    triggers: Triggers | None = None
    review: ReviewOverrides | None = None

    def checked(self) -> SensorIn:
        """Validates the kind-specific parts and fills in the kind's defaults."""
        if self.kind not in SENSOR_KINDS:
            raise HTTPException(400, f"Unknown sensor kind {self.kind!r}")
        _check_source_type(self.source_type)
        if self.kind == KIND_OBJECTS:
            if self.states:
                raise HTTPException(400, "Object sensors have no states")
            self.objects = self.objects or ObjectsIn()
            defaults = OBJECT_SENSOR_DEFAULTS
        else:
            validate_states(self.states)
            if self.objects is not None:
                raise HTTPException(400, "Only object sensors have objects")
            defaults = SENSOR_DEFAULTS
        for key in ("interval_s", "threshold", "debounce"):
            if getattr(self, key) is None:
                setattr(self, key, defaults[key])
        return self

    def new_sensor(self, slug: str) -> Sensor:
        sensor = Sensor(
            slug=slug,
            name=self.name,
            kind=self.kind,
            source_type=self.source_type,
            source=self.source,
            roi=self.roi.normalised() if self.roi else None,
            interval_s=self.interval_s,
            threshold=self.threshold,
            debounce=self.debounce,
            enabled=self.enabled,
            triggers=self.triggers.model_dump() if self.triggers else None,
            review=self.review.stored() if self.review else None,
            objects=self.objects.model_dump() if self.objects else None,
        )
        _apply_states(sensor, self.states)
        return sensor


class SensorPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    source_type: str | None = None
    source: str | None = Field(None, min_length=1, max_length=1024)
    roi: Roi | None = None
    clear_roi: bool = False
    states: list[StateIn] | None = None
    interval_s: float | None = Field(None, ge=lo["interval_s"], le=hi["interval_s"])
    threshold: float | None = Field(None, ge=lo["threshold"], le=hi["threshold"])
    debounce: int | None = Field(None, ge=lo["debounce"], le=hi["debounce"])
    enabled: bool | None = None
    triggers: Triggers | None = None
    review: ReviewOverrides | None = None
    objects: ObjectsIn | None = None


def _check_source_type(source_type: str | None) -> None:
    if source_type is not None and source_type not in SOURCE_TYPES:
        raise HTTPException(400, f"Unknown source type {source_type!r}")


def _apply_states(sensor: Sensor, states: list[StateIn]) -> None:
    """Sync states by key: keep existing ones (and their labels), add new, drop removed."""
    existing = {s.key: s for s in sensor.states}
    used_colors = {s.color for s in sensor.states}
    palette = [c for c in STATE_PALETTE if c not in used_colors] + STATE_PALETTE
    new_states = []
    for position, item in enumerate(states):
        key = item.key or slugify(item.name, "state")
        state = existing.pop(key, None)
        if state is None:
            state = State(key=key, name=item.name, color=item.color or palette.pop(0))
        else:
            state.name = item.name
            state.color = item.color or state.color
        state.position = position
        new_states.append(state)
    sensor.states = new_states


@router.get("")
def list_sensors(request: Request) -> list[dict]:
    rt = runtime(request)
    with rt.db.session() as s:
        return [sensor_view(rt, s, sensor) for sensor in s.scalars(select(Sensor).order_by(Sensor.name))]


@router.post("", status_code=201)
async def create_sensor(body: SensorIn, request: Request) -> dict:
    rt = runtime(request)
    body.checked()
    with rt.db.session() as s:
        sensor = body.new_sensor(unique_slug(s, body.name))
        s.add(sensor)
        s.flush()
        sensor_id = sensor.id
    await rt.sensor_created(sensor_id)
    return get_one(sensor_id, request)


@router.get("/{sensor_id}")
def get_one(sensor_id: int, request: Request) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        return sensor_view(rt, s, get_sensor(s, sensor_id))


@router.patch("/{sensor_id}")
async def update_sensor(sensor_id: int, body: SensorPatch, request: Request) -> dict:
    rt = runtime(request)
    _check_source_type(body.source_type)
    if body.states is not None:
        validate_states(body.states)
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id)
        if sensor.kind == KIND_OBJECTS and body.states is not None:
            raise HTTPException(400, "Object sensors have no states")
        if sensor.kind != KIND_OBJECTS and body.objects is not None:
            raise HTTPException(400, "Only object sensors have objects")
        old_roi, old_keys = sensor.roi, [st.key for st in sensor.states]
        for field in ("name", "source_type", "source", "interval_s", "threshold", "debounce", "enabled"):
            value = getattr(body, field)
            if value is not None:
                setattr(sensor, field, value)
        if body.review is not None:
            sensor.review = body.review.stored()
        if body.triggers is not None:
            sensor.triggers = body.triggers.model_dump()
        if body.clear_roi:
            sensor.roi = None
        elif body.roi is not None:
            sensor.roi = body.roi.normalised()
        if body.states is not None:
            _apply_states(sensor, body.states)
        if body.objects is not None:
            sensor.objects = body.objects.model_dump()
        s.flush()
        changed = sensor.roi != old_roi or [st.key for st in sensor.states] != old_keys
        retrain = changed and sensor.kind == KIND_STATES
    await rt.sensor_updated(sensor_id, retrain=retrain)
    return get_one(sensor_id, request)


@router.delete("/{sensor_id}", status_code=204)
async def delete_sensor(sensor_id: int, request: Request) -> Response:
    rt = runtime(request)
    cfg = await asyncio.to_thread(rt.load_sensor, sensor_id)
    if cfg is None:
        raise HTTPException(404, "Sensor not found")
    with rt.db.session() as s:
        s.delete(get_sensor(s, sensor_id))
    await rt.sensor_deleted(sensor_id, cfg.descriptor)
    return Response(status_code=204)


@router.get("/{sensor_id}/frame")
async def live_frame(sensor_id: int, request: Request, cached: bool = False, frame_id: str | None = None) -> Response:
    """A fresh frame from the sensor's camera. ``X-Frame-Id`` identifies it for labelling.

    ``frame_id`` returns that recent frame (e.g. the one the last check analysed) while it is
    still cached; 404 once it is gone.
    """
    rt = runtime(request)
    cfg = await asyncio.to_thread(rt.load_sensor, sensor_id)
    if cfg is None:
        raise HTTPException(404, "Sensor not found")
    live = rt.live_state(sensor_id)
    if frame_id is not None:
        data = next((d for fid, d in live.frames if fid == frame_id), None)
        if data is None:
            raise HTTPException(404, "Frame no longer cached")
        headers = {"X-Frame-Id": frame_id, "Cache-Control": "max-age=3600"}
        return Response(data, media_type="image/jpeg", headers=headers)
    if cached and live.frames:
        frame_id, data = live.frames[-1]
    else:
        try:
            frame_id, data = await rt.grab(cfg)
        except SourceError as err:
            raise HTTPException(502, f"Camera unavailable: {err}") from err
    return Response(data, media_type="image/jpeg", headers={"X-Frame-Id": frame_id, "Cache-Control": "no-store"})


@router.post("/{sensor_id}/classify")
async def classify_now(sensor_id: int, request: Request) -> dict:
    runtime(request).wake(sensor_id, force=True)
    return {"ok": True}


@router.post("/{sensor_id}/retrain")
async def retrain(sensor_id: int, request: Request) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        get_sensor(s, sensor_id, KIND_STATES)
    rt.schedule_retrain(sensor_id, delay=0)
    return {"ok": True}


def quality_tips(states: list[dict], counts: dict, confusion: dict | None, suspects: int = 0) -> list[dict]:
    """Human-readable suggestions derived from the dataset. Thresholds live in settings.QUALITY."""
    tips: list[dict] = []
    if suspects:
        tips.append(
            {
                "level": "warn",
                "title": f"{suspects} image{'s' if suspects != 1 else ''} may be labelled wrong",
                "text": "Check them under “Possibly mislabelled” below — one wrong label can pull the whole sensor down.",
                "action": "suspects",
            }
        )
    per_state = counts["per_state"]
    names = {s["key"]: s["name"] for s in states}
    totals = {k: v["day"] + v["night"] for k, v in per_state.items()}
    target = QUALITY["min_samples_per_state"]
    for key, total in totals.items():
        if total < target:
            tips.append(
                {
                    "level": "warn",
                    "title": f"{names[key]} has {total} samples",
                    "text": f"Aim for at least {target}. Label more on the Label or Upload tab.",
                    "action": "label",
                }
            )
    night_total = sum(v["night"] for v in per_state.values())
    if night_total:
        for key, v in per_state.items():
            if v["night"] < QUALITY["min_night_samples"]:
                tips.append(
                    {
                        "level": "warn",
                        "title": f"{names[key]} has only {v['night']} night images",
                        "text": "Label a few after dark so IR frames are recognised.",
                        "action": "review",
                    }
                )
    if confusion:
        keys, matrix = confusion["keys"], confusion["matrix"]
        worst = max(
            ((matrix[i][j], i, j) for i in range(len(keys)) for j in range(len(keys)) if i != j),
            default=(0, 0, 0),
        )
        if worst[0] > 0:
            a, b = names.get(keys[worst[1]], keys[worst[1]]), names.get(keys[worst[2]], keys[worst[2]])
            tips.append(
                {
                    "level": "warn",
                    "title": f"{a} is sometimes read as {b}",
                    "text": "Add borderline examples, or tighten the region so it only covers what changes.",
                    "action": "upload",
                }
            )
    largest = max(totals.values(), default=0)
    if largest and all(t >= largest * QUALITY["imbalance_ratio"] for t in totals.values()):
        tips.append(
            {
                "level": "ok",
                "title": "States are well balanced",
                "text": "No state has less than half the samples of the largest.",
                "action": None,
            }
        )
    return tips


@router.get("/{sensor_id}/quality")
def quality(sensor_id: int, request: Request) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id, KIND_STATES)
        view = sensor_view(rt, s, sensor)
        info = s.get(ModelInfo, sensor_id)
        counts = sample_counts(s, sensor)
        confusion = info.confusion if info else None
        suspects = current_suspects(s, sensor, info)
        return {
            "sensor": view,
            "counts": counts,
            "confusion": confusion,
            "accuracy": info.accuracy if info else None,
            "suspects": suspects,
            "tips": quality_tips(view["states"], counts, confusion, len(suspects)),
            "targets": QUALITY,
        }


def current_suspects(session, sensor: Sensor, info: ModelInfo | None) -> list[dict]:
    """Suspects from the last training that still exist, are unverified and still carry that label."""
    if info is None or not info.suspects:
        return []
    key_by_state = {st.id: st.key for st in sensor.states}
    ids = [item["sample_id"] for item in info.suspects]
    samples = {x.id: x for x in session.scalars(select(Sample).where(Sample.id.in_(ids)))}
    result = []
    for item in info.suspects:
        sample = samples.get(item["sample_id"])
        if sample is None or sample.verified:
            continue
        labels = [key_by_state.get(lab.state_id) for lab in sample.labels]
        if item["label"] in labels:
            result.append(item)
    return result


def prediction_view(p: Prediction) -> dict:
    return {
        "id": p.id,
        "sensor_id": p.sensor_id,
        "created_at": iso(p.created_at),
        "state_key": p.state_key,
        "published_key": p.published_key,
        "confidence": p.confidence,
        "probs": p.probs,
        "is_change": p.is_change,
        "review_reason": p.review_reason,
        "reviewed": p.reviewed,
        "has_frame": bool(p.frame),
        "detections": p.detections,
    }


@router.get("/{sensor_id}/history")
def history(sensor_id: int, request: Request, limit: int = 100, offset: int = 0) -> list[dict]:
    rt = runtime(request)
    with rt.db.session() as s:
        get_sensor(s, sensor_id)
        rows = s.scalars(
            select(Prediction)
            .where(Prediction.sensor_id == sensor_id)
            .order_by(Prediction.created_at.desc())
            .limit(min(limit, 500))
            .offset(offset)
        )
        return [prediction_view(p) for p in rows]


@router.get("/{sensor_id}/export")
async def export(sensor_id: int, request: Request, background: BackgroundTasks) -> FileResponse:
    rt = runtime(request)
    fd, name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    tmp = Path(name)
    try:
        filename = await asyncio.to_thread(bundle.export_sensor, rt.db, rt.storage, sensor_id, tmp)
    except KeyError as err:
        tmp.unlink(missing_ok=True)
        raise HTTPException(404, "Sensor not found") from err
    background.add_task(tmp.unlink, missing_ok=True)
    return FileResponse(tmp, media_type="application/zip", filename=filename)
