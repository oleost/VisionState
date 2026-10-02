"""System endpoints: status, UI config, settings, cameras, review queue and import."""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import backbones, bundle, detectors, imaging, readers
from ..db import Prediction, Sensor
from ..settings import (
    DETECTION,
    KIND_STATES,
    LIGHT_DOMAINS,
    MAX_STATES,
    OBJECT_DEFAULTS,
    OBJECT_LIMITS,
    OBJECT_MAX_CLASSES,
    OBJECT_SENSOR_DEFAULTS,
    QUALITY,
    READING,
    READING_DEFAULTS,
    READING_DEVICE_CLASSES,
    READING_DISPLAYS,
    READING_LIMITS,
    READING_MODES,
    READING_SENSOR_DEFAULTS,
    REVIEW_DEFAULTS,
    REVIEW_LIMITS,
    ROI_MAX_POINTS,
    SENSOR_DEFAULTS,
    SENSOR_KINDS,
    SENSOR_LIMITS,
    STATE_PALETTE,
    STORAGE_DEFAULTS,
    STORAGE_LIMITS,
    TRIGGER_DEFAULTS,
    TRIGGER_LIMITS,
    TRIGGER_MAX_ENTITIES,
    UNKNOWN_STATE,
    UPLOAD_LIMITS,
    VERSION,
    VIDEO,
    merge_objects,
    merge_reading,
    merge_review,
)
from ..sources import SOURCE_TYPES, SourceError
from .common import (
    API_PREFIX,
    ReadingIn,
    ReviewRules,
    Roi,
    get_sensor,
    iso,
    runtime,
    state_id_for,
    unique_name,
    unique_slug,
)
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
        "light_domains": LIGHT_DOMAINS,
        "roi_max_points": ROI_MAX_POINTS,
        "review_defaults": REVIEW_DEFAULTS,
        "review_limits": REVIEW_LIMITS,
        "sensor_kinds": SENSOR_KINDS,
        "object_sensor_defaults": OBJECT_SENSOR_DEFAULTS,
        "object_defaults": OBJECT_DEFAULTS,
        "object_limits": OBJECT_LIMITS,
        "object_max_classes": OBJECT_MAX_CLASSES,
        "object_labels": [{"key": x.key, "name": x.name, "group": x.group} for x in detectors.LABELS.labels],
        "object_popular": list(detectors.LABELS.popular),
        "reading_sensor_defaults": READING_SENSOR_DEFAULTS,
        "reading_defaults": READING_DEFAULTS,
        "reading_limits": READING_LIMITS,
        "reading_modes": READING_MODES,
        "reading_displays": READING_DISPLAYS,
        "reading_device_classes": READING_DEVICE_CLASSES,
        "reading_counter_cell_share": READING["counter_cell_share"],
        "storage_defaults": STORAGE_DEFAULTS,
        "storage_limits": STORAGE_LIMITS,
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
        "backbone_error": rt.embedder_error,
        "detector": rt.detector.spec.id if rt.detector else None,
        "detector_name": rt.detector.spec.name if rt.detector else None,
        "detector_error": rt.detector_error,
        "reader": rt.reader.spec.id if rt.reader else None,
        "reader_name": rt.reader.spec.name if rt.reader else None,
        "reader_error": rt.reader_error,
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
    detector: str | None = None  # object sensors; None keeps the current one
    reader: str | None = None  # reading sensors; None keeps the current one


@router.get("/settings")
def get_settings(request: Request) -> dict:
    rt = runtime(request)
    return {
        "backbone": rt.embedder.spec.id if rt.embedder else backbones.DEFAULT_BACKBONE,
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
        "detector": rt.db.get_setting("detector", detectors.DEFAULT_DETECTOR),
        "detectors": [
            {
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "installed": rt.model_path(spec) is not None,
                "size": spec.size,
                "license": spec.license,
                "source": spec.source,
            }
            for spec in detectors.DETECTORS.values()
        ],
        "reader": rt.db.get_setting("reader", readers.DEFAULT_READER),
        "readers": [
            {
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "installed": rt.model_path(spec) is not None,
                "size": spec.size,
                "license": spec.license,
                "source": spec.source,
            }
            for spec in readers.READERS.values()
        ],
        "options": {
            "discovery_prefix": rt.settings.discovery_prefix,
            "mqtt_host": rt.settings.mqtt_host or "(from Home Assistant)",
        },
    }


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request) -> dict:
    rt = runtime(request)
    if body.backbone not in backbones.BACKBONES:
        raise HTTPException(400, "Unknown backbone")
    if body.detector is not None and body.detector not in detectors.DETECTORS:
        raise HTTPException(400, "Unknown detector")
    if body.reader is not None and body.reader not in readers.READERS:
        raise HTTPException(400, "Unknown reader")
    current = get_settings(request)
    # Only reload what changed: a new backbone retrains every state sensor.
    if body.backbone != current["backbone"]:
        try:
            await rt.set_backbone(body.backbone)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(500, f"Could not load backbone: {err}") from err
    if body.detector is not None and body.detector != current["detector"]:
        try:
            await rt.set_detector(body.detector)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(500, f"Could not load detector: {err}") from err
    if body.reader is not None and body.reader != current["reader"]:
        try:
            await rt.set_reader(body.reader)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(500, f"Could not load reader: {err}") from err
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


class DetectPreviewIn(BaseModel):
    source_type: str
    source: str
    roi: Roi | None = None
    threshold: float = DETECTION["preview_threshold"]


