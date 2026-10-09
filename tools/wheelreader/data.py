"""Build the cell datasets as .npz: X (N, H, W) uint8, mid / half (interval of the position).

python data.py synth 400000      drawn wheels (exact positions)
python data.py dryad             Dryad recognition crops (nearest digit per wheel; train + test)
python data.py pedromfa          pedromfa exports (#32 for training, #40 0001-0017 held out)
"""

import csv
import json
import random
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

from common import cell_input, export_region, split_cells
from paths import DRYAD, LOCAL, WORK

OUT = WORK / "data"

REST, MOVING = 0.25, 0.6  # half width of the interval for a wheel at rest / possibly turning


def intervals(digits: str) -> list[tuple[float, float]]:
    """Per wheel (most significant first) the nearest-digit interval: the last wheel and every
    wheel whose right neighbour may be passing 9 -> 0 may be anywhere around its digit."""
    out = []
    moving = True  # the last wheel
    for k in range(len(digits) - 1, -1, -1):
        d = int(digits[k])
        out.append((float(d), MOVING if moving else REST))
        moving = moving and d in (9, 0)
    return out[::-1]


def _synth(args):
    seed, n = args
    import synth

    random.seed(seed)
    np.random.seed(seed % 2**32)
    xs, ps = [], []
    for _ in range(n):
        p = random.uniform(0, 10)
        if random.random() < 0.4:  # wheels at rest are the common case
            p = (round(p) + random.gauss(0, 0.06)) % 10
        xs.append((cell_input(synth.render(p)) * 255).astype(np.uint8))
        ps.append(p)
    return np.stack(xs), np.array(ps, np.float32)


def build_synth(n: int, name: str = "synth", seed0: int = 0) -> None:
    chunks = [(seed0 + i, 2000) for i in range(n // 2000)]
    with Pool(22) as pool:
        parts = pool.map(_synth, chunks)
    x = np.concatenate([p[0] for p in parts])
    p = np.concatenate([p[1] for p in parts])
    np.savez_compressed(OUT / f"{name}.npz", X=x, mid=p, half=np.full(len(p), 0.05, np.float32))
    print(name, x.shape)


def dryad_rows(split: str):
    for nd in (5, 6):
        folder = DRYAD / f"recognition_{nd}-digit" / split
        for row in csv.reader(open(folder / f"{nd}-digit_{split}_rec_label_CSV.csv", encoding="utf-8-sig")):
            yield folder / f"{nd}-digit_{split}_img" / row[0], row[1], row[2:]


def build_dryad() -> None:
    for split in ("train", "test"):
        xs, mids, halves, img_id, rot = [], [], [], [], []
        for k, (path, digits, _flags) in enumerate(dryad_rows(split)):
            im = Image.open(path).convert("RGB")
            for flip in (0, 1):  # both orientations; the right one is chosen later
                src = im.rotate(180) if flip else im
                for cell, (m, h) in zip(split_cells(src, len(digits)), intervals(digits), strict=True):
                    xs.append((cell_input(cell) * 255).astype(np.uint8))
                    mids.append(m)
                    halves.append(h)
                    img_id.append(k)
                    rot.append(flip)
        np.savez_compressed(
            OUT / f"dryad_{split}.npz",
            X=np.stack(xs),
            mid=np.array(mids, np.float32),
            half=np.array(halves, np.float32),
            img=np.array(img_id),
            rot=np.array(rot),
        )
        print("dryad", split, len(xs) // 2, "cells per orientation")


def pedromfa_items(folder: Path, only: set[int] | None = None):
    data = json.loads((folder / "readings.json").read_text(encoding="utf-8"))
    for r in data["readings"]:
        i = int(r["file"][-8:-4])
        if only and i not in only:
            continue
        if r["answer"] == "misread" and not r["right_value"]:
            continue
        v = float(r["right_value"] or r["value"])
        if v > 10000:
            v /= 1000  # an old export stored "629558" as 629558.000
        digits = f"{round(v * 1000):07d}"
        yield i, r["date"], digits, export_region(Image.open(folder / r["file"]).convert("RGB"))


def build_pedromfa() -> None:
    sets = {
        "pedro_train": [(LOCAL / "pedromfa-watermeter", None), (LOCAL / "pedromfa-watermeter-esp", None)],
        "pedro_test": [(LOCAL / "pedromfa-watermeter-issue40", set(range(1, 18)))],
    }
    for name, sources in sets.items():
        xs, mids, halves = [], [], []
        for folder, only in sources:
            for _i, _date, digits, region in pedromfa_items(folder, only):
                for cell, (m, h) in zip(split_cells(region, 7), intervals(digits), strict=True):
                    xs.append((cell_input(cell) * 255).astype(np.uint8))
                    mids.append(m)
                    halves.append(h)
        np.savez_compressed(
            OUT / f"{name}.npz", X=np.stack(xs), mid=np.array(mids, np.float32), half=np.array(halves, np.float32)
        )
        print(name, len(xs) // 7, "readings")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    what = sys.argv[1]
    if what == "synth":
        build_synth(
            int(sys.argv[2]),
            sys.argv[3] if len(sys.argv) > 3 else "synth",
            int(sys.argv[4]) if len(sys.argv) > 4 else 0,
        )
    elif what == "dryad":
        build_dryad()
    elif what == "pedromfa":
        build_pedromfa()
