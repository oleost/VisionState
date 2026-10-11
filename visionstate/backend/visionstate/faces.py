"""Faces in person boxes: find them, check that they are usable, align and embed them, and tell
who they are (object sensors with face recognition on; docs/FACE_RECOGNITION_PLAN.md).

The face models are declared in ``faces.json``: one face detector (YuNet: a face box and five
points — the eyes, the nose tip and the mouth corners) and the recognisers the user can choose
from (a face embedding from a face aligned on those five points). None of them is bundled: they
are downloaded when a sensor first turns face recognition on. Entries are never changed or
removed once released (their texts and footprint may be).

A face is only looked for in the upper part of a person box the object detector found, cut from
the full frame. A face that fails the quality gate (too small, blurry, turned away, unsure) is
*no face*, never someone unknown. A usable face is compared with the faces the user taught for
each person; see ``identify``.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .backbones import Footprint, cpu_session
from .settings import FACE_DEFAULTS, FACE_TUNABLE, FACES

REGISTRY_FILE = Path(__file__).with_name("faces.json")

# Where the five points of a face go in a 112 × 112 aligned face (the ArcFace template, which
# SFace uses too): right eye, left eye (as seen in the picture: left, right), nose tip, mouth corners.
TEMPLATE_112 = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float64,
)


@dataclass(frozen=True)
class FaceDetectorSpec:
    id: str
    name: str
    description: str
    url: str
    sha256: str
    size: int
    input_size: int
    license: str
    data: str
    source: str
    bundled: bool = False

    @property
    def filename(self) -> str:
        return f"{self.id}.onnx"


@dataclass(frozen=True)
class RecogniserSpec:
    id: str
    name: str
    description: str
    url: str
    sha256: str
    size: int
    input_size: int
    mean: float  # the aligned face (RGB, 0-255) is fed as (pixel - mean) / std
    std: float
    # Cosine similarities of this model (they mean nothing for another one): a match needs
    # ``match_similarity`` (and a margin over the next person), a name is published at once from
    # ``sure_similarity`` (else after a second check agrees), between ``ask_similarity`` and a
    # match the sensor is unsure and may ask, and a person who stays keeps their name down to
    # ``keep_similarity``.
    match_similarity: float
    sure_similarity: float
    ask_similarity: float
    keep_similarity: float
    license: str
    data: str  # what it was trained on, as its makers say
    source: str
    bundled: bool = False
    footprint: Footprint = field(default_factory=lambda: Footprint())

    @property
    def filename(self) -> str:
        return f"{self.id}.onnx"

    @property
    def thresholds(self) -> dict[str, float]:
        return {
            "match_similarity": self.match_similarity,
            "sure_similarity": self.sure_similarity,
            "ask_similarity": self.ask_similarity,
            "keep_similarity": self.keep_similarity,
        }


def load_registry() -> tuple[FaceDetectorSpec, str, dict[str, RecogniserSpec]]:
    """The face detector, the default recogniser's id and every recogniser in ``faces.json``."""
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    d = raw["detectors"][raw["detector"]]
    detector = FaceDetectorSpec(
        id=raw["detector"],
        name=d["name"],
        description=d.get("description", ""),
        url=d["url"],
        sha256=d["sha256"],
        size=int(d.get("size", 0)),
        input_size=int(d["input_size"]),
        license=d.get("license", ""),
        data=d.get("data", ""),
        source=d.get("source", ""),
        bundled=bool(d.get("bundled", False)),
    )
    recognisers = {
        key: RecogniserSpec(
            id=key,
            name=v["name"],
            description=v.get("description", ""),
            url=v["url"],
            sha256=v["sha256"],
            size=int(v.get("size", 0)),
            input_size=int(v["input_size"]),
            mean=float(v["mean"]),
            std=float(v["std"]),
            match_similarity=float(v["match_similarity"]),
            sure_similarity=float(v["sure_similarity"]),
            ask_similarity=float(v["ask_similarity"]),
            keep_similarity=float(v["keep_similarity"]),
            license=v.get("license", ""),
            data=v.get("data", ""),
            source=v.get("source", ""),
            bundled=bool(v.get("bundled", False)),
            footprint=Footprint.of(v),
        )
        for key, v in raw["recognisers"].items()
    }
    return detector, raw["default"], recognisers


