"""What every part of the runtime shares: its state (set up here) and the few methods all parts use.

The parts (``checks``, ``objects``, ``reading``, ``teaching``, ``models``, ``training``,
``publishing``, ``history``, ``reminders``) are mixins of ``Runtime``. Each one inherits ``RuntimeBase`` and the
parts it uses, so what a part depends on is written in its class line.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy import select

from .. import backbones, classifier, detectors, readers, teach
from ..db import Database, Sensor
from ..ha_events import HaEventListener
from ..lights import Lights
from ..mqtt import MqttBridge
from ..settings import RUNTIME, Settings, merge_review
from ..sources import FrameGrabber, HomeAssistant
from ..storage import Storage
from .state import LiveState, SensorConfig

log = logging.getLogger(__name__)


class RuntimeBase:
    # Made by Runtime.__init__: they call back into parts that are not in the base.
    mqtt: MqttBridge
    ha_events: HaEventListener

    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.storage = Storage(settings)
        self.ha = HomeAssistant(settings)
        self.lights = Lights(lambda: self.ha)  # sensors' lights, shared by checks and open views
        self.grabber = FrameGrabber(self.ha)
        self._entity_index: dict[str, set[int]] = {}  # trigger entity -> sensor ids
        # unique ID -> entity ID of our entities in Home Assistant's entity registry (empty outside HA)
        self.ha_entity_ids: dict[str, str] = {}
        self._registry_wanted = asyncio.Event()  # discovery was published: read the registry again
        self.global_review: dict = {}  # global review rules (DB setting "review")
        self.storage_rules: dict = {}  # history limits (DB setting "storage"); see storage_limits()
        self.history_trimmed = False  # frames were removed to stay under the size limit (until limits change)
        self.reminder_rules: dict = {}  # the review reminder (DB setting "reminder"); see reminder_settings()
        self.reminder_sent_at: float | None = None  # this round's last reminder (DB setting "reminder_sent_at")
        self._reminder_wanted = asyncio.Event()  # the review queue changed: look at the reminder now
        self.embedder: backbones.Embedder | None = None
        self.embedder_error = ""
        self.detector: detectors.Detector | None = None  # loaded on first use by an object sensor
        self.detector_error = ""
        self._detector_lock = asyncio.Lock()
        self._published_classes: dict[int, set[str]] = {}  # object sensor -> classes in discovery
        self.reader: readers.Reader | None = None  # loaded on first use by a reading sensor
        self.reader_error = ""
        self._reader_lock = asyncio.Lock()
        self.wheel_reader: readers.WheelReader | None = None  # loaded on first use by a counter
        self.wheel_reader_error = ""
        self._wheel_reader_lock = asyncio.Lock()
        self.heads: dict[int, classifier.Head] = {}
        self.taught: dict[int, teach.TaughtIndex] = {}  # object sensors; built on use, dropped on change
        self._taught_generation: dict[int, int] = {}  # counts changes to each sensor's taught boxes
        self.live: dict[int, LiveState] = {}
        self.training: set[int] = set()
        self._tasks: dict[int, asyncio.Task] = {}
        self._wake: dict[int, asyncio.Event] = {}
        self._retrain_handles: dict[int, asyncio.TimerHandle] = {}
        self._background: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(RUNTIME["max_concurrent_inferences"])
        self._loop: asyncio.AbstractEventLoop | None = None

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        """Run ``coro`` in the background, keeping a reference so it is not garbage-collected."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def _sensor_ids(self, kind: str | None = None) -> list[int]:
        with self.db.session() as s:
            query = select(Sensor.id)
            if kind is not None:
                query = query.where(Sensor.kind == kind)
            return list(s.scalars(query))

    def load_sensor(self, sensor_id: int) -> SensorConfig | None:
        with self.db.session() as s:
            row = s.get(Sensor, sensor_id)
            return SensorConfig.from_row(row) if row else None

    def live_state(self, sensor_id: int) -> LiveState:
        return self.live.setdefault(sensor_id, LiveState())

    def _on_loop(self, func: Callable[..., object], *args: object) -> bool:
        """Run ``func`` on the event loop. Returns True when called from another thread (deferred).

        Sync API endpoints and ``asyncio.to_thread`` workers run in threads; asyncio objects
        (events, timers) may only be touched from the loop thread.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is not None and running is not self._loop:
            self._loop.call_soon_threadsafe(func, *args)
            return True
        return False

    def wake(self, sensor_id: int, force: bool = False, paused_too: bool = False) -> None:
        """Let the sensor's loop look again; ``force`` checks now (a paused sensor only with ``paused_too``)."""
        if self._on_loop(self.wake, sensor_id, force, paused_too):
            return
        if force:
            live = self.live_state(sensor_id)
            live.force = True
            live.force_paused = live.force_paused or paused_too
        event = self._wake.get(sensor_id)
        if event:
            event.set()

    def review_rules(self, cfg: SensorConfig) -> dict:
        return merge_review(self.global_review, cfg.review)
