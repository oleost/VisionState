"""wheels-v2 as one ONNX file: a one-cell model and a context model read together.

python export_v2.py runs/<one-cell>.pt runs/<context>.pt <out.onnx>   (paths under VisionStateLocal/wheel)
Inputs: "cells" (N x 1 x 48 x 32, as wheels-v1) and "context" (N x 1 x 48 x 64: the same cells with
half a cell on each side). Outputs: "logits" (N x 100, the mean of the two models' log
probabilities, so a softmax gives their geometric mean) and "embedding" (N x 512: the layer before
the positions of both, for learning per sensor later).
"""

import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
import torch.nn as nn
import torch.nn.functional as F

from paths import WORK
from train import load_model


def logits_and_embedding(net, x):
    f = net.features(x * 2 - 1)
    head = net.head  # Flatten, Dropout, Linear, ReLU, Linear
    e = head[3](head[2](head[0](f)))
    return head[4](e), e


class WheelsV2(nn.Module):
    def __init__(self, cell, ctx):
        super().__init__()
        self.cell, self.ctx = cell, ctx

    def forward(self, cells, context):
        a, ea = logits_and_embedding(self.cell, cells)
        b, eb = logits_and_embedding(self.ctx, context)
        return (F.log_softmax(a, dim=1) + F.log_softmax(b, dim=1)) / 2, torch.cat([ea, eb], dim=1)


if __name__ == "__main__":
    cell, ctx = load_model(WORK / sys.argv[1], device="cpu"), load_model(WORK / sys.argv[2], device="cpu")
    out = Path(sys.argv[3])
    m = WheelsV2(cell, ctx).eval()
    torch.onnx.export(
        m,
        (torch.zeros(1, 1, 48, 32), torch.zeros(1, 1, 48, 64)),
        str(out),
        input_names=["cells", "context"],
        output_names=["logits", "embedding"],
        dynamic_axes={"cells": {0: "n"}, "context": {0: "n"}, "logits": {0: "n"}, "embedding": {0: "n"}},
        opset_version=17,
        dynamo=False,
    )
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    s = ort.InferenceSession(str(out), so, providers=["CPUExecutionProvider"])
    x32, x64 = np.random.rand(8, 1, 48, 32).astype(np.float32), np.random.rand(8, 1, 48, 64).astype(np.float32)
    with torch.no_grad():
        ref = m(torch.from_numpy(x32), torch.from_numpy(x64))[0].numpy()
    got, emb = s.run(None, {"cells": x32, "context": x64})
    print(out.name, out.stat().st_size, "bytes; max diff", float(np.abs(ref - got).max()), "; embedding", emb.shape)
    print("parameters", sum(p.numel() for p in m.parameters()))
    feed = {"cells": x32, "context": x64}
    for _ in range(5):
        s.run(["logits"], feed)
    t = time.perf_counter()
    n = 300
    for _ in range(n):
        s.run(["logits"], feed)
    print(f"8 wheels, 1 thread: {(time.perf_counter() - t) / n * 1000:.2f} ms")
