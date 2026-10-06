"""Teaching object sensors: boxes the user corrected change what later boxes count as.

A taught box is stored as a training image (its crop, see ``crop``) with a label: ``none`` (not
what the detector said), one of the sensor's classes, or one of the user's own labels such as
"Our car" (a kind of car). Every check compares the boxes of the taught classes with the taught
ones and gives a box the label of the closest taught box when it is clearly that one (see
settings.TEACH). Without a clear match the detector's answer stands, so a sensor behaves exactly as
before until it has been taught, and never decides on a weak resemblance.

Boxes the detector missed (drawn by the user) are found again among the boxes it was unsure
about: those are compared too, and count only when they are very close to a taught box.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from . import detectors, imaging
from .settings import NONE_LABEL, TEACH

if TYPE_CHECKING:
    from .backbones import Embedder


@dataclass
class TaughtIndex:
    """The taught boxes of one sensor, ready to compare with."""

    backbone: str
    ids: list[int]
    labels: list[str]
    vectors: np.ndarray  # one L2-normalised embedding per taught box
    keys: set[str]  # detector classes whose boxes are compared
    rescue: bool  # a missed box was taught: unsure boxes are compared too
    # What it was built from (own labels in use, and the runtime's count of changes to the taught
    # boxes), so it is rebuilt when either changed — also when that happened during a build.
    parents: dict[str, str] = field(default_factory=dict)
    generation: int = 0


@dataclass(frozen=True)
class Match:
    label: str
    similarity: float
    example_id: int


# Cache key (Embedding.roi_key) of the embedding of a taught box.
EMBEDDING_KEY = "taught"


def embed(embedder: Embedder, images: list[Image.Image]) -> np.ndarray:
    """Embeddings of box crops, one image at a time.

    The 8-bit backbone quantises a whole batch together, so the same crop gets a slightly
    different vector depending on what else is in the batch (up to ~0.06 in similarity). Taught
    and new boxes are always embedded alone, so the same object always compares the same.
    """
    return np.stack([embedder.embed([image])[0] for image in images])


def crop(image: Image.Image, box: Sequence[float]) -> Image.Image:
    """What is compared of a box: the box plus a small margin of its surroundings."""
    x1, y1, x2, y2 = box
    mx, my = (x2 - x1) * TEACH["crop_margin"], (y2 - y1) * TEACH["crop_margin"]
    return imaging.crop_box(image, (max(0.0, x1 - mx), max(0.0, y1 - my), min(1.0, x2 + mx), min(1.0, y2 + my)))


def resolve(label: str, parents: dict[str, str]) -> tuple[str | None, str | None]:
    """(class, own label) a taught label stands for; (None, None) for "none"."""
    if label == NONE_LABEL:
        return None, None
    if label in parents:
        return parents[label], label
    return label, None


def usable(label: str, parents: dict[str, str]) -> bool:
    """Whether a taught label still means something: an own label that is not in use is ignored."""
    return label == NONE_LABEL or label in parents or label in detectors.LABELS.by_key


def compared_keys(examples: list[tuple[str, str | None]], parents: dict[str, str]) -> set[str]:
    """Classes whose boxes are compared, from (label, detected) of the taught boxes."""
    keys: set[str] = set()
    for label, detected in examples:
        if detected:
            keys.add(detected)
        key, _ = resolve(label, parents)
        if key:
            keys.add(key)
    return keys


def build(
    backbone: str,
    ids: list[int],
    labels: list[str],
    detected: list[str | None],
    vectors: np.ndarray,
    parents: dict[str, str],
    generation: int = 0,
) -> TaughtIndex:
    """The taught boxes of one sensor, ready to compare new boxes with (one entry per box).

    ``detected`` is the class the detector gave a box, None for a box it missed (drawn by the
    user): those switch on ``rescue``, looking at the detector's unsure boxes too.
    ``generation`` counts changes to the taught boxes, so a stale index is noticed.
    """
    return TaughtIndex(
        backbone=backbone,
        ids=list(ids),
        labels=list(labels),
        vectors=vectors,
        keys=compared_keys(list(zip(labels, detected, strict=True)), parents),
        rescue=any(d is None for d in detected),
        parents=dict(parents),
        generation=generation,
    )


def nearest(index: TaughtIndex, vector: np.ndarray, min_similarity: float) -> Match | None:
    """The taught label this box clearly is, or None (too far from all, or between two labels)."""
    if not index.ids:
        return None
    sims = index.vectors @ vector
    best: dict[str, tuple[float, int]] = {}
    for i, sim in enumerate(sims):
        label = index.labels[i]
        if label not in best or sim > best[label][0]:
            best[label] = (float(sim), index.ids[i])
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    label, (similarity, example_id) = ranked[0]
    if similarity < min_similarity:
        return None
    if len(ranked) > 1 and similarity - ranked[1][1][0] < TEACH["margin"]:
        return None
    return Match(label, similarity, example_id)


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection over union of two boxes (x1, y1, x2, y2)."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def plan(found: list[dict], candidates: list[dict], index: TaughtIndex) -> tuple[list[int], list[dict]]:
    """Which counted boxes to compare (indices into ``found``) and which unsure boxes to look at again."""
    checked = sorted((i for i, d in enumerate(found) if d["key"] in index.keys), key=lambda i: -found[i]["score"])
    checked = checked[: TEACH["max_checked"]]
    rescue = []
    if index.rescue:
        for cand in sorted(candidates, key=lambda d: -d["score"]):
            if any(_iou(cand["box"], d["box"]) >= TEACH["rescue_overlap_iou"] for d in found):
                continue  # the same object as a counted box (e.g. a car also seen as a truck)
            rescue.append(cand)
            if len(rescue) >= TEACH["max_rescue"]:
                break
    return checked, rescue


def apply(
    found: list[dict],
    checked: list[int],
    checked_vectors: np.ndarray,
    rescue: list[dict],
    rescue_vectors: np.ndarray,
    index: TaughtIndex,
    classes: list[str],
    parents: dict[str, str],
    previous: list[dict] | None = None,
) -> list[dict]:
    """The boxes of a check after comparing them with the taught ones.

    A box that matches gets ``match`` ({id, label, similarity} of the taught box) and either
    ``filtered`` (not counted), another ``key`` (the detector's class kept in ``was``) or an own
    ``label``. Unsure boxes that match a taught box closely are added with ``rescued``.
    ``previous`` (the boxes of the last check) lets an object that stayed where it was keep its
    answer at a lower similarity (``kept``, see TEACH["keep_similarity"]). A box that may be an
    own label, but not clearly, gets ``ask`` ({id, label, similarity}; see TEACH["ask_similarity"]).
    """
    result = [dict(d) for d in found]
    for i, vector in zip(checked, checked_vectors, strict=True):
        match = nearest(index, vector, TEACH["match_similarity"])
        if match is None:
            match = _kept(index, vector, result[i]["box"], previous or [])
            if match is not None:
                result[i]["kept"] = True
        if match is None:
            if ask := _unsure(index, vector, result[i]["key"], parents):
                result[i]["ask"] = ask
            continue
        det = result[i]
        key, label = resolve(match.label, parents)
        if key == det["key"] and label is None:
            continue  # taught as right: nothing changes
        det["match"] = {"id": match.example_id, "label": match.label, "similarity": round(match.similarity, 3)}
        if key is None or key not in classes:
            det["filtered"] = True
            continue
        if key != det["key"]:
            det["was"], det["key"] = det["key"], key
        if label:
            det["label"] = label
    for cand, vector in zip(rescue, rescue_vectors, strict=True):
        match = nearest(index, vector, TEACH["rescue_similarity"])
        if match is None:
            continue
        key, label = resolve(match.label, parents)
        if key is None or key not in classes:
            continue
        if any(_iou(cand["box"], d["box"]) >= TEACH["rescue_overlap_iou"] for d in result if not d.get("filtered")):
            continue  # already counted (another unsure box of the same object was rescued)
        det = {**cand, "key": key, "rescued": True}
        det["match"] = {"id": match.example_id, "label": match.label, "similarity": round(match.similarity, 3)}
        if key != cand["key"]:
            det["was"] = cand["key"]
        if label:
            det["label"] = label
        result.append(det)
    return result


def _kept(index: TaughtIndex, vector: np.ndarray, box: Sequence[float], previous: list[dict]) -> Match | None:
    """The answer a box got in the last check, when it is the same object at the same place and
    still close to that taught label (just below match_similarity, e.g. in other light)."""
    before = [
        d["match"]["label"]
        for d in previous
        if d.get("match") and not d.get("rescued") and _iou(d["box"], box) >= TEACH["keep_iou"]
    ]
    match = nearest(index, vector, TEACH["keep_similarity"]) if before else None
    return match if match is not None and match.label in before else None


def _unsure(index: TaughtIndex, vector: np.ndarray, key: str, parents: dict[str, str]) -> dict | None:
    """The own label (of the box's class) a box is close to but not clearly, to ask about."""
    near = nearest(index, vector, TEACH["ask_similarity"])
    if near is None:
        return None
    parent, label = resolve(near.label, parents)
    if label is None or parent != key:
        return None
    return {"id": near.example_id, "label": label, "similarity": round(near.similarity, 3)}


def unsure_for(detection: dict, key: str) -> bool:
    """Whether a box may be the own label ``key`` but not clearly (see apply: ``ask``)."""
    return not detection.get("filtered") and detection.get("ask", {}).get("label") == key


def counts_for(detection: dict, key: str) -> bool:
    """Whether a box counts for a class or own label (filtered boxes count for nothing)."""
    return not detection.get("filtered") and (detection["key"] == key or detection.get("label") == key)


def seen_faintly(candidates: list[dict], box: Sequence[float]) -> bool:
    """Whether any of the detector's boxes (however unsure) overlaps a drawn box."""
    return any(_iou(c["box"], box) >= TEACH["seen_iou"] for c in candidates)
