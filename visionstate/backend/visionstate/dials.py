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
    centre (``READING["dial_wide_pointer"]``), where a needle has a hub and at most a short tail —
    and about as far to both sides: a black needle's counterweight may reach as far as a short
    pointer's end, but the needle reaches much farther. Its outline is what is red; its middle may
    be any colour. None for anything else."""
    reach, long, even = READING["dial_wide_pointer"]
    points = np.stack([dx[mask], dy[mask]], axis=1)
    _, spread, axes = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
    along = points @ axes[0]
    near, far = sorted((along.max(), -along.min()))
    if near < reach * radius or near < even * far or spread[0] < long * spread[1]:
        return None
    return float(axes[0][0]), float(axes[0][1])


def _needle_mask(
    rgb: np.ndarray, dist: np.ndarray, radius: float, thin: bool = False
) -> tuple[np.ndarray, np.ndarray, bool]:
    """The needle's pixels, the area they were looked for in and whether it is a needle without
    colour: what is clearly redder than the dial's face inside the numbers round the edge
    (``READING["dial_red_contrast"]``, so a pale needle in a picture with a colour cast is found as
    well as a bright one) and red at all (more red than green and blue: a black needle on a
    blue-grey face is redder than the face, but grey); where the dial has no red (a black needle, or
    a red one seen grey by an infrared camera at night), the dark part of its face joined to the
    middle (``_dark_needle``; ``thin``: with thin lines taken away)."""
    face = dist < 0.8 * radius
    redness = rgb[..., 0] - np.maximum(rgb[..., 1], rgb[..., 2])
    red = (redness - np.median(redness[face]) > READING["dial_red_contrast"]) & (redness > 0) & face
    if red[face].mean() >= READING["dial_min_needle"]:
        return red, face, False
    return *_dark_needle(rgb.mean(axis=2), dist, radius, thin), True


def _joined(mask: np.ndarray, dist: np.ndarray, middle: np.ndarray) -> np.ndarray | None:
    """The part of ``mask`` joined to its pixel nearest the centre within ``middle``; None without one."""
    seeds = mask & middle
    if not seeds.any():
        return None
    sy, sx = divmod(int(np.argmin(np.where(seeds, dist, np.inf))), mask.shape[1])
    part = Image.fromarray(mask.astype(np.uint8) * 255).copy()  # a copy: floodfill writes into it
    ImageDraw.floodfill(part, (sx, sy), 128)
    return np.asarray(part) == 128


def _dark_needle(grey: np.ndarray, dist: np.ndarray, radius: float, thin: bool) -> tuple[np.ndarray, np.ndarray]:
    """A needle without colour: what is darker than half way between the darkest pixels and the
    face, inside ``dial_dark_face`` of the radius, and of that only the part joined to the middle
    (within ``dial_dark_seed`` of the radius) — the needle with its hub, from its counterweight to
    its tip. Print inside the dial ("2 FEET") is as dark but not joined to it — unless the needle
    lies on it, or a long needle touches the dial's circle or its ticks. With ``thin``, thin lines
    (print, the circle) are taken away first by an opening (``dial_dark_open`` of the picture's
    size) and only the needle's own edges come back; a needle's thin tip is then lost too, so
    ``read_needle`` reads it both ways and keeps the surer."""
    face = dist < READING["dial_dark_face"] * radius
    inside = grey[face]
    empty = np.zeros_like(face)
    if inside.size == 0:
        return empty, face
    dark, light = np.percentile(inside, 1), np.median(inside)
    if light - dark < 40:  # no contrast: nothing that stands out as a needle
        return empty, face
    mask = (grey < (dark + light) / 2) & face
    middle = dist < READING["dial_dark_seed"] * radius
    if not thin:
        found = _joined(mask, dist, middle)
        return (empty if found is None else found), face
    peel = 2 * round(READING["dial_dark_open"] * mask.shape[0] / 2) + 1
    picture = Image.fromarray(mask.astype(np.uint8) * 255)
    opened = picture.filter(ImageFilter.MinFilter(peel)).filter(ImageFilter.MaxFilter(peel))
    needle = _joined(np.asarray(opened) > 0, dist, middle)
    if needle is None:
        return empty, face
    grown = Image.fromarray(needle.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(peel))
    return mask & (np.asarray(grown) > 0), face


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


def _tip(
    rgb: np.ndarray, mask: np.ndarray, dx: np.ndarray, dy: np.ndarray, radius: float, thin: bool
) -> tuple[float, float, float, float] | None:
    """The needle's hub (from the circle's centre) and the mean direction from it to its tip (as a
    vector, 1 long when all of the tip points one way), or None.

    The tip gives the direction: the needle's pixels farthest from its hub (dial_tip_share of them,
    none close to the hub, and none much nearer than the farthest: dial_tip_reach), so neither the
    hub, a drop-shaped needle's round body nor the tail or counterweight behind it turns it. The needle is looked for again round its hub, so a circle placed off it does
    not cut the tip off."""
    hx, hy = _hub(mask, dx, dy, radius)
    if hx or hy:
        mask, _, _ = _needle_mask(rgb, np.hypot(dx - hx, dy - hy), radius, thin)
    if not mask.any():  # nothing red enough round the hub (a dark picture): no needle, not a crash
        return None
    ax, ay = dx[mask] - hx, dy[mask] - hy
    away = np.maximum(np.hypot(ax, ay), 1e-9)
    farthest = float(away.max())
    tip = away >= max(
        0.2 * radius, float(np.quantile(away, 1 - READING["dial_tip_share"])), READING["dial_tip_reach"] * farthest
    )
    if not tip.any():
        return None
    return hx, hy, float((ax[tip] / away[tip]).mean()), float((ay[tip] / away[tip]).mean())


def read_needle(dial: Image.Image, ccw: bool = False) -> Needle:
    """Read one dial: ``dial`` is its picture (square, the dial's circle in the middle at
    1 / ``READING["dial_margin"]`` of its size), ``ccw`` a dial that turns anticlockwise."""
    size = dial.width
    rgb = np.asarray(dial.convert("RGB"), dtype=np.float64)
    radius = size / 2 / READING["dial_margin"]
    ys, xs = np.mgrid[0:size, 0:size]
    dx, dy = xs + 0.5 - size / 2, ys + 0.5 - size / 2
    dist = np.hypot(dx, dy)
    mask, ring, dark = _needle_mask(rgb, dist, radius)
    share = float(mask.sum()) / max(1, int(ring.sum()))
    picture = dial.convert("RGB").copy()
    nothing = Needle(None, 0.0, np.full(BINS, 1.0 / BINS), picture)
    if share < READING["dial_min_needle"]:
        return nothing
    low, high = READING["dial_sigma_bins"]
    # A needle without colour may lie on print or touch the dial's circle: it is also read with thin
    # lines taken away (which loses a thin tip, though). A wide pointer is looked for in that one.
    thin = _needle_mask(rgb, dist, radius, thin=True)[0] if dark else mask
    pointer = _wide_pointer(thin, dx, dy, radius)
    if pointer:
        vx, vy = pointer
        position = (math.atan2(-vx if ccw else vx, -vy) % (2 * math.pi)) / (2 * math.pi) * 10
        probs = _peak(position, low) + _peak((position + 5) % 10, low) + 1e-4
        reach = 0.75 * radius
        centre = (size / 2 - vx * reach, size / 2 - vy * reach), (size / 2 + vx * reach, size / 2 + vy * reach)
        ImageDraw.Draw(picture).line(centre, fill=NEEDLE_COLOUR, width=max(2, size // 40))
        return Needle(round(position, 2), 1.0, probs / probs.sum(), picture, ends=2)
    # The whole needle counts, unless its tip does not point one way (print or the circle joined to
    # it) and the needle without thin lines is surer.
    found = _tip(rgb, mask, dx, dy, radius, False)
    if dark and (found is None or math.hypot(found[2], found[3]) < READING["dial_dark_sure"]):
        other = _tip(rgb, thin, dx, dy, radius, True)
        if other is not None and (found is None or math.hypot(other[2], other[3]) > math.hypot(found[2], found[3])):
            found = other
    if found is None:
        return nothing
    hx, hy, vx, vy = found
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


def turning_dial(reading: dict, flat: imaging.Plane) -> dict:
    """A turning dial's circle in the plane of its region (``"dial"`` display): the region is drawn
    round the dial, so it is the circle inside the region, in units of the region's height."""
    width = flat.width / flat.height
    return {"x": width / 2, "y": 0.5, "r": min(width, 1.0) / 2, "ccw": bool(reading.get("ccw"))}


def read_turning(image: Image.Image, roi: dict | None, reading: dict, straightened: bool) -> Needle:
    """A turning dial's needle (``"dial"`` display): the circle inside its region, read in the plane
    the region is straightened to; no needle without a region that makes an area."""
    flat = imaging.reading_plane(image, roi, straightened)
    if flat is None:
        return Needle(None, 0.0, np.full(BINS, 1.0 / BINS), Image.new("RGB", (READING["dial_size"],) * 2))
    dial = turning_dial(reading, flat)
    size = (READING["dial_size"], READING["dial_size"])
    return read_needle(flat.crop(image, dial_box(dial, flat.height), size), dial["ccw"])
