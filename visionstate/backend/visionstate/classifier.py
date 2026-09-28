"""Per-sensor classifier head trained on backbone embeddings."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from .settings import CLASSIFIER, QUALITY


@dataclass
class Head:
    backbone: str
    keys: list[str]  # class order of the model
    model: LogisticRegression
    version: int = 0

    def predict(self, vectors: np.ndarray, all_keys: list[str]) -> list[dict[str, float]]:
        """Probabilities per state key; states without training data get 0."""
        probs = self.model.predict_proba(vectors)
        out = []
        for row in probs:
            result = {key: 0.0 for key in all_keys}
            for key, p in zip(self.keys, row, strict=False):
                if key in result:
                    result[key] = float(p)
            out.append(result)
        return out


@dataclass
class TrainResult:
    head: Head | None
    n_samples: int
    accuracy: float | None = None
    confusion: dict | None = None
    seconds: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)


def make_model() -> LogisticRegression:
    return LogisticRegression(max_iter=CLASSIFIER["max_iter"], C=CLASSIFIER["C"], class_weight="balanced")


def train(vectors: np.ndarray, labels: list[str], backbone: str, version: int, state_keys: list[str]) -> TrainResult:
    started = time.perf_counter()
    counts = {k: labels.count(k) for k in state_keys}
    classes = sorted({k for k in labels})
    if len(classes) < 2:
        return TrainResult(head=None, n_samples=len(labels), counts=counts)

    y = np.array(labels)
    model = make_model().fit(vectors, y)
    head = Head(backbone=backbone, keys=[str(c) for c in model.classes_], model=model, version=version)

    accuracy = confusion = None
    min_count = min(counts[c] for c in classes)
    folds = min(QUALITY["cv_folds"], min_count)
    if folds >= 2:
        cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=0)
        predicted = cross_val_predict(make_model(), vectors, y, cv=cv)
        accuracy = float((predicted == y).mean())
        confusion = {
            "keys": state_keys,
            "matrix": [
                [int(((y == actual) & (predicted == guess)).sum()) for guess in state_keys] for actual in state_keys
            ],
        }
    return TrainResult(
        head=head,
        n_samples=len(labels),
        accuracy=accuracy,
        confusion=confusion,
        seconds=time.perf_counter() - started,
        counts=counts,
    )


def save(head: Head, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    joblib.dump(head, tmp)
    tmp.replace(path)


def load(path: Path) -> Head | None:
    if not path.exists():
        return None
    try:
        return joblib.load(path)
    except Exception:  # noqa: BLE001 - a corrupt head is simply retrained
        return None
