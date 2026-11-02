"""Make the "other sounds" a sound sensor's own sounds are told apart from from the start.

A head for own sounds needs examples of what they are not. A sensor learns its room as it listens
(docs/SOUND_PLAN.md §6), but on the first day it knows nothing else, and five clips of a doorbell
against nothing made a head that took noise for the doorbell. So the app ships the embeddings of
200 everyday sounds (``other_clips.csv``: CC0 recordings from Freesound, as listed in the CC0 part of
FSD50K's evaluation set, none of them an alarm, a dog, glass, a door or a voice) and adds them to
every head as ``other`` (sounds.SoundHead, engine/sound.py).

    python make_other.py <sound model .onnx> [out.npy]

Downloads each clip (the FSD50K mirror on Hugging Face), takes its loudest 2 s of the first 20 s,
and stores the model's embedding of it as float16: N × 576, about 230 KB. The file belongs to the
model (``other`` of its entry in sounds.json); a new model needs its own.
"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
from pathlib import Path

import av
import numpy as np
import onnxruntime as ort

MIRROR = "https://huggingface.co/datasets/Fhrozen/FSD50k/resolve/main/clips/{clip}.wav"
RATE = 16_000


def decode(data: bytes) -> np.ndarray:
    chunks = []
    with av.open(io.BytesIO(data), mode="r") as container:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
    return np.concatenate(chunks)


def loudest(samples: np.ndarray, size: int = 2 * RATE) -> np.ndarray:
    """As the app's sounds.loudest_window."""
    if len(samples) <= size:
        return np.pad(samples, (0, size - len(samples))).astype(np.float32)
    step = RATE // 10
    energy = np.convolve(np.square(samples), np.ones(step), "valid")[::step]
    per_window = np.convolve(energy, np.ones(size // step), "valid")
    start = min(int(np.argmax(per_window)) * step, len(samples) - size)
    return samples[start : start + size].astype(np.float32)


def main() -> None:
    model = ort.InferenceSession(sys.argv[1], providers=["CPUExecutionProvider"])
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "other.npy")
    rows = list(csv.DictReader((Path(__file__).parent / "other_clips.csv").open(encoding="utf-8")))
    vectors = []
    for row in rows:
        with urllib.request.urlopen(MIRROR.format(clip=row["fsd50k_clip"]), timeout=120) as resp:  # noqa: S310
            samples = decode(resp.read())[: 20 * RATE]
        vectors.append(model.run(["embedding"], {"waveform": loudest(samples)[None, :]})[0][0])
    np.save(out, np.stack(vectors).astype(np.float16))
    print(f"{out}: {len(vectors)} sounds")


if __name__ == "__main__":
    main()