DETECTOR, DEFAULT_RECOGNISER, RECOGNISERS = load_registry()


def tunable_defaults(model_id: str) -> dict[str, float]:
    """The tunable face limits (settings.FACE_TUNABLE) as they are by default with a face model."""
    return {"min_face_px": FACE_DEFAULTS["min_face_px"], **recogniser_spec(model_id).thresholds}


def merge_limits(model_id: str, global_limits: dict | None, sensor: dict | None) -> dict[str, float]:
    """Effective face limits: the model's defaults < the global values < the sensor's own."""
    merged = tunable_defaults(model_id)
    for overrides in (global_limits, sensor):
        merged.update({k: float(v) for k, v in (overrides or {}).items() if k in FACE_TUNABLE and v is not None})
    return merged


def recogniser_spec(model_id: str) -> RecogniserSpec:
    return RECOGNISERS.get(model_id) or RECOGNISERS[DEFAULT_RECOGNISER]


# --- Finding faces -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Face:
    """A face found in a picture, in that picture's pixels."""

    box: tuple[float, float, float, float]  # x1, y1, x2, y2
    points: tuple[tuple[float, float], ...]  # the five points (see TEMPLATE_112)
    score: float

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def eye_distance(self) -> float:
        (ax, ay), (bx, by) = self.points[0], self.points[1]
        return math.hypot(bx - ax, by - ay)

    def moved(self, dx: float, dy: float, scale: float) -> Face:
        """The same face in another picture, whose pixels are ``x * scale + dx`` (and y alike)."""
        x1, y1, x2, y2 = self.box
        return Face(
            (x1 * scale + dx, y1 * scale + dy, x2 * scale + dx, y2 * scale + dy),
            tuple((x * scale + dx, y * scale + dy) for x, y in self.points),
            self.score,
        )


def head_region(box: Sequence[float], width: int, height: int) -> tuple[int, int, int, int]:
    """Where a face is looked for in a person box (normalised): its upper part, in frame pixels.

    A standing person's box is about three times as tall as wide; the head is in the top of it.
    A box of only head and shoulders (a doorbell) is about as tall as wide and is taken whole.
    """
    x1, y1, x2, y2 = (box[0] * width, box[1] * height, box[2] * width, box[3] * height)
    bw, bh = x2 - x1, y2 - y1
    margin = bw * FACES["region_margin"]
    top = y1 - margin
    bottom = y1 + min(bh, bw * FACES["region_height"]) + margin
    return (
        max(0, int(x1 - margin)),
        max(0, int(top)),
        min(width, int(math.ceil(x2 + margin))),
        min(height, int(math.ceil(bottom))),
    )


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    order = list(np.argsort(-scores))
    keep: list[int] = []
    while order:
        best, order = order[0], order[1:]
        keep.append(int(best))
        if not order:
            break
        rest = boxes[order]
        x1 = np.maximum(boxes[best, 0], rest[:, 0])
        y1 = np.maximum(boxes[best, 1], rest[:, 1])
        x2 = np.minimum(boxes[best, 2], rest[:, 2])
        y2 = np.minimum(boxes[best, 3], rest[:, 3])
        inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        overlap = inter / np.maximum(area(boxes[best]) + area(rest) - inter, 1e-9)
        order = [o for o, v in zip(order, overlap, strict=True) if v <= iou]
    return keep


