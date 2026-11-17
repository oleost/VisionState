"""A turning dial's turns between checks (docs/TURNING_DIAL_PLAN.md): pure logic, no I/O.

A turning dial is read as a position (0.0-9.9, the needle's direction; ``dials.read_needle``), and
its direction is known, so where it is now against where it was says how far it turned — as long as
it turned less than ``TURNING["max_turn"]`` of a turn between two checks. More, or too long without
a reading, and the count starts again: nothing is guessed. A move smaller than ``deadband`` is no
move (the counted position stays, so a slow leak still adds up), and a move back is jitter. A new
position counts once the next reading agrees with it — it lies between the counted one and the next —
so one misread needle never adds a turn.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .settings import TURNING

MOVED = "moved"  # a move was counted
STILL = "still"  # no move (or jitter)
WAITING = "waiting"  # a move that waits for the next reading to agree
JUMPED = "jumped"  # the reading before did not fit this one: it is not counted
TOO_FAST = "too fast"  # more than max_turn since the last reading: the count starts again
STARTED = "started"  # the first reading, or the first after a gap: the count starts here


@dataclass
class Turns:
    """What a turning dial's sensor keeps between checks (``LiveState.turns``)."""

    counted: float | None = None  # the position (in turns, 0-1) the count stands on
    pending: float | None = None  # a position ahead of it that waits for the next reading
    seen_at: float | None = None  # when the dial was last read
    total: float = 0.0  # turns counted since the count started
    samples: deque = field(default_factory=deque)  # (time, total) for the flow (readers.counter_rate)


def _ahead(start: float, end: float) -> float:
    """How far ``end`` is ahead of ``start`` in the dial's direction (0-1 of a turn)."""
    return (end - start) % 1.0


def _restart(turns: Turns, position: float, now: float) -> None:
    turns.counted, turns.pending, turns.seen_at = position, None, now
    turns.samples.clear()
    turns.samples.append((now, turns.total))


def turns_per_s(turns: Turns, now: float, window_s: float) -> float:
    """How fast the dial turned over about the last window (0 without two samples in it)."""
    recent = [(t, total) for t, total in turns.samples if t >= now - window_s]
    if len(recent) < 2 or recent[-1][0] <= recent[0][0]:
        return 0.0
    (t0, v0), (t1, v1) = recent[0], recent[-1]
    return (v1 - v0) / (t1 - t0)


def observe(turns: Turns, position: float, now: float, window_s: float) -> str:
    """Count a reading of the dial: ``position`` 0.0-9.9 in its direction. Returns what it was
    (``MOVED``, ``STILL``, ``WAITING``, ``JUMPED``, ``TOO_FAST``, ``STARTED``); ``turns.total`` holds the
    turns counted and ``turns.samples`` the times they were counted at."""
    pos = (position / 10) % 1.0
    max_turn, deadband = TURNING["max_turn"], TURNING["deadband"]
    if turns.counted is None or turns.seen_at is None:
        _restart(turns, pos, now)
        return STARTED
    # Too long without a reading at the speed it last turned: it may have gone round unseen.
    elapsed = now - turns.seen_at
    if elapsed > TURNING["max_gap_s"] or turns_per_s(turns, now, window_s) * elapsed > max_turn:
        _restart(turns, pos, now)
        return STARTED
    turns.seen_at = now
    counted = turns.counted
    if turns.pending is not None:
        before, after = _ahead(counted, turns.pending), _ahead(counted, pos)
        if after + deadband >= before and after <= 2 * max_turn:
            # It went on from the waiting position (or stays there): that move counts.
            turns.total += before
            turns.counted, turns.pending = turns.pending, None
            turns.samples.append((now, turns.total))
            rest = _ahead(turns.counted, pos)
            if deadband <= rest <= 1 - deadband:
                turns.pending = pos
            return MOVED
        # Back to where it was, or somewhere else: the waiting position was a misread.
        turns.pending = None
        step = _ahead(counted, pos)
        if deadband <= step <= max_turn:
            turns.pending = pos
        turns.samples.append((now, turns.total))
        return JUMPED
    step = _ahead(counted, pos)
    turns.samples.append((now, turns.total))
    if step < deadband or step > 1 - deadband:
        return STILL
    if step > max_turn:
        _restart(turns, pos, now)
        return TOO_FAST
    turns.pending = pos
    return WAITING
