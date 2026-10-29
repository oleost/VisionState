"""Backbone registry and embedding extraction (ONNX Runtime).

Available backbones are declared in ``backbones.json``; adding a model is a new entry there.
Entries are never changed or removed once released, so a stored choice keeps working.
Run ``python -m visionstate.backbones <dest>`` to download all bundled models (backbones,
detectors, readers) for the Dockerfile and CI; ``--all`` adds the ones downloaded on first use (a
model whose ``url`` is still empty, not published yet, is skipped). ``download``/``locate`` work for every kind of spec.
"""

from __future__ import annotations

import ctypes
import functools
import gc
import hashlib
import json
import logging
import sys
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from . import imaging
from .settings import RUNTIME

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
class Footprint:
    """What a model costs and gives, roughly, for the model comparison in Settings.

    Measured with ONNX Runtime on 4 threads of a desktop CPU (the extra memory once loaded and
    run, the time of one check); ``accuracy`` ranks the models of one kind from 1 to 5. Texts and
    these numbers may be updated in a released entry; its file and inputs never change.
    """

    ram_mb: int = 0
    time_ms: int = 0
    accuracy: int = 0

    @classmethod
    def of(cls, entry: dict) -> Footprint:
        return cls(int(entry.get("ram_mb", 0)), int(entry.get("time_ms", 0)), int(entry.get("accuracy", 0)))

    def as_dict(self) -> dict:
        return {"ram_mb": self.ram_mb, "time_ms": self.time_ms, "accuracy": self.accuracy}


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
    license: str = ""
    source: str = ""
    footprint: Footprint = field(default_factory=lambda: Footprint())

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
            license=v.get("license", ""),
            source=v.get("source", ""),
            footprint=Footprint.of(v),
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


def download(spec: ModelFile, dest_dir: Path, progress: Callable[[int], None] | None = None) -> Path:
    """Fetch a model into ``dest_dir`` (unless it is there already) and check its SHA-256;
    ``progress`` is told the bytes fetched so far."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / spec.filename
    if target.exists() and sha256_file(target) == spec.sha256:
        return target
    tmp = target.with_suffix(".part")
    log.info("Downloading model %s from %s", spec.id, spec.url)
    done = 0
    with urllib.request.urlopen(spec.url, timeout=60) as resp, tmp.open("wb") as out:  # noqa: S310
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done)
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
    Azure provider) is ever used. Only errors are logged: SFace's file lists its weights as graph
    inputs too, and ONNX Runtime warned about every one of them (174 lines) each time it loaded.

    No memory arena: with it, each session keeps the largest working memory any image ever
    needed (D-FINE S alone held about 265 MB after a few detections) for the rest of the run; without
    it that memory is freed after every run, for about 6 % more time per check.

    No spinning: by default ONNX Runtime's threads keep a core busy while they wait for work, which
    on a small machine takes CPU from Home Assistant itself; RUNTIME["inference_threads"] threads
    per model (0: ONNX Runtime's choice, one per physical core).
    """
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.enable_cpu_mem_arena = False
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    options.intra_op_num_threads = RUNTIME["inference_threads"]
    options.log_severity_level = 3
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])


def release_memory() -> None:
    """Give the memory of models that are no longer used back to the system.

    A dropped ONNX Runtime session is freed, but glibc keeps the freed memory for later use, so
    switching models left the app hundreds of MB larger every time (until a restart).
    ``malloc_trim`` returns it; on other C libraries (Windows, macOS) there is nothing to do."""
    gc.collect()
    trim_memory()


def trim_memory() -> None:
    """Give memory freed after a model run back to the system (``malloc_trim``, about 1 ms).

    Without it glibc keeps what the largest image needed: on Home Assistant OS the app stayed about
    190 MB larger with the default models. Only glibc has it; elsewhere this does nothing."""
    if (trim := _malloc_trim()) is not None:
        trim(0)


@functools.cache
def _malloc_trim():  # noqa: ANN202 - a ctypes function, or None
    try:
        return ctypes.CDLL("libc.so.6").malloc_trim
    except OSError, AttributeError:
        return None


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


def all_specs() -> list:
    """Every model of every registry: backbones (states), detectors (objects), readers and wheel
    readers (readings), the face detector and recognisers, the sound models."""
    from . import detectors, faces, readers, sounds

    return [
        *BACKBONES.values(),
        *detectors.DETECTORS.values(),
        *readers.READERS.values(),
        *readers.WHEEL_READERS.values(),
        faces.DETECTOR,
        *faces.RECOGNISERS.values(),
        *sounds.MODELS.values(),
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    args = [a for a in sys.argv[1:] if a != "--all"]
    destination = Path(args[0] if args else "models")
    # Every bundled model; with --all also the ones downloaded on first use (the face models are
    # never bundled), for the tests of every model.
    for spec in all_specs():
        if not spec.url:  # not published yet (tools/soundmodel/README.md): made by hand meanwhile
            print(f"{spec.id}: not published yet, skipped", file=sys.stderr)
        elif spec.bundled or "--all" in sys.argv:
            print(download(spec, destination))
