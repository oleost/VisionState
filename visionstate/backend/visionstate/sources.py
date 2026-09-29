"""Image sources: Home Assistant cameras, HTTP snapshot URLs and RTSP streams."""

from __future__ import annotations

import asyncio
import io
import logging

import httpx

from .settings import RUNTIME, SUPERVISOR_URL, Settings

log = logging.getLogger(__name__)

SOURCE_TYPES = {
    "ha_camera": "Home Assistant camera",
    "http": "HTTP snapshot URL",
    "rtsp": "RTSP stream",
}


class SourceError(Exception):
    pass


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
        try:
            if source_type == "ha_camera":
                return await self.ha.snapshot(source)
            if source_type == "http":
                resp = await self._http.get(source)
                if resp.status_code != 200:
                    raise SourceError(f"HTTP {resp.status_code}")
                return resp.content
            if source_type == "rtsp":
                return await asyncio.to_thread(grab_rtsp, source)
        except httpx.HTTPError as err:
            raise SourceError(str(err)) from err
        raise SourceError(f"Unknown source type {source_type!r}")


def grab_rtsp(url: str) -> bytes:
    import av

    try:
        with av.open(url, options={"rtsp_transport": "tcp", "stimeout": "10000000"}, timeout=10) as container:
            for frame in container.decode(video=0):
                buf = io.BytesIO()
                frame.to_image().save(buf, format="JPEG", quality=90)
                return buf.getvalue()
    except av.FFmpegError as err:
        raise SourceError(str(err)) from err
    raise SourceError("No video frame received")
