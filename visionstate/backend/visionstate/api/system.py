"""App-wide endpoints: the UI config, status, the AI models, history storage limits, review rules
and the review reminder.

Cameras and previews are in cameras.py, the review queue in review.py, import in imports.py."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import backbones, detectors, readers
from ..db import Prediction, Sensor
from ..redact import redact
from ..settings import (
    COUNTER_READERS,
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
    TRIGGER_DEFAULTS,
    TRIGGER_LIMITS,
    TRIGGER_MAX_ENTITIES,
    UNKNOWN_STATE,
    VERSION,
    VIDEO,
    merge_review,
)
from ..sources import SOURCE_TYPES, SourceError
from .common import (
    API_PREFIX,
    ReviewRules,
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
        "object_sensor_defaults": OBJECT_SENSOR_DEFAULTS,
        "object_defaults": OBJECT_DEFAULTS,
        "object_limits": OBJECT_LIMITS,
        "object_max_classes": OBJECT_MAX_CLASSES,
        "object_labels": [{"key": x.key, "name": x.name, "group": x.group} for x in detectors.LABELS.labels],
        "object_popular": list(detectors.LABELS.popular),
        "teach": {"none_label": NONE_LABEL, "max_labels": TEACH["max_labels"]},
        "reading_sensor_defaults": READING_SENSOR_DEFAULTS,
        "reading_defaults": READING_DEFAULTS,
        "reading_limits": READING_LIMITS,
        "reading_modes": READING_MODES,
        "reading_displays": READING_DISPLAYS,
        "counter_readers": COUNTER_READERS,
        "reading_device_classes": READING_DEVICE_CLASSES,
        "reading_counter_cell_share": READING["counter_cell_share"],
        "reading_export_limit": READING["export_limit"],
        "reading_export_unchecked_limit": READING["export_unchecked_limit"],
        "reading_export_meter_max_chars": READING["export_meter_max_chars"],
        "reading_spot_check_count": READING["spot_check_count"],
        "storage_defaults": STORAGE_DEFAULTS,
        "storage_limits": STORAGE_LIMITS,
        "history": HISTORY,
        "history_events": HISTORY_EVENTS,
    }


@router.get("/status")
async def status(request: Request) -> dict:
    """The header and the Settings page: version, counts, MQTT, Home Assistant and model state."""
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
        "wheel_reader": rt.wheel_reader.spec.id if rt.wheel_reader else None,
        "wheel_reader_name": rt.wheel_reader.spec.name if rt.wheel_reader else None,
        "wheel_reader_error": rt.wheel_reader_error,
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
    """The chosen AI models and every model that can be chosen."""
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
        "detector": rt.db.get_text("detector", detectors.DEFAULT_DETECTOR),
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
        "reader": rt.db.get_text("reader", readers.DEFAULT_READER),
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
        # Mechanical counters set to the wheel reader; one model for now, so nothing to choose.
        "wheel_reader": readers.DEFAULT_WHEEL_READER,
        "wheel_readers": [
            {
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "installed": rt.model_path(spec) is not None,
                "size": spec.size,
                "license": spec.license,
                "source": spec.source,
            }
            for spec in readers.WHEEL_READERS.values()
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
    return get_settings(request)


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
