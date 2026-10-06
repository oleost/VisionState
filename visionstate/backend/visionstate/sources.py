"""Image sources: Home Assistant cameras, HTTP snapshot URLs and RTSP streams."""

from __future__ import annotations

import asyncio
import io
import logging
import re

import httpx

from .redact import redact
from .settings import RUNTIME, SUPERVISOR_URL, Settings

log = logging.getLogger(__name__)

SOURCE_TYPES = {
    "ha_camera": "Home Assistant camera",
    "http": "HTTP snapshot URL",
    "rtsp": "RTSP stream",
}


class SourceError(Exception):
    pass


ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_SCHEME = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://")
HTTP_SCHEMES = {"http", "https"}
# FFmpeg also opens local files and its own pseudo-protocols (file:, concat:, subfile:); a stream
# source must be a network address.
STREAM_SCHEMES = {"rtsp", "rtsps", "rtmp", "rtmps", "http", "https", "srt", "udp", "tcp"}


def check_source(source_type: str, source: str) -> None:
    """Raises SourceError when ``source`` is not a valid address for ``source_type``."""
    if source_type == "ha_camera":
        if not ENTITY_ID.match(source):
            raise SourceError(f"Not an entity ID: {source!r}")
        return
    match = _SCHEME.match(source)
    scheme = match.group("scheme").lower() if match else ""
    allowed = HTTP_SCHEMES if source_type == "http" else STREAM_SCHEMES if source_type == "rtsp" else None
    if allowed is None:
        raise SourceError(f"Unknown source type {source_type!r}")
    if scheme not in allowed:
        raise SourceError(f"Unsupported address (expected {', '.join(sorted(f'{s}://' for s in allowed))})")


class HomeAssistant:
    """Minimal Home Assistant REST client (via the Supervisor proxy or a long-lived token)."""

    def __init__(self, settings: Settings):
        if settings.is_supervised:
            self.base = f"{SUPERVISOR_URL}/core/api"
            token = settings.supervisor_token
        else:
            self.base = settings.ha_url.rstrip("/") + "/api" if settings.ha_url else ""
            token = settings.ha_token
        self.enabled = bool(self.base and token)
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=RUNTIME["http_timeout_s"]
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def cameras(self) -> list[dict]:
        if not self.enabled:
            return []
        resp = await self._client.get(f"{self.base}/states")
        resp.raise_for_status()
        cams = [
            {
                "entity_id": s["entity_id"],
                "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                "state": s.get("state"),
            }
            for s in resp.json()
            if s["entity_id"].startswith("camera.")
        ]
        return sorted(cams, key=lambda c: c["name"].lower())

    async def entities(self) -> list[dict]:
        """All entities (id, name, domain, state), for picking trigger entities."""
        if not self.enabled:
            return []
        resp = await self._client.get(f"{self.base}/states")
        resp.raise_for_status()
        items = [
            {
                "entity_id": s["entity_id"],
                "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                "domain": s["entity_id"].split(".", 1)[0],
                "state": s.get("state"),
            }
            for s in resp.json()
        ]
        return sorted(items, key=lambda e: (e["domain"], e["name"].lower()))

    async def snapshot(self, entity_id: str) -> bytes:
        if not self.enabled:
            raise SourceError("Home Assistant API is not configured")
        resp = await self._client.get(f"{self.base}/camera_proxy/{entity_id}")
        if resp.status_code != 200:
            raise SourceError(f"{entity_id}: HTTP {resp.status_code}")
        return resp.content

    async def state(self, entity_id: str) -> str | None:
        """The current state of an entity, or None when it does not exist."""
        if not self.enabled:
            raise SourceError("Home Assistant API is not configured")
        resp = await self._client.get(f"{self.base}/states/{entity_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("state")

    async def switch(self, entity_id: str, on: bool) -> None:
        """Turn a light, switch or helper on or off."""
        if not self.enabled:
            raise SourceError("Home Assistant API is not configured")
        service = "turn_on" if on else "turn_off"
        resp = await self._client.post(f"{self.base}/services/homeassistant/{service}", json={"entity_id": entity_id})
        resp.raise_for_status()

    async def ping(self) -> bool:
        if not self.enabled:
            return False
        try:
            resp = await self._client.get(f"{self.base}/")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


class FrameGrabber:
    """Fetches a single JPEG/PNG frame from any supported source type."""

    def __init__(self, ha: HomeAssistant):
        self.ha = ha
        self._http = httpx.AsyncClient(timeout=RUNTIME["http_timeout_s"], follow_redirects=True)

    async def close(self) -> None:
        await self._http.aclose()

    async def grab(self, source_type: str, source: str) -> bytes:
        check_source(source_type, source)
        try:
            if source_type == "ha_camera":
                return await self.ha.snapshot(source)
            if source_type == "http":
                return await self._get_limited(source)
            return await asyncio.to_thread(grab_rtsp, source)
        except httpx.HTTPError as err:
            raise SourceError(redact(str(err))) from err

    async def _get_limited(self, url: str) -> bytes:
        """The body of a snapshot URL, refused when it is larger than RUNTIME["max_frame_mb"]."""
        limit = RUNTIME["max_frame_mb"] * 1_000_000
        async with self._http.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise SourceError(f"HTTP {resp.status_code}")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise SourceError(f"Picture larger than {RUNTIME['max_frame_mb']} MB")
            return bytes(body)


def grab_rtsp(url: str) -> bytes:
    import av

    try:
        with av.open(url, options={"rtsp_transport": "tcp", "stimeout": "10000000"}, timeout=10) as container:
            for frame in container.decode(video=0):
                buf = io.BytesIO()
                frame.to_image().save(buf, format="JPEG", quality=90)
                return buf.getvalue()
    except av.FFmpegError as err:
        raise SourceError(redact(str(err))) from err
    raise SourceError("No video frame received")
