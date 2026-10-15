"""Pure decisions of the engine (no I/O): object tracks, review and its reminder, triggers and when
to check next."""

from __future__ import annotations

import math

from .. import detectors, imaging, readers, teach
from ..settings import DETECTION, FACES
from .state import FaceBurst, LiveState, ObjectTrack, SensorConfig


def review_reason(
    rules: dict,
    confidence: float,
    threshold: float,
    recent_changes: int,
    seconds_since_flag: float,
    roll: float,
) -> str | None:
    """Decide whether a frame should go to the review queue, and why (rules: see merge_review)."""
    if not rules["enabled"] or seconds_since_flag < rules["cooldown_s"]:
        return None
    # Frames below the reporting threshold (reported as unknown) always qualify.
    if confidence < max(rules["below"], threshold):
        return "low_confidence"
    if recent_changes >= rules["flip_limit"]:
        return "flip"
    if roll < rules["spot_rate"]:
        return "spot_check"
    return None


DAY_S = 86_400


def reminder_action(
    rules: dict, waiting: int, oldest_age_s: float | None, sent_at: float | None, now: float
) -> str | None:
    """What the review reminder does now (rules: see merge_reminder): "send", "clear" or nothing.

    It is due while at least ``min_items`` frames wait and the oldest has waited ``after_days``.
    One reminder per round (``sent_at``: when this round's last one went out); again after
    ``repeat_days`` only when ``repeat`` is on. The round ends — and the notification is cleared —
    as soon as it is no longer due (the queue was handled, or reminders were turned off).
    """
    due = (
        rules["enabled"]
        and waiting >= rules["min_items"]
        and oldest_age_s is not None
        and oldest_age_s >= rules["after_days"] * DAY_S
    )
    if not due:
        return "clear" if sent_at is not None else None
    if sent_at is None or (rules["repeat"] and now - sent_at >= rules["repeat_days"] * DAY_S):
        return "send"
    return None


