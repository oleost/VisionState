"""Decoding a counter over time, not frame by frame (`docs/WHEEL_READER_PLAN.md` §4.2, step 2).

python temporal.py <model> [--sets ole_a,ole_b,ped_a,ped_b] [--cost 0.5,1,2] [--reset 8]

A counter never goes down and mostly moves slowly, so every frame's value is chosen together with
the frames before it: a forward Viterbi pass, frame by frame as the app would run it. Per frame the
candidates are the frame's own best value and its neighbours (+-3 steps) and the values carried
from the frame before (a meter at rest). A candidate's score is how well the frame's wheels fit it
(the app's slack and common shift); a step from u to v costs ``cost`` x (v - u) / (expected + 1),
expected being the steps the rate learnt so far (``learnt_rate``) gives over the time between the frames, and a
step down is only possible through a reset (``reset``, in log probability), so one wrong frame
cannot lock the path in. The chosen value then goes through the same checks as the single-frame
replay (bench.simulate: went down, change limit, threshold 0.70). Its confidence is the smaller
of the weakest wheel's mass at it and the share of the probability among the candidates within
one step of it (a wheel half way is not doubt): a frame
that fits two values about as well is not sure, whichever the path prefers.
"""

import argparse
import math

import numpy as np

import bench
from common import BINS, positions

NEAR = 3  # steps around the frame's own best value
KEEP = 12  # candidates carried to the next frame


def wheel_logp(probs, slack=1):
    near = sum(np.roll(probs, s, axis=1) for s in range(-slack, slack + 1))
    return np.log(np.maximum(near, 1e-12))


def fit(logp, probs, value, digits, max_shift=2, penalty=0.3):
    """Log probability of a value under one frame and the weakest wheel's mass at it. The value is
    a step of the last wheel: that wheel may stand anywhere in [value - 0.5, value + 0.5) (the
    wheels to its left follow it), and the region may sit a little high or low (common shift)."""
    best, best_bins, best_shift = -1e18, None, 0
    for f in np.arange(-0.5, 0.5, 0.1):
        bins = [round(p * BINS / 10) % BINS for p in positions(float(value) + f, digits)]
        for shift in range(-max_shift, max_shift + 1):
            s = sum(logp[k, (b + shift) % BINS] for k, b in enumerate(bins)) - penalty * abs(shift)
            if s > best:
                best, best_bins, best_shift = s, bins, shift
    mass = min(sum(probs[k, (b + best_shift + d) % BINS] for d in range(-4, 5)) for k, b in enumerate(best_bins))
    return best, float(mass)


RATE_WINDOW_S = 86_400  # the rate is learnt from the values chosen over the last day


def learnt_rate(history, now):
    """Steps per second of the values chosen before ``now`` over the last RATE_WINDOW_S: only
    what the app would know then (never the labels, never later frames)."""
    recent = [(t, v) for t, v in history if now - t <= RATE_WINDOW_S]
    if len(recent) < 2 or recent[-1][0] == recent[0][0]:
        return 0.0
    return max(0.0, (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0]))


def decode(rows, P, digits, cost, reset):
    """Each frame's value as chosen by the forward pass, with the weakest-wheel confidence at it."""
    prev, prev_t, out, history = {}, None, [], []
    for r, probs in zip(rows, P, strict=True):
        logp = wheel_logp(probs)
        own = r["v"]
        cand = {(own + d) % 10**digits for d in range(-NEAR, NEAR + 1)} | set(prev)
        scores = {v: fit(logp, probs, v, digits) for v in cand}
        cur = {}
        if not prev:
            cur = {v: s for v, (s, _m) in scores.items()}
        else:
            expected = learnt_rate(history, r["seconds"]) * (r["seconds"] - prev_t)
            floor = max(prev.values()) - reset
            for v, (s, _m) in scores.items():
                best = floor
                for u, su in prev.items():
                    if u <= v:
                        best = max(best, su - cost * (v - u) / (expected + 1))
                cur[v] = s + best
        top = max(cur.values())
        prev = dict(sorted(((v, s - top) for v, s in cur.items()), key=lambda x: -x[1])[:KEEP])
        prev_t = r["seconds"]
        v = max(cur, key=cur.get)
        history.append((r["seconds"], v))
        # how sure: the weakest wheel at the value, and the value's share among the candidates
        total = sum(math.exp(s - cur[v]) for s in cur.values())
        share = sum(math.exp(s - cur[v]) for u, s in cur.items() if abs(u - v) <= 1) / total
        out.append((v, min(scores[v][1], share)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--sets", default="ole_a,ole_b,ped_a,ped_b")
    ap.add_argument("--cost", default="0.5,1,2,4")
    ap.add_argument("--reset", type=float, default=8.0)
    a = ap.parse_args()
    model = bench.Model(a.model)
    for name in a.sets.split(","):
        d, c = np.load(bench.BENCH / f"{name}.npz"), np.load(bench.BENCH / f"{name}_ctx.npz")
        n, digits = d["X"].shape[:2]
        P = model.probs(d["X"].reshape(-1, *d["X"].shape[2:]), c["X"].reshape(-1, *c["X"].shape[2:])).reshape(n, digits, BINS)
        order = np.argsort(d["seconds"], kind="stable")
        rows, probs = [], []
        for i in order:
            v, mean, wmin, _score = bench.read(P[i])
            rows.append({"v": v, "conf": (mean, wmin), "lo": int(d["lo"][i]), "hi": int(d["hi"][i]), "seconds": int(d["seconds"][i])})
            probs.append(P[i])
        ok = lambda rs: sum(r["lo"] - 1 <= r["v"] <= r["hi"] + 1 for r in rs)  # noqa: E731
        high = lambda rs: sum(r["v"] > r["hi"] + 1 for r in rs)  # noqa: E731
        p, w, _h, s = bench.simulate(rows, 1, 0.7)
        print(f"{name:6s} frame by frame   ok {ok(rows):4d}/{n} high {high(rows):3d} | replay: {p} published, {w} wrong, {s:.1f} h too high")
        for cost in (float(x) for x in a.cost.split(",")):
            chosen = decode(rows, probs, digits, cost, a.reset)
            tr = [{**r, "v": v, "conf": (m, m)} for r, (v, m) in zip(rows, chosen, strict=True)]
            p, w, _h, s = bench.simulate(tr, 1, 0.7)
            print(f"{'':6s} over time {cost:4g}    ok {ok(tr):4d}/{n} high {high(tr):3d} | replay: {p} published, {w} wrong, {s:.1f} h too high")


if __name__ == "__main__":
    main()
