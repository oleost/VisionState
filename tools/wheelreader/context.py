"""Cells with context (wheels-v2): each wheel's cell plus CTX of a cell on each side, taken from the
picture around the region where there is some (else the edge repeated), as a CTX_W x CELL_H input.
Must match ``readers.wheel_context_cells`` in the app.

python context.py dryad       data/dryad_train_ctx.npz (both orientations, as data.py dryad)
python context.py pedromfa    data/pedro_train_ctx.npz, data/pedro_test_ctx.npz (as data.py pedromfa)
"""

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from common import CELL_H, MARGIN, darkest
from paths import LOCAL, WORK

CTX = 0.5
CTX_W = 64


def ctx_input(part: Image.Image) -> np.ndarray:
    """A cell with its context (any size) -> uint8 CELL_H x CTX_W."""
    g = part if part.mode == "L" else darkest(part)
    g = ImageOps.autocontrast(g.resize((CTX_W, CELL_H), Image.Resampling.BILINEAR), cutoff=1)
    return np.asarray(g, dtype=np.uint8)


def ctx_cells(image: Image.Image, box, digits: int) -> np.ndarray:
    """``box`` (x0, y0, x1, y1) is the region inside ``image``; the cells reach CTX beyond it."""
    x0, y0, x1, y1 = box
    cell = (x1 - x0) / digits
    pad = int(math.ceil(cell * CTX)) + 2
    g = np.pad(np.asarray(darkest(image)), ((0, 0), (pad, pad)), mode="edge")
    wide = Image.fromarray(g)
    out = []
    for k in range(digits):
        a = x0 + k * cell - CTX * cell + pad
        b = x0 + (k + 1) * cell + CTX * cell + pad
        out.append(ctx_input(wide.crop((round(a), round(y0), round(b), round(y1)))))
    return np.stack(out)


def centre_box(image: Image.Image):
    """Where the region is in an exported image without a "box" (older exports: the middle)."""
    w, h = image.width / (1 + 2 * MARGIN), image.height / (1 + 2 * MARGIN)
    x, y = (image.width - w) / 2, (image.height - h) / 2
    return (x, y, x + w, y + h)


def save(name: str, xs, mids, halves, **extra) -> None:
    np.savez_compressed(
        WORK / "data" / f"{name}.npz",
        X=np.stack(xs),
        mid=np.array(mids, np.float32),
        half=np.array(halves, np.float32),
        **extra,
    )
    print(name, len(xs), "cells")


def build_dryad() -> None:
    from data import dryad_rows, intervals

    xs, mids, halves, img_id, rot = [], [], [], [], []
    for k, (path, digits, _flags) in enumerate(dryad_rows("train")):
        im = Image.open(path).convert("RGB")
        for flip in (0, 1):
            src = im.rotate(180) if flip else im
            for cell, (m, h) in zip(ctx_cells(src, (0, 0, src.width, src.height), len(digits)), intervals(digits), strict=True):
                xs.append(cell)
                mids.append(m)
                halves.append(h)
                img_id.append(k)
                rot.append(flip)
    save("dryad_train_ctx", xs, mids, halves, img=np.array(img_id), rot=np.array(rot))


def build_pedromfa() -> None:
    import json

    from data import intervals

    sets = {
        "pedro_train_ctx": [(LOCAL / "pedromfa-watermeter", None), (LOCAL / "pedromfa-watermeter-esp", None)],
        "pedro_test_ctx": [(LOCAL / "pedromfa-watermeter-issue40", set(range(1, 18)))],
    }
    for name, sources in sets.items():
        xs, mids, halves = [], [], []
        for folder, only in sources:
            data = json.loads((Path(folder) / "readings.json").read_text(encoding="utf-8"))
            for r in data["readings"]:
                i = int(r["file"][-8:-4])
                if only and i not in only:
                    continue
                if r["answer"] == "unchecked" or (r["answer"] == "misread" and not r["right_value"]):
                    continue
                v = float(r["right_value"] or r["value"])
                if v > 10000:
                    v /= 1000  # an old export stored "629558" as 629558.000
                im = Image.open(Path(folder) / r["file"]).convert("RGB")
                cells = ctx_cells(im, r.get("box") or centre_box(im), 7)
                for cell, (m, h) in zip(cells, intervals(f"{round(v * 1000):07d}"), strict=True):
                    xs.append(cell)
                    mids.append(m)
                    halves.append(h)
        save(name, xs, mids, halves)


if __name__ == "__main__":
    {"dryad": build_dryad, "pedromfa": build_pedromfa}[sys.argv[1]]()
