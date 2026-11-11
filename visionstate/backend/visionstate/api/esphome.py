"""The ESP32 guide (docs/ESP32_PLAN.md): the ESPHome configuration for a board and a kind of device,
the devices Home Assistant knows the address of, and a check that the device answers with its
sound and its pictures before a sensor is made from it. And a sound sensor's device: its
microphone's controls on the Live tab, set through Home Assistant, and whether a newer firmware is out."""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import esphome
from ..redact import redact
from ..settings import ESPHOME
from .common import API_PREFIX, get_sensor, runtime

router = APIRouter(prefix=API_PREFIX, tags=["esphome"])

WAV_MAGIC = (b"RIFF", b"WAVE")
JPEG_MAGIC = b"\xff\xd8"


class ConfigurationIn(BaseModel):
    board: str
    kind: str
    name: str = Field(max_length=60)
    mute: bool = False
    camera_use: str | None = None  # settings.ESPHOME["camera_uses"]; None: the first
    # A microphone that is a voice assistant too: the media player its answer plays on ("": later).
    assistant: str | None = Field(None, max_length=100)


class ControlIn(BaseModel):
    entity_id: str = Field(max_length=255)
    value: bool | float | str


class CheckIn(BaseModel):
    host: str = Field(max_length=253)
    password: str = Field(min_length=1, max_length=64)
    sound: bool = True
    picture: bool = False


@router.post("/esphome/configuration")
def configuration(body: ConfigurationIn) -> dict:
    try:
        return esphome.configuration(body.board, body.kind, body.name, body.mute, body.camera_use, body.assistant)
    except esphome.ESPHomeError as err:
        raise HTTPException(400, str(err)) from err


@router.get("/esphome/devices")
async def devices(request: Request) -> dict:
    """Devices whose IP address Home Assistant shows (ESPHome's *IP address* entity), whether each is
    an ESP32 of ours with a camera or a microphone (offered as a sensor's source), and the ESPHome
    devices Home Assistant has found but not been told to add yet (they have no entities until then)."""
    ha = runtime(request).ha
    try:
        entities = await ha.entities()
    except Exception as err:  # noqa: BLE001 - Home Assistant not reachable: the address is typed instead
        return {"devices": [], "waiting": [], "error": redact(str(err)) or type(err).__name__}
    ids = {e["entity_id"] for e in entities}
    try:
        registry, registered = await ha.registries()
    except Exception:  # noqa: BLE001 - no WebSocket API: the devices are listed, none as ours
        registry, registered = [], []
    found = []
    for e in entities:
        if not (e["entity_id"].startswith("sensor.") and e["entity_id"].endswith("_ip_address")):
            continue
        try:
            ipaddress.ip_address(str(e.get("state")))
        except ValueError:
            continue
        name = str(e.get("name") or e["entity_id"]).removesuffix(" IP address").removesuffix(" IP Address")
        base = e["entity_id"].removeprefix("sensor.").removesuffix("_ip_address")
        light = f"light.{base}_flash"  # an ESP32-CAM's flash (esphome/packages/camera-esp32-cam.yaml)
        found.append(
            {
                "entity_id": e["entity_id"],
                "name": name,
                "address": e["state"],
                "light": light if light in ids else None,
                **esphome.device_parts(e["entity_id"], registry, registered),
            }
        )
    try:
        waiting = await ha.discovered("esphome")
    except Exception:  # noqa: BLE001 - not allowed or not reachable: only the added devices are listed
        waiting = []
    return {"devices": found, "waiting": waiting, "error": None}


async def _try(client: httpx.AsyncClient, url: str, magic: tuple[bytes, ...] | bytes, stream: bool) -> dict:
    try:
        if stream:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return {"ok": False, "status": resp.status_code}
                head = b""
                async for chunk in resp.aiter_bytes():
                    head += chunk
                    if len(head) >= 12:
                        break
        else:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"ok": False, "status": resp.status_code}
            head = resp.content
    except httpx.TimeoutException:
        return {"ok": False, "status": None, "detail": "The device did not answer in time"}
    except httpx.HTTPError as err:
        return {"ok": False, "status": None, "detail": redact(str(err)) or type(err).__name__}
    if isinstance(magic, tuple):
        ok = head[:4] == magic[0] and head[8:12] == magic[1]
    else:
        ok = head[:2] == magic
    out: dict = {"ok": ok, "status": 200}
    if not ok:
        out["detail"] = "The device answered with something else than expected"
    elif not stream:
        out["bytes"] = len(head)
    return out


