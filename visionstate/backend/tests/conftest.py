import os
from pathlib import Path

import pytest

from visionstate import backbones

MODEL_DIR = Path(os.environ.get("VISIONSTATE_TEST_MODELS", Path(__file__).parent.parent / "models"))
_default = backbones.BACKBONES[backbones.DEFAULT_BACKBONE]

requires_model = pytest.mark.skipif(
    not (MODEL_DIR / _default.filename).exists(),
    reason=f"Backbone not downloaded; run `python -m visionstate.backbones {MODEL_DIR}`",
)


@pytest.fixture
def model_dir() -> Path:
    return MODEL_DIR
