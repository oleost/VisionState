"""Per-sensor classifier head trained on backbone embeddings: logistic regression in NumPy.

The model of scikit-learn's ``LogisticRegression(C=…, class_weight="balanced")`` with its default
lbfgs solver: an L2 penalty on the weights (not on the intercept), one weight vector for two states
and a softmax over all states for more. The problem is convex, so it has one optimum and any solver
that reaches it gives the same head. scikit-learn (with SciPy) did this until it was replaced here:
loading it cost about 90 MB of memory on Home Assistant OS for a few matrix products.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .settings import CLASSIFIER, QUALITY

# Kept in each head file; a head of another format is retrained (from the stored embeddings).
HEAD_FORMAT = 1
# Heads before HEAD_FORMAT were scikit-learn pickles (<sensor id>.joblib): retrained from the stored
# embeddings. The old file is left (a few kB) for an older version the app may be taken back to;
# deleting the sensor removes it.
LEGACY_SUFFIX = ".joblib"


def head_path(heads_dir: Path, sensor_id: int) -> Path:
    return heads_dir / f"{sensor_id}.npz"


def legacy_path(heads_dir: Path, sensor_id: int) -> Path:
    return heads_dir / f"{sensor_id}{LEGACY_SUFFIX}"


@dataclass
class Head:
    backbone: str
    keys: list[str]  # class order of the model
    coef: np.ndarray  # (1, dim) for two classes, (classes, dim) for more
    intercept: np.ndarray
    version: int = 0
    format: int = HEAD_FORMAT

    def probabilities(self, vectors: np.ndarray) -> np.ndarray:
        """One row per vector, one column per class (in ``keys`` order)."""
        return _probabilities(np.asarray(vectors, dtype=np.float64), self.coef, self.intercept)

    def predict(self, vectors: np.ndarray, all_keys: list[str]) -> list[dict[str, float]]:
        """Probabilities per state key; states without training data get 0."""
        out = []
        for row in self.probabilities(vectors):
            result = {key: 0.0 for key in all_keys}
            for key, p in zip(self.keys, row, strict=False):
                if key in result:
                    result[key] = float(p)
            out.append(result)
        return out


def _probabilities(x: np.ndarray, coef: np.ndarray, intercept: np.ndarray) -> np.ndarray:
    z = x @ coef.T + intercept
    if coef.shape[0] == 1:  # two classes: the second one is the positive one
        p = 0.5 * (1.0 + np.tanh(0.5 * z[:, 0]))  # the logistic function, without overflow
        return np.stack([1.0 - p, p], axis=1)
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _objective(theta: np.ndarray, x: np.ndarray, target: np.ndarray, weight: np.ndarray, rows: int, alpha: float):
    """Weighted mean log loss plus ``alpha / 2 · |coef|²``, and its gradient (scikit-learn's scaling)."""
    dim = x.shape[1]
    coef = theta[: rows * dim].reshape(rows, dim)
    intercept = theta[rows * dim :]
    z = x @ coef.T + intercept
    if rows == 1:
        z = z[:, 0]
        loss = np.logaddexp(0.0, z) - target * z  # target 0/1
        diff = (0.5 * (1.0 + np.tanh(0.5 * z)) - target)[:, None]
    else:
        top = z.max(axis=1, keepdims=True)
        lse = top[:, 0] + np.log(np.exp(z - top).sum(axis=1))
        loss = lse - z[np.arange(len(z)), target]
        diff = np.exp(z - lse[:, None])
        diff[np.arange(len(z)), target] -= 1.0
    value = float(weight @ loss) + 0.5 * alpha * float((coef * coef).sum())
    g = diff * weight[:, None]
    grad_coef = g.T @ x + alpha * coef
    return value, np.concatenate([grad_coef.ravel(), g.sum(axis=0)])


def _lbfgs(fun, theta: np.ndarray, max_iter: int, tol: float, memory: int = 10) -> np.ndarray:
    """Minimises a smooth convex function: L-BFGS with a backtracking (Armijo) line search."""
    value, grad = fun(theta)
    steps: list[tuple[np.ndarray, np.ndarray, float]] = []
    for _ in range(max_iter):
        if np.abs(grad).max() <= tol:
            break
        q = grad.copy()
        alphas = []
        for s, y, rho in reversed(steps):
            a = rho * float(s @ q)
            alphas.append(a)
            q -= a * y
        if steps:
            s, y, _ = steps[-1]
            q *= float(s @ y) / float(y @ y)
        else:
            q /= max(1.0, float(np.linalg.norm(grad)))
        for (s, y, rho), a in zip(steps, reversed(alphas), strict=True):
            q += s * (a - rho * float(y @ q))
        direction = -q
        slope = float(grad @ direction)
        if slope >= 0:  # not a descent direction (rounding): start over from the gradient
            steps.clear()
            direction, slope = -grad, -float(grad @ grad)
        step = 1.0
        while True:
            new_value, new_grad = fun(theta + step * direction)
            if new_value <= value + 1e-4 * step * slope:
                break
            step *= 0.5
            if step < 1e-12:  # no lower value along it: as close to the optimum as rounding allows
                return theta
        s = step * direction
        y = new_grad - grad
        sy = float(s @ y)
        if sy > 1e-12:
            steps.append((s, y, 1.0 / sy))
            if len(steps) > memory:
                steps.pop(0)
        theta, value, grad = theta + s, new_value, new_grad
    return theta


def fit(vectors: np.ndarray, labels: np.ndarray) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Trains on vectors with their labels; returns the classes (sorted), coef and intercept."""
    x = np.asarray(vectors, dtype=np.float64)
    classes, target = np.unique(labels, return_inverse=True)
    n, dim = x.shape
    count = np.bincount(target)
    # class_weight="balanced": every class weighs as much in total; the mean over all samples.
    weight = (n / (len(classes) * count))[target] / n
    rows = 1 if len(classes) == 2 else len(classes)
    alpha = 1.0 / (CLASSIFIER["C"] * n)
    theta = _lbfgs(
        lambda t: _objective(t, x, target, weight, rows, alpha),
        np.zeros(rows * dim + rows),
        CLASSIFIER["max_iter"],
        CLASSIFIER["tol"],
    )
    coef = theta[: rows * dim].reshape(rows, dim)
    return [str(c) for c in classes], coef, theta[rows * dim :].copy()


@dataclass
class TrainResult:
    head: Head | None
    n_samples: int
    accuracy: float | None = None
    confusion: dict | None = None
    seconds: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)
    suspects: list[dict] = field(default_factory=list)  # {index, label, predicted, confidence}


def _folds(target: np.ndarray, folds: int) -> np.ndarray:
    """A fold for each sample, every class spread evenly over the folds (shuffled, always the same)."""
    rng = np.random.default_rng(0)
    fold = np.empty(len(target), dtype=int)
    for c in np.unique(target):
        members = rng.permutation(np.flatnonzero(target == c))
        fold[members] = (np.arange(len(members)) + int(rng.integers(folds))) % folds
    return fold


def train(vectors: np.ndarray, labels: list[str], backbone: str, version: int, state_keys: list[str]) -> TrainResult:
    """Train a head on the labelled samples (one embedding and one state key each).

    Needs two states with samples; with fewer, ``head`` is None (the sensor is untrained).
    With enough samples per state the quality is measured too: accuracy and confusion from
    cross-validation, and "suspects" — samples a model that never saw them disagrees with,
    likely labelled wrong (shown on the Quality tab).
    """
    started = time.perf_counter()
    counts = {k: labels.count(k) for k in state_keys}
    classes = sorted({k for k in labels})
    if len(classes) < 2:
        return TrainResult(head=None, n_samples=len(labels), counts=counts)

    y = np.array(labels)
    keys, coef, intercept = fit(vectors, y)
    head = Head(backbone=backbone, keys=keys, coef=coef, intercept=intercept, version=version)

    accuracy = confusion = None
    suspects: list[dict] = []
    min_count = min(counts[c] for c in classes)
    folds = min(QUALITY["cv_folds"], min_count)
    if folds >= 2:
        # Each sample is predicted by a model that never saw it, so disagreements point at
        # samples that are either labelled wrong or genuinely ambiguous.
        fold = _folds(np.searchsorted(classes, y), folds)
        proba = np.zeros((len(y), len(classes)))
        for k in range(folds):
            held = fold == k
            _, c, b = fit(vectors[~held], y[~held])  # every class is in each training part
            proba[held] = _probabilities(np.asarray(vectors[held], dtype=np.float64), c, b)
        order = np.array(classes)
        predicted = order[proba.argmax(axis=1)]
        accuracy = float((predicted == y).mean())
        suspects = sorted(
            (
                {"index": i, "label": str(y[i]), "predicted": str(predicted[i]), "confidence": float(proba[i].max())}
                for i in range(len(y))
                if predicted[i] != y[i]
            ),
            key=lambda item: -float(item["confidence"]),
        )[: QUALITY["max_suspects"]]
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
        suspects=suspects,
    )


def save(head: Head, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as file:
        np.savez(
            file,
            format=np.array(head.format),
            backbone=np.array(head.backbone),
            version=np.array(head.version),
            keys=np.array(head.keys, dtype=str),
            coef=head.coef,
            intercept=head.intercept,
        )
    tmp.replace(path)


def load(path: Path) -> Head | None:
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            return Head(
                backbone=str(data["backbone"]),
                keys=[str(k) for k in data["keys"]],
                coef=data["coef"],
                intercept=data["intercept"],
                version=int(data["version"]),
                format=int(data["format"]),
            )
    except Exception:  # noqa: BLE001 - a corrupt or unknown head is simply retrained
        return None


def is_current(head: Head, backbone: str | None) -> bool:
    """True when the head has this version's format and was trained for the active backbone."""
    return head.format == HEAD_FORMAT and (backbone is None or head.backbone == backbone)