@router.post("/preview/detect")
async def preview_detect(body: DetectPreviewIn, request: Request) -> dict:
    """A frame from a source plus every object found in its region (new sensor wizard).

    The frame is returned with the detections so the boxes always match the picture.
    """
    rt = runtime(request)
    if body.source_type not in SOURCE_TYPES:
        raise HTTPException(400, "Unknown source type")
    try:
        data = await rt.grabber.grab(body.source_type, body.source)
    except SourceError as err:
        raise HTTPException(502, f"Camera unavailable: {err}") from err
    image = await asyncio.to_thread(imaging.decode, data)
    roi = body.roi.normalised() if body.roi else None
    try:
        found = await rt.detect_objects(image, roi, merge_objects(None), body.threshold, all_classes=True)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(503, f"Object detector unavailable: {err}") from err
    return {
        "image": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
        "width": image.width,
        "height": image.height,
        "detections": found,
    }


class ReadPreviewIn(BaseModel):
    source_type: str
    source: str
    roi: Roi | None = None
    reading: ReadingIn = ReadingIn()


@router.post("/preview/read")
async def preview_read(body: ReadPreviewIn, request: Request) -> dict:
    """A frame from a source and what the number reader makes of its region (new sensor wizard)."""
    rt = runtime(request)
    if body.source_type not in SOURCE_TYPES:
        raise HTTPException(400, "Unknown source type")
    try:
        data = await rt.grabber.grab(body.source_type, body.source)
    except SourceError as err:
        raise HTTPException(502, f"Camera unavailable: {err}") from err
    image = await asyncio.to_thread(imaging.decode, data)
    settings = merge_reading(body.reading.model_dump())
    try:
        text, used = await rt.read_number(image, body.roi.normalised() if body.roi else None, settings)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(503, f"Number reader unavailable: {err}") from err
    used_jpeg = await asyncio.to_thread(imaging.encode_jpeg, used, 85)
    return {
        "image": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
        "read_image": "data:image/jpeg;base64," + base64.b64encode(used_jpeg).decode(),
        "text": text.text,
        "score": round(text.score, 4),
        "value": readers.format_value(readers.parse(text.text, settings), settings),
        # A mechanical counter read with another number of digits than it has wheels.
        "wrong_digit_count": readers.wrong_digit_count(text.text, settings),
    }


# --- storage ----------------------------------------------------------------------


class StorageIn(BaseModel):
    """History limits; whichever is reached first applies. Limits: settings.STORAGE_LIMITS."""

    history_days: int = Field(ge=STORAGE_LIMITS["history_days"][0], le=STORAGE_LIMITS["history_days"][1])
    history_max_gb: float = Field(ge=STORAGE_LIMITS["history_max_gb"][0], le=STORAGE_LIMITS["history_max_gb"][1])


@router.get("/storage")
async def get_storage(request: Request) -> dict:
    """Disk use of history frames and training images, free space and the history limits."""
    usage = await asyncio.to_thread(runtime(request).storage_usage)
    return {**usage, "oldest_history": iso(usage["oldest_history"])}


@router.put("/storage")
async def put_storage(body: StorageIn, request: Request) -> dict:
    rt = runtime(request)
    await asyncio.to_thread(rt.set_storage_limits, body.model_dump())
    await asyncio.to_thread(rt.cleanup_history)  # apply the new limits right away
    await rt.publish_review_count()
    return await get_storage(request)


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
    cfg = await asyncio.to_thread(rt.load_sensor, sensor_id)
    if cfg and cfg.kind == KIND_STATES:
        rt.schedule_retrain(sensor_id, delay=0)
    return {"id": sensor_id}


def _import_sync(rt, path: Path) -> int:
    from pydantic import ValidationError

    from .sensors import SensorIn

    manifest = bundle.read_manifest(path)
    data = manifest["sensor"]
    # Validate exactly like a sensor created in the UI (kind, limits, source type, states, objects, triggers).
    try:
        spec = SensorIn.model_validate(
            {
                key: data[key]
                for key in (
                    "name",
                    "kind",
                    "source_type",
                    "source",
                    "roi",
                    "states",
                    "objects",
                    "reading",
                    "interval_s",
                    "threshold",
                    "debounce",
                    "triggers",
                    "review",
                )
                if data.get(key) is not None
            }
        )
        spec.checked()
    except ValidationError as err:
        raise ValueError(f"invalid sensor in bundle: {err.errors()[0].get('msg')}") from err
    except HTTPException as err:
        raise ValueError(f"invalid sensor in bundle: {err.detail}") from err
    samples = manifest.get("samples", []) if spec.kind == KIND_STATES else []
    if not isinstance(samples, list) or len(samples) > UPLOAD_LIMITS["max_zip_members"]:
        raise ValueError("too many samples in bundle")
    spec.enabled = True
    with rt.db.session() as s:
        spec.name = unique_name(s, spec.name)
        sensor = spec.new_sensor(unique_slug(s, spec.name))
        s.add(sensor)
        s.flush()
        sensor_id = sensor.id
        state_ids = {st.key: st.id for st in sensor.states}
    for item in samples:
        image = imaging.decode(bundle.read_sample_bytes(path, str(item["file"])))
        labels = [state_ids[k] for k in item.get("labels", []) if k in state_ids]
        rt.add_sample(sensor_id, image, "import", labels[0] if labels else None, item.get("use_roi", True))
    return sensor_id
