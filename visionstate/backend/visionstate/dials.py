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
from PIL import Image, ImageDraw, ImageFilter

from . import imaging
from .settings import READING

BINS = 100  # positions 0.0-9.9, as the wheel decoder takes them
NEEDLE_COLOUR = (126, 226, 184)  # the needle drawn on what the reader saw (the app's accent)


@dataclass(frozen=True)
class Needle:
    """A dial as read: its position (0.0-9.9; None when no needle was found), how sure (0-1), the
    probability of every position (BINS) and the dial with the needle that was found drawn on it.
    ``ends``: 2 for a wide pointer across the whole dial, whose two ends are equally likely until
    the dials around it settle which one counts; ``position`` is then one of them."""

    position: float | None
    certainty: float
    probs: np.ndarray
    image: Image.Image
    ends: int = 1


def _peak(position: float, sigma: float) -> np.ndarray:
    """A peak ``sigma`` bins wide at ``position``, around the dial both ways."""
    offset = (np.arange(BINS) - position * BINS / 10 + BINS / 2) % BINS - BINS / 2
    return np.exp(-0.5 * (offset / sigma) ** 2)


def _wide_pointer(mask: np.ndarray, dx: np.ndarray, dy: np.ndarray, radius: float) -> tuple[float, float] | None:
    """The direction of a wide pointer: a long shape reaching far to both sides of the dial's
    centre (``READING["dial_wide_pointer"]``), where a needle has a hub and at most a short tail.
    Its outline is what is red; its middle may be any colour. None for anything else."""
    reach, long = READING["dial_wide_pointer"]
    points = np.stack([dx[mask], dy[mask]], axis=1)
    _, spread, axes = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
    along = points @ axes[0]
    if min(along.max(), -along.min()) < reach * radius or spread[0] < long * spread[1]:
        return None
    return float(axes[0][0]), float(axes[0][1])


def _needle_mask(rgb: np.ndarray, dist: np.ndarray, radius: float) -> tuple[np.ndarray, np.ndarray]:
    """The needle's pixels and the area they were looked for in: what is clearly redder than the
    dial's face inside the numbers round the edge (``READING["dial_red_contrast"]``, so a pale
    needle in a picture with a colour cast is found as well as a bright one); where the dial has no
    red (an infrared camera at night), the darkest part of its face between the hub and the
    numbers, which are dark too."""
    face = dist < 0.8 * radius
    redness = rgb[..., 0] - np.maximum(rgb[..., 1], rgb[..., 2])
    red = (redness - np.median(redness[face]) > READING["dial_red_contrast"]) & face
    if red[face].mean() >= READING["dial_min_needle"]:
        return red, face
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


def _hub(mask: np.ndarray, dx: np.ndarray, dy: np.ndarray, radius: float) -> tuple[float, float]:
    """Where the needle turns, from the dial's centre: the middle of its thick part (a hub, or a
    drop-shaped needle's body), what is left with its edges peeled off. A circle placed a little
    beside the dial then still reads the needle's direction right. Holes in the mask are closed
    first: what is left of a body full of holes would lie towards the tip and turn the needle round.
    A thin needle without a hub seen (or one found far from the centre) turns round the circle's centre."""
    peel = 2 * round(READING["dial_hub_erode"] * mask.shape[0] / 2) + 1
    picture = Image.fromarray(mask.astype(np.uint8) * 255)
    closed = picture.filter(ImageFilter.MaxFilter(peel)).filter(ImageFilter.MinFilter(peel))
    core = np.asarray(closed.filter(ImageFilter.MinFilter(peel))) > 0
    if not core.any():
        return 0.0, 0.0
    hx, hy = float(dx[core].mean()), float(dy[core].mean())
    return (hx, hy) if math.hypot(hx, hy) < 0.5 * radius else (0.0, 0.0)


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
    low, high = READING["dial_sigma_bins"]
    pointer = _wide_pointer(mask, dx, dy, radius)
    if pointer:
        vx, vy = pointer
        position = (math.atan2(-vx if ccw else vx, -vy) % (2 * math.pi)) / (2 * math.pi) * 10
        probs = _peak(position, low) + _peak((position + 5) % 10, low) + 1e-4
        reach = 0.75 * radius
        centre = (size / 2 - vx * reach, size / 2 - vy * reach), (size / 2 + vx * reach, size / 2 + vy * reach)
        ImageDraw.Draw(picture).line(centre, fill=NEEDLE_COLOUR, width=max(2, size // 40))
        return Needle(round(position, 2), 1.0, probs / probs.sum(), picture, ends=2)
    # The tip gives the direction: the needle's pixels farthest from its hub (dial_tip_share of
    # them, none close to the hub), so neither the hub, a drop-shaped needle's round body nor the
    # short tail behind it turns it.
    hx, hy = _hub(mask, dx, dy, radius)
    if hx or hy:  # looked for again round the hub, so a circle placed off it does not cut the tip off
        mask, _ = _needle_mask(rgb, np.hypot(dx - hx, dy - hy), radius)
    ax, ay = dx[mask] - hx, dy[mask] - hy
    away = np.maximum(np.hypot(ax, ay), 1e-9)
    tip = away >= max(0.2 * radius, float(np.quantile(away, 1 - READING["dial_tip_share"])))
    if not tip.any():
        return Needle(None, 0.0, np.full(BINS, 1.0 / BINS), picture)
    vx, vy = float((ax[tip] / away[tip]).mean()), float((ay[tip] / away[tip]).mean())
    spread = math.hypot(vx, vy)  # 1: the whole tip in one direction
    angle = math.atan2(vx, -vy)  # 0 at the top, clockwise (y points down in an image)
    if ccw:
        angle = -angle
    position = (angle % (2 * math.pi)) / (2 * math.pi) * 10
    # How sure: how much the tip's pixels agree on one direction (a needle's own width keeps it
    # under 1; dial_spread_floor and less: no direction at all).
    floor = READING["dial_spread_floor"]
    certainty = max(0.0, min(1.0, (spread - floor) / (1 - floor)))
    sigma = low + (1 - certainty) * (high - low)
    probs = _peak(position, sigma) + 1e-4
    probs /= probs.sum()
    draw = ImageDraw.Draw(picture)
    hub = (size / 2 + hx, size / 2 + hy)
    end = (
        hub[0] + math.sin(math.atan2(vx, -vy)) * 0.75 * radius,
        hub[1] - math.cos(math.atan2(vx, -vy)) * 0.75 * radius,
    )
    draw.line([hub, end], fill=NEEDLE_COLOUR, width=max(2, size // 40))
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