def decode_yunet(outputs: Sequence[np.ndarray], size: int, min_score: float) -> list[Face]:
    """Faces from YuNet's outputs (cls, obj, bbox, kps for strides 8, 16, 32), in input pixels.

    The same decoding as OpenCV's ``FaceDetectorYN``: the score is sqrt(cls × obj), boxes and
    points are offsets from each cell of the stride's grid.
    """
    strides = (8, 16, 32)
    boxes, scores, points = [], [], []
    for i, stride in enumerate(strides):
        cls, obj = outputs[i][0, :, 0], outputs[i + 3][0, :, 0]
        bbox, kps = outputs[i + 6][0], outputs[i + 9][0]
        score = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
        idx = np.nonzero(score >= min_score)[0]
        if not len(idx):
            continue
        cols = size // stride
        r, c = (idx // cols).astype(np.float32), (idx % cols).astype(np.float32)
        cx, cy = (c + bbox[idx, 0]) * stride, (r + bbox[idx, 1]) * stride
        w, h = np.exp(bbox[idx, 2]) * stride, np.exp(bbox[idx, 3]) * stride
        boxes.append(np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1))
        scores.append(score[idx])
        pts = kps[idx].reshape(-1, 5, 2)
        points.append(np.stack([(pts[:, :, 0] + c[:, None]) * stride, (pts[:, :, 1] + r[:, None]) * stride], axis=2))
    if not boxes:
        return []
    all_boxes, all_scores, all_points = np.concatenate(boxes), np.concatenate(scores), np.concatenate(points)
    keep = _nms(all_boxes, all_scores, FACES["nms_iou"])
    return [
        Face(
            tuple(float(v) for v in all_boxes[k]),  # type: ignore[arg-type]
            tuple((float(x), float(y)) for x, y in all_points[k]),
            float(all_scores[k]),
        )
        for k in keep
    ]


class FaceDetector:
    """YuNet with a fixed square input: up to four head regions are looked at in one run, each
    scaled into a quarter of the input (YuNet finds faces of about 10 to 300 pixels)."""

    def __init__(self, spec: FaceDetectorSpec, model_path: Path):
        self.spec = spec
        self.session = cpu_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self._lock = threading.Lock()

    @property
    def cell(self) -> int:
        return self.spec.input_size // 2

    def detect(self, regions: list[Image.Image]) -> list[Face | None]:
        """The best face in each region (None: none found), in that region's pixels."""
        result: list[Face | None] = []
        for start in range(0, len(regions), 4):
            result.extend(self._detect_four(regions[start : start + 4]))
        return result

    def _detect_four(self, regions: list[Image.Image]) -> list[Face | None]:
        size, cell = self.spec.input_size, self.cell
        canvas = np.zeros((size, size, 3), dtype=np.float32)
        placed = []
        for i, region in enumerate(regions):
            scale = cell / max(region.width, region.height)
            w, h = max(1, round(region.width * scale)), max(1, round(region.height * scale))
            ox, oy = (i % 2) * cell, (i // 2) * cell
            canvas[oy : oy + h, ox : ox + w] = np.asarray(
                region.convert("RGB").resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32
            )
            placed.append((ox, oy, w, h, scale))
        batch = canvas[:, :, ::-1].transpose(2, 0, 1)[None].copy()  # YuNet takes BGR, 0-255
        with self._lock:
            outputs = [np.asarray(o) for o in self.session.run(None, {self.input_name: batch})]
        found = decode_yunet(outputs, size, FACES["detect_score"])
        best: list[Face | None] = []
        for ox, oy, w, h, scale in placed:
            inside = [
                f
                for f in found
                if ox <= (f.box[0] + f.box[2]) / 2 < ox + w and oy <= (f.box[1] + f.box[3]) / 2 < oy + h
            ]
            face = max(inside, key=lambda f: f.score, default=None)
            best.append(face.moved(-ox / scale, -oy / scale, 1 / scale) if face else None)
        return best


# --- Quality, alignment ------------------------------------------------------------------------


def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """The 2 × 3 rotation, scale and shift that best maps points ``src`` onto ``dst`` (Umeyama)."""
    n = len(src)
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    s_c, d_c = src - mu_s, dst - mu_d
    cov = d_c.T @ s_c / n
    u, sv, vt = np.linalg.svd(cov)
    d = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1, 1] = -1
    rotation = u @ d @ vt
    scale = float(np.trace(np.diag(sv) @ d) / ((s_c**2).sum() / n))
    shift = mu_d - scale * rotation @ mu_s
    return np.hstack([scale * rotation, shift[:, None]])


