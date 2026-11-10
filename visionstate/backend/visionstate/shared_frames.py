"""Short-lived batches of snapshot requests; no completed-frame cache."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from PIL import Image


@dataclass
class SharedFrame:
    """A picture taken after everyone sharing it asked; decoded once, by the first who needs it (a
    change-detection probe decodes it small itself). Whoever gets the image only reads it."""

    data: bytes
    decode: Callable[[bytes], Image.Image]
    taken_at: float | None = None  # epoch seconds: when the camera was asked for it
    _image: asyncio.Future[Image.Image] | None = field(default=None, repr=False)

    async def image(self) -> Image.Image:
        if self._image is None:
            self._image = asyncio.ensure_future(asyncio.to_thread(self.decode, self.data))
        return await asyncio.shield(self._image)


@dataclass
class _Batch:
    task: asyncio.Task[SharedFrame]
    waiters: int = 0


class SharedFrames:
    """Share capture and decode only among requests received before capture starts, so a picture
    is always taken after the request for it: a trigger never gets a frame from before what
    triggered it. Keys are exact (source type, source) pairs; this object belongs to one runtime/HA
    connection. Nothing is retained after the batch has completed.
    """

    def __init__(
        self,
        grab: Callable[[str, str], Awaitable[bytes]],
        decode: Callable[[bytes], Image.Image],
        window_s: float,
    ):
        self._grab = grab
        self._decode = decode
        self._window_s = window_s
        self._pending: dict[tuple[str, str], _Batch] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closed = False

    async def get(self, source_type: str, source: str) -> SharedFrame:
        if self._closed:
            raise RuntimeError("Shared frame capture is closed")
        key = (source_type, source)
        batch = self._pending.get(key)
        if batch is None:
            task = asyncio.create_task(self._capture(key))
            batch = _Batch(task)
            self._pending[key] = batch
            self._tasks.add(task)
            task.add_done_callback(self._finished)
        batch.waiters += 1
        try:
            # Cancelling one sensor must not cancel another sensor's camera request.
            return await asyncio.shield(batch.task)
        finally:
            batch.waiters -= 1
            if not batch.waiters:
                if self._pending.get(key) is batch:
                    del self._pending[key]
                if not batch.task.done():
                    batch.task.cancel()

    async def _capture(self, key: tuple[str, str]) -> SharedFrame:
        await asyncio.sleep(self._window_s)
        # Close membership before I/O: a later request needs its own fresh frame,
        # even when an earlier camera request or JPEG decoder is slow.
        self._pending.pop(key, None)
        taken_at = time.time()
        return SharedFrame(await self._grab(*key), self._decode, taken_at)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()  # retrieve failures even if the final waiter was cancelled

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
        self._tasks.clear()
