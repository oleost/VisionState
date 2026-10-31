import os
from pathlib import Path

import pytest

from visionstate import backbones, detectors, faces, readers, sounds

MODEL_DIR = Path(os.environ.get("VISIONSTATE_TEST_MODELS", Path(__file__).parent.parent / "models"))
ASSETS = Path(__file__).parent / "assets"  # real photos (CC0), see assets/README.md
_default = backbones.BACKBONES[backbones.DEFAULT_BACKBONE]
_detector = detectors.DETECTORS[detectors.DEFAULT_DETECTOR]

requires_model = pytest.mark.skipif(
    not (MODEL_DIR / _default.filename).exists(),
    reason=f"Backbone not downloaded; run `python -m visionstate.backbones {MODEL_DIR}`",
)
_reader = readers.READERS[readers.DEFAULT_READER]
_wheels = readers.WHEEL_READERS[readers.DEFAULT_WHEEL_READER]
requires_reader = pytest.mark.skipif(
    not all((MODEL_DIR / spec.filename).exists() for spec in (_default, _reader, _wheels)),
    reason=f"Models not downloaded; run `python -m visionstate.backbones {MODEL_DIR}`",
)
requires_detector = pytest.mark.skipif(
    not (MODEL_DIR / _default.filename).exists() or not (MODEL_DIR / _detector.filename).exists(),
    reason=f"Models not downloaded; run `python -m visionstate.backbones {MODEL_DIR}`",
)


@pytest.fixture
def model_dir() -> Path:
    return MODEL_DIR


@pytest.fixture(autouse=True)
def _close_databases(monkeypatch):
    """Close every database a test opened (a test that only prepares a file never closes it)."""
    from visionstate.db import Database

    opened = []
    original = Database.__init__

    def init(self, path):
        original(self, path)
        opened.append(self)

    monkeypatch.setattr(Database, "__init__", init)
    yield
    for db in opened:
        db.engine.dispose()


_sface = faces.RECOGNISERS[faces.DEFAULT_RECOGNISER]
# Face models are never bundled: on disk after `python -m visionstate.backbones models --all` (as in CI).
requires_faces = pytest.mark.skipif(
    not all((MODEL_DIR / spec.filename).exists() for spec in (_default, _detector, faces.DETECTOR, _sface)),
    reason=f"Face models not downloaded; run `python -m visionstate.backbones {MODEL_DIR} --all`",
)


_sound = sounds.MODELS[sounds.DEFAULT_MODEL]
# The sound model is never bundled: on disk after `python -m visionstate.backbones models --all`.
requires_sound = pytest.mark.skipif(
    not (MODEL_DIR / _sound.filename).exists(),
    reason=f"Sound model not in {MODEL_DIR}; see tools/soundmodel/README.md",
)
