"""Backbone registry and embedding extraction (ONNX Runtime).

Available backbones are declared in ``backbones.json``; adding a model is a new entry there.
Entries are never changed or removed once released, so a stored choice keeps working.
Run ``python -m visionstate.backbones <dest>`` to download all bundled models (backbones,
detectors, readers) for the Dockerfile and CI. ``download``/``locate`` work for every kind of spec.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from . import imaging

log = logging.getLogger(__name__)

REGISTRY_FILE = Path(__file__).with_name("backbones.json")


class ModelFile(Protocol):
    """What downloading and finding a model file needs: any spec (backbone, detector, reader)."""

    @property
    def id(self) -> str: ...
    @property
    def url(self) -> str: ...
    @property
    def sha256(self) -> str: ...
    @property
    def filename(self) -> str: ...


@dataclass(frozen=True)
class BackboneSpec:
    id: str
    name: str
    description: str
    url: str
    sha256: str
    size: int
    input_size: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    pooling: str
    bundled: bool

    @property
    def filename(self) -> str:
        return f"{self.id}.onnx"


def load_registry() -> tuple[str, dict[str, BackboneSpec]]:
    """The default backbone's id and every backbone in ``backbones.json``."""
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    specs = {
        key: BackboneSpec(
            id=key,
            name=v["name"],
            description=v.get("description", ""),
            url=v["url"],
            sha256=v["sha256"],
            size=int(v.get("size", 0)),
            input_size=int(v["input_size"]),
            mean=tuple(v["mean"]),
            std=tuple(v["std"]),
            pooling=v.get("pooling", "cls_mean"),
            bundled=bool(v.get("bundled", False)),
        )
        for key, v in raw["backbones"].items()
    }
    return raw["default"], specs


DEFAULT_BACKBONE, BACKBONES = load_registry()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(spec: ModelFile, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / spec.filename
    if target.exists() and sha256_file(target) == spec.sha256:
        return target
    tmp = target.with_suffix(".part")
    log.info("Downloading model %s from %s", spec.id, spec.url)
    with urllib.request.urlopen(spec.url, timeout=60) as resp, tmp.open("wb") as out:  # noqa: S310
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    actual = sha256_file(tmp)
    if actual != spec.sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Checksum mismatch for {spec.id}: {actual}")
    tmp.replace(target)
    return target


def locate(spec: ModelFile, search_dirs: list[Path]) -> Path | None:
    for directory in search_dirs:
        candidate = directory / spec.filename
        if candidate.exists():
            return candidate
    return None


def cpu_session(model_path: Path):
    """An ONNX Runtime session on the CPU — the only target, deliberately (docs/SCOPE.md).

    Only the CPU provider is passed, so nothing else ONNX Runtime ships with (such as its
    Azure provider) is ever used.
    """
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])


class Embedder:
    """Turns images into L2-normalised feature vectors with one ONNX backbone."""

    def __init__(self, spec: BackboneSpec, model_path: Path):
        self.spec = spec
        self.session = cpu_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self._mean = np.array(spec.mean, dtype=np.float32).reshape(1, 1, 3)
        self._std = np.array(spec.std, dtype=np.float32).reshape(1, 1, 3)
        self._lock = threading.Lock()

    def preprocess(self, image: Image.Image) -> np.ndarray:
        square = imaging.letterbox(image, self.spec.input_size)
        arr = np.asarray(square, dtype=np.float32) / 255.0
        arr = (arr - self._mean) / self._std
        return arr.transpose(2, 0, 1)

    def embed(self, images: list[Image.Image]) -> np.ndarray:
        if not images:
            return np.zeros((0, 0), dtype=np.float32)
        batch = np.stack([self.preprocess(img) for img in images]).astype(np.float32)
        with self._lock:
            hidden = np.asarray(self.session.run(None, {self.input_name: batch})[0])
        features = self._pool(hidden)
        norms = np.linalg.norm(features, axis=1, keepdims=True)
        return (features / np.maximum(norms, 1e-8)).astype(np.float32)

    def _pool(self, hidden: np.ndarray) -> np.ndarray:
        if hidden.ndim == 2:
            return hidden
        if self.spec.pooling == "cls":
            return hidden[:, 0]
        # "cls_mean": class token concatenated with the mean of the patch tokens.
        return np.concatenate([hidden[:, 0], hidden[:, 1:].mean(axis=1)], axis=1)


if __name__ == "__main__":
    from . import detectors, readers

    logging.basicConfig(level=logging.INFO)
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    # Every bundled model: backbones (states), detectors (objects), readers and wheel readers (readings).
    specs = [*BACKBONES.values(), *detectors.DETECTORS.values(), *readers.READERS.values()]
    for spec in [*specs, *readers.WHEEL_READERS.values()]:
        if spec.bundled:
            print(download(spec, destination))
