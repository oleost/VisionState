"""Pointer dials (issue #59, docs/POINTER_DIAL_PLAN.md)."""

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

from .conftest import ASSETS, MODEL_DIR, requires_reader
from .displays import counter_box, render_counter, render_dial, render_meter, render_wide_dial, turned_meter
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
    assert extra_digits({**wheels, "fraction": True}) == 0  # stored by 0.12.0b1 only: no longer read
    dialled = {**wheels, "decimals": 0, "digits": 5, "dials": [{"x": 0, "y": 2, "r": 0.5}] * 4}
    assert extra_digits(dialled) == 4 and value_decimals(dialled) == 4
    assert extra_digits({**dialled, "counter_reader": "ocr"}) == 0  # only the wheel reader
    assert readers.parse("001873456", dialled) == pytest.approx(187.3456)
    assert readers.format_value(187.3456, dialled) == "187.3456"
    assert not readers.wrong_digit_count("001873456", dialled) and readers.wrong_digit_count("00187", dialled)
    # The finer last digit jitters: two steps below the value still keep it, three do not.
    assert readers.settling(187.3454, 187.3456, dialled) and not readers.settling(187.3453, 187.3456, dialled)
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


@pytest.mark.parametrize("position", [0.4, 2.5, 3.8, 7.1, 9.2])
@pytest.mark.parametrize("ccw", [False, True])
def test_a_wide_pointer_has_two_possible_ends(position, ccw):
    needle = dials.read_needle(cut(render_wide_dial(position, 92, ccw)), ccw)
    assert needle.ends == 2 and needle.position is not None
    off = (needle.position - position + 2.5) % 5 - 2.5  # either end
    assert abs(off) < 0.2, needle.position
    # Both ends are equally likely, nothing between them.
    first, second = round(position * 10) % BINS, round(position * 10 + BINS / 2) % BINS
    assert needle.probs[first] == pytest.approx(needle.probs[second], rel=0.5)
    assert needle.probs[(first + 25) % BINS] < 0.01 * needle.probs[first]


@pytest.mark.parametrize("position", [0.0, 3.46, 7.2])
def test_a_needle_is_not_taken_for_a_wide_pointer(position):
    assert dials.read_needle(cut(render_dial(position, 92))).ends == 1


class StubWheels(readers.WheelReader):
    """A wheel reader whose model sees the given positions, and that is not loaded from a file."""

    def __init__(self, positions: list[float]):
        self.spec = readers.WHEEL_READERS["wheels-v2"]
        self.positions_seen = positions

    def probabilities(self, image, digits, box=None):
        return np.stack([np.exp(peak(p, 0.8)) for p in self.positions_seen])

    def cells_image(self, image, digits, box=None, dials=()):
        return image


def needle_at(position: float) -> dials.Needle:
    return dials.read_needle(cut(render_dial(position, 92)))


def test_a_wheel_that_lags_does_not_pull_a_needle_off_its_number():
    # 647.0394 on a meter whose wheels lag the needle: the last wheels show 3.2 and 9.2 though the
    # needle on the ×0.0001 dial stands at 4.9 (seen on a real meter, one step low before).
    picture = Image.new("RGB", (64, 32))
    text, _ = StubWheels([0, 6, 4, 7, 0, 3.2, 9.2]).read_counter(picture, 7, None, [needle_at(4.9)])
    assert text.text == "06470394" and text.score >= 0.7
    # Past the turn: the needle just past 0, the last wheel still lags behind its digit: 647.0400.
    text, _ = StubWheels([0, 6, 4, 7, 0, 3.8, 9.8]).read_counter(picture, 7, None, [needle_at(0.3)])
    assert text.text == "06470400"


def _turning(number: str, wheel: float, shift: float = 0.0) -> readers.Text:
    """A reading of five wheels and two dials, with the last wheel seen at ``wheel``."""
    rows = [{"at": 0.0, "p": 1.0, "top": [[0.0, 1.0]]} for _ in range(4)]
    rows.append({"at": wheel, "p": 1.0, "top": [[wheel, 0.7], [wheel - 0.1, 0.3]]})
    details = {"reader": "wheels", "wheels": {"value": float(number), "shift": shift, "per_wheel": rows}}
    return readers.Text(number, 0.9, details)


