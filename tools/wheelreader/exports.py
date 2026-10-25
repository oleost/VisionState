"""Labels and cells from a shared readings export (wheels-v2; `docs/WHEEL_READER_PLAN.md` §3, §5).

python exports.py <name> <export dir> <split date> [moved]

Every reading gets an interval of values [lo, hi] in steps of the last wheel, never from its own
read: a person's answer, else the interval between the trusted readings before and after it in
time (trusted: checked, or accepted on the longest non-decreasing chain of checked and accepted
values; checked ones weigh most). ``eye.json`` in the export folder overrides stretches looked at
by eye: [[first image number, last, lo, hi], ...]; ``frames.json`` there overrides single frames
read by eye: {"<image number>": [lo, hi], or null for an image nobody can read}. Every reading helps the labels; only those read
with the region they were cut with and whose export says where it is (``"region": "as read"``
with ``"box"``, VisionState 0.10.1b1 on) become frames: a guessed region teaches the wrong cells.

Writes, for part a (dates up to the split date) and part b (after it):
  bench/<name>_<part>.npz, bench/<name>_<part>_ctx.npz   readings x wheels cells, lo, hi, seconds,
                                                          group (a run of frames in a row with the
                                                          same label: a still meter, counted once)
  data/<name>_<part>.npz, data/<name>_<part>_ctx.npz     training cells with an interval per wheel
  data/<name>_<part>_moved.npz, ..._moved_ctx.npz         with [moved] > 0: every frame cut that many
                                                          times more with its region moved at random,
                                                          as rows.py cuts drawn rows
"""

import json
import random
import sys
from bisect import bisect_left
from pathlib import Path

import numpy as np
from PIL import Image

from common import cell_input, positions, split_cells
from context import ctx_cells
from paths import WORK

REST = 0.3  # a wheel at rest may sit this far off its digit
PAD = 0.15  # added to the interval of a wheel that turned


def steps(text: str | None, decimals: int) -> int | None:
    return None if text in (None, "") else round(float(text) * 10**decimals)


def labels(readings: list[dict], decimals: int) -> list[dict]:
    order = sorted(range(len(readings)), key=lambda i: readings[i]["seconds"])
    known = {}
    for i, r in enumerate(readings):
        if r["answer"] == "read correctly":
            known[i] = steps(r["value"], decimals)
        elif r["answer"] == "misread" and r["right_value"]:
            known[i] = steps(r["right_value"], decimals)
    cand = [i for i in order if i in known or (r := readings[i])["answer"] == "unchecked" and not r["rejected"] and r["value"]]
    val = {i: known.get(i, steps(readings[i]["value"], decimals)) for i in cand}
    weight = {i: (1000 if i in known else 1) for i in cand}
    best, prev = [], []
    for j, i in enumerate(cand):  # the heaviest non-decreasing chain in time
        b, p = weight[i], -1
        for k in range(j):
            if val[cand[k]] <= val[i] and best[k] + weight[i] > b:
                b, p = best[k] + weight[i], k
        best.append(b)
        prev.append(p)
    chain = []
    j = max(range(len(cand)), key=lambda k: best[k]) if cand else -1
    while j >= 0:
        chain.append(cand[j])
        j = prev[j]
    chain.reverse()
    t = [readings[i]["seconds"] for i in chain]
    out = []
    for i, r in enumerate(readings):
        if i in known:
            out.append({"lo": known[i], "hi": known[i], "how": "checked"})
            continue
        s = r["seconds"]
        k = bisect_left(t, s)
        before = [c for c in chain[max(0, k - 2) : k + 1] if c != i and readings[c]["seconds"] <= s]
        after = [c for c in chain[k : k + 2] if c != i and readings[c]["seconds"] >= s]
        if not before or not after:
            out.append({"lo": None, "hi": None, "how": "edge"})
        else:
            out.append({"lo": val[before[-1]], "hi": val[after[0]], "how": "chain" if i in chain else "between"})
    return out


def wheel_intervals(lo: int, hi: int, digits: int) -> list[tuple[float, float]]:
    """Every wheel's interval of positions (middle, half width) over the values [lo - 0.5, hi + 0.5];
    half 5 for a wheel that went more than half way round (no information)."""
    vs = np.linspace(lo - 0.5, hi + 0.5, min(2000, max(21, (hi - lo + 1) * 20)))
    pos = np.array([positions(float(v) % 10**digits, digits) for v in vs])
    out = []
    for k in range(digits):
        p = np.unwrap(pos[:, k] * 2 * np.pi / 10) * 10 / (2 * np.pi)
        a, b = p.min(), p.max()
        if b - a > 5:
            out.append((0.0, 5.0))
        elif b - a < 0.05:
            out.append(((a + b) / 2 % 10, REST))
        else:
            out.append(((a + b) / 2 % 10, (b - a) / 2 + PAD))
    return out


MAX_OFFSET = 0.38  # of a cell, as rows.MAX_OFFSET


