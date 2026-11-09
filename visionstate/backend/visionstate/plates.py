"""Licence plates in vehicle boxes: find them, read them, and tell whose car it is (object sensors
with plate reading on; docs/PLATE_RECOGNITION_PLAN.md).

The plate models are declared in ``plates.json``: each *plate model* the user can choose is a
finder (where the plate is in a vehicle box) and a reader (its text). The open one finds the text
on the vehicle with PP-OCR's text detector and reads it with PP-OCR's recogniser (both Apache-2.0,
trained on PaddleOCR's own data); the others are made for plates but say nothing about the licence
of their weights, so the user accepts them first. None is bundled except what the reading sensors
already bundle. Entries are never changed or removed once released (their texts may be).

A plate is only looked for inside a vehicle box the object detector found, cut from the full
frame. Its text is compared with the household's cars (shared by every sensor) allowing for the
characters a reader confuses; see ``match``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .backbones import Footprint, cpu_session
from .settings import PLATES

REGISTRY_FILE = Path(__file__).with_name("plates.json")
ALPHANUMERIC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class PlateFileSpec:
    """One model file of a plate model (a finder or a reader)."""

    id: str
    kind: str  # finder: "text" (PP-OCR detection) or "yolo"; reader: "ppocr" or "cct"
    url: str
    sha256: str
    size: int
    input_size: tuple[int, int] = (0, 0)  # (width, height); 0 = any (PP-OCR detection)
    chars: dict[str, int] = field(default_factory=dict)  # ppocr: character -> output class (0 = blank)
    alphabet: str = ""  # cct: the output classes in order, with the pad character last
    pad: str = "_"
    bundled: bool = False  # never: the plate models are downloaded when a sensor turns plates on

    @property
    def filename(self) -> str:
        return f"{self.id}.onnx"


@dataclass(frozen=True)
class PlateModelSpec:
    id: str
    name: str
    description: str
    finder: PlateFileSpec
    reader: PlateFileSpec
    license: str
    data: str
    source: str
    open: bool  # its licence and training data allow any use: used without asking
    footprint: Footprint  # accuracy: from the plate benchmark (docs/PLATE_RECOGNITION_PLAN.md)

    @property
    def files(self) -> tuple[PlateFileSpec, PlateFileSpec]:
        return (self.finder, self.reader)

    @property
    def size(self) -> int:
        return self.finder.size + self.reader.size


def _file(v: dict) -> PlateFileSpec:
    return PlateFileSpec(
        id=v["id"],
        kind=v["kind"],
        url=v["url"],
        sha256=v["sha256"],
        size=int(v["size"]),
        input_size=tuple(v.get("input_size", (0, 0))),  # type: ignore[arg-type]
        chars={c: int(i) for c, i in v.get("chars", {}).items()},
        alphabet=v.get("alphabet", ""),
        pad=v.get("pad", "_"),
    )


def load_registry() -> tuple[str, dict[str, PlateModelSpec]]:
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    files = {k: _file({"id": k, **v}) for k, v in raw["files"].items()}
    models = {
        k: PlateModelSpec(
            id=k,
            name=v["name"],
            description=v["description"],
            finder=files[v["finder"]],
            reader=files[v["reader"]],
            license=v["license"],
            data=v["data"],
            source=v["source"],
            open=bool(v["open"]),
            footprint=Footprint.of(v),
        )
        for k, v in raw["models"].items()
    }
    if raw["default"] not in models or not models[raw["default"]].open:
        raise ValueError("plates.json: the default plate model must exist and be open")
    return raw["default"], models


DEFAULT_MODEL, MODELS = load_registry()


def model_spec(model_id: str) -> PlateModelSpec:
    return MODELS.get(model_id) or MODELS[DEFAULT_MODEL]


# --- plate texts ----------------------------------------------------------------------------------

# Characters a reader takes for one another on a plate (each pair costs CONFUSED instead of 1).
CONFUSABLE = [set(p) for p in ("0OQD", "1I", "8B", "5S", "2Z", "6G", "7T", "4A", "UV", "MN")]
CONFUSED = 0.5


def normalise(text: str) -> str:
    """A plate as it is compared: capital letters and digits only (spaces, dashes and the country
    strip's dots dropped)."""
    return "".join(c for c in text.upper() if c in ALPHANUMERIC)


def _cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return CONFUSED if any(a in group and b in group for group in CONFUSABLE) else 1.0


def distance(read: str, plate: str) -> float:
    """How far a read is from a plate: an edit distance where a confusable character costs
    CONFUSED, and a character missing or extra at either end (the plate's edge, a country letter
    read as part of it) costs CONFUSED too."""
    a, b = normalise(read), normalise(plate)
    rows, cols = len(a) + 1, len(b) + 1
    d = np.zeros((rows, cols))
    for i in range(1, rows):
        d[i, 0] = d[i - 1, 0] + CONFUSED
    for j in range(1, cols):
        d[0, j] = d[0, j - 1] + CONFUSED
    for i in range(1, rows):
        for j in range(1, cols):
            # Inside the plate a missing or extra character costs 1; along an edge (first or last
            # row / column) it costs CONFUSED.
            edge_a = CONFUSED if j in (1, cols - 1) and cols > 1 else 1.0
            edge_b = CONFUSED if i in (1, rows - 1) and rows > 1 else 1.0
            d[i, j] = min(d[i - 1, j] + edge_a, d[i, j - 1] + edge_b, d[i - 1, j - 1] + _cost(a[i - 1], b[j - 1]))
    return float(d[-1, -1])


@dataclass(frozen=True)
class Match:
    key: str | None  # the car's key, or None
    distance: float
    plate: str  # the car's plate (normalised) it was compared with, "" without one


def match(read: str, cars: Sequence[dict]) -> Match:
    """The car whose plate is closest to a read: {key, plate} each; a car may have several plates
    separated by commas. Ties go to the first car."""
    best = Match(None, float("inf"), "")
    for car in cars:
        for plate in str(car.get("plate", "")).split(","):
            if not normalise(plate):
                continue
            dist = distance(read, plate)
            if dist < best.distance:
                best = Match(car["key"], dist, normalise(plate))
    return best


def looks_like_plate(text: str) -> bool:
    """A read that can be a plate: PLATES["min_chars"] to PLATES["max_chars"] letters and digits,
    at least one digit (every country's plates have one; words on a car mostly have none)."""
    plain = normalise(text)
    return PLATES["min_chars"] <= len(plain) <= PLATES["max_chars"] and any(c.isdigit() for c in plain)


# --- finding a plate --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Found:
    box: tuple[float, float, float, float]  # in the image given, pixels
    score: float


def _letterbox(image: Image.Image, width: int, height: int) -> tuple[np.ndarray, float, tuple[float, float]]:
    r = min(width / image.width, height / image.height)
    new = (max(1, round(image.width * r)), max(1, round(image.height * r)))
    canvas = Image.new("RGB", (width, height), (114, 114, 114))
    dw, dh = (width - new[0]) / 2, (height - new[1]) / 2
    canvas.paste(image.convert("RGB").resize(new, Image.Resampling.BILINEAR), (round(dw - 0.1), round(dh - 0.1)))
    return np.asarray(canvas, dtype=np.float32) / 255.0, r, (dw, dh)


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, np.ndarray]]:
    """Connected areas of a boolean mask (4-neighbours): (x1, y1, x2, y2, member rows/cols)."""
    height, width = mask.shape
    labels = np.zeros(mask.shape, dtype=np.int32)
    parent = [0]

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for y in range(height):
        row = mask[y]
        if not row.any():
            continue
        xs = np.flatnonzero(row)
        starts = xs[np.r_[True, np.diff(xs) > 1]]
        ends = xs[np.r_[np.diff(xs) > 1, True]] + 1
        for s, e in zip(starts, ends, strict=True):
            above = set(labels[y - 1, s:e][labels[y - 1, s:e] > 0].tolist()) if y else set()
            if above:
                roots = sorted({find(a) for a in above})
                label = roots[0]
                for other in roots[1:]:
                    parent[other] = label
            else:
                label = len(parent)
                parent.append(label)
            labels[y, s:e] = label
    if len(parent) == 1:
        return []
    roots = np.array([find(i) for i in range(len(parent))])
    labels = roots[labels]
    result = []
    for label in np.unique(labels[labels > 0]):
        ys, xs = np.nonzero(labels == label)
        result.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, labels == label))
    return result