TWO_DIALS = merge_reading(
    {
        "mode": "counter",
        "display": "counter",
        "counter_reader": "wheels",
        "digits": 5,
        "decimals": 0,
        "dials": [{"x": 1.0, "y": 1.0, "r": 0.5, "ccw": False}] * 2,
    }
)


def test_the_last_value_decides_a_wheel_on_its_digit_with_the_dial_nearly_round():
    settle = readers.settle_turning_wheel
    # Issue #59: 187.99 read as 188.99 with the wheel on a clean 8.
    settled = settle(_turning("0018899", 8.0), 187.98, TWO_DIALS)
    assert settled.text == "0018799"
    assert settled.details["wheels"]["by_last_value"]
    assert settled.score == 0.9
    # A wheel that stands on its digit until the dial is round: 8.99 is right, also when read too low.
    assert settle(_turning("0018899", 8.0), 188.95, TWO_DIALS).text == "0018899"
    assert settle(_turning("0018799", 8.0), 188.95, TWO_DIALS).text == "0018899"
    # Across a wheel that carries: 189.99 and 190.99 with the last wheel on 0.
    assert settle(_turning("0019099", 0.0), 189.9, TWO_DIALS).text == "0018999"
    # The common shift of the wheels is taken off what the wheel shows.
    assert settle(_turning("0018899", 8.1, shift=0.1), 187.98, TWO_DIALS).text == "0018799"
    # Never down to below the last value, even when that is closer (a last value held back by its limit).
    assert settle(_turning("0018899", 8.0), 188.2, TWO_DIALS).text == "0018899"
    # No last value: the reading stays, unsure.
    undecided = settle(_turning("0018899", 8.0), None, TWO_DIALS)
    assert (undecided.text, undecided.score) == ("0018899", READING["dial_wheel_undecided_score"])


def test_a_reading_that_is_not_in_doubt_stays_as_it_is():
    settle = readers.settle_turning_wheel
    far = 500.0  # a last value that would pull the reading if it could
    for text in (
        _turning("0018850", 8.0),  # the dial is not nearly round: it says which side
        _turning("0018899", 8.5),  # the wheel is between digits: it says which digit
        _turning("0018199", 8.0),  # the value is not next to what the wheel shows
    ):
        assert settle(text, far, TWO_DIALS) is text
    # Without dials nothing changes.
    plain = readers.Text("00188", 0.9, _turning("00188", 8.0).details)
    assert settle(plain, far, {**TWO_DIALS, "dials": []}) is plain


def test_a_last_wheel_that_stands_on_its_digit_until_the_dial_is_round():
    # 979.7746 on a meter whose last wheel only turns when the ×0.1 dial is nearly round: not 978.7746.
    picture = Image.new("RGB", (64, 32))
    wheels = StubWheels([0, 0, 9, 7, 9])
    text, _ = wheels.read_counter(picture, 5, None, [needle_at(7.7), needle_at(7.4), needle_at(4.6), needle_at(6.0)])
    assert text.text == "009797746" and text.score >= 0.7
    # Turning by the end of the round, and on the next digit after it.
    text, _ = StubWheels([0, 0, 9, 7.5, 9.5]).read_counter(picture, 5, None, [needle_at(9.8)])
    assert text.text[:6] == "009799"
    text, _ = StubWheels([0, 0, 9, 8, 0]).read_counter(picture, 5, None, [needle_at(0.2)])
    assert text.text[:6] == "009800"
    # A wheel that follows its dial and is nearly on the next digit is still on this one.
    text, _ = StubWheels([0, 0, 1, 8, 6.9]).read_counter(picture, 5, None, [needle_at(9.5)])
    assert text.text[:6] == "001869"


@pytest.mark.parametrize(
    ("name", "seen"), [("0146-dial1", 8.9), ("0045-dial1", 0.1), ("0446-dial2", 8.8), ("0086-dial1", 9.8)]
)
def test_holes_in_a_needle_do_not_turn_it_round(name, seen):
    # Real dark pictures: holes in the mask of a drop-shaped needle's body moved its hub towards the tip.
    needle = dials.read_needle(Image.open(ASSETS / "meter_readings" / f"zapadee-{name}.jpg").convert("RGB"))
    assert needle.position is not None and abs((needle.position - seen + 5) % 10 - 5) < 0.3, needle.position
    assert needle.certainty > 0.5