def align(image: Image.Image, face: Face, size: int = 112) -> Image.Image:
    """The face turned and scaled so that its five points sit on the template (``size`` square)."""
    src = np.array(face.points, dtype=np.float64)
    m = similarity_transform(src, TEMPLATE_112 * (size / 112))
    a, t = m[:, :2], m[:, 2]
    inv = np.linalg.inv(a)
    back = -inv @ t
    data = (inv[0, 0], inv[0, 1], back[0], inv[1, 0], inv[1, 1], back[1])
    return image.convert("RGB").transform((size, size), Image.Transform.AFFINE, data, Image.Resampling.BILINEAR)


def sharpness(aligned: Image.Image) -> float:
    """Variance of the Laplacian of an aligned face (grey): low = blurry."""
    g = np.asarray(aligned.convert("L"), dtype=np.float32)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def turn(face: Face) -> float:
    """How far the face is turned sideways: the nose's offset from between the eyes, along the
    line of the eyes, in eye distances (0 = straight at the camera, about 0.35 = 30 degrees, 0.6 = 45)."""
    (ax, ay), (bx, by), (nx, ny) = face.points[0], face.points[1], face.points[2]
    dist = math.hypot(bx - ax, by - ay)
    if dist <= 0:
        return 1.0
    ux, uy = (bx - ax) / dist, (by - ay) / dist
    mx, my = (ax + bx) / 2, (ay + by) / 2
    return abs((nx - mx) * ux + (ny - my) * uy) / dist


def cut_off(face: Face, frame: tuple[int, int]) -> bool:
    """Whether the frame's edge cuts the face: its box reaches the edge or a point is outside.
    The face detector then guesses where the eyes are, and the face looks like nobody."""
    w, h = frame
    x1, y1, x2, y2 = face.box
    inside = all(0 <= x < w and 0 <= y < h for x, y in face.points)
    return not inside or x1 <= 0 or y1 <= 0 or x2 >= w - 1 or y2 >= h - 1


def problem(face: Face, aligned: Image.Image, min_face_px: float, frame: tuple[int, int]) -> str | None:
    """Why a face cannot be used ("cut", "small", "turned", "blurry", "unsure"), or None.

    ``face`` in the pixels of the frame of size ``frame``; ``min_face_px`` is the sensor's
    smallest face (its width)."""
    if cut_off(face, frame):
        return "cut"
    if face.width < min_face_px:
        return "small"
    if face.score < FACES["min_score"]:
        return "unsure"
    if turn(face) > FACES["max_turn"] or face.eye_distance < face.width * FACES["turned_eye_share"]:
        return "turned"
    if sharpness(aligned) < FACES["min_sharpness"]:
        return "blurry"
    return None


def clearly_seen(face: Face, min_face_px: float) -> bool:
    """Whether a usable face is seen well enough that matching nobody makes it a stranger: big
    enough, nearly straight at the camera (a turned face may just not look like its photos) and
    surely a face."""
    return (
        face.width >= min_face_px * FACES["unknown_size"]
        and turn(face) <= FACES["unknown_max_turn"]
        and face.score >= FACES["unknown_min_score"]
    )


# --- Telling who it is ------------------------------------------------------------------------


class Recogniser:
    """Turns aligned faces into L2-normalised embeddings with one ONNX recogniser."""

    def __init__(self, spec: RecogniserSpec, model_path: Path):
        self.spec = spec
        self.session = cpu_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self._lock = threading.Lock()

    def embed(self, faces: list[Image.Image]) -> np.ndarray:
        """One embedding per aligned face; one at a time (some models take a batch of one only)."""
        vectors = []
        for face in faces:
            arr = np.asarray(face.convert("RGB"), dtype=np.float32)
            arr = ((arr - self.spec.mean) / self.spec.std).transpose(2, 0, 1)[None]
            with self._lock:
                out = np.asarray(self.session.run(None, {self.input_name: arr})[0])[0]
            vectors.append(out / max(float(np.linalg.norm(out)), 1e-8))
        return np.stack(vectors).astype(np.float32) if vectors else np.zeros((0, 0), dtype=np.float32)


