"""Short-lived batches of snapshot requests; no completed-frame cache."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from PIL import Image


@dataclass
class _Batch:
    task: asyncio.Task[tuple[bytes, Image.Image]]
    waiters: int = 0


class SharedFrames:
    """Share capture and decode only among requests received before capture starts.

    Callers use the decoded image read-only. Keys are exact (source type, source)
    pairs; this object belongs to one runtime/HA connection. Manual, light and
    burst checks bypass it. Nothing is retained after the batch has completed.
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

    async def get(self, source_type: str, source: str) -> tuple[bytes, Image.Image]:
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

    async def _capture(self, key: tuple[str, str]) -> tuple[bytes, Image.Image]:
        await asyncio.sleep(self._window_s)
        # Close membership before I/O: a later request needs its own fresh frame,
        # even when an earlier camera request or JPEG decoder is slow.
        self._pending.pop(key, None)
        data = await self._grab(*key)
        image = await asyncio.to_thread(self._decode, data)
        return data, image

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