class Finder:
    """Where the plates are in a vehicle box (an image cut from the frame)."""

    def __init__(self, spec: PlateFileSpec, model_path: Path):
        self.spec = spec
        self.session = cpu_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self._lock = threading.Lock()

    def find(self, image: Image.Image) -> list[Found]:
        return self._yolo(image) if self.spec.kind == "yolo" else self._text(image)

    def _yolo(self, image: Image.Image) -> list[Found]:
        width, height = self.spec.input_size
        arr, r, (dw, dh) = _letterbox(image, width, height)
        with self._lock:
            out = np.asarray(self.session.run(None, {self.input_name: arr.transpose(2, 0, 1)[None]})[0])
        found = []
        for row in out.reshape(-1, 7):  # batch, x1, y1, x2, y2, class, score
            score = float(row[6])
            if score < PLATES["finder_score"]:
                continue
            x1, y1, x2, y2 = (row[1] - dw) / r, (row[2] - dh) / r, (row[3] - dw) / r, (row[4] - dh) / r
            found.append(Found((max(0.0, x1), max(0.0, y1), min(image.width, x2), min(image.height, y2)), score))
        return sorted(found, key=lambda f: -f.score)

    def _text(self, image: Image.Image) -> list[Found]:
        """PP-OCR's text detector (a DB map): each area of text, widened as PP-OCR widens it."""
        side = PLATES["text_side"]
        r = min(1.0, side / max(image.width, image.height)) if max(image.width, image.height) > side else 1.0
        w = max(32, round(image.width * r / 32) * 32)
        h = max(32, round(image.height * r / 32) * 32)
        rgb = np.asarray(image.convert("RGB").resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
        bgr = rgb[:, :, ::-1]
        mean, std = np.array([0.485, 0.456, 0.406]), np.array([0.229, 0.224, 0.225])
        batch = ((bgr - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)
        with self._lock:
            prob = np.asarray(self.session.run(None, {self.input_name: batch})[0])[0, 0]
        sx, sy = image.width / w, image.height / h
        found = []
        for x1, y1, x2, y2, members in _components(prob > PLATES["text_thresh"]):
            if (x2 - x1) < 4 or (y2 - y1) < 3:
                continue
            score = float(prob[members].mean())
            if score < PLATES["text_box_thresh"]:
                continue
            # Unclip: the map marks the middle of the text; PP-OCR widens it by area × ratio / perimeter.
            area, perimeter = float(members.sum()), 2.0 * ((x2 - x1) + (y2 - y1))
            grow = area * PLATES["text_unclip"] / perimeter
            box = (
                max(0.0, (x1 - grow) * sx),
                max(0.0, (y1 - grow) * sy),
                min(float(image.width), (x2 + grow) * sx),
                min(float(image.height), (y2 + grow) * sy),
            )
            found.append(Found(box, score))
        return sorted(found, key=lambda f: -f.score)


# --- reading a plate --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Read:
    text: str  # capital letters and digits
    score: float  # the reader's confidence, 0-1


class PlateReader:
    def __init__(self, spec: PlateFileSpec, model_path: Path):
        self.spec = spec
        self.session = cpu_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self._lock = threading.Lock()
        if spec.kind == "ppocr":
            allowed = [c for c in ALPHANUMERIC if c in spec.chars]
            self._chars = ["", *allowed]
            self._classes = np.array([0, *(spec.chars[c] for c in allowed)])

    def read(self, image: Image.Image) -> Read:
        return self._cct(image) if self.spec.kind == "cct" else self._ppocr(image)

    def _ppocr(self, image: Image.Image) -> Read:
        height = 48
        width = max(16, min(PLATES["max_read_width"], round(height * image.width / max(1, image.height))))
        # Contrast stretched: a dirty or dim plate reads better (measured on the plate benchmark).
        stretched = ImageOps.autocontrast(image.convert("L"), cutoff=2).convert("RGB")
        rgb = np.asarray(stretched.resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32)
        batch = ((rgb[:, :, ::-1] / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None].astype(np.float32)
        with self._lock:
            probs = np.asarray(self.session.run(None, {self.input_name: batch})[0])[0]
        # Greedy CTC over letters and digits only; a character's confidence is its probability
        # among all of the model's characters (a letter the model took for a sign scores low).
        sub = probs[:, self._classes]
        best = sub.argmax(axis=1)
        text, scores, previous = [], [], -1
        for t, index in enumerate(best):
            if index != previous and index != 0:
                text.append(self._chars[index])
                scores.append(float(sub[t, index]))
            previous = index
        return Read("".join(text), float(np.mean(scores)) if scores else 0.0)

    def _cct(self, image: Image.Image) -> Read:
        width, height = self.spec.input_size
        arr = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR), dtype=np.uint8)
        with self._lock:
            out = self.session.run(None, {self.input_name: arr[None]})
        probs = np.asarray(out[0])[0]  # slots × alphabet
        best = probs.argmax(axis=1)
        text, scores = [], []
        for slot, index in enumerate(best):
            char = self.spec.alphabet[index]
            if char == self.spec.pad:
                continue
            text.append(char)
            scores.append(float(probs[slot, index]))
        return Read("".join(text), float(np.mean(scores)) if scores else 0.0)


@dataclass
class PlateModel:
    """A loaded plate model: its finder and its reader."""

    spec: PlateModelSpec
    finder: Finder
    reader: PlateReader

    @property
    def id(self) -> str:
        return self.spec.id


@dataclass(frozen=True)
class PlateRead:
    box: tuple[float, float, float, float]  # in the vehicle image, pixels
    text: str
    score: float  # the reader's confidence
    found: float  # the finder's confidence


def _pad(box: tuple[float, float, float, float], image: Image.Image, share: float) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    dx, dy = (x2 - x1) * share, (y2 - y1) * share
    return (
        max(0, round(x1 - dx)),
        max(0, round(y1 - dy)),
        min(image.width, round(x2 + dx)),
        min(image.height, round(y2 + dy)),
    )


def read_plates(model: PlateModel, vehicle: Image.Image) -> list[PlateRead]:
    """Every plate found in a vehicle image that reads like a plate, best first.

    The text finder finds every text on the vehicle (a dealer's sticker, a model badge): each is
    read and only reads that look like a plate are kept. A plate on two lines is two areas of
    text, one above the other: those are read line by line and joined (a reader made for plates
    reads both lines at once)."""
    found = model.finder.find(vehicle)[: PLATES["max_candidates"]]
    text_finder = model.finder.spec.kind == "text"
    reads: list[PlateRead] = []
    for f in found:
        x1, y1, x2, y2 = f.box
        if text_finder and (x2 - x1) < (y2 - y1) * PLATES["min_aspect"]:
            continue
        read = _read(model, vehicle, f.box)
        if read is not None and looks_like_plate(read.text):
            reads.append(PlateRead(f.box, read.text, read.score, f.score))
    if text_finder:
        for top, bottom in _stacked([f.box for f in found]):
            first, second = _read(model, vehicle, top), _read(model, vehicle, bottom)
            if first is None or second is None:
                continue
            text = first.text + second.text
            if looks_like_plate(text):
                box = (min(top[0], bottom[0]), top[1], max(top[2], bottom[2]), bottom[3])
                reads.append(PlateRead(box, text, (first.score + second.score) / 2, 0.0))
    return sorted(reads, key=lambda r: -(r.score * (0.5 + 0.5 * min(1.0, r.found or 1.0))))


def _read(model: PlateModel, vehicle: Image.Image, box: tuple[float, float, float, float]) -> Read | None:
    crop = vehicle.crop(_pad(box, vehicle, PLATES["crop_pad"]))
    if crop.width < 8 or crop.height < 4:
        return None
    read = model.reader.read(crop)
    if PLATES["straighten"]:
        # Read straightened too, when it is seen at an angle; the reader's surer read is kept (a
        # plate seen straight on that straightening would spoil keeps its first read).
        level = straighten(crop)
        if level is not crop:
            other = model.reader.read(level)
            if other.score > read.score:
                return other
    return read


# --- straightening a plate seen at an angle ---------------------------------------------------------

_SAMPLE_HEIGHT = 48  # the plate is measured this tall (its angle does not need more)


def _profile_score(edges: np.ndarray, axis: int) -> float:
    """How sharp the lines of an edge map are along one axis: the sum of the squared row (axis 1)
    or column (axis 0) sums. Highest when the plate's lines are level (rows) or its strokes upright
    (columns)."""
    profile = edges.sum(axis=axis)
    return float((profile * profile).sum())


def _turned(edges: Image.Image, angle: float) -> np.ndarray:
    return np.asarray(edges.rotate(angle, Image.Resampling.BILINEAR, expand=True), dtype=np.float32)


def _sheared(edges: np.ndarray, shear: float) -> np.ndarray:
    """Each row moved sideways by shear × its distance from the middle row (whole pixels, which is
    enough to measure by)."""
    height, width = edges.shape
    pad = int(abs(shear) * height / 2) + 1
    out = np.zeros((height, width + 2 * pad), dtype=np.float32)
    for y in range(height):
        dx = pad + round(shear * (y - height / 2))
        out[y, dx : dx + width] = edges[y]
    return out


def _best(score, values: Sequence[float]) -> tuple[float, float, float]:
    """The value with the highest score: (value, its score, the score of 0)."""
    scores = {v: score(v) for v in values}
    best = max(scores, key=lambda v: (scores[v], -abs(v)))
    return best, scores[best], scores.get(0.0, scores[best])


def _steps(limit: float, step: float) -> list[float]:
    n = int(limit / step)
    return [round(i * step, 4) for i in range(-n, n + 1)]


def straighten(plate: Image.Image) -> Image.Image:
    """A plate seen at an angle, turned level and its characters made upright.

    A camera above and to the side of a drive sees a plate turned (its lines slope) and sheared
    (its characters lean). Both are found on an edge map of the plate: the turn that makes the sums
    of its rows most uneven (the plate's top and bottom edges and its line of text level), then
    the shear that does the same for its columns (each character's strokes upright). Each is only
    used when it makes those sums clearly more uneven than as the plate was
    (PLATES["straighten_gain"]), so a plate seen straight on is left as it is."""
    small = plate.convert("L")
    scale = _SAMPLE_HEIGHT / max(1, small.height)
    small = small.resize((max(8, round(small.width * scale)), _SAMPLE_HEIGHT), Image.Resampling.BILINEAR)
    gray = np.asarray(small, dtype=np.float32)
    dx = np.abs(np.diff(gray, axis=1))[:-1, :]
    dy = np.abs(np.diff(gray, axis=0))[:, :-1]
    magnitude = Image.fromarray(np.hypot(dx, dy))
    gain = PLATES["straighten_gain"]

    def turn_score(angle: float) -> float:
        return _profile_score(_turned(magnitude, angle), 1)

    coarse, _, _ = _best(turn_score, _steps(PLATES["straighten_max_turn"], 3.0))
    angle, best, level = _best(turn_score, [0.0, *(coarse + d for d in (-2.0, -1.0, 0.0, 1.0, 2.0))])
    if best < level * gain or abs(angle) < 2.0:
        angle = 0.0
    strokes = Image.fromarray(dx).rotate(angle, Image.Resampling.BILINEAR, expand=True)
    upright = np.asarray(strokes, dtype=np.float32)

    def shear_score(shear: float) -> float:
        return _profile_score(_sheared(upright, shear), 0)

    shear, best, level = _best(shear_score, _steps(PLATES["straighten_max_shear"], 0.05))
    if best < level * gain or abs(shear) < 0.1:
        shear = 0.0
    if not angle and not shear:
        return plate
    rgb = plate.convert("RGB")
    fill = tuple(int(v) for v in np.median(np.asarray(rgb).reshape(-1, 3), axis=0))
    # Turned and sheared about its middle, keeping its size: the cut was the box around the turned
    # plate, so the level plate fits inside it (only the corners are filled).
    out = rgb.rotate(angle, Image.Resampling.BICUBIC, fillcolor=fill) if angle else rgb
    if shear:
        # Each row moves by shear × (y - middle) as it was measured. (An affine transform maps
        # output to input pixels.)
        w, h = out.size
        out = out.transform(
            (w, h),
            Image.Transform.AFFINE,
            (1, -shear, shear * h / 2, 0, 1, 0),
            Image.Resampling.BICUBIC,
            fillcolor=fill,
        )
    return out


Box = tuple[float, float, float, float]


def _stacked(boxes: list[Box]) -> list[tuple[Box, Box]]:
    """Pairs of text boxes that are the two lines of one plate: one right below the other,
    overlapping most of the narrower one's width, about as tall."""
    result = []
    for a in boxes:
        for b in boxes:
            if a is b or b[1] <= a[1]:
                continue
            ha, hb = a[3] - a[1], b[3] - b[1]
            gap = b[1] - a[3]
            overlap = min(a[2], b[2]) - max(a[0], b[0])
            narrow = min(a[2] - a[0], b[2] - b[0])
            if -0.2 * hb < gap < max(ha, hb) * 0.6 and overlap > narrow * 0.6 and 0.5 < ha / max(hb, 1e-6) < 2.0:
                result.append((a, b))
    return result
