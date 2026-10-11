"""Every model in the registries, on real inputs: a broken file or conversion must fail here.

Models that are downloaded on first use are only on disk after
``python -m visionstate.backbones models --all`` (CI does that); otherwise their tests are skipped.
"""

import re

import pytest
from PIL import Image, ImageEnhance

from visionstate import backbones, detectors, readers, teach
from visionstate.settings import merge_teach

from .conftest import ASSETS, MODEL_DIR
from .displays import render


def on_disk(spec) -> object:
    return pytest.param(
        spec,
        id=spec.id,
        marks=pytest.mark.skipif(
            not (MODEL_DIR / spec.filename).exists(), reason=f"{spec.id} not downloaded (backbones models --all)"
        ),
    )


ALL_SPECS = [
    *backbones.BACKBONES.values(),
    *detectors.DETECTORS.values(),
    *readers.READERS.values(),
    *readers.WHEEL_READERS.values(),
]


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.id)
def test_every_model_is_pinned_and_described(spec):
    assert "/resolve/" in spec.url and re.fullmatch(r"[0-9a-f]{64}", spec.sha256)
    assert spec.size > 0 and spec.description
    # The model comparison in Settings shows these for every model.
    assert spec.footprint.ram_mb > 0 and spec.footprint.time_ms > 0 and 1 <= spec.footprint.accuracy <= 5


@pytest.mark.parametrize("registry", [backbones.BACKBONES, detectors.DETECTORS, readers.READERS])
def test_slower_models_are_more_accurate(registry):
    """Within a kind, a model that costs more time must rank at least as accurate (else why offer it)."""
    by_time = sorted(registry.values(), key=lambda s: s.footprint.time_ms)
    ranks = [s.footprint.accuracy for s in by_time]
    assert ranks == sorted(ranks)


def counts(found) -> dict[str, int]:
    result: dict[str, int] = {}
    for det in found:
        result[det.key] = result.get(det.key, 0) + 1
    return result


@pytest.mark.parametrize("spec", [on_disk(s) for s in detectors.DETECTORS.values()])
def test_every_detector_finds_objects_in_real_photos(spec):
    """At the default threshold, on the CC0 photos (tests/assets/README.md)."""
    detector = detectors.Detector(spec, MODEL_DIR / spec.filename)
    driveway = counts(detector.detect(Image.open(ASSETS / "driveway.jpg"), 0.6))
    beach = counts(detector.detect(Image.open(ASSETS / "beach.jpg"), 0.6))
    cat = counts(detector.detect(Image.open(ASSETS / "cat.jpg"), 0.6))
    assert driveway.get("car", 0) >= 3 and driveway.get("person") == 1
    assert beach.get("dog", 0) >= 4 and beach.get("person") == 1
    assert cat == {"cat": 1}


@pytest.mark.parametrize("spec", [on_disk(s) for s in backbones.BACKBONES.values()])
def test_every_backbone_tells_taught_objects_apart(spec):
    """With its default teaching limits: the same dog in other light is recognised, another dog
    and a car are not even asked about."""
    embedder = backbones.Embedder(spec, MODEL_DIR / spec.filename)
    limits = merge_teach(spec.id, None, None)
    beach = Image.open(ASSETS / "beach.jpg").convert("RGB")
    driveway = Image.open(ASSETS / "driveway.jpg").convert("RGB")
    detector_spec = detectors.DETECTORS[detectors.DEFAULT_DETECTOR]
    found = detectors.Detector(detector_spec, MODEL_DIR / detector_spec.filename).detect(beach, 0.6)
    dogs = sorted((d.box for d in found if d.key == "dog"), key=lambda b: b[0])
    car = next(d.box for d in detectors.Detector(detector_spec, MODEL_DIR / detector_spec.filename).detect(driveway, 0.6)
               if d.key == "car")  # fmt: skip

    def vector(image, box):
        return teach.embed(embedder, [teach.crop(image, box)])[0]

    dog = vector(beach, dogs[0])
    darker = vector(ImageEnhance.Brightness(beach).enhance(0.8), dogs[0])
    assert float(dog @ darker) >= limits["match_similarity"]
    assert float(dog @ vector(driveway, car)) < limits["ask_similarity"]
    assert max(float(dog @ vector(beach, other)) for other in dogs[1:]) < limits["match_similarity"]


@pytest.mark.parametrize("spec", [on_disk(s) for s in readers.READERS.values()])
@pytest.mark.parametrize(("text", "style"), [("74590", "lcd"), ("0123456.7", "lcd"), ("2:38", "led"), ("98765", "led")])
def test_every_reader_reads_drawn_displays(spec, text, style):
    reader = readers.Reader(spec, MODEL_DIR / spec.filename)
    result, _ = reader.read_display(render(text, style), "auto")
    assert result.text == text and result.score > 0.8


@pytest.mark.parametrize("spec", [on_disk(s) for s in readers.WHEEL_READERS.values()])
def test_every_wheel_reader_reads_a_real_meter(spec):
    wheels = readers.WheelReader(spec, MODEL_DIR / spec.filename)
    result, _ = wheels.read_counter(Image.open(ASSETS / "counter_red.jpg").convert("RGB"), 7)
    assert result.text == "0632289"