@dataclass
class FaceModel:
    """The face detector and the chosen recogniser, loaded together."""

    detector: FaceDetector
    recogniser: Recogniser

    @property
    def id(self) -> str:
        return self.recogniser.spec.id


@dataclass
class FaceIndex:
    """The people's taught faces (shared by every sensor), ready to compare with."""

    model: str
    ids: list[int]
    labels: list[str]  # the person (their key) of each face
    vectors: np.ndarray
    generation: int = 0


@dataclass(frozen=True)
class Identity:
    """Who a face is: ``label`` is the closest person (None: nobody is taught), ``similarity`` its
    score, ``margin`` over the next person (1 when there is only one)."""

    label: str | None
    similarity: float
    margin: float
    example_id: int | None


def scores(index: FaceIndex, vector: np.ndarray) -> list[tuple[str, float, int]]:
    """(person, score, closest face's id) for each taught person, best first.

    A person's score is the mean similarity to their ``FACES["top_k"]`` closest faces, not the
    single closest: one face taught under the wrong name does not then match everything like it.
    """
    if not index.ids:
        return []
    sims = index.vectors @ vector
    per: dict[str, list[tuple[float, int]]] = {}
    for sim, label, example in zip(sims, index.labels, index.ids, strict=True):
        per.setdefault(label, []).append((float(sim), example))
    ranked = []
    for label, values in per.items():
        values.sort(key=lambda v: -v[0])
        top = values[: FACES["top_k"]]
        ranked.append((label, sum(v for v, _ in top) / len(top), top[0][1]))
    ranked.sort(key=lambda r: -r[1])
    return ranked


def identify(index: FaceIndex, vector: np.ndarray) -> Identity:
    ranked = scores(index, vector)
    if not ranked:
        return Identity(None, 0.0, 0.0, None)
    label, score, example = ranked[0]
    margin = score - ranked[1][1] if len(ranked) > 1 else 1.0
    return Identity(label, round(score, 3), round(margin, 3), example)


def decide(identity: Identity, limits: dict[str, float], kept: str | None = None) -> str:
    """What a usable face is: "match" (it is ``identity.label``), "ask" (maybe, not sure) or
    "unknown" (nobody taught). ``kept``: the name this person box had in the last check; it keeps
    it while the face stays close enough (``keep_similarity``)."""
    if identity.label is None:
        return "unknown"
    if kept is not None and identity.label == kept and identity.similarity >= limits["keep_similarity"]:
        return "match"
    if identity.similarity >= limits["match_similarity"] and identity.margin >= FACES["margin"]:
        return "match"
    if identity.similarity >= limits["ask_similarity"]:
        return "ask"
    return "unknown"


def face_crop(image: Image.Image, face: Face) -> tuple[Image.Image, Face]:
    """What is stored of a taught face: the face with a margin (not aligned, so another model can
    align it its own way later), and the face in that crop's pixels."""
    x1, y1, x2, y2 = face.box
    m = max(x2 - x1, y2 - y1) * FACES["crop_margin"]
    left, top = max(0, int(x1 - m)), max(0, int(y1 - m))
    right, bottom = min(image.width, int(math.ceil(x2 + m))), min(image.height, int(math.ceil(y2 + m)))
    return image.crop((left, top, right, bottom)).convert("RGB"), face.moved(-left, -top, 1.0)


def face_from_dict(data: dict) -> Face:
    return Face(tuple(data["box"]), tuple(tuple(p) for p in data["points"]), float(data.get("score", 1.0)))  # type: ignore[arg-type]


def face_to_dict(face: Face) -> dict:
    return {
        "box": [round(v, 1) for v in face.box],
        "points": [[round(x, 1), round(y, 1)] for x, y in face.points],
        "score": round(face.score, 3),
    }
