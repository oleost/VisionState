"""Wheel model vs today's reader on the evaluation sets.

python evaluate.py runs/<model>.pt [sets...] [--width 32] [--show]
"""

import argparse
import json
import math
from collections import Counter

import numpy as np
import torch

from common import cell_input, decode, decode_shifted, split_cells
from items import SETS
from paths import LOCAL, WORK
from train import DEV, WheelNet, predict

HERE = WORK
DEFAULT = ["pedro40", "pedro_esp", "pedro32", "bol_cold", "bol_warm"]


def smooth(logp: np.ndarray, slack: int) -> np.ndarray:
    """Allow a wheel at rest to sit up to ``slack`` bins off its digit."""
    if slack == 0:
        return logp
    p = np.exp(logp)
    s = sum(np.roll(p, k, axis=1) for k in range(-slack, slack + 1))
    return np.log(np.maximum(s, 1e-12))


def category(got, truth) -> str:
    if got is None:
        return "none"
    d = got - truth
    return "exact" if d == 0 else "+1" if d == 1 else "-1" if d == -1 else "HIGH" if d > 0 else "low"


SHIFT = 4


def read_value(model, region, digits: int, slack: int) -> tuple[float, float]:
    cells = np.stack([(cell_input(c) * 255).astype(np.uint8) for c in split_cells(region, digits)])
    logp = smooth(predict(model, cells), slack)
    if SHIFT == 0:
        return decode(logp)
    value, score, _ = decode_shifted(logp, SHIFT)
    return value, score


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("sets", nargs="*")
    ap.add_argument("--width", type=int, default=32)
    ap.add_argument("--slack", type=int, default=1)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--shift", type=int, default=4)
    args = ap.parse_args()
    global SHIFT
    SHIFT = args.shift
    model = WheelNet(args.width).to(DEV)
    model.load_state_dict(torch.load(args.model, map_location=DEV))
    model.eval()
    base = {}
    for rid in ("ppocrv6-small", "ppocrv6-tiny"):
        f = HERE / "runs" / f"baseline-{rid}.json"
        if f.exists():
            base[rid] = {k: {r["id"]: r for r in v} for k, v in json.loads(f.read_text()).items()}
    for name in args.sets or DEFAULT:
        rows = []
        try:
            for key, digits, truth, region in SETS[name]():
                value, score = read_value(model, region, digits, args.slack)
                truth_int = math.floor(truth + 0.5)
                got = math.floor(value + 0.5)
                rows.append((key, truth, truth_int, value, got, score))
        except FileNotFoundError:
            print(f"{name:13s} skipped (data not in {LOCAL})")
            continue
        cats = Counter(category(r[4], r[2]) for r in rows)
        line = f"{name:13s} n={len(rows):4d}  wheel: {cats['exact']:4d} exact  " + " ".join(
            f"{k}={cats[k]}" for k in ("+1", "-1", "HIGH", "low") if cats[k]
        )
        for rid, sets in base.items():
            if name in sets:
                bc = Counter(category(sets[name][r[0]]["got"], r[2]) for r in rows if r[0] in sets[name])
                line += f" | {rid[8:]}: {bc['exact']} exact " + " ".join(
                    f"{k}={bc[k]}" for k in ("+1", "-1", "HIGH", "low", "none") if bc[k]
                )
        print(line, flush=True)
        if args.show:
            for key, truth, ti, value, got, score in rows:
                if got != ti:
                    b = base.get("ppocrv6-small", {}).get(name, {}).get(key, {}).get("got")
                    print(f"     {key:45s} truth {truth:>10} wheel {value:10.1f} (score {score:6.2f}) small {b}")


if __name__ == "__main__":
    main()
