"""One check's clock: when its frame was asked for, and how long its parts took.

A check (``checking``) lives in a context variable, so the code that grabs, waits for a model or
publishes notes its part without passing anything around: ``step`` times a part (``lap`` the
stretch since the last one), ``slot`` times
the wait for a free model run (that wait is left out of the step around it, so a part shows its
own work and the queue shows separately). ``captured`` marks when the camera was asked for the
frame the check analyses; the pictures sent to Home Assistant carry that time (imaging.frame_exif).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class Check:
    started: float = field(default_factory=time.monotonic)
    captured_at: float | None = None  # epoch seconds: when the camera was asked for the frame
    steps: dict[str, float] = field(default_factory=dict)
    waited: float = 0.0  # seconds spent waiting for a free model run (RUNTIME["max_concurrent_inferences"])
    # Where the last part ended (monotonic seconds, and ``waited`` then): where ``lap`` starts.
    mark: float = field(default_factory=time.monotonic)
    waited_at_mark: float = 0.0

    def add(self, name: str, since: float, waited_since: float) -> None:
        now = time.monotonic()
        self.steps[name] = self.steps.get(name, 0.0) + now - since - (self.waited - waited_since)
        self.mark, self.waited_at_mark = now, self.waited

    def summary(self) -> str:
        """``5.21 s (grab 0.35, detect 0.90, …; waited 2.10)``: the parts in the order they ran."""
        parts = [f"{name} {seconds:.2f}" for name, seconds in self.steps.items()]
        if self.waited >= 0.005:
            parts.append(f"waited {self.waited:.2f}")
        return f"{time.monotonic() - self.started:.2f} s ({', '.join(parts)})"


_check: ContextVar[Check | None] = ContextVar("visionstate_check", default=None)


def current() -> Check | None:
    return _check.get()


@contextmanager
def checking() -> Iterator[Check]:
    """The check under way, or a new one: a probe that finds a change hands its frame (and the
    time it asked for it) to the check it starts."""
    existing = _check.get()
    if existing is not None:
        yield existing
        return
    check = Check()
    token = _check.set(check)
    try:
        yield check
    finally:
        _check.reset(token)


def captured() -> None:
    """The camera is asked for the frame this check will analyse now (a later frame replaces it)."""
    check = _check.get()
    if check is not None:
        check.captured_at = time.time()


def captured_at() -> float | None:
    check = _check.get()
    return check.captured_at if check is not None else None


@contextmanager
def step(name: str) -> Iterator[None]:
    """Time a part of the check; a part that runs twice adds up. Without a check: nothing."""
    check = _check.get()
    if check is None:
        yield
        return
    start, waited = time.monotonic(), check.waited
    try:
        yield
    finally:
        check.add(name, start, waited)


def lap(name: str) -> None:
    """The time since the last part ended counts for ``name``: for a long stretch of code (the
    publishing at the end of a check) without indenting it."""
    check = _check.get()
    if check is not None:
        check.add(name, check.mark, check.waited_at_mark)


@asynccontextmanager
async def slot(lock: AbstractAsyncContextManager) -> AsyncIterator[None]:
    """Hold ``lock`` (the semaphore of model runs); the time spent waiting for it counts as waited."""
    start = time.monotonic()
    async with lock:
        check = _check.get()
        if check is not None:
            check.waited += time.monotonic() - start
        yield
