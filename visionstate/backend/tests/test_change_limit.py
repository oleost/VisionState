"""How much a counter may go up: the limit over time, what it learns, agreeing readings."""

import pytest

from visionstate import change_limit
from visionstate.change_limit import Learned, Watch
from visionstate.settings import COUNTER_LIMIT, merge_reading

WATER = merge_reading({"mode": "counter", "display": "counter", "digits": 7, "decimals": 3})
FIXED = {**WATER, "change_limit": "fixed", "max_rate": 2.4}  # 0.04 m³ a minute
MARGIN = COUNTER_LIMIT["margin_steps"] * 0.001
BUSY = Learned(per_hour=0.55, per_day=0.8, days=9)


def test_limit_grows_with_time():
    assert change_limit.limit(FIXED, None, 60) == pytest.approx(MARGIN + 0.04)
    assert change_limit.limit(FIXED, None, 35 * 60) == pytest.approx(MARGIN + 1.4)
    assert change_limit.limit({**FIXED, "max_rate": 0}, None, 60) is None
    assert change_limit.limit({**WATER, "change_limit": "off"}, BUSY, 60) is None
    assert change_limit.limit(WATER, None, 60) is None  # "auto", nothing learned yet: no limit
    safety = COUNTER_LIMIT["safety"]
    assert change_limit.limit(WATER, BUSY, 60) == pytest.approx(MARGIN + safety * 0.55 / 60)
    # Away for three days: no more than three busy days, not three days of showering.
    assert change_limit.limit(WATER, BUSY, 3 * 86_400) == pytest.approx(MARGIN + safety * 3 * 0.8)


def test_learn_needs_days_and_ignores_the_busiest_one():
    week = [(0.05, 0.5)] * (COUNTER_LIMIT["min_days"] - 1)
    assert change_limit.learn(week) is None
    learned = change_limit.learn([*week, (0.09, 0.7), (4.0, 9.0)])  # one day with a misread
    assert learned is not None and learned.days == COUNTER_LIMIT["min_days"] + 1
    assert learned.per_hour == pytest.approx(0.09 * 3600 / COUNTER_LIMIT["window_s"])
    assert learned.per_day == pytest.approx(0.7)
    assert change_limit.learn([(0.0, 0.0)] * 10) is None  # nothing used: nothing to learn


def test_only_unseen_time_counts():
    watch = Watch(seen_at=0.0, accepted_at=0.0)
    change_limit.advance(watch, 60, 60)
    assert watch.budget_s == 60
    # Read with confidence over the limit: the next minutes do not add to the limit.
    watch.held = True
    for minute in range(2, 30):
        change_limit.advance(watch, minute * 60, 60)
    assert watch.budget_s == 60
    # The app was down for an hour: that does count (beyond a normal pause).
    change_limit.advance(watch, 29 * 60 + 3600, 60)
    assert watch.budget_s == pytest.approx(60 + 3600 - change_limit.gap(60))


def test_learns_the_rise_over_the_window():
    watch = Watch()
    assert change_limit.observe(watch, 0, 100.0, 60) is None  # the first one: only the start
    for minute in range(1, 21):
        rise = change_limit.observe(watch, minute * 60, 100.0 + 0.01 * minute, 60)
        assert rise is not None and rise[1] == pytest.approx(0.01 if minute > 1 else 0.0)
    assert rise is not None and rise[0] == pytest.approx(0.01 * COUNTER_LIMIT["window_s"] / 60)
    # One reading too high (a misread that got through) counts with the next one's value.
    assert change_limit.observe(watch, 21 * 60, 101.2, 60) == (pytest.approx(0.1), pytest.approx(0.01))
    assert change_limit.observe(watch, 22 * 60, 100.22, 60) == (pytest.approx(0.11), pytest.approx(0.02))
    # A pause (down, unsure, rejected): the rise over it is not learned.
    assert change_limit.observe(watch, 3600, 101.0, 60) is None
    assert change_limit.observe(watch, 3660, 101.0, 60) == (0.0, 0.0)
    # Accepted because readings over the limit agreed: it was read all along, so it counts.
    assert change_limit.observe(watch, 3660 + 600, 101.5, 60, agreed=True) is not None


def test_accept_starts_the_limit_again():
    watch = Watch(accepted_at=0.0, budget_s=900.0, held=True, pending=[(60.0, 101.0)])
    change_limit.accept(watch, 1000)
    assert (watch.accepted_at, watch.budget_s, watch.held, watch.pending) == (1000, 0.0, False, [])


@pytest.mark.parametrize(
    ("jump", "misread"),
    [
        (0.1, True),  # the 0.1 wheel one step on
        (0.104, True),  # ... plus a little water
        (1.0, True),
        (9.002, True),  # the 1 wheel read 9 instead of 0
        (0.27, False),  # a shower while the readings were unsure
        (0.62, False),
        (0.5, False),
    ],
)
def test_looks_misread(jump, misread):
    assert change_limit.looks_misread(jump, 0.042, WATER) is misread


def test_digits_on_a_display_may_be_read_as_any_other():
    display = {**WATER, "display": "auto"}
    assert change_limit.looks_misread(0.5, 0.042, display)
    assert not change_limit.looks_misread(0.55, 0.042, display)


def run(watch: Watch, settings: dict, last: float, readings: list[tuple[float, float]]) -> list[bool]:
    """Readings over the limit, one after another: whether each is accepted after all."""
    return [change_limit.hold(watch, at, value, "changed too much", last, settings) for at, value in readings]


def test_agreeing_readings_are_accepted():
    watch = Watch(seen_at=0.0, accepted_at=0.0, learned=BUSY)
    # A shower while the light was broken: 0.27 more, then going on up a little.
    readings = [(1800 + i * 60, 100.27 + i * 0.005) for i in range(6)]
    accepted = run(watch, WATER, 100.0, readings)
    assert accepted == [False] * 5 + [True]  # agree_readings in a row, over agree_s
    assert watch.held


@pytest.mark.parametrize("wheel", [0.1, 1.0])
def test_a_misread_wheel_is_never_accepted_by_itself(wheel):
    watch = Watch(seen_at=0.0, accepted_at=0.0, learned=BUSY)
    readings = [(60 + i * 60, 100.0 + wheel + i * 0.001) for i in range(300)]  # a wheel a step on, for hours
    assert not any(run(watch, WATER, 100.0, readings))
    assert watch.pending[0] == readings[0]  # measured from where the series started


def test_readings_that_do_not_agree_start_again():
    watch = Watch(seen_at=0.0, accepted_at=0.0, learned=BUSY)
    assert not any(run(watch, WATER, 100.0, [(60, 100.3), (120, 100.31), (180, 100.2), (240, 100.21)]))
    assert watch.pending == [(180, 100.2), (240, 100.21)]
    assert not change_limit.hold(watch, 300, 99.0, "went down", 100.0, WATER)
    assert watch.pending == []
