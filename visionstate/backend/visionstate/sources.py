"""Image sources: Home Assistant cameras, HTTP snapshot URLs and RTSP streams."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np

from .redact import redact
from .settings import RUNTIME, SOUND, SUPERVISOR_URL, Settings

log = logging.getLogger(__name__)

SOURCE_TYPES = {
    "ha_camera": "Home Assistant camera",
    "http": "HTTP snapshot URL",
    "rtsp": "RTSP stream",
}


# Notify services not offered for the push: the notification in Home Assistant is sent anyway, and
# send_message needs a notify entity instead of a message alone.
NOTIFY_NOT_PUSH = {"persistent_notification", "send_message"}


# The first Home Assistant (year, month) whose app pages live at /app/<slug>.
HA_APP_PANEL_VERSION = (2026, 2)


def ha_version(version: str) -> tuple[int, int]:
    """(year, month) of a Home Assistant version such as "2026.10.0b1"; (0, 0) when unreadable."""
    match = re.match(r"^(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


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
        self.supervised = settings.is_supervised
        self._app_path: str | None = None
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
        async with self._client.stream("GET", f"{self.base}/camera_proxy/{entity_id}") as resp:
            if resp.status_code != 200:
                raise SourceError(f"{entity_id}: HTTP {resp.status_code}")
            return await read_limited(resp)

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

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        """Call a Home Assistant service (a notification, a push to a phone)."""
        if not self.enabled:
            raise SourceError("Home Assistant API is not configured")
        resp = await self._client.post(f"{self.base}/services/{domain}/{service}", json=data)
        if resp.status_code >= 400:
            raise SourceError(f"{domain}.{service}: HTTP {resp.status_code}")

    async def notify_services(self) -> list[str]:
        """The notify services that push somewhere ("notify.mobile_app_pixel", …), phones first."""
        if not self.enabled:
            return []
        resp = await self._client.get(f"{self.base}/services")
        resp.raise_for_status()
        names = [
            f"notify.{name}"
            for domain in resp.json()
            if domain.get("domain") == "notify"
            for name in domain.get("services", {})
            if name not in NOTIFY_NOT_PUSH
        ]
        return sorted(names, key=lambda n: (not n.startswith("notify.mobile_app_"), n))

    async def app_path(self) -> str | None:
        """The app's page in Home Assistant, for links in notifications; None outside Home Assistant.

        The slug differs per repository (stable, beta), so the Supervisor is asked. Home Assistant
        2026.2 moved the page from /hassio/ingress/<slug> to /app/<slug> (the old one is gone).
        """
        if not self.supervised:
            return None
        if self._app_path is None:
            resp = await self._client.get(f"{SUPERVISOR_URL}/addons/self/info")
            resp.raise_for_status()
            slug = resp.json()["data"]["slug"]
            resp = await self._client.get(f"{self.base}/config")
            resp.raise_for_status()
            new = ha_version(resp.json().get("version", "")) >= HA_APP_PANEL_VERSION
            self._app_path = f"/app/{slug}" if new else f"/hassio/ingress/{slug}"
        return self._app_path

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
        # An RTSP grab blocks a thread for seconds (connecting, waiting for a key frame): on threads
        # of its own, so cameras that do not answer never hold up the database or the AI models.
        self._rtsp = ThreadPoolExecutor(max_workers=RUNTIME["rtsp_workers"], thread_name_prefix="rtsp")
        self._streams: dict[str, RtspStream] = {}  # streams kept open while frames come often
        self._last_grab: dict[str, float] = {}  # when each stream address was last grabbed

    async def close(self) -> None:
        await self._http.aclose()
        for stream in self._streams.values():
            stream.close()
        self._rtsp.shutdown(wait=False, cancel_futures=True)

    def _grab_stream(self, url: str) -> bytes:
        """A frame of an RTSP stream (on an RTSP thread).

        A check now and then connects for one frame (connecting and waiting for a key frame take a
        second or more). Frames asked for again within RUNTIME["rtsp_keep_open_s"] (a burst of
        checks, someone watching the live view) come from the stream kept open meanwhile; it is
        closed once nobody asked for that long, as decoding it costs CPU all the while.
        """
        keep_open = RUNTIME["rtsp_keep_open_s"]
        now = time.monotonic()
        last, self._last_grab[url] = self._last_grab.get(url), now
        stream = self._streams.get(url)
        if stream is not None and not stream.alive:
            stream = self._streams.pop(url)
            stream.close()
            stream = None
        if stream is None and (keep_open <= 0 or last is None or now - last > keep_open):
            return grab_rtsp(url)
        if stream is None:
            stream = self._streams[url] = RtspStream(url, keep_open)
        return stream.grab()

    async def grab(self, source_type: str, source: str) -> bytes:
        check_source(source_type, source)
        try:
            if source_type == "ha_camera":
                return await self.ha.snapshot(source)
            if source_type == "http":
                return await self._get_limited(source)
            return await asyncio.get_running_loop().run_in_executor(self._rtsp, self._grab_stream, source)
        except httpx.HTTPError as err:
            raise SourceError(describe(err)) from err

    async def _get_limited(self, url: str) -> bytes:
        """The body of a snapshot URL, refused when it is larger than RUNTIME["max_frame_mb"]."""
        async with self._http.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise SourceError(f"HTTP {resp.status_code}")
            return await read_limited(resp)


async def read_limited(resp: httpx.Response) -> bytes:
    """A picture's body, refused when it is larger than RUNTIME["max_frame_mb"] (a misconfigured
    address streaming without end)."""
    limit = RUNTIME["max_frame_mb"] * 1_000_000
    body = bytearray()
    async for chunk in resp.aiter_bytes():
        body += chunk
        if len(body) > limit:
            raise SourceError(f"Picture larger than {RUNTIME['max_frame_mb']} MB")
    return bytes(body)


def describe(err: httpx.HTTPError) -> str:
    """Why a request failed, for the UI and the log (some httpx errors have no message)."""
    if message := redact(str(err)):
        return message
    if isinstance(err, httpx.TimeoutException):
        return f"no answer within {RUNTIME['http_timeout_s']:g} s"
    return type(err).__name__


STREAM_OPTIONS = {"rtsp_transport": "tcp", "timeout": "10000000"}  # FFmpeg's socket timeout, in µs


def _jpeg(frame) -> bytes:  # noqa: ANN001 - an av.VideoFrame
    buf = io.BytesIO()
    frame.to_image().save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def grab_rtsp(url: str) -> bytes:
    """One frame of a stream: connect, decode the first frame, disconnect."""
    import av

    try:
        # "timeout" was "stimeout" before FFmpeg 5 (an unknown option is ignored without a word).
        with av.open(url, options=STREAM_OPTIONS, timeout=10) as container:
            for frame in container.decode(video=0):
                return _jpeg(frame)
    except av.FFmpegError as err:
        raise SourceError(redact(str(err))) from err
    raise SourceError("No video frame received")


class RtspStream:
    """An RTSP stream kept open: a thread of its own decodes it and keeps the newest frame, until
    no frame was asked for in ``keep_open`` seconds or the stream fails (see FrameGrabber._grab_stream)."""

    def __init__(self, url: str, keep_open: float):
        self.url, self.keep_open = url, keep_open
        self._changed = threading.Condition()
        self._frame = None
        self._frame_at = 0.0
        self._error: SourceError | None = None
        self._asked_at = time.monotonic()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="rtsp-stream", daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def close(self) -> None:
        with self._changed:
            self._closed = True
            self._changed.notify_all()

    def grab(self) -> bytes:
        """A frame decoded after this call (never an old one), or the stream's error."""
        asked = time.monotonic()
        with self._changed:
            self._asked_at = asked
            ready = self._changed.wait_for(
                lambda: self._frame_at > asked or self._error is not None or not self.alive,
                timeout=RUNTIME["http_timeout_s"],
            )
            if self._frame_at > asked:
                frame = self._frame
            elif self._error is not None:
                raise self._error
            else:
                raise SourceError("No video frame received" if ready else "The stream stopped sending frames")
        return _jpeg(frame)

    def _run(self) -> None:
        import av

        try:
            with av.open(self.url, options=STREAM_OPTIONS, timeout=10) as container:
                for frame in container.decode(video=0):
                    with self._changed:
                        self._frame, self._frame_at = frame, time.monotonic()
                        self._changed.notify_all()
                        if self._closed or time.monotonic() - self._asked_at > self.keep_open:
                            return
        except av.FFmpegError as err:
            with self._changed:
                self._error = SourceError(redact(str(err)))
        except Exception as err:  # noqa: BLE001 - the thread must end with a reason for the waiting grab
            with self._changed:
                self._error = SourceError(redact(str(err)) or type(err).__name__)
        finally:
            with self._changed:
                self._changed.notify_all()


