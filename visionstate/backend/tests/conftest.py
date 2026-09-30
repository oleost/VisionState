import os
from pathlib import Path

import pytest

from visionstate import backbones, detectors, readers

MODEL_DIR = Path(os.environ.get("VISIONSTATE_TEST_MODELS", Path(__file__).parent.parent / "models"))
ASSETS = Path(__file__).parent / "assets"  # real photos (CC0), see assets/README.md
_default = backbones.BACKBONES[backbones.DEFAULT_BACKBONE]
_detector = detectors.DETECTORS[detectors.DEFAULT_DETECTOR]

requires_model = pytest.mark.skipif(
    not (MODEL_DIR / _default.filename).exists(),
    reason=f"Backbone not downloaded; run `python -m visionstate.backbones {MODEL_DIR}`",
)
_reader = readers.READERS[readers.DEFAULT_READER]
requires_reader = pytest.mark.skipif(
    not (MODEL_DIR / _default.filename).exists() or not (MODEL_DIR / _reader.filename).exists(),
    reason=f"Models not downloaded; run `python -m visionstate.backbones {MODEL_DIR}`",
)
requires_detector = pytest.mark.skipif(
    not (MODEL_DIR / _default.filename).exists() or not (MODEL_DIR / _detector.filename).exists(),
    reason=f"Models not downloaded; run `python -m visionstate.backbones {MODEL_DIR}`",
)


@pytest.fixture
def model_dir() -> Path:
    return MODEL_DIR
