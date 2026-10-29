"""Sounds: the sound model, levels, the sensor's own sounds and clips (sound sensors;
docs/SOUND_PLAN.md).

The model is declared in ``sounds.json``: an ONNX file that takes 16 kHz mono audio (``waveform``:
N × samples, -1…1) and gives the score of every AudioSet class (``logits``: N × 527, already
probabilities) and an embedding (``embedding``: N × 576) own sounds are taught on. Each of the
model's *sounds* (a siren, a smoke alarm …) is one or more of its classes with a threshold. It is
never bundled: downloaded when the first sound sensor needs it. Entries are never changed or
removed once released (their texts, footprint and thresholds may be).
"""

from __future__ import annotations

import io
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import classifier
from .backbones import Footprint, cpu_session
from .settings import SOUND

REGISTRY_FILE = Path(__file__).with_name("sounds.json")
SILENCE_DB = -100.0  # the level of digital silence


@dataclass(frozen=True)
class SoundSpec:
    """One of the model's sounds: the classes it is (the best of their scores) and when it is heard."""

    key: str
    name: str
    classes: tuple[int, ...]
    threshold: float
    device_class: str
    icon: str


@dataclass(frozen=True)
class SoundModelSpec:
    id: str
    name: str
    description: str
    url: str  # "" until the file is published (tools/soundmodel/README.md)
    sha256: str
    size: int
    rate: int
    license: str
    data: str  # what it was trained on
    source: str
    made_by: str
    sounds: dict[str, SoundSpec]
    bundled: bool = False
    footprint: Footprint = field(default_factory=lambda: Footprint())

    @property
    def filename(self) -> str:
        return f"{self.id}.onnx"


def load_registry() -> tuple[str, dict[str, SoundModelSpec]]:
    """The default model's id and every sound model in ``sounds.json``."""
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    models = {
        key: SoundModelSpec(
            id=key,
            name=v["name"],
            description=v.get("description", ""),
            url=v.get("url", ""),
            sha256=v["sha256"],
            size=int(v.get("size", 0)),
            rate=int(v["rate"]),
            license=v.get("license", ""),
            data=v.get("data", ""),
            source=v.get("source", ""),
            made_by=v.get("made_by", ""),
            sounds={
                k: SoundSpec(
                    key=k,
                    name=s["name"],
                    classes=tuple(int(c) for c in s["classes"]),
                    threshold=float(s["threshold"]),
                    device_class=s.get("device_class", "sound"),
                    icon=s.get("icon", "mdi:ear-hearing"),
                )
                for k, s in v["sounds"].items()
            },
            bundled=bool(v.get("bundled", False)),
            footprint=Footprint.of(v),
        )
        for key, v in raw["models"].items()
    }
    return raw["default"], models


DEFAULT_MODEL, MODELS = load_registry()
SOUNDS = MODELS[DEFAULT_MODEL].sounds  # the sounds a sensor can listen for


