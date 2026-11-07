"""The ESP32 guide (docs/ESP32_PLAN.md): the ESPHome configuration for a board and a kind of device,
the devices Home Assistant knows the address of, and a check that the device answers with its
sound and its pictures before a sensor is made from it."""

from __future__ import annotations

import ipaddress

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import esphome
from ..redact import redact
from ..settings import ESPHOME
from .common import API_PREFIX, runtime

router = APIRouter(prefix=API_PREFIX, tags=["esphome"])

WAV_MAGIC = (b"RIFF", b"WAVE")
JPEG_MAGIC = b"\xff\xd8"


class ConfigurationIn(BaseModel):
    board: str
    kind: str
    name: str = Field(max_length=60)
    mute: bool = False
    camera_use: str | None = None  # settings.ESPHOME["camera_uses"]; None: the first


class CheckIn(BaseModel):
    host: str = Field(max_length=253)
    password: str = Field(min_length=1, max_length=64)
    sound: bool = True
    picture: bool = False


@router.post("/esphome/configuration")
def configuration(body: ConfigurationIn) -> dict:
    try:
        return esphome.configuration(body.board, body.kind, body.name, body.mute, body.camera_use)
    except esphome.ESPHomeError as err:
        raise HTTPException(400, str(err)) from err


@router.get("/esphome/devices")
async def devices(request: Request) -> dict:
    """Devices whose IP address Home Assistant shows (ESPHome's *IP address* entity), and the ESPHome
    devices it has found but not been told to add yet (they have no entities until then)."""
    ha = runtime(request).ha
    try:
        entities = await ha.entities()
    except Exception as err:  # noqa: BLE001 - Home Assistant not reachable: the address is typed instead
        return {"devices": [], "waiting": [], "error": redact(str(err)) or type(err).__name__}
    ids = {e["entity_id"] for e in entities}
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
            {"entity_id": e["entity_id"], "name": name, "address": e["state"], "light": light if light in ids else None}
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
