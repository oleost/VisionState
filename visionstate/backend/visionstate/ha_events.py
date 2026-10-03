"""Home Assistant WebSocket API: state changes of selected entities, and our entities' IDs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from .settings import APP_SLUG, RUNTIME, SUPERVISOR_URL, TRIGGER_IGNORED_STATES, Settings

log = logging.getLogger(__name__)

# (entity_id, old_state, new_state)
StateHandler = Callable[[str, str | None, str | None], Awaitable[None]]


def websocket_url(settings: Settings) -> str:
    if settings.is_supervised:
        return SUPERVISOR_URL.replace("http://", "ws://") + "/core/websocket"
    if settings.ha_url:
        return settings.ha_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    return ""


def trigger_subscription(message_id: int, entities: list[str]) -> dict:
    """A state trigger for the entities. ``to: None`` = any state change, but not attribute-only changes."""
    return {
        "id": message_id,
        "type": "subscribe_trigger",
        "trigger": {"platform": "state", "entity_id": entities, "to": None},
    }


def parse_trigger_event(message: dict) -> tuple[str, str | None, str | None] | None:
    """Extracts (entity_id, old, new) from a subscribe_trigger event; None if not relevant."""
    if message.get("type") != "event":
        return None
    trigger = message.get("event", {}).get("variables", {}).get("trigger", {})
    entity_id = trigger.get("entity_id")
    if not entity_id:
        return None
    old = (trigger.get("from_state") or {}).get("state")
    new = (trigger.get("to_state") or {}).get("state")
    if new in TRIGGER_IGNORED_STATES or old == new:
        return None
    return entity_id, old, new


def our_entity_ids(registry: list[dict]) -> dict[str, str]:
    """unique ID -> entity ID of our MQTT entities, from Home Assistant's entity registry list."""
    return {
        e["unique_id"]: e["entity_id"]
        for e in registry
        if e.get("platform") == "mqtt" and str(e.get("unique_id") or "").startswith(f"{APP_SLUG}_")
    }


async def fetch_entity_ids(url: str, token: str) -> dict[str, str]:
    """The entity IDs Home Assistant gave our entities: they may end in _2, or the user changed them."""
    from websockets.asyncio.client import connect

    async with connect(url, max_size=None) as ws:
        first = json.loads(await ws.recv())
        if first.get("type") == "auth_required":
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            auth = json.loads(await ws.recv())
            if auth.get("type") != "auth_ok":
                raise RuntimeError(f"authentication failed: {auth.get('message', auth.get('type'))}")
        await ws.send(json.dumps({"id": 1, "type": "config/entity_registry/list"}))
        while True:
            message = json.loads(await ws.recv())
            if message.get("id") == 1 and message.get("type") == "result":
                break
    if not message.get("success"):
        raise RuntimeError(f"entity registry: {message.get('error', {}).get('message')}")
    return our_entity_ids(message.get("result") or [])


class HaEventListener:
    """Keeps one subscription for the union of all sensors' trigger entities."""

    def __init__(self, settings: Settings, on_state: StateHandler):
        self.url = websocket_url(settings)
        self.token = settings.supervisor_token or settings.ha_token
        self.on_state = on_state
        self.connected = False
        self.last_error = ""
        self._entities: set[str] = set()
        self._changed = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    @property
    def entities(self) -> set[str]:
        return set(self._entities)

    def start(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._run(), name="ha-events")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def set_entities(self, entities: set[str]) -> None:
        if entities != self._entities:
            self._entities = set(entities)
            self._changed.set()

    async def _run(self) -> None:
        while True:
            self._changed.clear()
            wanted = sorted(self._entities)
            if not wanted:
                self.connected = False
                await self._changed.wait()
                continue
            try:
                await self._session(wanted)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - any failure: log, wait, reconnect
                self.connected = False
                self.last_error = str(err) or type(err).__name__
                log.warning("Home Assistant event stream: %s", self.last_error)
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=RUNTIME["ha_reconnect_delay_s"])
                except TimeoutError:
                    pass

    async def _session(self, wanted: list[str]) -> None:
        from websockets.asyncio.client import connect

        async with connect(self.url, max_size=None) as ws:
            first = json.loads(await ws.recv())
            if first.get("type") == "auth_required":
                await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                auth = json.loads(await ws.recv())
                if auth.get("type") != "auth_ok":
                    raise RuntimeError(f"authentication failed: {auth.get('message', auth.get('type'))}")
            await ws.send(json.dumps(trigger_subscription(1, wanted)))
            self.connected, self.last_error = True, ""
            log.info("Listening for state changes of %d entities", len(wanted))
            changed = asyncio.create_task(self._changed.wait())
            try:
                while True:
                    receive = asyncio.create_task(ws.recv())
                    done, _ = await asyncio.wait({receive, changed}, return_when=asyncio.FIRST_COMPLETED)
                    if changed in done:
                        receive.cancel()
                        return  # entity list changed: reconnect with the new subscription
                    message = json.loads(receive.result())
                    if message.get("type") == "result" and not message.get("success", True):
                        raise RuntimeError(f"subscription rejected: {message.get('error', {}).get('message')}")
                    parsed = parse_trigger_event(message)
                    if parsed:
                        await self.on_state(*parsed)
            finally:
                changed.cancel()
                self.connected = False
