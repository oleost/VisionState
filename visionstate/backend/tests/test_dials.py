"""Pointer dials and the last wheel's fraction (issue #59, docs/POINTER_DIAL_PLAN.md)."""

import io
import json
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate import dials, imaging, readers
from visionstate.main import create_app
from visionstate.settings import READING, extra_digits, merge_reading, value_decimals

from .conftest import MODEL_DIR, requires_reader
from .displays import counter_box, render_counter, render_dial, render_meter, turned_meter
from .test_integration import wait_for

BINS = dials.BINS


@pytest.fixture
def settings(tmp_path):
    from visionstate.settings import Settings

    return Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=tmp_path / "none",
        bundled_models_dir=MODEL_DIR,
    )


def peak(position: float, sigma: float = 1.5) -> np.ndarray:
    """Log probabilities of a wheel or dial clearly at ``position`` (0.0-9.9)."""
    offset = (np.arange(BINS) - position * 10 + BINS / 2) % BINS - BINS / 2
    probs = np.exp(-0.5 * (offset / sigma) ** 2) + 1e-6
    return np.log(probs / probs.sum())


def cut(dial: Image.Image) -> Image.Image:
    """A drawn dial as the reader is given it: its circle at 1 / dial_margin of the picture."""
    size = round(dial.width * READING["dial_margin"])
    out = Image.new("RGB", (size, size), (196, 200, 204))
    out.paste(dial, ((size - dial.width) // 2, (size - dial.height) // 2))
    return out.resize((READING["dial_size"], READING["dial_size"]))


def test_extra_digits_and_value_decimals():
    wheels = merge_reading({"mode": "counter", "display": "counter", "decimals": 3, "digits": 7})
    assert extra_digits(wheels) == 0 and value_decimals(wheels) == 3
    assert extra_digits({**wheels, "fraction": True}) == 1 and value_decimals({**wheels, "fraction": True}) == 4
    dialled = {**wheels, "fraction": True, "decimals": 0, "digits": 5, "dials": [{"x": 0, "y": 2, "r": 0.5}] * 4}
    assert extra_digits(dialled) == 4 and value_decimals(dialled) == 4  # the dials, not the fraction
    assert extra_digits({**wheels, "fraction": True, "counter_reader": "ocr"}) == 0  # only the wheel reader
    assert readers.parse("001873456", dialled) == pytest.approx(187.3456)
    assert readers.format_value(187.3456, dialled) == "187.3456"
    assert not readers.wrong_digit_count("001873456", dialled) and readers.wrong_digit_count("00187", dialled)
    # The finer last digit jitters: two steps below the value still keep it, three do not.
    fine = {**wheels, "fraction": True}
    assert readers.settling(89.9312, 89.9314, fine) and not readers.settling(89.9311, 89.9314, fine)
    assert not readers.settling(89.930, 89.932, wheels)


def test_the_last_wheel_and_the_dials_are_decoded_together():
    smooth = ["step", "step", "smooth", "smooth", "smooth"]
    # 187.3456: the last wheel at 7.3 and the dials 3.46, 4.56, 5.6 — each one's tenth the next one's digit.
    rows = np.stack([peak(1), peak(8), peak(7.35), peak(3.46), peak(4.56), peak(5.6)])
    value, _, _ = readers.wheel_decode(rows, smooth)
    assert round(value, 1) == 187345.6
    # A last wheel that already shows the next digit coming (7.9) is settled by the ×0.1 dial at 0.2: 8.
    rows = np.stack([peak(1), peak(8), peak(7.9, 4), peak(0.2), peak(2.0), peak(0.0)])
    value, _, _ = readers.wheel_decode(rows, smooth)
    assert int(value) // 1000 == 188
    # A dial between 9 and 0 is settled by the dial to its right: 9.98 with the next at 8 is still 9.
    rows = np.stack([peak(8), peak(7.99), peak(0.0), peak(9.7)])
    value, _, _ = readers.wheel_decode(rows, ["step", "smooth", "smooth"])
    assert round(value, 1) == 8799.7
    # Without links every pair is a counter's step, as before.
    assert (
        readers.wheel_decode(np.stack([peak(8), peak(9.5)]))[0]
        == readers.wheel_decode(np.stack([peak(8), peak(9.5)]), ["step"])[0]
    )


@pytest.mark.parametrize("position", [0.0, 1.5, 3.46, 5.0, 7.2, 9.9])
@pytest.mark.parametrize("ccw", [False, True])
def test_a_needle_is_read_by_its_colour(position, ccw):
    needle = dials.read_needle(cut(render_dial(position, 92, ccw)), ccw)
    assert needle.position is not None
    off = (needle.position - position + 5) % 10 - 5
    assert abs(off) < 0.2, needle.position
    assert needle.certainty > 0.5 and int(np.argmax(needle.probs)) in {
        (round(position * 10) + d) % 100 for d in (-2, -1, 0, 1, 2)
    }
    assert needle.image.size == (READING["dial_size"], READING["dial_size"])


def test_a_needle_without_colour_is_read_as_the_darkest_part():
    # An infrared camera at night: the red needle is grey, darker than the face.
    grey = cut(render_dial(6.3)).convert("L").convert("RGB")
    needle = dials.read_needle(grey)
    assert needle.position is not None and abs(needle.position - 6.3) < 0.3
    # A dial with no needle at all (a blank face) gives no position and an even distribution.
    blank = Image.new("RGB", (96, 96), (240, 240, 240))
    empty = dials.read_needle(blank)
    assert empty.position is None and empty.certainty == 0.0 and np.allclose(empty.probs, 1 / BINS)


def test_dials_are_cut_from_the_plane_the_counter_is_read_in():
    image, region, placed = render_meter(187.3456)
    for straightened in (False, True):
        needles = dials.read_needles(image, region, {"dials": placed}, straightened)
        assert [round(n.position or 0, 1) for n in needles] == pytest.approx([3.5, 4.6, 5.6, 6.0], abs=0.15)
    # Turned and seen at an angle: the dials are straightened with the region.
    seen, corners, seen_placed = turned_meter(187.3456, 125, 0.4)
    needles = dials.read_needles(seen, corners, {"dials": seen_placed}, True)
    assert [round(n.position or 0, 1) for n in needles] == pytest.approx([3.5, 4.6, 5.6, 6.0], abs=0.25)
    assert dials.read_needles(image, region, {"dials": []}, True) == []
    # The meter face around the region, where dials are placed: it holds all four dials.
    flat = imaging.reading_plane(seen, corners, True)
    assert flat is not None
    face, box = imaging.meter_face(seen, flat, READING["dial_reach"], READING["face_size"])
    assert max(face.size) == READING["face_size"]
    for d in seen_placed:
        assert box[0] < d["x"] - d["r"] and d["x"] + d["r"] < box[2] and box[1] < d["y"] - d["r"] < box[3]


class MeterCamera:
    """A camera looking at a drawn water meter with four pointer dials, turned and at an angle."""

    def __init__(self, value: float):
        image, self.roi, self.dials = turned_meter(value, 20, 0.2)
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=92)
        self.jpeg = buf.getvalue()

    async def grab(self, source_type, source):
        return self.jpeg

    async def close(self):
        pass


@requires_reader
def test_the_last_wheel_is_read_with_its_fraction(settings):
    """Half way to the next digit is still the digit it has passed, with its tenth one more decimal."""

    class Camera:
        value = 89931.6

        async def grab(self, source_type, source):
            buf = io.BytesIO()
            render_counter(self.value).save(buf, format="JPEG", quality=92)
            return buf.getvalue()

        async def close(self):
            pass

    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = Camera()
        reading = {"mode": "counter", "display": "counter", "digits": 7, "decimals": 3, "straighten": False}
        preview = {"source_type": "http", "source": "http://fake", "roi": counter_box(7)}
        fine = client.post("/api/v1/preview/read", json={**preview, "reading": reading}).json()
        assert fine["text"][:7] == "0089931" and len(fine["text"]) == 8  # a new counter reads the fraction
        assert fine["value"].startswith("89.931") and not fine["wrong_digit_count"]
        whole = client.post("/api/v1/preview/read", json={**preview, "reading": {**reading, "fraction": False}}).json()
        assert whole["text"] == "0089932"  # rounded, as counters saved before read
        assert client.get("/api/v1/config").json()["reading_defaults"]["fraction"] is True
        assert merge_reading({"mode": "counter"})["fraction"] is False


@requires_reader
def test_a_meter_with_pointer_dials(settings):
    camera = MeterCamera(187.34565)  # the last dial half way between 6 and 7
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        reading = {"mode": "counter", "display": "counter", "digits": 5, "decimals": 0, "unit": "m³"}
        preview = {"source_type": "http", "source": "http://fake", "roi": camera.roi}
        # Without dials: the meter face to place them on, in units of the region's height.
        plain = client.post("/api/v1/preview/read", json={**preview, "reading": reading}).json()
        assert plain["text"][:5] == "00187" and plain["face"].startswith("data:image/jpeg") and plain["dials"] == []
        x0, y0, x1, y1 = plain["face_box"]
        assert x0 < 0 < 1 < y1 and x1 > 4
        # With dials: one more digit per dial, the needles as found.
        dialled = {**reading, "dials": camera.dials}
        read = client.post("/api/v1/preview/read", json={**preview, "reading": dialled}).json()
        assert read["text"] == "001873456" and read["value"] == "187.3456"
        assert [d["needle"] for d in read["dials"]] == pytest.approx([3.46, 4.57, 5.65, 6.5], abs=0.25)
        # Too many dials, or a dial far off the region, is refused.
        many = {**reading, "dials": camera.dials * 2}
        assert client.post("/api/v1/preview/read", json={**preview, "reading": many}).status_code == 422
        far = {**reading, "dials": [{"x": 0, "y": READING["dial_reach"] + 2, "r": 0.5}]}
        assert client.post("/api/v1/preview/read", json={**preview, "reading": far}).status_code == 422
        # Other displays have no meter face.
        lcd = client.post("/api/v1/preview/read", json={**preview, "reading": {"display": "lcd"}}).json()
        assert "face" not in lcd

        # A sensor: its value, History and the readings export carry the dials.
        body = {"name": "Water", "kind": "reading", "source_type": "http", "source": "http://fake", "roi": camera.roi}
        sid = client.post("/api/v1/sensors", json={**body, "reading": dialled}).json()["id"]
        value = lambda: client.get(f"/api/v1/sensors/{sid}").json()["reading"]  # noqa: E731
        assert wait_for(lambda: value()["value"] == "187.3456", timeout=20), {
            k: v for k, v in value().items() if k in ("value", "last", "dials", "change_limit")
        }
        history = f"/api/v1/history?sensor={sid}"
        assert wait_for(lambda: client.get(history).json()["items"], timeout=30)
        row = client.get(history).json()["items"][0]["probs"]
        assert row["dials"] == camera.dials and row["wheels"]["dials"] == 4
        export = client.get(f"/api/v1/sensors/{sid}/reading-export").content
        with zipfile.ZipFile(io.BytesIO(export)) as archive:
            manifest = json.loads(archive.read("readings.json"))
            entry = manifest["readings"][0]
            assert manifest["format"] == 7 and len(manifest["settings"]["dials"]) == 4
            assert [d["file"] for d in entry["dials"]] == [f"images/0001-dial{k}.jpg" for k in range(1, 5)]
            assert Image.open(archive.open(entry["dials"][0]["file"])).size == (READING["dial_size"],) * 2