def reminder_text(waiting: int, oldest_age_s: float) -> str:
    """The reminder's message: how many frames wait and for how long the oldest has."""
    if waiting == 0:  # only a test sends it then
        return "No frames are waiting for review in VisionState right now."
    days = int(oldest_age_s // DAY_S)
    since = f"{days} day{'' if days == 1 else 's'}"
    if waiting == 1:
        return f"There is 1 frame waiting for review in VisionState, for {since}."
    return f"There are {waiting} frames waiting for review in VisionState, the oldest for {since}."


def update_tracks(
    tracks: dict[str, ObjectTrack],
    detections: list[dict],
    classes: list[str],
    required: int,
    clear_after_s: float,
    now: float,
) -> list[str]:
    """Advance each class after one check; returns the classes that switched on or off.

    An object switches on after ``required`` checks in a row with it present, and off once it
    has been missing for ``DETECTION["clear_misses"]`` checks in a row and ``clear_after_s``
    seconds, so a person turning around or one missed check does not make it flicker. A box that
    may be an own label (``ask``, see teach.apply) counts neither way for that label: unsure, it
    keeps what was published. ``classes`` may hold own labels too; filtered boxes count for nothing.
    """
    changed = []
    for key in classes:
        track = tracks.setdefault(key, ObjectTrack())
        found = [d for d in detections if teach.counts_for(d, key)]
        track.score = max((d["score"] for d in found), default=0.0)
        if found:
            track.streak += 1
            track.misses = 0
            track.last_seen = now
            if track.on or track.streak >= required:
                if not track.on:
                    changed.append(key)
                track.on, track.count = True, len(found)
        elif any(teach.unsure_for(d, key) for d in detections):
            track.streak = 0  # neither here nor gone: keep what was published
        else:
            track.streak = 0
            track.misses += 1
            gone = now - track.last_seen >= clear_after_s and track.misses >= DETECTION["clear_misses"]
            if track.on and gone:
                track.on, track.count = False, 0
                changed.append(key)
    for key in [k for k in tracks if k not in classes]:
        del tracks[key]
    return changed


def filter_detections(
    found: list[detectors.Detection],
    analysed: imaging.Box,
    roi: dict | None,
    objects: dict,
    all_classes: bool = False,
) -> list[dict]:
    """Map detections from the analysed box to the whole frame and keep the ones that count.

    An object counts when its bottom centre (where a person or car stands) lies inside the
    region, it is big enough (share of the region's area) and its class is selected.
    """
    x1, y1, x2, y2 = analysed
    aw, ah = x2 - x1, y2 - y1
    rx1, ry1, rx2, ry2 = imaging.region_box(roi)
    region_area = (rx2 - rx1) * (ry2 - ry1)
    classes = set(objects["classes"])
    result = []
    for det in found:
        if not all_classes and det.key not in classes:
            continue
        bx1, by1, bx2, by2 = det.box
        box = (x1 + bx1 * aw, y1 + by1 * ah, x1 + bx2 * aw, y1 + by2 * ah)
        if (box[2] - box[0]) * (box[3] - box[1]) < objects["min_size"] * region_area:
            continue
        if not imaging.in_region((box[0] + box[2]) / 2, box[3], roi):
            continue
        result.append({"key": det.key, "score": round(det.score, 4), "box": [round(v, 4) for v in box]})
    return result


def entity_triggers(triggers: dict, entity_id: str, new: str | None) -> bool:
    """Whether this state change of a trigger entity starts a check (see TRIGGER_DEFAULTS["only_states"])."""
    wanted = triggers["only_states"].get(entity_id)
    return not wanted or (new or "").strip().lower() == wanted.strip().lower()


def next_check_at(cfg: SensorConfig, live: LiveState, now: float) -> tuple[float, str]:
    """When the sensor loop should wake up next, and whether that is a full check or a cheap probe."""
    if live.last_run is None:
        return now, "full"
    pace = burst_pace(cfg, live, now)
    if pace is not None:
        full_at = live.last_run + pace
    elif cfg.triggers["regular"]:
        # Counted from the last check, whatever caused it: frequent triggers postpone it.
        full_at = live.last_run + cfg.interval_s
    else:
        full_at = math.inf  # only triggers
    if live.recheck_at and live.recheck_at < full_at:
        full_at = live.recheck_at  # an object went missing: look again soon (DETECTION["recheck_s"])
    if cfg.triggers["change_detection"]:
        probe_at = max(live.last_run, live.last_probe) + cfg.triggers["change_interval_s"]
        if probe_at < full_at:
            return probe_at, "probe"
    return full_at, "full"


def burst_pace(cfg: SensorConfig, live: LiveState, now: float) -> float | None:
    """Seconds between checks while a burst runs (a trigger's, or looking for a face), or None."""
    paces = [cfg.triggers["burst_interval_s"]] if now < live.burst_until else []
    if now < live.face_burst.until:
        paces.append(FACES["burst_interval_s"])
    return min(paces) if paces else None


def steer_face_burst(burst: FaceBurst, found: list[dict], now: float) -> str | None:
    """After a check of a sensor that recognises faces: start or end looking for a face more
    often while a person is there (FACES["burst_*"]). Returns "start" or "stop" when that changes."""
    people = [d for d in found if d["key"] == "person" and not d.get("filtered")]
    if not people:
        burst.absent += 1
        if burst.absent >= FACES["burst_absent_checks"]:
            burst.spent = False  # gone: the next visit gets a burst of its own
            return _end_face_burst(burst, spent=False)
        return None
    burst.absent = 0
    unnamed = [d for d in people if (d.get("face") or {}).get("state") != "match"]
    height = max(d["box"][3] - d["box"][1] for d in people)
    width = max(float((d.get("face") or {}).get("face_px") or 0) for d in people)
    if burst.until == 0.0:
        if burst.spent or not unnamed:
            return None
        burst.until, burst.idle, burst.best_height, burst.best_face = now + FACES["burst_max_s"], 0, height, width
        return "start"
    if not unnamed or now >= burst.until:
        return _end_face_burst(burst, spent=True)  # everyone named, or the time is up
    usable = any((d.get("face") or {}).get("box") and not (d.get("face") or {}).get("problem") for d in unnamed)
    closer = height > burst.best_height * FACES["burst_progress"] or width > burst.best_face * FACES["burst_progress"]
    burst.idle = 0 if usable or closer else burst.idle + 1
    burst.best_height, burst.best_face = max(burst.best_height, height), max(burst.best_face, width)
    if burst.idle >= FACES["burst_idle_checks"]:
        return _end_face_burst(burst, spent=True)  # standing turned away, or no face to be had
    return None


def _end_face_burst(burst: FaceBurst, spent: bool) -> str | None:
    running = burst.until > 0.0
    burst.until, burst.idle = 0.0, 0
    burst.spent = burst.spent or spent
    return "stop" if running else None


def reading_verdict(
    text: readers.Text,
    value: float | None,
    last: float | None,
    threshold: float,
    settings: dict,
    limit: float | None = None,
) -> tuple[str | None, bool]:
    """Whether a reading is rejected, and why (None: accepted), and whether it is "settling".

    Rejected: nothing read, another number of digits than the counter has wheels, too unsure,
    or implausible next to the ``last`` published value (see readers.implausible; ``limit``: how
    much a counter may go up, change_limit.limit). Settling: one
    step below the last value, the last wheel of a counter turning; the value stays, but that is
    no rejection.
    """
    if value is None:
        return "nothing read", False
    if readers.wrong_digit_count(text.text, settings):
        return "wrong digit count", False
    if text.score < threshold:
        return "unsure", False
    reason = readers.implausible(value, last, settings, limit)
    if reason == "went down" and readers.settling(value, last, settings):
        return None, True
    return reason, False