class SoundModel:
    """The sound model: class scores and embeddings of windows of audio."""

    def __init__(self, spec: SoundModelSpec, model_path: Path):
        self.spec = spec
        self.session = cpu_session(model_path)
        self._lock = threading.Lock()

    @property
    def id(self) -> str:
        return self.spec.id

    def run(self, windows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(scores N × classes, embeddings N × 576) of N windows of equal length (16 kHz, -1…1)."""
        batch = np.ascontiguousarray(windows, dtype=np.float32).reshape(len(windows), -1)
        with self._lock:
            scores, embedding = self.session.run(["logits", "embedding"], {"waveform": batch})
        return np.asarray(scores), np.asarray(embedding)

    def sound_scores(self, scores: np.ndarray, keys: list[str]) -> dict[str, float]:
        """The score of each of the model's sounds in ``keys``: the best of its classes."""
        return {
            key: float(scores[list(self.spec.sounds[key].classes)].max()) for key in keys if key in self.spec.sounds
        }

    def silence_speech(self, samples: np.ndarray, threshold: float) -> tuple[np.ndarray, int]:
        """``samples`` with every second in which speech scored ``threshold`` or more silenced
        (SOUND["speech_key"]), and how many seconds were. Each second is judged with half a second
        on either side, so a word at its edge counts."""
        rate = self.spec.rate
        speech = self.spec.sounds.get(SOUND["speech_key"])
        if speech is None or not len(samples):
            return samples, 0
        out = samples.copy()
        seconds = int(np.ceil(len(samples) / rate))
        padded = np.pad(samples, (rate // 2, rate // 2 + seconds * rate - len(samples)))
        windows = np.stack([padded[i * rate : i * rate + 2 * rate] for i in range(seconds)])
        scores, _ = self.run(windows)
        loud = scores[:, list(speech.classes)].max(axis=1) >= threshold
        for i in np.flatnonzero(loud):
            out[i * rate : (i + 1) * rate] = 0.0
        return out, int(loud.sum())


def level_db(samples: np.ndarray) -> float:
    """The level of ``samples`` (-1…1) in dB relative to full scale (RMS); SILENCE_DB for silence."""
    if not len(samples):
        return SILENCE_DB
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return max(SILENCE_DB, 20.0 * np.log10(rms)) if rms > 0 else SILENCE_DB


def background_db(levels: list[float]) -> float | None:
    """The background level: SOUND["background_percentile"] of the recent levels (None: none yet)."""
    if not levels:
        return None
    return float(np.percentile(levels, SOUND["background_percentile"]))


def line_db(settings: dict, background: float | None) -> float | None:
    """The level a window must reach to be analysed (None: not known yet — "auto" without a
    background). Never below SOUND["floor_db"]."""
    if settings["level"] == "fixed":
        line = float(settings["fixed_db"])
    elif background is None:
        return None
    else:
        line = background + float(settings["above_db"])
    return max(line, SOUND["floor_db"])


def encode_clip(samples: np.ndarray, rate: int) -> bytes:
    """A clip as AAC in an MP4 (.m4a) file: plays in every browser, Safari on iPhones included."""
    import av

    buf = io.BytesIO()
    with av.open(
        buf, mode="w", format="mp4", options={"movflags": "+frag_keyframe+empty_moov+default_base_moof"}
    ) as out:
        stream = out.add_stream("aac", rate=rate, layout="mono")
        stream.bit_rate = SOUND["clip_bitrate"]
        frame = av.AudioFrame.from_ndarray(
            np.clip(samples, -1.0, 1.0).astype(np.float32)[None, :], format="flt", layout="mono"
        )
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def decode_audio(data: bytes, rate: int) -> np.ndarray:
    """The sound of a clip or an uploaded file (any format FFmpeg reads) as ``rate`` Hz mono, -1…1."""
    import av

    chunks: list[np.ndarray] = []
    with av.open(io.BytesIO(data), mode="r") as container:
        if not container.streams.audio:
            raise ValueError("The file has no sound")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)


def loudest_window(samples: np.ndarray, rate: int, window_s: float) -> np.ndarray:
    """The ``window_s`` seconds of ``samples`` with the most energy (all of it when shorter, padded)."""
    size = int(window_s * rate)
    if len(samples) <= size:
        return np.pad(samples, (0, size - len(samples))).astype(np.float32)
    step = max(1, rate // 10)
    energy = np.convolve(np.square(samples), np.ones(step), "valid")[::step]
    per_window = np.convolve(energy, np.ones(max(1, size // step)), "valid")
    start = min(int(np.argmax(per_window)) * step, len(samples) - size)
    return samples[start : start + size].astype(np.float32)


# --- Own sounds -----------------------------------------------------------------------------------

OTHER = "other"  # the head's class for everything that is none of the own sounds


@dataclass
class SoundHead:
    """A sensor's own sounds: logistic regression on the model's embeddings, standardised over the
    clips it was trained on (each dimension, then the length), with OTHER as one of its classes."""

    model: str
    keys: list[str]
    mean: np.ndarray
    std: np.ndarray
    coef: np.ndarray
    intercept: np.ndarray

    def _prepare(self, vectors: np.ndarray) -> np.ndarray:
        x = (np.asarray(vectors, dtype=np.float64) - self.mean) / self.std
        return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-8)

    def predict(self, vectors: np.ndarray) -> list[dict[str, float]]:
        """Per vector: the probability of each own sound (OTHER left out)."""
        probs = classifier.Head(self.model, self.keys, self.coef, self.intercept).probabilities(self._prepare(vectors))
        return [{k: float(p) for k, p in zip(self.keys, row, strict=True) if k != OTHER} for row in probs]


def train_head(model: str, vectors: np.ndarray, labels: list[str]) -> SoundHead | None:
    """A head for the own sounds in ``labels`` (OTHER among them for what they are not); None
    without at least one own sound and something else to tell it from."""
    if len(set(labels)) < 2 or not any(label != OTHER for label in labels):
        return None
    x = np.asarray(vectors, dtype=np.float64)
    mean, std = x.mean(axis=0), x.std(axis=0) + 1e-6
    head = SoundHead(model, [], mean, std, np.zeros(0), np.zeros(0))
    keys, coef, intercept = classifier.fit(head._prepare(x), np.asarray(labels))
    head.keys, head.coef, head.intercept = list(keys), coef, intercept
    return head
