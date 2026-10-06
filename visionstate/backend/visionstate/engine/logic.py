"""Pure decisions of the engine (no I/O): object tracks, review, triggers and when to check next."""

from __future__ import annotations

import math

from .. import detectors, imaging, readers, teach
from .state import LiveState, ObjectTrack, SensorConfig


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
    has not been seen for ``clear_after_s`` seconds, so a person turning around does not flicker.
    ``classes`` may hold own labels too; filtered boxes count for nothing (see teach.counts_for).
    """
    changed = []
    for key in classes:
        track = tracks.setdefault(key, ObjectTrack())
        found = [d for d in detections if teach.counts_for(d, key)]
        track.score = max((d["score"] for d in found), default=0.0)
        if found:
            track.streak += 1
            track.last_seen = now
            if track.on or track.streak >= required:
                if not track.on:
                    changed.append(key)
                track.on, track.count = True, len(found)
        else:
            track.streak = 0
            if track.on and now - track.last_seen >= clear_after_s:
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
    in_burst = now < live.burst_until
    if in_burst:
        full_at = live.last_run + cfg.triggers["burst_interval_s"]
    elif cfg.triggers["regular"]:
        # Counted from the last check, whatever caused it: frequent triggers postpone it.
        full_at = live.last_run + cfg.interval_s
    else:
        full_at = math.inf  # only triggers
    if cfg.triggers["change_detection"]:
        probe_at = max(live.last_run, live.last_probe) + cfg.triggers["change_interval_s"]
        if probe_at < full_at:
            return probe_at, "probe"
    return full_at, "full"


def reading_verdict(
    text: readers.Text, value: float | None, last: float | None, threshold: float, settings: dict
) -> tuple[str | None, bool]:
    """Whether a reading is rejected, and why (None: accepted), and whether it is "settling".

    Rejected: nothing read, another number of digits than the counter has wheels, too unsure,
    or implausible next to the ``last`` published value (see readers.implausible). Settling: one
    step below the last value, the last wheel of a counter turning; the value stays, but that is
    no rejection.
    """
    if value is None:
        return "nothing read", False
    if readers.wrong_digit_count(text.text, settings):
        return "wrong digit count", False
    if text.score < threshold:
        return "unsure", False
    reason = readers.implausible(value, last, settings)
    if reason == "went down" and readers.settling(value, last, settings):
        return None, True
    return reason, False