@router.post("/esphome/check")
async def check(body: CheckIn) -> dict:
    """Asks the device for its sound and its picture, as the sensor will."""
    try:
        found = esphome.addresses(body.host, body.password, sound=body.sound, picture=body.picture)
    except esphome.ESPHomeError as err:
        raise HTTPException(400, str(err)) from err
    result: dict = {"addresses": found}
    async with httpx.AsyncClient(timeout=ESPHOME["check_timeout_s"]) as client:
        if "sound" in found:
            result["sound"] = await _try(client, found["sound"], WAV_MAGIC, stream=True)
        if "picture" in found:
            result["picture"] = await _try(client, found["picture"], JPEG_MAGIC, stream=False)
    return result


def _host(rt, sensor_id: int) -> str | None:  # noqa: ANN001 - the Runtime
    """The host a sensor listens to or takes its pictures from (an ESP32 of ours is reached by its
    address)."""
    with rt.db.session() as s:
        sensor = get_sensor(s, sensor_id)
        return urlsplit(sensor.source).hostname if sensor.source_type in ("rtsp", "http") else None


async def _device(request: Request, sensor_id: int) -> dict | None:
    rt = runtime(request)
    host = await asyncio.to_thread(_host, rt, sensor_id)
    if not host:
        return None
    entities, devices = await rt.ha.registries()
    states = await rt.ha.raw_states()
    return esphome.device_controls(host, entities, devices, states)


@router.get("/sensors/{sensor_id}/device")
async def device(sensor_id: int, request: Request) -> dict:
    """The ESPHome device a sensor listens to or looks through, if Home Assistant has it: its
    microphone's and camera's controls and whether a newer firmware of ours is out."""
    try:
        found = await _device(request, sensor_id)
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001 - Home Assistant not reachable: the Live tab shows no controls
        return {"device": None, "error": redact(str(err)) or type(err).__name__}
    return {"device": found, "error": None}


@router.post("/sensors/{sensor_id}/device")
async def set_control(sensor_id: int, body: ControlIn, request: Request) -> dict:
    """Switch the device's microphone or stream, set its gain, or choose its picture's orientation
    (only the device's own controls)."""
    found = await _device(request, sensor_id)
    control = next((c for c in (found or {}).get("controls", []) if c["entity_id"] == body.entity_id), None)
    if control is None:
        raise HTTPException(404, "No such control on this sensor's device")
    ha = runtime(request).ha
    try:
        if control["kind"] == "switch":
            await ha.switch(body.entity_id, bool(body.value))
        elif control["kind"] == "text":  # Answer on: a media player, or none
            if not isinstance(body.value, str) or (body.value and not esphome.MEDIA_PLAYER.fullmatch(body.value)):
                raise HTTPException(400, "A media player (media_player.…), or none")
            await ha.call_service("text", "set_value", {"entity_id": body.entity_id, "value": body.value})
        elif control["kind"] == "select":
            if body.value not in control["options"]:
                raise HTTPException(400, f"One of: {', '.join(control['options'])}")
            await ha.call_service("select", "select_option", {"entity_id": body.entity_id, "option": body.value})
        else:
            if isinstance(body.value, (bool, str)):
                raise HTTPException(400, "A number")
            value = float(body.value)
            low, high = control.get("min"), control.get("max")
            if (low is not None and value < low) or (high is not None and value > high):
                raise HTTPException(400, f"Between {low:g} and {high:g}")
            await ha.call_service("number", "set_value", {"entity_id": body.entity_id, "value": value})
    except HTTPException:
        raise
    except Exception as err:  # noqa: BLE001 - Home Assistant refused or is gone
        raise HTTPException(502, redact(str(err)) or type(err).__name__) from err
    return {"ok": True}