def test_the_dials_around_a_wide_pointer_settle_which_end_counts():
    # 187.2879 and, with the pointer on the same line, 187.2829: the same picture of the wide
    # pointer, the ×0.01 dial before it says which end.
    picture = Image.new("RGB", (64, 32))
    wide = dials.read_needle(cut(render_wide_dial(2.74, 92)))
    wheels = StubWheels([0, 0, 1, 8, 7])
    text, _ = wheels.read_counter(picture, 5, None, [needle_at(2.84), needle_at(8.75), wide, needle_at(9.2)])
    assert text.text == "001872879" and text.score >= 0.7
    assert text.details is not None
    assert abs(text.details["wheels"]["per_wheel"][7]["needle"] - 7.74) < 0.2  # the end that counts
    text, _ = wheels.read_counter(picture, 5, None, [needle_at(2.82), needle_at(8.25), wide, needle_at(9.2)])
    assert text.text == "001872829" and text.score >= 0.7


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
@pytest.mark.parametrize("off", [(0, 0), (-0.15, -0.15), (0.2, 0), (0, 0.2)])
def test_a_real_meter_with_pale_needles(settings, off):
    """A real meter (tests/assets/meter_dials.jpg, issue #59): pale, drop-shaped needles in a
    picture with a greenish cast, read with the wheels as the meter shows them — also with the
    circles placed a little beside the dials (``off``, in dial radii), as a user places them."""
    jpeg = (ASSETS / "meter_dials.jpg").read_bytes()
    width, height = Image.open(ASSETS / "meter_dials.jpg").size
    x0, y0, x1, y1 = 104, 74, 284, 112  # the wheels' window (pixels)
    h, r = y1 - y0, 42
    roi = {"x": x0 / width, "y": y0 / height, "w": (x1 - x0) / width, "h": h / height}
    centres = [(363, 186), (318, 288), (217, 326), (113, 273)]  # ×0.1, ×0.01, ×0.001, ×0.0001
    placed = [{"x": (cx + off[0] * r - x0) / h, "y": (cy + off[1] * r - y0) / h, "r": r / h} for cx, cy in centres]

    class Camera:
        async def grab(self, source_type, source):
            return jpeg

        async def close(self):
            pass

    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = Camera()
        reading = {"mode": "counter", "display": "counter", "digits": 5, "decimals": 0, "straighten": False}
        preview = {"source_type": "http", "source": "http://fake", "roi": roi, "reading": {**reading, "dials": placed}}
        read = client.post("/api/v1/preview/read", json=preview).json()
        assert read["value"] == "979.1540" and read["score"] >= 0.7, read
        needles = [d["needle"] for d in read["dials"]]
        for found, seen in zip(needles, [1.4, 5.4, 4.1, 0.0], strict=True):
            assert abs((found - seen + 5) % 10 - 5) < 0.3, needles


@requires_reader
def test_real_meters_read_as_by_eye():
    """Frames of three real meters (tests/assets/meter_readings, values read by eye): wheels that lag
    the one needle, four dials of which one has a wide pointer, and a last wheel that stands on its
    digit until the ×0.1 dial is nearly round, with dark needles full of holes."""
    folder = ASSETS / "meter_readings"
    spec = readers.WHEEL_READERS["wheels-v2"]
    reader = readers.WheelReader(spec, MODEL_DIR / "wheels-v2.onnx")
    for frame in json.loads((folder / "readings.json").read_text(encoding="utf-8")):
        needles = [dials.read_needle(Image.open(folder / name).convert("RGB")) for name in frame["dials"]]
        image = Image.open(folder / frame["image"]).convert("RGB")
        text, _ = reader.read_counter(image, frame["digits"], tuple(frame["box"]), needles)
        places = frame["decimals"] + len(needles)
        assert f"{int(text.text) / 10**places:.{places}f}" == frame["value"], frame["image"]


