"""Export a trained model to ONNX and time it on the CPU.  python export.py runs/<m>.pt <width>"""

import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch

from common import CELL_H, CELL_W
from train import WheelNet

pt, width = Path(sys.argv[1]), int(sys.argv[2])
m = WheelNet(width)
m.load_state_dict(torch.load(pt, map_location="cpu"))
m.eval()
out = pt.with_suffix(".onnx")
torch.onnx.export(
    m,
    torch.zeros(1, 1, CELL_H, CELL_W),
    str(out),
    input_names=["cells"],
    output_names=["logits"],
    dynamic_axes={"cells": {0: "n"}, "logits": {0: "n"}},
    opset_version=17,
    dynamo=False,
)
so = ort.SessionOptions()
so.intra_op_num_threads = 1
s = ort.InferenceSession(str(out), so, providers=["CPUExecutionProvider"])
x = np.random.rand(8, 1, CELL_H, CELL_W).astype(np.float32)
ref = m(torch.from_numpy(x)).detach().numpy()
got = s.run(None, {"cells": x})[0]
print(out.name, out.stat().st_size // 1024, "KB", "max diff", float(np.abs(ref - got).max()))
for _ in range(5):
    s.run(None, {"cells": x})
t = time.perf_counter()
n = 200
for _ in range(n):
    s.run(None, {"cells": x})
print(f"8 wheels, 1 thread: {(time.perf_counter() - t) / n * 1000:.2f} ms")
