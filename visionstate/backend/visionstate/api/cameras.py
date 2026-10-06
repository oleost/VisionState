"""Cameras and what the wizard and the editors look through: camera and entity lists, the
sensor's light while a view is open, and previews of a frame, of detection and of reading."""

from __future__ import annotations

import asyncio
import base64
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from .. import imaging, readers
from ..redact import redact
from ..settings import (
    DETECTION,
    RUNTIME,
    TRIGGER_DEFAULTS,
    TRIGGER_LIMITS,
    merge_objects,
    merge_reading,
)
from ..sources import SOURCE_TYPES, SourceError
from .common import (
    API_PREFIX,
    ReadingIn,
    Roi,
    runtime,
    valid_light,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix=API_PREFIX, tags=["cameras"])


@router.get("/cameras")
async def cameras(request: Request) -> list[dict]:
    try:
        return await runtime(request).ha.cameras()
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"Could not list cameras: {redact(str(err))}") from err


@router.get("/entities")
async def entities(request: Request) -> list[dict]:
    try:
        return await runtime(request).ha.entities()
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"Could not list entities: {redact(str(err))}") from err


class LightHoldIn(BaseModel):
    """A view with live frames holds a sensor's light on while it is open (see lights.py)."""

    entity_id: str
    holder: str = Field(min_length=8, max_length=64)  # one per open view, made up by the UI
    delay_s: float = Field(
        TRIGGER_DEFAULTS["light_delay_s"], ge=TRIGGER_LIMITS["light_delay_s"][0], le=TRIGGER_LIMITS["light_delay_s"][1]
    )
    on: bool = True  # False: the view is closed or its switch was turned off

    @field_validator("entity_id")
    @classmethod
    def _a_light(cls, value: str) -> str:
        value = valid_light(value)
        if not value:
            raise ValueError("No light given")
        return value


@router.post("/lights/hold")
async def hold_light(body: LightHoldIn, request: Request) -> dict:
    """Hold the light on for one more lease (renew it every light_view.renew_s), or let go of it.

    ``wait_s``: seconds until frames are taken in the light; null when it is not on.
    """
    lights = runtime(request).lights
    holder = f"view:{body.holder}"
    if not body.on:
        await lights.release(body.entity_id, holder)
        return {"on": False, "wait_s": None, "error": ""}
    light = await lights.hold(body.entity_id, holder, RUNTIME["light_view_lease_s"])
    wait = lights.wait_s(body.entity_id, body.delay_s)
    return {"on": not light.error, "wait_s": None if light.error else wait, "error": light.error}


@router.get("/preview")
async def preview(source_type: str, source: str, request: Request) -> Response:
    """A frame from a source that is not a sensor yet (used by the new sensor wizard)."""
    if source_type not in SOURCE_TYPES:
        raise HTTPException(400, "Unknown source type")
    try:
        data = await runtime(request).grabber.grab(source_type, source)
    except SourceError as err:
        raise HTTPException(502, f"Camera unavailable: {redact(str(err))}") from err
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
        raise HTTPException(502, f"Camera unavailable: {redact(str(err))}") from err
    image = await asyncio.to_thread(imaging.decode, data)
    roi = body.roi.normalised() if body.roi else None
    try:
        found = await rt.detect_objects(image, roi, merge_objects(None), body.threshold, all_classes=True)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(503, f"Object detector unavailable: {redact(str(err))}") from err
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
        raise HTTPException(502, f"Camera unavailable: {redact(str(err))}") from err
    image = await asyncio.to_thread(imaging.decode, data)
    settings = merge_reading(body.reading.model_dump())
    try:
        text, used = await rt.read_number(image, body.roi.normalised() if body.roi else None, settings)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(503, f"Number reader unavailable: {redact(str(err))}") from err
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
