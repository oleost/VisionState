"""Object detector registry and inference (ONNX Runtime).

Available detectors are declared in ``detectors.json``; adding a model is a new entry there.
Entries are never changed or removed once released, so a stored choice keeps working.
Bundled detectors are downloaded together with the backbones (``python -m visionstate.backbones``).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .backbones import cpu_session
from .settings import DETECTION

REGISTRY_FILE = Path(__file__).with_name("detectors.json")


@dataclass(frozen=True)
class Label:
    key: str  # Home Assistant-safe key, e.g. "traffic_light"
    name: str  # display name, e.g. "Traffic light"
    group: str
    # Material Design icon shown in Home Assistant; names checked against @mdi/svg 7.4.47,
    # the icon set Home Assistant ships.
    icon: str = "mdi:eye"


@dataclass(frozen=True)
class DetectorSpec:
    id: str
    name: str
    description: str
    url: str
    sha256: str
    size: int
    input_size: int
    label_set: str
    license: str
    source: str
    bundled: bool

    @property
    def filename(self) -> str:
        return f"{self.id}.onnx"


@dataclass(frozen=True)
class LabelSet:
    labels: tuple[Label, ...]  # in the model's class order
    popular: tuple[str, ...]

    @property
    def by_key(self) -> dict[str, Label]:
        return {label.key: label for label in self.labels}


def load_registry() -> tuple[str, dict[str, DetectorSpec], dict[str, LabelSet]]:
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    label_sets = {
        key: LabelSet(
            labels=tuple(
                Label(item["key"], item["name"], item["group"], item.get("icon", "mdi:eye")) for item in value["labels"]
            ),
            popular=tuple(value.get("popular", [])),
        )
        for key, value in raw["label_sets"].items()
    }
    specs = {
        key: DetectorSpec(
            id=key,
            name=v["name"],
            description=v.get("description", ""),
            url=v["url"],
            sha256=v["sha256"],
            size=int(v.get("size", 0)),
            input_size=int(v["input_size"]),
            label_set=v["label_set"],
            license=v.get("license", ""),
            source=v.get("source", ""),
            bundled=bool(v.get("bundled", False)),
        )
        for key, v in raw["detectors"].items()
    }
    return raw["default"], specs, label_sets


DEFAULT_DETECTOR, DETECTORS, LABEL_SETS = load_registry()
# All detectors share one label set today; object sensors store these keys.
LABELS = LABEL_SETS[DETECTORS[DEFAULT_DETECTOR].label_set]


@dataclass(frozen=True)
class Detection:
    key: str
    score: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2, normalised to the analysed image

    def as_dict(self) -> dict:
        return {"key": self.key, "score": round(self.score, 4), "box": [round(v, 4) for v in self.box]}


def _iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU of box ``a`` against each box in ``b`` (x1, y1, x2, y2)."""
    x1, y1 = np.maximum(a[0], b[:, 0]), np.maximum(a[1], b[:, 1])
    x2, y2 = np.minimum(a[2], b[:, 2]), np.minimum(a[3], b[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a + area_b - inter, 1e-9)


def postprocess(logits: np.ndarray, boxes: np.ndarray, labels: tuple[Label, ...], min_score: float) -> list[Detection]:
    """Turn DETR-style outputs (queries × classes logits, cx/cy/w/h boxes) into detections.

    Each query/class pair above ``min_score`` is a candidate; overlapping candidates of the same
    class are merged (non-maximum suppression) so one object is counted once.
    """
    scores = 1.0 / (1.0 + np.exp(-logits))
    queries, classes = np.nonzero(scores > min_score)
    if not len(queries):
        return []
    cx, cy, w, h = (boxes[queries, i] for i in range(4))
    xyxy = np.clip(np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1), 0.0, 1.0)
    cand_scores = scores[queries, classes]
    result: list[Detection] = []
    for cls in np.unique(classes):
        idx = np.nonzero(classes == cls)[0]
        idx = idx[np.argsort(-cand_scores[idx])]
        while len(idx):
            best, idx = idx[0], idx[1:]
            result.append(Detection(labels[cls].key, float(cand_scores[best]), tuple(float(v) for v in xyxy[best])))
            if len(idx):
                idx = idx[_iou(xyxy[best], xyxy[idx]) <= DETECTION["nms_iou"]]
    result.sort(key=lambda d: -d.score)
    return result[: DETECTION["max_detections"]]


class Detector:
    """Finds objects in an image with one ONNX detector (D-FINE / RT-DETR style outputs)."""

    def __init__(self, spec: DetectorSpec, model_path: Path):
        self.spec = spec
        self.labels = LABEL_SETS[spec.label_set].labels
        self.session = cpu_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self._lock = threading.Lock()

    def preprocess(self, image: Image.Image) -> np.ndarray:
        # The model was trained on images stretched to a square (no letterbox), scaled to 0-1.
        size = self.spec.input_size
        arr = np.asarray(image.convert("RGB").resize((size, size), Image.Resampling.BILINEAR), dtype=np.float32)
        return (arr / 255.0).transpose(2, 0, 1)[None]

    def detect(self, image: Image.Image, min_score: float) -> list[Detection]:
        batch = self.preprocess(image)
        with self._lock:
            logits, boxes = self.session.run(None, {self.input_name: batch})
        return postprocess(logits[0], boxes[0], self.labels, min_score)
