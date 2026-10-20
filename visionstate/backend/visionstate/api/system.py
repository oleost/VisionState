"""App-wide endpoints: the UI config, status, the AI models, history storage limits, review rules,
the review reminder and the teaching limits.

Cameras and previews are in cameras.py, the review queue in review.py, import in imports.py."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import backbones, detectors, faces, readers
from ..db import Person, Prediction, Sensor
from ..redact import redact
from ..settings import (
    BACKUP,
    BACKUP_PARTS,
    COUNTER_LIMIT,
    COUNTER_READERS,
    FACE_DEFAULTS,
    FACE_LIMITS,
    FACE_TUNABLE,
    FACE_UNKNOWN_KEY,
    FACES,
    HISTORY,
    HISTORY_EVENTS,
    LIGHT_DOMAINS,
    MAX_STATES,
    NONE_LABEL,
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
    REMINDER_DEFAULTS,
    REMINDER_LIMITS,
    REVIEW_DEFAULTS,
    REVIEW_LIMITS,
    ROI_MAX_POINTS,
    RUNTIME,
    SENSOR_DEFAULTS,
    SENSOR_KINDS,
    SENSOR_LIMITS,
    SENSOR_PUBLISH_DEFAULT,
    STATE_PALETTE,
    STORAGE_DEFAULTS,
    STORAGE_LIMITS,
    TEACH,
    TEACH_TUNABLE,
    TEACH_TUNABLE_LIMITS,
    TRIGGER_DEFAULTS,
    TRIGGER_LIMITS,
    TRIGGER_MAX_ENTITIES,
    UNKNOWN_STATE,
    VERSION,
    VIDEO,
    merge_review,
    merge_teach,
    teach_defaults,
)
from ..sources import SOURCE_TYPES, SourceError
from .common import (
    API_PREFIX,
    FaceLimits,
    ReviewRules,
    TeachLimits,
    check_faces,
    check_teach,
    iso,
    runtime,
)

log = logging.getLogger(__name__)
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
        "publish_default": SENSOR_PUBLISH_DEFAULT,
        "light_view": {"renew_s": RUNTIME["light_view_renew_s"], "lease_s": RUNTIME["light_view_lease_s"]},
        "roi_max_points": ROI_MAX_POINTS,
        "review_defaults": REVIEW_DEFAULTS,
        "review_limits": REVIEW_LIMITS,
        "reminder_defaults": REMINDER_DEFAULTS,
        "reminder_limits": REMINDER_LIMITS,
        "sensor_kinds": SENSOR_KINDS,
        "backup": {
            "parts": [{"key": k, "optional": v["optional"]} for k, v in BACKUP_PARTS.items()],
            "credentials": BACKUP["credentials"],
            "max_restore_gb": BACKUP["max_restore_gb"],
        },
        "object_sensor_defaults": OBJECT_SENSOR_DEFAULTS,
        "object_defaults": OBJECT_DEFAULTS,
        "object_limits": OBJECT_LIMITS,
        "object_max_classes": OBJECT_MAX_CLASSES,
        "object_labels": [{"key": x.key, "name": x.name, "group": x.group} for x in detectors.LABELS.labels],
        "object_popular": list(detectors.LABELS.popular),
        "teach": {
            "none_label": NONE_LABEL,
            "max_labels": TEACH["max_labels"],
            "tunable": list(TEACH_TUNABLE),
            "limits": TEACH_TUNABLE_LIMITS,
        },
        "face_defaults": {k: FACE_DEFAULTS[k] for k in ("enabled", "ask_unknown")},  # the limits: face-limits
        "face_limits": FACE_LIMITS,
        "face_unknown_key": FACE_UNKNOWN_KEY,
        "faces": {k: FACES[k] for k in ("max_people", "max_faces_per_person", "suggest_faces", "stats_window")},
        "reading_sensor_defaults": READING_SENSOR_DEFAULTS,
        "reading_defaults": READING_DEFAULTS,
        "reading_limits": READING_LIMITS,
        "counter_standard_steps_per_min": COUNTER_LIMIT["standard_steps_per_min"],
        "reading_modes": READING_MODES,
        "reading_displays": READING_DISPLAYS,
        "counter_readers": COUNTER_READERS,
        "reading_device_classes": READING_DEVICE_CLASSES,
        "reading_counter_cell_share": READING["counter_cell_share"],
        "reading_export_meter_max_chars": READING["export_meter_max_chars"],
        "storage_defaults": STORAGE_DEFAULTS,
        "storage_limits": STORAGE_LIMITS,
        "history": HISTORY,
        "history_events": HISTORY_EVENTS,
    }


@router.get("/alive")
async def alive() -> dict:
    """Home Assistant's watchdog (config.yaml): answers at once, also while a restore swaps the
    data (main.py does not hold it), so the watchdog never restarts the app in the middle of one."""
    return {"version": VERSION}


@router.get("/status")
async def status(request: Request) -> dict:
    """The header and the Settings page: version, counts, MQTT, Home Assistant and model state."""
    rt = runtime(request)

    def counts():
        with rt.db.session() as s:
            return (
                s.scalar(select(func.count()).select_from(Sensor)) or 0,
                s.scalar(select(func.count()).select_from(Prediction).where(Prediction.reviewed.is_(False))) or 0,
                s.scalar(select(func.count()).select_from(Person)) or 0,
            )

    sensors, review, people = await asyncio.to_thread(counts)
    face_sensors = await asyncio.to_thread(rt.face_sensor_ids)
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
        "wheel_reader": rt.wheel_reader.spec.id if rt.wheel_reader else None,
        "wheel_reader_name": rt.wheel_reader.spec.name if rt.wheel_reader else None,
        "wheel_reader_error": rt.wheel_reader_error,
        # The face model, the people and how many sensors recognise them (the People page).
        "faces": {**rt.face_status(), "people": people, "sensors": len(face_sensors)},
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
    face_model: str | None = None  # face recognition on object sensors; None keeps the current one


# The models a user chooses, per kind (the keys of their lists in GET /settings).
MODELS = {
    "backbones": backbones.BACKBONES,
    "detectors": detectors.DETECTORS,
    "readers": readers.READERS,
    "face_models": faces.RECOGNISERS,
}


def _in_use(rt, kind: str, model_id: str, face_sensors: bool) -> str:
    """Why a model cannot be deleted now ("" if it can)."""
    if kind == "backbones" and model_id == rt.backbone_id:
        return "It is the state model in use. Choose another one first."
    if kind == "detectors" and model_id == rt.db.get_text("detector", detectors.DEFAULT_DETECTOR):
        return "It is the object model in use. Choose another one first."
    if kind == "readers" and model_id == rt.db.get_text("reader", readers.DEFAULT_READER):
        return "It is the display reader in use. Choose another one first."
    if kind == "face_models" and model_id == rt.face_choice and face_sensors:
        return "A sensor recognises faces with it. Turn that off there first, or choose another face model."
    return ""


@router.get("/settings")
def get_settings(request: Request) -> dict:
    """The chosen AI models and every model that can be chosen, with whether each was downloaded
    and can be deleted again (``in_use`` says why not)."""
    rt = runtime(request)
    face_sensors = bool(rt.face_sensor_ids())

    def model(spec, kind: str = "") -> dict:
        return {
            "id": spec.id,
            "name": spec.name,
            "description": spec.description,
            "installed": rt.model_path(spec) is not None,
            "downloaded": rt.downloaded(spec),
            "in_use": _in_use(rt, kind, spec.id, face_sensors),
            "size": spec.size,
            "license": spec.license,
            "source": spec.source,
            **spec.footprint.as_dict(),
        }

    return {
        "backbone": rt.embedder.spec.id if rt.embedder else backbones.DEFAULT_BACKBONE,
        "backbones": [model(spec, "backbones") for spec in backbones.BACKBONES.values()],
        "detector": rt.db.get_text("detector", detectors.DEFAULT_DETECTOR),
        "detectors": [model(spec, "detectors") for spec in detectors.DETECTORS.values()],
        "reader": rt.db.get_text("reader", readers.DEFAULT_READER),
        "readers": [model(spec, "readers") for spec in readers.READERS.values()],
        # Mechanical counters set to the wheel reader; one model for now, so nothing to choose.
        "wheel_reader": readers.DEFAULT_WHEEL_READER,
        "wheel_readers": [model(spec) for spec in readers.WHEEL_READERS.values()],
        # Faces: the recogniser to choose (one face detector goes with every one); downloaded when
        # a sensor turns face recognition on. "size" and "installed" count the detector too.
        "face_model": rt.face_choice,
        "face_models": [
            {
                **model(spec, "face_models"),
                "size": spec.size + (0 if rt.model_path(faces.DETECTOR) else faces.DETECTOR.size),
                "installed": rt.face_files_present(spec.id),
                "data": spec.data,
                "detector": {
                    "name": faces.DETECTOR.name,
                    "license": faces.DETECTOR.license,
                    "data": faces.DETECTOR.data,
                    "source": faces.DETECTOR.source,
                },
            }
            for spec in faces.RECOGNISERS.values()
        ],
        "options": {
            "discovery_prefix": rt.settings.discovery_prefix,
            "mqtt_host": rt.settings.mqtt_host or "(from Home Assistant)",
        },
    }


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request) -> dict:
    """Choose the AI models; only a changed one is loaded (a new backbone retrains the state sensors)."""
    rt = runtime(request)
    if body.backbone not in backbones.BACKBONES:
        raise HTTPException(400, "Unknown backbone")
    if body.detector is not None and body.detector not in detectors.DETECTORS:
        raise HTTPException(400, "Unknown detector")
    if body.reader is not None and body.reader not in readers.READERS:
        raise HTTPException(400, "Unknown reader")
    if body.face_model is not None and body.face_model not in faces.RECOGNISERS:
        raise HTTPException(400, "Unknown face model")
    current = get_settings(request)
    # Only reload what changed: a new backbone retrains every state sensor.
    if body.backbone != current["backbone"]:
        try:
            await rt.set_backbone(body.backbone)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(500, f"Could not load backbone: {redact(str(err))}") from err
    if body.detector is not None and body.detector != current["detector"]:
        try:
            await rt.set_detector(body.detector)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(500, f"Could not load detector: {redact(str(err))}") from err
    if body.reader is not None and body.reader != current["reader"]:
        try:
            await rt.set_reader(body.reader)
        except Exception as err:  # noqa: BLE001
            raise HTTPException(500, f"Could not load reader: {redact(str(err))}") from err
    if body.face_model is not None and body.face_model != current["face_model"]:
        await rt.set_face_model(body.face_model)  # loads in the background when a sensor uses faces
    return get_settings(request)


@router.post("/faces/retry")
async def faces_retry(request: Request) -> dict:
    """Try to download and load the face model again after it failed."""
    rt = runtime(request)
    rt.retry_faces()
    return rt.face_status()


@router.delete("/models/{kind}/{model_id}")
async def delete_model(kind: str, model_id: str, request: Request) -> dict:
    """Delete a downloaded AI model that is not in use (it is downloaded again when chosen)."""
    rt = runtime(request)
    spec = MODELS.get(kind, {}).get(model_id)
    if spec is None:
        raise HTTPException(404, "Unknown model")
    face_sensors = bool(await asyncio.to_thread(rt.face_sensor_ids))
    reason = await asyncio.to_thread(_in_use, rt, kind, model_id, face_sensors)
    if reason:
        raise HTTPException(409, reason)
    if not rt.downloaded(spec):
        raise HTTPException(404, "This model is included in the app or not downloaded")
    if kind == "face_models":
        if rt.face_model is not None:
            await rt.faces_settled()  # let the unused face model go before its file
        await asyncio.to_thread(rt.delete_face_files, model_id)
    else:
        await asyncio.to_thread((rt.settings.models_dir / spec.filename).unlink, missing_ok=True)
    return await asyncio.to_thread(get_settings, request)


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


# --- teaching limits ----------------------------------------------------------


def _teach_view(rt) -> dict:
    """The global teaching limits: the backbone's defaults, what the user set (None: the default)
    and the result, which each object sensor inherits unless it has its own value."""
    backbone = rt.backbone_id
    return {
        "backbone": backbone,
        "defaults": teach_defaults(backbone),
        "values": {key: rt.global_teach.get(key) for key in TEACH_TUNABLE},
        "effective": {key: merge_teach(backbone, rt.global_teach, None)[key] for key in TEACH_TUNABLE},
    }


@router.get("/teach-limits")
def get_teach_limits(request: Request) -> dict:
    return _teach_view(runtime(request))


@router.put("/teach-limits")
async def put_teach_limits(body: TeachLimits, request: Request) -> dict:
    rt = runtime(request)
    check_teach(merge_teach(rt.backbone_id, body.stored(), None))
    await asyncio.to_thread(rt.set_global_teach, body.stored())
    return _teach_view(rt)


# --- face limits --------------------------------------------------------------


def _faces_view(rt) -> dict:
    """The global face limits: the face model's defaults, what the user set (None: the default)
    and the result, which each object sensor inherits unless it has its own value."""
    return {
        "model": rt.face_choice,
        "defaults": faces.tunable_defaults(rt.face_choice),
        "values": {key: rt.global_faces.get(key) for key in FACE_TUNABLE},
        "effective": faces.merge_limits(rt.face_choice, rt.global_faces, None),
    }


@router.get("/face-limits")
def get_face_limits(request: Request) -> dict:
    return _faces_view(runtime(request))


@router.put("/face-limits")
async def put_face_limits(body: FaceLimits, request: Request) -> dict:
    rt = runtime(request)
    check_faces(faces.merge_limits(rt.face_choice, body.stored(), None))
    await asyncio.to_thread(rt.set_global_faces, body.stored())
    return _faces_view(rt)


# --- review reminder ----------------------------------------------------------

_mlo = {k: v[0] for k, v in REMINDER_LIMITS.items()}
_mhi = {k: v[1] for k, v in REMINDER_LIMITS.items()}


class ReminderIn(BaseModel):
    """The review reminder. Defaults and limits: settings.REMINDER_*."""

    enabled: bool = REMINDER_DEFAULTS["enabled"]
    after_days: int = Field(REMINDER_DEFAULTS["after_days"], ge=_mlo["after_days"], le=_mhi["after_days"])
    min_items: int = Field(REMINDER_DEFAULTS["min_items"], ge=_mlo["min_items"], le=_mhi["min_items"])
    repeat: bool = REMINDER_DEFAULTS["repeat"]
    repeat_days: int = Field(REMINDER_DEFAULTS["repeat_days"], ge=_mlo["repeat_days"], le=_mhi["repeat_days"])
    notify_service: str = Field(REMINDER_DEFAULTS["notify_service"], pattern=r"^(notify\.[a-z0-9_]+)?$")


def _reminder_view(rt) -> dict:
    sent_at = rt.reminder_sent_at
    return {**rt.reminder_settings(), "sent_at": iso(datetime.fromtimestamp(sent_at, UTC)) if sent_at else None}


@router.get("/review-reminder")
def get_reminder(request: Request) -> dict:
    """The review reminder, and when the reminder that is up now was sent (None: none is up)."""
    return _reminder_view(runtime(request))


@router.put("/review-reminder")
async def put_reminder(body: ReminderIn, request: Request) -> dict:
    rt = runtime(request)
    await asyncio.to_thread(rt.set_reminder, body.model_dump())
    return _reminder_view(rt)


@router.post("/review-reminder/test")
async def test_reminder(body: ReminderIn, request: Request) -> dict:
    """Send a reminder now, as it would look, with the settings in the form (saved or not)."""
    rt = runtime(request)
    if not rt.ha.enabled:
        raise HTTPException(409, "The Home Assistant API is not available")
    try:
        push_error = await rt.test_reminder(body.model_dump())
    except SourceError as err:
        raise HTTPException(502, f"Home Assistant did not take the notification: {redact(str(err))}") from err
    return {"push_error": push_error}


@router.get("/notify-services")
async def notify_services(request: Request) -> list[str]:
    """Home Assistant's notify services a reminder can also be pushed with (phones first)."""
    try:
        return await runtime(request).ha.notify_services()
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"Could not read Home Assistant's services: {redact(str(err))}") from err


# --- import -------------------------------------------------------------------
