"""The wheel reader benchmark (`docs/WHEEL_READER_PLAN.md` §3).

python bench.py build                          bench/ sets of pedromfa #32/#40, bolausson, Dryad test
python bench.py eval <model> [--sets a,b] [--tag name] [--sweep]
python bench.py geometry <model> <export dir>   the same frames with the region's edges moved
python bench.py compare <base tag> <tag> [<tag> ...] [--sets a,b]
                                               frame by frame against a base (results of eval --tag)

Export sets come from exports.py (bench/<name>_a.npz, _b.npz, each with a _ctx twin). <model>:
runs/<m>.pt (train.py; --input 64 reads the _ctx twins), "<one-cell>.pt+<context>.pt" (both, as
wheels-v2), or an .onnx file (wheels-v1: one input; wheels-v2: "cells" and "context").

Decoding is the app's (readers.WheelReader.read_counter): slack 1 bin, common shift +-2 bins
(penalty 0.3 per bin). Confidence: "mean" (geometric mean of the decoded bins, wheels-v1) and
"wmin" (the weakest wheel's mass within +-0.4 of where the value puts it, wheels-v2).
Metrics: right (+-1 on the last wheel), exact, too high; risk-coverage (AURC, share accepted at
1 % wrong); for export sets a replay through "went down" and the change limit at 0.70 (--sweep: also
at other limits, for a model whose confidence is spread differently), and "ok"
and "too high" with every run of frames with the same label (a still meter) counted once.

compare: the tags after the base are training runs of one candidate. Per set, the difference in
"ok" and "too high" per run of frames (each run counted once), averaged over the candidate's
training runs, with a 95 % interval from resampling the runs of frames (bootstrap). An interval
that holds 0 is no difference.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from common import BINS, cell_input, positions, split_cells
from context import ctx_cells
from paths import WORK

BENCH = WORK / "bench"
TENTH = BINS // 10


def wheel_decode(logp):
    """readers.wheel_decode."""
    wheels, bins = logp.shape
    best = logp[-1].copy()
    back = []
    for k in range(wheels - 2, -1, -1):
        rest = int(best[: 9 * TENTH + 1].argmax())
        lead = np.concatenate([[best[rest]], best[9 * TENTH + 1 :]])
        lead_bin = np.concatenate([[rest], np.arange(9 * TENTH + 1, bins)])
        best = (logp[k].reshape(10, TENTH) + lead[None, :]).reshape(bins)
        back.append(np.tile(lead_bin, 10))
    b = int(best.argmax())
    score = float(best[b])
    at = [b]
    for arg in reversed(back):
        b = int(arg[b])
        at.append(b)
    value = 0.0
    for a in at[:-1]:
        value = value * 10 + a // TENTH
    return value * 10 + at[-1] / TENTH, score, at


def read(probs, slack=1, max_shift=2, penalty=0.3):
    """Value (last wheel rounded), mean and weakest-wheel confidence, log probability."""
    near = sum(np.roll(probs, s, axis=1) for s in range(-slack, slack + 1))
    logp = np.log(np.maximum(near, 1e-12))
    best = None
    for shift in range(-max_shift, max_shift + 1):
        value, score, at = wheel_decode(np.roll(logp, -shift, axis=1))
        score -= penalty * abs(shift)
        if best is None or score > best[1]:
            best = (value, score, at, shift)
    value, score, at, shift = best
    digits = probs.shape[0]
    seen = [(b + shift) % BINS for b in at]
    mass = [sum(probs[k, (b + d) % BINS] for d in range(-4, 5)) for k, b in enumerate(seen)]
    return math.floor(value + 0.5) % 10**digits, math.exp(score / digits), float(min(mass)), score


def softmax(logits):
    logits = logits - logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    return p / p.sum(axis=1, keepdims=True)


class Model:
    """probs(cells, context) -> N x BINS for a .pt, a "one-cell.pt+context.pt" pair or an .onnx.

    "<spec>@<t>" divides the log probabilities by a temperature t (below 1: surer).
    """

    def __init__(self, spec: str):
        self.parts = []
        spec, _, t = spec.partition("@")
        self.t = float(t or 1)
        if spec.endswith(".onnx"):
            import onnxruntime as ort

            self.onnx = ort.InferenceSession(spec, providers=["CPUExecutionProvider"])
            self.inputs = [i.name for i in self.onnx.get_inputs()]
        else:
            from train import load_model

            self.onnx = None
            self.parts = [load_model(p, 16) for p in spec.split("+")]

    def probs(self, cells, context):
        if self.onnx is not None:
            feeds = [cells, context][: len(self.inputs)]
            out = []
            for i in range(0, len(cells), 2048):
                feed = {n: x[i : i + 2048].astype(np.float32)[:, None] / 255 for n, x in zip(self.inputs, feeds)}
                out.append(self.onnx.run(None, feed)[0])
            return softmax(np.concatenate(out).astype(np.float64) / self.t)
        from train import predict

        logp = sum(predict(m, context if _is_ctx(m) else cells) for m in self.parts)
        return softmax(logp.astype(np.float64) / len(self.parts) / self.t)


def _is_ctx(model) -> bool:
    """Whether a train.py model reads cells with context (its head is twice as wide)."""
    width = model.features[0][0].out_channels
    return model.head[2].in_features == 4 * width * 6 * 8


def simulate(rows, conf_i, t):
    """Replay through "went down" + the change limit while it learns (40 steps a minute + 2)."""
    rows = sorted(rows, key=lambda r: r["seconds"])
    last, last_t = rows[0]["lo"], rows[0]["seconds"]
    pub = wrong = high = 0
    stale = 0.0
    prev = rows[0]["seconds"]
    for r in rows[1:]:
        if last > r["hi"] + 1:
            stale += r["seconds"] - prev
        prev = r["seconds"]
        v, conf = r["v"], r["conf"][conf_i]
        if conf < t or v < last or v - last > 2 + 40 * (r["seconds"] - last_t) / 60:
            continue
        pub += 1
        if not (r["lo"] - 1 <= v <= r["hi"] + 1):
            wrong += 1
            high += v > r["hi"] + 1
        last, last_t = v, r["seconds"]
    return pub, wrong, high, stale / 3600


def evaluate(model: Model, sets: list[str], sweep: bool = False) -> tuple[dict, dict]:
    """Per set: the metrics, and per frame ok / too high / weakest-wheel confidence / run."""
    results, frames = {}, {}
    for name in sets:
        d, c = np.load(BENCH / f"{name}.npz"), np.load(BENCH / f"{name}_ctx.npz")
        n, digits = d["X"].shape[:2]
        P = model.probs(d["X"].reshape(-1, *d["X"].shape[2:]), c["X"].reshape(-1, *c["X"].shape[2:])).reshape(n, digits, BINS)
        rows = []
        for i in range(n):
            v, mean, wmin, score = read(P[i])
            rows.append({"v": v, "conf": (mean, wmin), "lo": int(d["lo"][i]), "hi": int(d["hi"][i]),
                         "seconds": int(d["seconds"][i]), "group": int(d["group"][i])})
        if name.startswith("dryad"):  # both orientations of every photo: keep the one the label fits
            for i, r in enumerate(rows):
                pos = positions(float(r["lo"]), digits)
                r["fit"] = sum(math.log(max(1e-12, sum(P[i, k, (round(p * 10) + o) % BINS] for o in range(-5, 6)))) for k, p in enumerate(pos))
            best = {}
            for r in rows:
                if r["group"] not in best or r["fit"] > best[r["group"]]["fit"]:
                    best[r["group"]] = r
            rows = list(best.values())
        frames[name] = {
            "ok": np.array([r["lo"] - 1 <= r["v"] <= r["hi"] + 1 for r in rows]),
            "high": np.array([r["v"] > r["hi"] + 1 for r in rows]),
            "wmin": np.array([r["conf"][1] for r in rows]),
            "group": np.array([r["group"] for r in rows]),
        }
        res = {"n": len(rows)}
        res["exact"] = sum(r["lo"] <= r["v"] <= r["hi"] for r in rows)
        res["ok"] = sum(r["lo"] - 1 <= r["v"] <= r["hi"] + 1 for r in rows)
        res["high"] = sum(r["v"] > r["hi"] + 1 for r in rows)
        sequence = name.endswith(("_a", "_b", "_a_eye", "_b_eye"))
        if sequence:  # every run of frames with the same label once
            f = frames[name]
            runs, inv = np.unique(f["group"], return_inverse=True)
            w = 1 / np.bincount(inv)[inv]
            res["runs"] = len(runs)
            res["ok_runs"] = float((f["ok"] * w).sum())
            res["high_runs"] = float((f["high"] * w).sum())
        for ci, cname in ((0, "mean"), (1, "wmin")):
            srt = sorted(rows, key=lambda r: -r["conf"][ci])
            risk = np.cumsum([not (r["lo"] - 1 <= r["v"] <= r["hi"] + 1) for r in srt]) / np.arange(1, len(srt) + 1)
            res[f"aurc_{cname}"] = float(risk.mean() * 100)
            res[f"cov1_{cname}"] = max([k + 1 for k in range(len(srt)) if risk[k] <= 0.01] or [0]) / len(srt)
            if sequence:
                res[f"replay_{cname}"] = simulate(rows, ci, 0.7)
        results[name] = res
        line = (f"{name:10s} n={res['n']:5d} exact {res['exact'] / res['n']:6.1%} ok {res['ok'] / res['n']:6.1%} "
                f"high {res['high']:4d}  AURC mean {res['aurc_mean']:5.2f} wmin {res['aurc_wmin']:5.2f}  "
                f"cov@1% wmin {res['cov1_wmin']:.2f}")
        if sequence:
            p, w, _h, s = res["replay_wmin"]
            line += (f"  | runs {res['runs']}: ok {res['ok_runs'] / res['runs']:6.1%} high {res['high_runs']:5.1f}"
                     f"  | replay@0.7 wmin: {p} published, {w} wrong, {s:.1f} h too high")
        print(line, flush=True)
        if sequence and sweep:
            print("            " + "  ".join(f"@{t:.2f}: {p} pub {w} wrong" for t in (0.4, 0.5, 0.6, 0.8)
                                          for p, w, _h, _s in [simulate(rows, 1, t)]), flush=True)
    return results, frames


def compare(base: str, tags: list[str], sets: list[str] | None, rounds: int = 2000) -> None:
    load = lambda tag: np.load(WORK / "results" / f"{tag}.npz")  # noqa: E731
    b, cands = load(base), [load(t) for t in tags]
    names = sorted({k.split("|")[0] for k in b.files})
    rng = np.random.default_rng(0)
    print(f"{', '.join(tags)} against {base}: difference per run of frames (95 % interval)")
    for name in sets or names:
        if f"{name}|ok" not in b.files or any(f"{name}|ok" not in c.files for c in cands):
            continue
        group = b[f"{name}|group"]
        runs, inv = np.unique(group, return_inverse=True)
        w = 1 / np.bincount(inv)[inv]
        out = []
        for metric in ("ok", "high"):
            diff = np.mean([c[f"{name}|{metric}"].astype(float) for c in cands], axis=0) - b[f"{name}|{metric}"]
            per_run = np.bincount(inv, weights=diff * w)  # each run's share, summing to its difference
            total = per_run.sum()
            boot = per_run[rng.integers(0, len(runs), (rounds, len(runs)))].sum(axis=1)
            lo, hi = np.percentile(boot, [2.5, 97.5])
            scale = 100 / len(runs) if metric == "ok" else 1
            unit = " pts" if metric == "ok" else ""
            out.append(f"{metric} {total * scale:+6.2f}{unit} [{lo * scale:+.2f}, {hi * scale:+.2f}]")
        print(f"  {name:10s} {len(runs):4d} runs  " + "   ".join(out))


def save(name, cells, context, lo, hi, digits):
    for suffix, X in (("", cells), ("_ctx", context)):
        np.savez_compressed(BENCH / f"{name}{suffix}.npz", X=np.stack(X), lo=np.array(lo, np.int64), hi=np.array(hi, np.int64),
                            seconds=np.arange(len(X)), group=np.array(_groups(name, len(X))), digits=digits)
    print(name, len(cells))


def _groups(name, n):
    return [k // 2 for k in range(n)] if name.startswith("dryad") else list(range(n))


def one_cell(region: Image.Image, digits: int) -> np.ndarray:
    return np.stack([(cell_input(c) * 255).astype(np.uint8) for c in split_cells(region, digits)])


def build() -> None:
    from items import SETS, pedromfa

    BENCH.mkdir(exist_ok=True)
    pedro = {"pedro40": ("pedromfa-watermeter-issue40", set(range(1, 18))), "pedro_esp": ("pedromfa-watermeter-esp", None),
             "pedro32": ("pedromfa-watermeter", None)}
    for name, (folder, only) in pedro.items():
        cells, context, lo = [], [], []
        for _id, nd, truth, (image, box) in pedromfa(folder, only, whole=True):
            cells.append(one_cell(image.crop(tuple(round(v) for v in box)), nd))
            context.append(ctx_cells(image, box, nd))
            lo.append(round(truth))
        save(name, cells, context, lo, lo, 7)
    for name in ("bol_cold", "bol_warm"):  # GPL-3.0: test only, skipped when missing
        try:
            items = list(SETS[name]())
        except FileNotFoundError:
            print(name, "skipped")
            continue
        cells, context, lo, hi = [], [], [], []
        for _id, nd, truth, region in items:
            cells.append(one_cell(region, nd))
            context.append(ctx_cells(region, (0, 0, region.width, region.height), nd))
            frac = truth - math.floor(truth)  # the tenths read by eye: near half way either neighbour is fair
            lo.append(round(truth) if abs(frac - 0.5) > 0.2 else math.floor(truth))
            hi.append(round(truth) if abs(frac - 0.5) > 0.2 else math.floor(truth) + 1)
        save(name, cells, context, lo, hi, 7)
    for nd in (5, 6):  # both orientations (a third of the photos is upside down)
        cells, context, lo = [], [], []
        for _id, d, truth, region in (r for r in SETS["dryad_test"]() if r[1] == nd):
            for flip in (0, 1):
                im = region.rotate(180) if flip else region
                cells.append(one_cell(im, nd))
                context.append(ctx_cells(im, (0, 0, im.width, im.height), nd))
                lo.append(round(truth))
        save(f"dryad{nd}", cells, context, lo, lo, nd)


GEOMETRY = {  # the region's left, right (in cells), top, bottom (share of its height) moved by
    "as drawn": (0, 0, 0, 0),
    "shift right 0.25 cell": (0.25, 0.25, 0, 0),
    "shift left 0.25 cell": (-0.25, -0.25, 0, 0),
    "wider 0.5 cell each side": (-0.5, 0.5, 0, 0),
    "narrower 0.3 cell each side": (0.3, -0.3, 0, 0),
    "right edge in 0.5 cell": (0, -0.5, 0, 0),
    "up 10 %": (0, 0, -0.1, -0.1),
    "down 10 %": (0, 0, 0.1, 0.1),
    "taller 10 % each side": (0, 0, -0.1, 0.1),
    "shorter 10 % each side": (0, 0, 0.1, -0.1),
}


def geometry(model: Model, folder: Path, every: int = 3) -> None:
    import exports

    data = json.loads((folder / "readings.json").read_text(encoding="utf-8"))
    readings, digits = data["readings"], int(data["settings"]["digits"])
    labels = exports.labels(readings, int(data["settings"]["decimals"]))
    frames = []
    for r, lab in list(zip(readings, labels, strict=True))[::every]:
        if lab["lo"] is not None and r.get("region") == "as read" and r.get("box"):
            frames.append((Image.open(folder / r["file"]).convert("RGB"), r["box"], lab))
    print(f"{folder.name}: {len(frames)} frames")
    for case, (left, right, top, bottom) in GEOMETRY.items():
        cells, context = [], []
        for im, (x0, y0, x1, y1), _lab in frames:
            cw, h = (x1 - x0) / digits, y1 - y0
            box = (x0 + left * cw, max(0, y0 + top * h), x1 + right * cw, min(im.height, y1 + bottom * h))
            region = im.crop((round(max(0, box[0])), round(box[1]), round(min(im.width, box[2])), round(box[3])))
            cells.append(one_cell(region, digits))
            context.append(ctx_cells(im, box, digits))
        X, C = np.stack(cells), np.stack(context)
        P = model.probs(X.reshape(-1, *X.shape[2:]), C.reshape(-1, *C.shape[2:])).reshape(len(X), digits, BINS)
        ok = high = 0
        for (_im, _box, lab), p in zip(frames, P, strict=True):
            v = read(p)[0]
            ok += lab["lo"] - 1 <= v <= lab["hi"] + 1
            high += v > lab["hi"] + 1
        print(f"  {case:28s} ok {ok / len(frames):6.1%}  too high {high}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["build", "eval", "geometry", "compare"])
    ap.add_argument("model", nargs="?")
    ap.add_argument("folder", nargs="*")
    ap.add_argument("--sets")
    ap.add_argument("--tag")
    ap.add_argument("--sweep", action="store_true")
    a = ap.parse_args()
    if a.what == "build":
        build()
    elif a.what == "geometry":
        geometry(Model(a.model), Path(a.folder[0]))
    elif a.what == "compare":
        compare(a.model, a.folder, a.sets.split(",") if a.sets else None)
    else:
        names = a.sets.split(",") if a.sets else sorted(p.stem for p in BENCH.glob("*.npz") if not p.stem.endswith("_ctx"))
        res, frames = evaluate(Model(a.model), names, a.sweep)
        if a.tag:
            (WORK / "results").mkdir(exist_ok=True)
            (WORK / "results" / f"{a.tag}.json").write_text(json.dumps(res, indent=1))
            np.savez_compressed(WORK / "results" / f"{a.tag}.npz", **{f"{n}|{k}": v for n, f in frames.items() for k, v in f.items()})
