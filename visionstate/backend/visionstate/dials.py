"""Pointer dials: the small dials beside a water meter's counter (×0.1, ×0.01 … m³), read without a
model (docs/POINTER_DIAL_PLAN.md).

A dial is cut from the counter's straightened plane (imaging.Plane), so it is round and its 0 is
at the top. Its needle is found by colour (red, as on most water meters) or, where the picture has
no colour (an infrared camera at night), as the darkest part of the dial's face; the direction from
the centre to the needle's tip gives its position, 0.0-9.9. The position becomes a probability
over the 100 bins the wheel decoder uses (readers.wheel_decode), narrow for a clear needle, wide for
an unclear one, so the decoder weighs the dial with the wheels and the other dials.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from . import imaging
from .settings import READING

BINS = 100  # positions 0.0-9.9, as the wheel decoder takes them
NEEDLE_COLOUR = (126, 226, 184)  # the needle drawn on what the reader saw (the app's accent)


@dataclass(frozen=True)
class Needle:
    """A dial as read: its position (0.0-9.9; None when no needle was found), how sure (0-1), the
    probability of every position (BINS) and the dial with the needle that was found drawn on it."""

    position: float | None
    certainty: float
    probs: np.ndarray
    image: Image.Image


def _needle_mask(rgb: np.ndarray, dist: np.ndarray, radius: float) -> tuple[np.ndarray, np.ndarray]:
    """The needle's pixels and the ring they were looked for in: red between the hub and the
    numbers round the edge; where the dial has no red (an infrared camera at night), the darkest
    part of its face well inside the numbers, which are dark too."""
    ring = (dist > 0.2 * radius) & (dist < 0.8 * radius)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    red = (r - np.maximum(g, b) > 50) & (r > 90) & ring
    if red[ring].mean() >= READING["dial_min_needle"]:
        return red, ring
    ring = (dist > 0.2 * radius) & (dist < 0.62 * radius)
    grey = rgb.mean(axis=2)
    inside = grey[ring]
    if inside.size == 0:
        return np.zeros_like(ring), ring
    # A needle is a small part of the face, much darker than the rest: what is darker than half
    # way between the darkest pixels and the face.
    dark, face = np.percentile(inside, 1), np.median(inside)
    if face - dark < 40:  # no contrast: nothing that stands out as a needle
        return np.zeros_like(ring), ring
    return (grey < (dark + face) / 2) & ring, ring


def read_needle(dial: Image.Image, ccw: bool = False) -> Needle:
    """Read one dial: ``dial`` is its picture (square, the dial's circle in the middle at
    1 / ``READING["dial_margin"]`` of its size), ``ccw`` a dial that turns anticlockwise."""
    size = dial.width
    rgb = np.asarray(dial.convert("RGB"), dtype=np.float64)
    radius = size / 2 / READING["dial_margin"]
    ys, xs = np.mgrid[0:size, 0:size]
    dx, dy = xs + 0.5 - size / 2, ys + 0.5 - size / 2
    dist = np.hypot(dx, dy)
    mask, ring = _needle_mask(rgb, dist, radius)
    share = float(mask.sum()) / max(1, int(ring.sum()))
    picture = dial.convert("RGB").copy()
    if share < READING["dial_min_needle"]:
        return Needle(None, 0.0, np.full(BINS, 1.0 / BINS), picture)
    # The tip counts most (weights grow with the distance from the centre), so the hub and the
    # short tail behind it do not turn the direction.
    w = dist[mask] ** 2
    ux, uy = dx[mask] / dist[mask], dy[mask] / dist[mask]
    vx, vy = float((w * ux).sum() / w.sum()), float((w * uy).sum() / w.sum())
    spread = math.hypot(vx, vy)  # 1: every needle pixel in one direction
    angle = math.atan2(vx, -vy)  # 0 at the top, clockwise (y points down in an image)
    if ccw:
        angle = -angle
    position = (angle % (2 * math.pi)) / (2 * math.pi) * 10
    # How sure: how much the needle's pixels agree on one direction (a needle's own width keeps it
    # under 1; dial_spread_floor and less: no direction at all).
    floor = READING["dial_spread_floor"]
    certainty = max(0.0, min(1.0, (spread - floor) / (1 - floor)))
    low, high = READING["dial_sigma_bins"]
    sigma = low + (1 - certainty) * (high - low)
    centre = position * BINS / 10
    offset = (np.arange(BINS) - centre + BINS / 2) % BINS - BINS / 2  # around the dial, both ways
    probs = np.exp(-0.5 * (offset / sigma) ** 2) + 1e-4
    probs /= probs.sum()
    draw = ImageDraw.Draw(picture)
    tip = (
        size / 2 + math.sin(math.atan2(vx, -vy)) * 0.75 * radius,
        size / 2 - math.cos(math.atan2(vx, -vy)) * 0.75 * radius,
    )
    draw.line([(size / 2, size / 2), tip], fill=NEEDLE_COLOUR, width=max(2, size // 40))
    return Needle(round(position, 2), round(certainty, 3), probs, picture)


def dial_box(dial: dict, unit: float) -> tuple[float, float, float, float]:
    """Where a dial is cut from its counter's plane (pixels): its circle (``x``, ``y``, ``r`` in units
    of the region's height) with the margin round it."""
    r = dial["r"] * READING["dial_margin"]
    return ((dial["x"] - r) * unit, (dial["y"] - r) * unit, (dial["x"] + r) * unit, (dial["y"] + r) * unit)


def read_needles(image: Image.Image, roi: dict | None, reading: dict, straightened: bool) -> list[Needle]:
    """Every pointer dial of a counter (``reading["dials"]``, ×0.1 first), read in the plane the
    counter is read in (``imaging.reading_plane``); none without dials or a plane."""
    placed = reading.get("dials") or []
    flat = imaging.reading_plane(image, roi, straightened) if placed else None
    if flat is None:
        return []
    size = (READING["dial_size"], READING["dial_size"])
    return [read_needle(flat.crop(image, dial_box(d, flat.height), size), bool(d.get("ccw"))) for d in placed]
