"""Make the sound model VisionState ships: CED-Tiny with an embedding output.

Downloads the ONNX file of CED-Tiny (mispeech/ced-tiny, Apache-2.0) at a pinned revision, checks
its SHA-256, and adds one output to its graph: ``embedding``, the encoder's tokens (the input of
its last layer) pooled three ways — mean, standard deviation and maximum over the tokens, 3 × 192
numbers. Nothing else changes: the weights, the input (``waveform``: N × samples, 16 kHz mono,
float -1…1) and the class scores (``logits``: N × 527, already probabilities: the graph ends in a
sigmoid) are the original's. Own sounds are taught on the embedding (docs/SOUND_PLAN.md §5).

    python make_model.py [out_dir]

Prints the file's SHA-256 and size; with the versions in requirements.txt the file is the same
byte for byte every time. Checks that the class scores are unchanged and the embedding is there.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, helper

SOURCE_REVISION = "ace276d29dd0bb3f3517b0fa8cf300738c409019"
SOURCE_URL = f"https://huggingface.co/mispeech/ced-tiny/resolve/{SOURCE_REVISION}/model.onnx"
SOURCE_SHA256 = "3890f227660be71886ff48633d11a731993e0206db8a486781412c28a0873435"
OUT_NAME = "ced-tiny-embedding.onnx"
# The encoder's tokens after its final norm, reshaped to N × tokens × 192 (found by walking back
# from the output: Sigmoid <- Gemm <- LayerNormalization <- ReduceMean("mean") <- Reshape).
TOKENS = "view_17"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_source(cache: Path) -> bytes:
    if cache.exists() and sha256(cache.read_bytes()) == SOURCE_SHA256:
        return cache.read_bytes()
    with urllib.request.urlopen(SOURCE_URL, timeout=120) as resp:  # noqa: S310 - a pinned https URL
        data = resp.read()
    if sha256(data) != SOURCE_SHA256:
        raise SystemExit(f"The source model's SHA-256 is {sha256(data)}, expected {SOURCE_SHA256}")
    cache.write_bytes(data)
    return data


def add_embedding(model: onnx.ModelProto) -> onnx.ModelProto:
    """The model with ``embedding`` (N × 576): mean, std and max of the tokens over axis 1."""
    graph = model.graph
    if not any(TOKENS in node.output for node in graph.node):
        raise SystemExit(f"No node makes {TOKENS!r}: is this the pinned source model?")
    axes = helper.make_tensor("emb_axes", TensorProto.INT64, [1], [1])
    graph.initializer.append(axes)
    graph.node.extend(
        [
            helper.make_node("ReduceMean", [TOKENS, "emb_axes"], ["emb_mean"], keepdims=0, name="emb_mean"),
            helper.make_node("ReduceMean", [TOKENS, "emb_axes"], ["emb_mean_k"], keepdims=1, name="emb_mean_k"),
            helper.make_node("Sub", [TOKENS, "emb_mean_k"], ["emb_centred"], name="emb_centred"),
            helper.make_node("Mul", ["emb_centred", "emb_centred"], ["emb_sq"], name="emb_sq"),
            helper.make_node("ReduceMean", ["emb_sq", "emb_axes"], ["emb_var"], keepdims=0, name="emb_var"),
            helper.make_node("Sqrt", ["emb_var"], ["emb_std"], name="emb_std"),
            helper.make_node("ReduceMax", [TOKENS, "emb_axes"], ["emb_max"], keepdims=0, name="emb_max"),
            helper.make_node("Concat", ["emb_mean", "emb_std", "emb_max"], ["embedding"], axis=1, name="embedding"),
        ]
    )
    graph.output.append(helper.make_tensor_value_info("embedding", TensorProto.FLOAT, ["batch_size", 576]))
    # Not onnx.checker: the source lists its DequantizeLinear nodes after the nodes that use them,
    # which ONNX Runtime accepts and the checker does not. ``check`` runs the result instead.
    return model


def check(source: bytes, made: bytes) -> None:
    """Same class scores as the source; an embedding that is the pooled tokens."""
    rng = np.random.default_rng(0)
    batch = (rng.standard_normal((2, 32000)) * 0.1).astype(np.float32)
    original = ort.InferenceSession(source, providers=["CPUExecutionProvider"])
    ours = ort.InferenceSession(made, providers=["CPUExecutionProvider"])
    scores, embedding = ours.run(["logits", "embedding"], {"waveform": batch})
    expected = original.run(["logits"], {"waveform": batch})[0]
    if not np.allclose(scores, expected, atol=1e-6):
        raise SystemExit("The class scores differ from the source model's")
    if embedding.shape != (2, 576) or not np.isfinite(embedding).all():
        raise SystemExit(f"Unexpected embedding: {embedding.shape}")
    short = ours.run(["logits", "embedding"], {"waveform": batch[:, :8000]})  # half a second still works
    if short[1].shape != (2, 576):
        raise SystemExit("A short window gives no embedding")


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    source = fetch_source(out_dir / "ced-tiny-source.onnx")
    model = add_embedding(onnx.load_from_string(source))
    made = model.SerializeToString()
    check(source, made)
    target = out_dir / OUT_NAME
    target.write_bytes(made)
    print(f"{target}\nsha256 {sha256(made)}\nsize {len(made)}")


if __name__ == "__main__":
    main()
