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
    # "auto", nothing learned yet: the rate set, or the standard one (0.04 m³ a minute here).
    assert change_limit.limit(WATER, None, 60) == pytest.approx(MARGIN + 0.04)
    assert change_limit.limit({**WATER, "max_rate": 1.2}, None, 60) == pytest.approx(MARGIN + 0.02)
    assert change_limit.standard_rate({**WATER, "decimals": 1}) == pytest.approx(240)
    # "step": the same for every reading, however long the counter was away; 0: no limit.
    step = {**WATER, "change_limit": "step", "max_step": 0.04}
    assert change_limit.limit(step, BUSY, 60) == change_limit.limit(step, BUSY, 86_400) == 0.04
    assert change_limit.limit({**step, "max_step": 0}, BUSY, 60) is None
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


def test_accept_starts_the_limit_again():
    watch = Watch(accepted_at=0.0, budget_s=900.0, held=True)
    change_limit.accept(watch, 1000)
    assert (watch.accepted_at, watch.budget_s, watch.held) == (1000, 0.0, False)


def test_held_by_readings_over_the_limit():
    """However long readings over the limit go on, the limit does not grow: only time away counts."""
    watch = Watch(seen_at=0.0, accepted_at=0.0, learned=BUSY)
    change_limit.hold(watch)
    for minute in range(1, 301):
        change_limit.advance(watch, minute * 60, 60)
    assert watch.held and watch.budget_s == 0.0


def test_learning_counter_keeps_out_issue_40s_misread():
    """Issue #40: switched to "auto", a wheel read two digits off (635.709 to 637.799) 32 minutes
    after the last accepted reading went through, as nothing was learned yet."""
    allowed = change_limit.limit(WATER, None, 1920)
    assert allowed is not None and allowed < 637.799 - 635.709