@requires_reader
def test_the_last_wheel_is_rounded_without_dials(settings):
    """Without pointer dials the last wheel is rounded to the nearest digit — also for a counter that
    stored "fraction" (the last wheel's tenth as one more digit, offered in 0.12.0b1 only)."""

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
        whole = client.post("/api/v1/preview/read", json={**preview, "reading": reading}).json()
        assert whole["text"] == "0089932" and whole["value"] == "89.932" and not whole["wrong_digit_count"]
        stored = client.post("/api/v1/preview/read", json={**preview, "reading": {**reading, "fraction": True}}).json()
        assert stored["text"] == "0089932"
        assert "fraction" not in client.get("/api/v1/config").json()["reading_defaults"]


@requires_reader
def test_a_wheel_on_its_digit_does_not_make_a_meter_jump(settings):
    """Issue #59: the last wheel on a clean 8 with the ×0.1 needle at 9.9 was published as 188.99
    for 187.99. A sensor goes on from its last value, and a whole m³ more is over its limit."""

    def picture(value: float, wheels: float | None = None) -> bytes:
        image, _, _ = render_meter(value)
        if wheels is not None:
            image.paste(render_counter(wheels, 5, 0), (0, 0))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    class Camera:
        jpeg = picture(187.79)

        async def grab(self, source_type, source):
            return self.jpeg

        async def close(self):
            pass

    _, roi, placed = render_meter(187.79)
    camera = Camera()
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        sid = client.post(
            "/api/v1/sensors",
            json={
                "name": "Water",
                "kind": "reading",
                "source_type": "http",
                "source": "http://fake",
                "roi": roi,
                "interval_s": 1,
                "debounce": 1,
                "reading": {
                    "mode": "counter",
                    "display": "counter",
                    "digits": 5,
                    "decimals": 0,
                    "dials": placed,
                    "change_limit": "fixed",
                    "max_rate": 1000,  # room for the 0.2 m³ in the seconds of this test, not for a whole m³
                },
            },
        ).json()["id"]
        reading = lambda: client.get(f"/api/v1/sensors/{sid}").json()["reading"]  # noqa: E731
        assert wait_for(lambda: reading()["value"] == "187.7900", timeout=20), (
            reading()["value"],
            reading()["last"],
            reading()["limit"],
        )
        camera.jpeg = picture(187.9921, 188.0)
        assert wait_for(lambda: reading()["value"] == "187.9921", timeout=20), (
            reading()["value"],
            reading()["last"],
            reading()["limit"],
        )
        camera.jpeg = picture(188.9921)
        assert wait_for(lambda: (reading()["last"] or {}).get("reason") == "changed too much", timeout=20), (
            reading()["value"],
            reading()["last"],
            reading()["limit"],
        )
        assert reading()["value"] == "187.9921"


def test_a_value_of_another_precision_is_no_measure_for_the_limit():
    """Dials added to a sensor that published whole m³: 187 says nothing about 187.3456."""
    assert readers.same_precision(None, TWO_DIALS)
    assert readers.same_precision("187.34", TWO_DIALS)
    assert not readers.same_precision("187", TWO_DIALS)
    assert readers.same_precision("187", {**TWO_DIALS, "dials": []})


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
        # What the reader saw is made again from the stored frame: wheels and dials, wider than the wheels.
        item = client.get(history).json()["items"][0]["id"]
        seen = Image.open(io.BytesIO(client.get(f"/api/v1/history/{item}/seen").content))
        region = Image.open(io.BytesIO(client.get(f"/api/v1/history/{item}/image?size=region").content))
        assert seen.width > seen.height * 4 and seen.size != region.size
        assert client.get("/api/v1/history/999999/seen").status_code == 404
        export = client.get(f"/api/v1/sensors/{sid}/reading-export").content
        with zipfile.ZipFile(io.BytesIO(export)) as archive:
            manifest = json.loads(archive.read("readings.json"))
            entry = manifest["readings"][0]
            assert manifest["format"] == 7 and len(manifest["settings"]["dials"]) == 4
            assert [d["file"] for d in entry["dials"]] == [f"images/0001-dial{k}.jpg" for k in range(1, 5)]
            assert Image.open(archive.open(entry["dials"][0]["file"])).size == (READING["dial_size"],) * 2
