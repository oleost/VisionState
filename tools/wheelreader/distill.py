"""Teacher targets for distillation (`docs/WHEEL_READER_PLAN.md` §4.2, step 4).

python distill.py <tag> <model> <set> [<set> ...]

<model> is an ensemble as bench.Model takes it ("a.pt+b.pt+..."). For every training set, writes
data/<set>_t_<tag>.npz {"T": N x BINS float16}: the ensemble's probabilities, the mean log
probability of all its parts that can read the set (one-cell parts on a set without a _ctx twin,
such as synth). A _ctx set uses its one-cell twin's targets, so name the one-cell sets only.
train.py --teacher <tag> then trains towards them.
"""

import argparse

import numpy as np

import bench
from train import HERE, load, predict


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("model")
    ap.add_argument("sets", nargs="+")
    a = ap.parse_args()
    parts = bench.Model(a.model).parts
    for name in a.sets:
        cells = load(name)["X"]
        ctx_path = HERE / "data" / f"{name}_ctx.npz"
        context = np.load(ctx_path)["X"] if ctx_path.exists() else None
        use = [m for m in parts if context is not None or not bench._is_ctx(m)]
        logp = sum(predict(m, context if bench._is_ctx(m) else cells) for m in use) / len(use)
        np.savez(HERE / "data" / f"{name}_t_{a.tag}.npz", T=bench.softmax(logp.astype(np.float64)).astype(np.float16))
        print(name, len(cells), "from", len(use), "parts", flush=True)


if __name__ == "__main__":
    main()
