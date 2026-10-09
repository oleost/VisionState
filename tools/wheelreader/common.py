"""Shared by data building, training and evaluation: how a wheel cell becomes model input,
and how wheel positions become a counter value."""

import numpy as np
from PIL import Image, ImageOps

CELL_W, CELL_H = 32, 48
BINS = 100  # positions 0.0, 0.1, ..., 9.9 around the wheel


def darkest(image: Image.Image) -> Image.Image:
    return Image.fromarray(np.asarray(image.convert("RGB")).min(axis=2))


def cell_input(cell: Image.Image) -> np.ndarray:
    """One wheel cell (any size, RGB or L) -> float32 CELL_H x CELL_W in 0..1."""
    grey = darkest(cell) if cell.mode != "L" else cell
    grey = grey.resize((CELL_W, CELL_H), Image.Resampling.BILINEAR)
    grey = ImageOps.autocontrast(grey, cutoff=1)
    return np.asarray(grey, dtype=np.float32) / 255.0


def split_cells(region: Image.Image, digits: int) -> list[Image.Image]:
    cell = region.width / digits
    return [region.crop((round(i * cell), 0, round((i + 1) * cell), region.height)) for i in range(digits)]


MARGIN = 0.15  # export margin around the region (settings.READING["export_margin"])


def export_region(image: Image.Image) -> Image.Image:
    w = image.width / (1 + 2 * MARGIN)
    h = image.height / (1 + 2 * MARGIN)
    x = (image.width - w) / 2
    y = (image.height - h) / 2
    return image.crop((round(x), round(y), round(x + w), round(y + h)))


def positions(value: float, digits: int) -> list[float]:
    """Wheel positions, most significant first, for a value in units of the last wheel."""
    out, lower = [], 0.0
    for k in range(digits):
        p = value % 10 if k == 0 else (value // 10**k) % 10 + max(0.0, lower - 9.0)
        out.append(p % 10)
        lower = p
    return out[::-1]


def decode(logp: np.ndarray) -> tuple[float, float]:
    """Most probable mechanically consistent reading from per-wheel log probabilities.

    ``logp``: wheels (most significant first) x BINS. The last wheel turns freely; every other
    wheel is at its digit plus how far the wheel to its right is past 9. Returns the value in
    units of the last wheel (with the last wheel's fraction) and the total log probability.
    """
    n = logp.shape[0]
    frac = np.maximum(0, np.arange(BINS) - 90)  # bins past 9 of the right neighbour = fraction bins
    # best[b] = best score for the wheels to the right, ending with this wheel at bin b
    best = logp[n - 1].copy()
    back = []
    for k in range(n - 2, -1, -1):
        new = np.full(BINS, -np.inf)
        arg = np.zeros(BINS, dtype=np.int64)
        for b_right in range(BINS):
            for d in range(10):
                b = d * 10 + frac[b_right]
                s = best[b_right] + logp[k, b]
                if s > new[b]:
                    new[b] = s
                    arg[b] = b_right
        back.append(arg)
        best = new
    b = int(best.argmax())
    score = float(best[b])
    bins = [b]
    for arg in reversed(back):
        b = int(arg[b])
        bins.append(b)
    # bins: most significant first
    digits = [bb // 10 for bb in bins[:-1]]
    value = 0.0
    for d in digits:
        value = value * 10 + d
    return value * 10 + bins[-1] / 10, score


def decode_shifted(logp: np.ndarray, max_shift: int = 4, penalty: float = 0.3) -> tuple[float, float, int]:
    """``decode`` allowing every wheel to appear shifted by the same few bins (a region drawn a
    little above or below the digits). Returns value, score and the shift in bins."""
    best = None
    for s in range(-max_shift, max_shift + 1):
        value, score = decode(np.roll(logp, -s, axis=1))  # observed bin b+s -> true bin b
        score -= penalty * abs(s)
        if best is None or score > best[1]:
            best = (value, score, s)
    return best
