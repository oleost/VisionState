"""Every model runs on the CPU only — deliberately (docs/SCOPE.md); ONNX Runtime also ships an
Azure provider, which must never be offered or used."""

from visionstate import backbones, detectors, readers

from .conftest import MODEL_DIR, requires_detector, requires_reader


@requires_detector
def test_backbone_and_detector_use_only_the_cpu():
    embedder = backbones.Embedder(
        backbones.BACKBONES[backbones.DEFAULT_BACKBONE],
        MODEL_DIR / backbones.BACKBONES[backbones.DEFAULT_BACKBONE].filename,
    )
    detector = detectors.Detector(
        detectors.DETECTORS[detectors.DEFAULT_DETECTOR],
        MODEL_DIR / detectors.DETECTORS[detectors.DEFAULT_DETECTOR].filename,
    )
    assert embedder.session.get_providers() == ["CPUExecutionProvider"]
    assert detector.session.get_providers() == ["CPUExecutionProvider"]
    # Threads do not spin while they wait for work (that takes CPU from Home Assistant).
    options = detector.session.get_session_options()
    assert options.get_session_config_entry("session.intra_op.allow_spinning") == "0"
    assert options.get_session_config_entry("session.inter_op.allow_spinning") == "0"


@requires_reader
def test_reader_uses_only_the_cpu():
    spec = readers.READERS[readers.DEFAULT_READER]
    assert readers.Reader(spec, MODEL_DIR / spec.filename).session.get_providers() == ["CPUExecutionProvider"]