class AudioStream:
    """The sound of a stream (sound sensors), kept open for as long as the sensor listens.

    A thread of its own opens the stream, decodes its audio track — video packets are dropped
    undecoded — to SOUND["rate"] Hz mono and keeps the last SOUND["buffer_s"] seconds. A stream
    that fails, or sends no audio for SOUND["stall_s"], is opened again after a pause that doubles
    from SOUND["reconnect_min_s"] up to SOUND["reconnect_max_s"] (back to the minimum once audio
    flows). ``total`` counts every sample received, so readers ask for what is new since their last
    look (``since``) and clips are cut by sample number (``segment``).
    """

    def __init__(self, url: str):
        self.url = url
        self.rate = SOUND["rate"]
        self._buf = np.zeros(int(SOUND["buffer_s"] * self.rate), dtype=np.float32)
        self.total = 0  # samples received since the stream was first opened
        self.error = ""  # why the stream is not delivering sound now ("" while it is)
        self.connected = False  # audio arrived within SOUND["stall_s"]
        self.info: dict = {}  # the stream's audio: codec, sample_rate, channels
        self._last_audio = 0.0
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audio-stream", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._closed.set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def since(self, total: int) -> tuple[np.ndarray, int]:
        """The samples received after sample number ``total`` (at most the buffer), and the new total."""
        with self._lock:
            count = min(self.total - total, len(self._buf))
            return self._last(count), self.total

    def tail(self, seconds: float) -> np.ndarray:
        """The last ``seconds`` received (fewer at the start)."""
        with self._lock:
            return self._last(min(int(seconds * self.rate), self.total, len(self._buf)))

    def segment(self, start: int, end: int) -> np.ndarray | None:
        """Samples ``start`` to ``end`` (sample numbers, see ``total``); None when they are not all
        received yet or no longer in the buffer."""
        with self._lock:
            if end > self.total or start < self.total - len(self._buf) or start >= end:
                return None
            return self._last(self.total - start)[: end - start]

    def _last(self, count: int) -> np.ndarray:
        if count <= 0:
            return np.zeros(0, dtype=np.float32)
        end = self.total % len(self._buf)
        if count <= end:
            return self._buf[end - count : end].copy()
        return np.concatenate([self._buf[len(self._buf) - (count - end) :], self._buf[:end]])

    def _append(self, samples: np.ndarray) -> None:
        samples = samples[-len(self._buf) :]
        with self._lock:
            start = self.total % len(self._buf)
            first = min(len(samples), len(self._buf) - start)
            self._buf[start : start + first] = samples[:first]
            self._buf[: len(samples) - first] = samples[first:]
            self.total += len(samples)
            self._last_audio = time.monotonic()
            self.connected = True
            self.error = ""

    def _run(self) -> None:
        pause = SOUND["reconnect_min_s"]
        while not self._closed.is_set():
            got = self._listen()
            self.connected = False
            if got:
                pause = SOUND["reconnect_min_s"]
            if self._closed.wait(pause):
                return
            pause = min(pause * 2, SOUND["reconnect_max_s"])

    def _listen(self) -> bool:
        """Open the stream and read its audio until it fails, stalls or the stream is closed.
        Returns whether any audio arrived."""
        import av

        got = False
        options = {"timeout": STREAM_OPTIONS["timeout"]}
        if self.url.lower().startswith(("rtsp://", "rtsps://")):
            options["rtsp_transport"] = STREAM_OPTIONS["rtsp_transport"]
        try:
            with av.open(self.url, options=options, timeout=10) as container:
                if not container.streams.audio:
                    self.error = "The stream has no sound (is the camera's microphone switched on?)"
                    return False
                stream = container.streams.audio[0]
                ctx = stream.codec_context
                self.info = {"codec": ctx.name, "sample_rate": stream.sample_rate, "channels": ctx.channels}
                resampler = av.AudioResampler(format="flt", layout="mono", rate=self.rate)
                self._last_audio = time.monotonic()
                for packet in container.demux(stream):
                    if self._closed.is_set():
                        return got
                    for frame in packet.decode():
                        for out in resampler.resample(frame):
                            self._append(out.to_ndarray().reshape(-1))
                            got = True
                    if time.monotonic() - self._last_audio > SOUND["stall_s"]:
                        self.error = f"No sound for {SOUND['stall_s']:g} s"
                        return got
                self.error = "The stream ended"
        except av.FFmpegError as err:
            self.error = redact(str(err)) or type(err).__name__
        except Exception as err:  # noqa: BLE001 - the thread must go on (and reconnect) whatever happens
            self.error = redact(str(err)) or type(err).__name__
            log.warning("Sound of %s failed: %s", redact(self.url), self.error)
        return got
