"""How much a counter may go up since its last accepted reading (settings.COUNTER_LIMIT).

The limit grows with the time the counter was not seen: the app down, the camera away, or the
readings unsure. Time in which the counter was read with confidence but went over the limit does
not count, so a wheel misread over and over never waits its way through. Such readings are never
accepted by themselves, however many agree: they wait for an answer in Review.

"auto" learns the limit from the counter's own accepted readings: the largest rise over a few
minutes per day and the rise per day (db.ReadingStat ``peak`` and ``total``), only over stretches
in which it was read all the time; until then a rate (set, or the standard one) applies. "step"
is the older limit: the same change for every reading, however long since the last one. Pure logic, no I/O:
the engine (engine/reading.py) keeps a ``Watch`` per sensor and stores it (db.ReadingWatch).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .settings import COUNTER_LIMIT

# Readings that were read with confidence but do not fit the last value.
IMPLAUSIBLE = ("went down", "changed too much")


@dataclass
class Learned:
    """What a counter does on a busy day (the second busiest of the last days)."""

    per_hour: float  # the largest rise over COUNTER_LIMIT["window_s"], as a rate per hour
    per_day: float
    days: int  # days with readings it was learned from


@dataclass
class Watch:
    """A counter's limit between readings. The first four fields are stored (db.ReadingWatch)."""

    seen_at: float | None = None  # the last reading, of any kind
    accepted_at: float | None = None  # the last accepted reading
    budget_s: float = 0.0  # time since then that counts towards the limit
    held: bool = False  # read with confidence over the limit since then: its time no longer counts
    # Learning: accepted readings over the last window (time, value), and the newest one, which
    # counts once the next one says it was no misread (see observe).
    window: deque[tuple[float, float]] = field(default_factory=deque)
    sample: tuple[float, float] | None = None
    learned: Learned | None = None
    days: int = 0  # days with readings so far (learned from min_days on)
    learned_at: float = 0.0
    learned_day: str = ""


def gap(interval_s: float) -> float:
    """A pause between two readings longer than this is time the counter was not seen."""
    return max(COUNTER_LIMIT["gap_checks"] * interval_s, COUNTER_LIMIT["gap_min_s"])


def margin(settings: dict) -> float:
    return COUNTER_LIMIT["margin_steps"] * 10 ** -int(settings["decimals"])


def standard_rate(settings: dict) -> float:
    """The standard limit per hour: COUNTER_LIMIT["standard_steps_per_min"] steps of the last digit a minute."""
    return COUNTER_LIMIT["standard_steps_per_min"] * 10 ** -int(settings["decimals"]) * 60


def learned_rate(learned: Learned | None) -> float | None:
    """The rate per hour "auto" allows once it learned (with its room to spare), or None."""
    return COUNTER_LIMIT["safety"] * learned.per_hour if learned is not None else None


def limit(settings: dict, learned: Learned | None, seconds: float) -> float | None:
    """The largest rise allowed after ``seconds`` of counting time, or None (no limit)."""
    hours = max(0.0, seconds) / 3600
    mode = settings["change_limit"]
    if mode == "step":  # the same for every reading, as counters had before the rate
        step = float(settings["max_step"])
        return step if step > 0 else None
    if mode == "fixed":
        rate = float(settings["max_rate"])
        return margin(settings) + rate * hours if rate > 0 else None
    if mode == "auto" and learned is not None:
        days = max(1, math.ceil(seconds / 86_400))
        rise = min(learned.per_hour * hours, learned.per_day * days)
        return margin(settings) + COUNTER_LIMIT["safety"] * rise
    if mode == "auto":  # still learning: the rate set, or the standard one
        rate = float(settings["max_rate"]) or standard_rate(settings)
        return margin(settings) + rate * hours
    return None


def learn(days: list[tuple[float, float]]) -> Learned | None:
    """The limit learned from (largest rise over the window, total rise) of each day with readings."""
    if len(days) < COUNTER_LIMIT["min_days"]:
        return None
    peaks = sorted(peak for peak, _ in days)
    totals = sorted(total for _, total in days)
    per_hour = peaks[-2] / (COUNTER_LIMIT["window_s"] / 3600)
    if per_hour <= 0:
        return None  # nothing used yet: nothing to learn from
    return Learned(per_hour=per_hour, per_day=max(totals[-2], peaks[-2]), days=len(days))


def advance(watch: Watch, now: float, interval_s: float) -> None:
    """Before a reading is checked: the time since the last one counts towards the limit.

    Once the counter was read with confidence over the limit, only the part of a pause longer
    than a normal one counts (the app was down)."""
    if watch.seen_at is not None:
        passed = max(0.0, now - watch.seen_at)
        watch.budget_s += max(0.0, passed - gap(interval_s)) if watch.held else passed
    watch.seen_at = now


def accept(watch: Watch, now: float) -> None:
    """A reading confirmed the published value (or made it): the limit starts again."""
    watch.accepted_at, watch.budget_s, watch.held = now, 0.0, False


def observe(watch: Watch, now: float, value: float, interval_s: float) -> tuple[float, float] | None:
    """An accepted reading, for learning. Returns what the one before it adds to the day — (rise
    over the window up to it, rise since the one before) — or None.

    A reading counts with the lower of its value and the next one's, so one read too high adds
    nothing. A pause longer than a normal one (down, unsure, rejected) starts again: a rise over
    a pause says nothing about how fast the counter goes up."""
    before = watch.sample
    watch.sample = (now, value)
    if before is None or now - before[0] > gap(interval_s):
        watch.window.clear()
        return None
    at, earlier = before[0], min(before[1], value)
    if not watch.window:
        watch.window.append((at, earlier))
        return 0.0, 0.0
    cut = at - COUNTER_LIMIT["window_s"]
    base = watch.window[0][1]
    for t, v in watch.window:
        if t > cut:
            break
        base = v
    rise = earlier - watch.window[-1][1]
    watch.window.append((at, max(earlier, watch.window[-1][1])))
    while len(watch.window) >= 2 and watch.window[1][0] <= cut:
        watch.window.popleft()
    return max(0.0, earlier - base), max(0.0, rise)


def hold(watch: Watch) -> None:
    """A reading read with confidence but over the limit: from now on, normal pauses no longer
    count towards the limit (see advance), so it does not grow until the counter was away."""
    watch.held = True
