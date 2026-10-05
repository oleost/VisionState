"""The lights VisionState switches on: for a check, and while someone looks at live frames.

A light can serve several sensors, their checks and open views (the region editor, labelling, the
new sensor wizard) at the same time: each of them *holds* it. The light is switched on when the
first one needs it and off when the last one lets go, and only if VisionState switched it on — a
light that was already on is somebody else's and is left alone. A view holds the light with a
lease that it renews while it is open, so a closed tab or a phone put away cannot leave it on.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .redact import redact
from .settings import RUNTIME
from .sources import HomeAssistant

log = logging.getLogger(__name__)


@dataclass
class Light:
    holders: dict[str, float] = field(default_factory=dict)  # holder -> lease end (inf: until released)
    ours: bool = False  # VisionState switched it on, so it switches it off again
    on_since: float = 0.0  # when VisionState switched it on (0: it was already on)
    error: str = ""  # why it could not be switched on

    def prune(self, now: float) -> None:
        for key in [k for k, until in self.holders.items() if until <= now]:
            del self.holders[key]


class Lights:
    def __init__(self, ha: Callable[[], HomeAssistant]):
        self._ha = ha  # looked up on use: the runtime's client (tests swap it)
        self._lights: dict[str, Light] = {}
        self._switched_off: dict[str, float] = {}  # entity -> when VisionState switched it off
        self._lock = asyncio.Lock()

    async def hold(self, entity: str, holder: str, lease_s: float | None = None) -> Light:
        """Hold the light on (switching it on if nobody does yet). Errors are in ``Light.error``."""
        async with self._lock:
            now = time.time()
            light = self._lights.setdefault(entity, Light())
            light.prune(now)
            first = not light.holders
            light.holders[holder] = now + lease_s if lease_s else math.inf
            if first:
                ha = self._ha()
                try:
                    if not ha.enabled:
                        raise RuntimeError("Home Assistant API is not configured")
                    state = await ha.state(entity)
                    if state is None:
                        raise RuntimeError(f"{entity} does not exist")
                    # Just switched off by us, Home Assistant may still say "on": then it is ours,
                    # not somebody else's — switch it on and wait for it as usual.
                    just_off = now - self._switched_off.get(entity, -math.inf) < RUNTIME["light_off_settle_s"]
                    light.ours = state != "on" or just_off
                    if light.ours:
                        await ha.switch(entity, True)
                        light.on_since = time.time()
                    else:
                        light.on_since = 0.0
                    light.error = ""
                except Exception as err:  # noqa: BLE001 - the caller goes on without the light
                    del light.holders[holder]
                    light.ours = False
                    light.error = redact(str(err)) or type(err).__name__
            return light

    async def release(self, entity: str, holder: str) -> None:
        async with self._lock:
            light = self._lights.get(entity)
            if light is not None:
                light.holders.pop(holder, None)
                await self._off_if_free(entity, light)

    async def sweep(self) -> None:
        """Let go of the leases of views that stopped renewing (closed tab, phone put away)."""
        async with self._lock:
            for entity, light in list(self._lights.items()):
                light.prune(time.time())
                await self._off_if_free(entity, light)

    async def _off_if_free(self, entity: str, light: Light) -> None:
        if light.holders:
            return
        self._lights.pop(entity, None)
        if light.ours:
            self._switched_off[entity] = time.time()
            try:
                await self._ha().switch(entity, False)
            except Exception as err:  # noqa: BLE001
                log.warning("Could not switch off %s: %s", entity, redact(str(err)) or type(err).__name__)

    def wait_s(self, entity: str, delay_s: float) -> float | None:
        """Seconds until frames are taken in the light (it warms up for ``delay_s``); None: not held."""
        light = self._lights.get(entity)
        if light is None or not light.holders:
            return None
        return max(0.0, light.on_since + delay_s - time.time()) if light.ours else 0.0

    def held(self, entity: str, holder: str) -> bool:
        light = self._lights.get(entity)
        return light is not None and holder in light.holders
