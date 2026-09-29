"""System endpoints: status, UI config, settings, cameras, review queue and import."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import backbones, bundle, imaging
from ..db import Prediction, Sensor, State
from ..settings import (
    MAX_STATES,
    QUALITY,
    REVIEW_DEFAULTS,
    REVIEW_LIMITS,
    SENSOR_DEFAULTS,
    SENSOR_LIMITS,
    STATE_PALETTE,
    TRIGGER_DEFAULTS,
    TRIGGER_LIMITS,
    TRIGGER_MAX_ENTITIES,
    UNKNOWN_STATE,
    UPLOAD_LIMITS,
    VERSION,
    VIDEO,
    merge_review,
)
from ..sources import SOURCE_TYPES, SourceError
from .common import API_PREFIX, ReviewRules, get_sensor, runtime, slugify, state_id_for, unique_slug, validate_states
from .samples import copy_limited
from .sensors import prediction_view

router = APIRouter(prefix=API_PREFIX, tags=["system"])


@router.get("/config")
def ui_config() -> dict:
    """Constants the UI needs, so they are defined once (in settings.py)."""
    return {
        "version": VERSION,
        "sensor_defaults": SENSOR_DEFAULTS,
        "sensor_limits": SENSOR_LIMITS,
        "state_palette": STATE_PALETTE,
        "max_states": MAX_STATES,
        "unknown_state": UNKNOWN_STATE,
        "source_types": SOURCE_TYPES,
        "video": VIDEO,
        "quality": QUALITY,
        "trigger_defaults": TRIGGER_DEFAULTS,
        "trigger_limits": TRIGGER_LIMITS,
        "trigger_max_entities": TRIGGER_MAX_ENTITIES,
        "review_defaults": REVIEW_DEFAULTS,
        "review_limits": REVIEW_LIMITS,
    }


@router.get("/status")
async def status(request: Request) -> dict:
    rt = runtime(request)

    def counts():
        with rt.db.session() as s:
            return (
                s.scalar(select(func.count()).select_from(Sensor)) or 0,
                s.scalar(select(func.count()).select_from(Prediction).where(Prediction.reviewed.is_(False))) or 0,
            )

    sensors, review = await asyncio.to_thread(counts)
    mqtt = rt.mqtt
    return {
        "version": VERSION,
        "sensors": sensors,
        "review_count": review,
        "backbone": rt.embedder.spec.id if rt.embedder else None,
        "backbone_name": rt.embedder.spec.name if rt.embedder else None,
        "provider": rt.embedder.provider if rt.embedder else None,
        "backbone_error": rt.embedder_error,
        "mqtt": {
            "connected": mqtt.connected,
            "host": mqtt.config.host if mqtt.config else None,
            "error": mqtt.last_error,
        },
        "home_assistant": rt.ha.enabled,
        "ha_events": {
            "enabled": rt.ha_events.enabled,
            "connected": rt.ha_events.connected,
            "entities": len(rt.ha_events.entities),
            "error": rt.ha_events.last_error,
        },
        "supervised": rt.settings.is_supervised,
    }


class SettingsIn(BaseModel):
    backbone: str
    execution_provider: str = "CPUExecutionProvider"


@router.get("/settings")
def get_settings(request: Request) -> dict:
    rt = runtime(request)
    return {
        "backbone": rt.embedder.spec.id if rt.embedder else backbones.DEFAULT_BACKBONE,
        "execution_provider": rt.embedder.provider if rt.embedder else "CPUExecutionProvider",
        "backbones": [
            {
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "installed": rt.model_path(spec) is not None,
                "size": spec.size,
            }
            for spec in backbones.BACKBONES.values()
        ],
        "providers": backbones.available_providers(),
        "options": {
            "history_retention_days": rt.settings.history_retention_days,
            "discovery_prefix": rt.settings.discovery_prefix,
            "mqtt_host": rt.settings.mqtt_host or "(from Home Assistant)",
        },
    }


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request) -> dict:
    rt = runtime(request)
    if body.backbone not in backbones.BACKBONES:
        raise HTTPException(400, "Unknown backbone")
    try:
        await rt.set_backbone(body.backbone, body.execution_provider)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(500, f"Could not load backbone: {err}") from err
    return get_settings(request)


@router.get("/cameras")
async def cameras(request: Request) -> list[dict]:
    try:
        return await runtime(request).ha.cameras()
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"Could not list cameras: {err}") from err


@router.get("/entities")
async def entities(request: Request) -> list[dict]:
    try:
        return await runtime(request).ha.entities()
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"Could not list entities: {err}") from err


@router.get("/preview")
async def preview(source_type: str, source: str, request: Request) -> Response:
    """A frame from a source that is not a sensor yet (used by the new sensor wizard)."""
    if source_type not in SOURCE_TYPES:
        raise HTTPException(400, "Unknown source type")
    try:
        data = await runtime(request).grabber.grab(source_type, source)
    except SourceError as err:
        raise HTTPException(502, f"Camera unavailable: {err}") from err
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# --- review queue -------------------------------------------------------------


@router.get("/review-rules")
def get_review_rules(request: Request) -> dict:
    return merge_review(runtime(request).global_review, None)


@router.put("/review-rules")
async def put_review_rules(body: ReviewRules, request: Request) -> dict:
    rt = runtime(request)
    await asyncio.to_thread(rt.set_global_review, body.model_dump())
    return merge_review(rt.global_review, None)


class ReviewIn(BaseModel):
    action: str  # confirm | label | skip
    state_key: str | None = None


@router.get("/review")
def review_queue(request: Request, limit: int = 50) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        query = select(Prediction).where(Prediction.reviewed.is_(False))
        total = s.scalar(select(func.count()).select_from(query.subquery()))
        rows = s.scalars(query.order_by(Prediction.created_at.desc()).limit(limit)).all()
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
                        "roi": sensor.roi,
                        "states": [{"key": st.key, "name": st.name, "color": st.color} for st in sensor.states],
                    },
                }
            )
        return {"total": total, "items": items}


@router.post("/review/{prediction_id}")
async def review_answer(prediction_id: int, body: ReviewIn, request: Request) -> dict:
    rt = runtime(request)
    with rt.db.session() as s:
        row = s.get(Prediction, prediction_id)
        if row is None:
            raise HTTPException(404, "Review item not found")
        sensor = get_sensor(s, row.sensor_id)
        key = row.state_key if body.action == "confirm" else body.state_key
        state_id = state_id_for(sensor, key) if body.action in ("confirm", "label") else None
        sensor_id, frame = row.sensor_id, row.frame
        row.reviewed = True
    if state_id is not None and frame:
        path = rt.storage.history_path(sensor_id, frame)
        if path.exists():
            image = await asyncio.to_thread(lambda: Image.open(path).convert("RGB"))
            await asyncio.to_thread(rt.add_sample, sensor_id, image, "review", state_id)
            rt.schedule_retrain(sensor_id)
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


# --- import -------------------------------------------------------------------


@router.post("/import", status_code=201)
async def import_bundle(request: Request, file: UploadFile = File(...)) -> dict:
    rt = runtime(request)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        path = Path(tmp.name)
        within_limit = copy_limited(file.file, tmp, UPLOAD_LIMITS["max_file_mb"] * 1_000_000)
    if not within_limit:
        path.unlink(missing_ok=True)
        raise HTTPException(413, f"Bundle is larger than {UPLOAD_LIMITS['max_file_mb']} MB")
    try:
        sensor_id = await asyncio.to_thread(_import_sync, rt, path)
    except (ValueError, KeyError, OSError) as err:
        raise HTTPException(400, f"Import failed: {err}") from err
    finally:
        path.unlink(missing_ok=True)
    await rt.sensor_created(sensor_id)
    rt.schedule_retrain(sensor_id, delay=0)
    return {"id": sensor_id}


def _import_sync(rt, path: Path) -> int:
    from pydantic import ValidationError

    from .sensors import SensorIn

    manifest = bundle.read_manifest(path)
    data = manifest["sensor"]
    # Validate exactly like a sensor created in the UI (limits, source type, states, triggers).
    try:
        spec = SensorIn.model_validate(
            {
                key: data[key]
                for key in (
                    "name",
                    "source_type",
                    "source",
                    "roi",
                    "states",
                    "interval_s",
                    "threshold",
                    "debounce",
                    "triggers",
                    "review",
                )
                if data.get(key) is not None
            }
        )
    except ValidationError as err:
        raise ValueError(f"invalid sensor in bundle: {err.errors()[0].get('msg')}") from err
    if spec.source_type not in SOURCE_TYPES:
        raise ValueError(f"unknown source type {spec.source_type!r}")
    validate_states(spec.states)
    samples = manifest.get("samples", [])
    if not isinstance(samples, list) or len(samples) > UPLOAD_LIMITS["max_zip_members"]:
        raise ValueError("too many samples in bundle")
    with rt.db.session() as s:
        sensor = Sensor(
            slug=unique_slug(s, spec.name),
            name=spec.name,
            source_type=spec.source_type,
            source=spec.source,
            roi=imaging.normalise_roi(spec.roi.model_dump() if spec.roi else None),
            interval_s=spec.interval_s,
            threshold=spec.threshold,
            debounce=spec.debounce,
            enabled=True,
            triggers=spec.triggers.model_dump() if spec.triggers else None,
            review=spec.review.stored() if spec.review else None,
        )
        sensor.states = [
            State(
                key=st.key or slugify(st.name, "state"),
                name=st.name,
                color=st.color or STATE_PALETTE[i % len(STATE_PALETTE)],
                position=i,
            )
            for i, st in enumerate(spec.states)
        ]
        s.add(sensor)
        s.flush()
        sensor_id = sensor.id
        state_ids = {st.key: st.id for st in sensor.states}
    for item in samples:
        image = imaging.decode(bundle.read_sample_bytes(path, str(item["file"])))
        labels = [state_ids[k] for k in item.get("labels", []) if k in state_ids]
        rt.add_sample(sensor_id, image, "import", labels[0] if labels else None, item.get("use_roi", True))
    return sensor_id
