"""Boxes that fit the wheels, for training on a shared export (`docs/WHEEL_READER_PLAN.md` §5).

python fit_boxes.py <export dir> <model>        writes fitted.json ({file: box}) in the export folder

An export says where the user's region was, and a region can be off (cut a wheel, sit a little
wide): trained on as it is, a model learns to read the neighbouring wheel. For every stretch of
frames read with the same region, this finds the move of its edges (left/right in cells, top/bottom
in shares of its height) under which the frames' labels are most likely: the labels come from
other frames (exports.labels), never from what the model reads. <model> as in bench.py.
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import bench
import exports
from context import ctx_cells

SHIFTS = np.round(np.arange(-0.5, 0.501, 0.05), 2)  # cells, the whole box
WIDTHS = np.round(np.arange(-0.3, 0.301, 0.05), 2)  # cells, each side
HEIGHTS = (-0.1, -0.05, 0.0, 0.05, 0.1)  # shares of the height, top and bottom
MIN_GAIN = 0.25  # log probability per frame a move must gain, else the region stays as drawn
COST = 0.1  # per frame and cell (or share of the height) moved: of two moves that explain the labels alike, the smaller


def stretches(items, tolerance=0.03):
    """Runs of frames (in time) whose box differs by less than ``tolerance`` of its width."""
    runs, current = [], []
    for item in sorted(items, key=lambda it: it["seconds"]):
        if current:
            first = current[0]["box"]
            if max(abs(a - b) for a, b in zip(item["box"], first)) > tolerance * (first[2] - first[0]):
                runs.append(current)
                current = []
        current.append(item)
    return runs + [current] if current else runs


def moved(box, digits, left, right, top, bottom):
    x0, y0, x1, y1 = box
    cw, h = (x1 - x0) / digits, y1 - y0
    return (x0 + left * cw, y0 + top * h, x1 + right * cw, y1 + bottom * h)


def score(model, frames, digits, move):
    """Sum over frames of the log probability of every wheel's label interval, less COST for the move."""
    cells, context, masks = [], [], []
    for f in frames:
        x0, y0, x1, y1 = moved(f["box"], digits, *move)
        im = f["image"]
        y0, y1 = max(0, y0), min(im.height, y1)
        region = im.crop((round(max(0, x0)), round(y0), round(min(im.width, x1)), round(y1)))
        cells.append(bench.one_cell(region, digits))
        context.append(ctx_cells(im, (x0, y0, x1, y1), digits))
        masks.append(f["mask"])
    X, C = np.stack(cells), np.stack(context)
    P = model.probs(X.reshape(-1, *X.shape[2:]), C.reshape(-1, *C.shape[2:]))
    M = np.concatenate(masks)
    fit = float(np.log(np.maximum((P * M).sum(axis=1), 1e-9)).sum())
    return fit - COST * len(frames) * sum(abs(v) for v in move)


def wheel_masks(lo, hi, digits):
    centres = np.arange(bench.BINS) / 10
    out = []
    for mid, half in exports.wheel_intervals(lo, hi, digits):
        dist = np.abs((centres - mid + 5) % 10 - 5)
        out.append(dist <= half + 1e-4)
    return np.array(out, np.float64)


def fit(folder: Path, model) -> None:
    data = json.loads((folder / "readings.json").read_text(encoding="utf-8"))
    readings, digits = data["readings"], int(data["settings"]["digits"])
    labels = exports.labels(readings, int(data["settings"]["decimals"]))
    boxes = json.loads((folder / "boxes.json").read_text()) if (folder / "boxes.json").exists() else {}
    items = []
    for r, lab in zip(readings, labels, strict=True):
        box = r.get("box") or boxes.get(r["file"])
        if box is not None:
            items.append({"file": r["file"], "box": box, "seconds": r.get("seconds", 0), "date": r.get("date"), "label": lab})
    out = {}
    for run in stretches(items):
        # a wide interval (or none) says little about where the wheels are: such frames only take
        # the box their stretch gets
        known = [f for f in run if f["label"]["lo"] is not None and f["label"]["hi"] - f["label"]["lo"] <= 10]
        sample = known[:: max(1, len(known) // 40)]  # enough frames to fit a box, not every one
        if not sample:
            for f in run:
                out[f["file"]] = f["box"]
            continue
        for f in sample:
            f["image"] = Image.open(folder / f["file"]).convert("RGB")
            f["mask"] = wheel_masks(f["label"]["lo"], f["label"]["hi"], digits)
        best = (0.0, 0.0, 0.0, 0.0)
        best_score = score(model, sample, digits, best)
        as_drawn = best_score
        for shift in SHIFTS:
            for width in WIDTHS:
                move = (shift - width, shift + width, 0.0, 0.0)
                s = score(model, sample, digits, move)
                if s > best_score:
                    best, best_score = move, s
        for top in HEIGHTS:
            for bottom in HEIGHTS:
                move = (best[0], best[1], top, bottom)
                s = score(model, sample, digits, move)
                if s > best_score:
                    best, best_score = move, s
        per_frame = (best_score - as_drawn) / len(sample)
        if per_frame < MIN_GAIN:
            best = (0.0, 0.0, 0.0, 0.0)
        print(f"{run[0]['date']}..{run[-1]['date']} ({len(run)} frames, {len(sample)} fitted on): left {best[0]:+.2f} right {best[1]:+.2f} cells, "
              f"top {best[2]:+.2f} bottom {best[3]:+.2f}; log p per frame {per_frame:+.2f}", flush=True)
        for f in run:
            out[f["file"]] = [round(v) for v in moved(f["box"], digits, *best)]
            f.pop("image", None)
    (folder / "fitted.json").write_text(json.dumps(out))
    print(len(out), "boxes")


if __name__ == "__main__":
    fit(Path(sys.argv[1]), bench.Model(sys.argv[2]))