def moved_box(box, digits: int, height: int):
    """The region moved as a user might draw it: shifted by up to MAX_OFFSET of a cell, a little
    narrower or wider, higher or lower (rows.row_cell's cut, on a real frame)."""
    x0, y0, x1, y1 = box
    cw, h = (x1 - x0) / digits, y1 - y0
    off = random.uniform(-MAX_OFFSET, MAX_OFFSET) if random.random() < 0.8 else random.uniform(-0.1, 0.1)
    wide = random.uniform(-0.15, 0.15) * digits / 2  # cells, each side
    top, bottom = random.uniform(-0.12, 0.12), random.uniform(-0.12, 0.12)
    return (x0 + (off - wide) * cw, max(0, y0 + top * h), x1 + (off + wide) * cw, min(height, y1 + bottom * h))


def cut(im: Image.Image, box, digits: int):
    """The one-cell input and the context input of a region in a frame."""
    x0, y0, x1, y1 = box
    region = im.crop((round(max(0, x0)), round(y0), round(min(im.width, x1)), round(y1)))
    return np.stack([(cell_input(c) * 255).astype(np.uint8) for c in split_cells(region, digits)]), ctx_cells(im, box, digits)


def training_cells(X, lo, hi, digits):
    xs, mids, halves = [], [], []
    for i in range(len(X)):
        for k, (m, h) in enumerate(wheel_intervals(int(lo[i]), int(hi[i]), digits)):
            if h < 5:
                xs.append(X[i, k])
                mids.append(m)
                halves.append(h)
    return {"X": np.stack(xs), "mid": np.array(mids, np.float32), "half": np.array(halves, np.float32)}


def build(name: str, folder: Path, split: str, moved: int = 0) -> None:
    random.seed(0)
    data = json.loads((folder / "readings.json").read_text(encoding="utf-8"))
    readings, digits = data["readings"], int(data["settings"]["digits"])
    labs = labels(readings, int(data["settings"]["decimals"]))
    eye = folder / "eye.json"
    if eye.exists():
        number = {r["file"][-8:-4]: i for i, r in enumerate(readings)}
        for first, last, lo, hi in json.loads(eye.read_text()):
            for n in range(first, last + 1):
                if (i := number.get(f"{n:04d}")) is not None:
                    labs[i] = {"lo": lo, "hi": hi, "how": "eye"}
    frames = folder / "frames.json"
    if frames.exists():
        number = {r["file"][-8:-4]: i for i, r in enumerate(readings)}
        for n, v in json.loads(frames.read_text()).items():
            if (i := number.get(n)) is not None:
                labs[i] = {"lo": None, "hi": None, "how": "unreadable"} if v is None else {"lo": v[0], "hi": v[1], "how": "eye"}
    parts = {p: {"X": [], "C": [], "lo": [], "hi": [], "seconds": [], "MX": [], "MC": [], "Mlo": [], "Mhi": []} for p in "ab"}
    skipped = 0
    for r, lab in sorted(zip(readings, labs, strict=True), key=lambda x: x[0]["seconds"]):
        if lab["lo"] is None or not (folder / r["file"]).exists():
            continue
        if r.get("region") != "as read" or not r.get("box"):
            skipped += 1
            continue
        im = Image.open(folder / r["file"]).convert("RGB")
        box = r["box"]
        part = parts["a" if r["date"] <= split else "b"]
        cells, context = cut(im, box, digits)
        part["X"].append(cells)
        part["C"].append(context)
        for _ in range(moved):
            cells, context = cut(im, moved_box(box, digits, im.height), digits)
            part["MX"].append(cells)
            part["MC"].append(context)
            part["Mlo"].append(lab["lo"])
            part["Mhi"].append(lab["hi"])
        part["lo"].append(lab["lo"])
        part["hi"].append(lab["hi"])
        part["seconds"].append(r["seconds"])
    (WORK / "bench").mkdir(exist_ok=True)
    for p, d in parts.items():
        if not d["X"]:
            continue
        lo, hi = np.array(d["lo"], np.int64), np.array(d["hi"], np.int64)
        group = np.cumsum(np.r_[True, (lo[1:] != lo[:-1]) | (hi[1:] != hi[:-1])]) - 1  # in time order
        for suffix, key in (("", "X"), ("_ctx", "C")):
            X = np.stack(d[key])
            np.savez_compressed(
                WORK / "bench" / f"{name}_{p}{suffix}.npz",
                X=X, lo=lo, hi=hi, seconds=np.array(d["seconds"], np.int64), group=group, digits=digits,
            )
            np.savez_compressed(WORK / "data" / f"{name}_{p}{suffix}.npz", **training_cells(X, lo, hi, digits))
            if moved:
                mlo, mhi = np.array(d["Mlo"], np.int64), np.array(d["Mhi"], np.int64)
                MX = np.stack(d["M" + key])
                np.savez_compressed(WORK / "data" / f"{name}_{p}_moved{suffix}.npz", **training_cells(MX, mlo, mhi, digits))
        print(f"{name}_{p}", len(lo), "readings in", group[-1] + 1, "runs", f"(+ {len(d['Mlo'])} moved)" if moved else "")
    print(skipped, "readings without a region as read left out (still used for the labels)")


if __name__ == "__main__":
    build(sys.argv[1], Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 0)
