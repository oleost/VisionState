"""Reading sensors: parsing, plausibility, decoding, the reader on drawn displays, and the API."""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate import readers
from visionstate.main import create_app
from visionstate.mqtt import SensorDescriptor, discovery_messages
from visionstate.readers import counter_line, decode, format_value, implausible, parse, wrong_digit_count
from visionstate.settings import KIND_READING, merge_reading

from .conftest import MODEL_DIR, requires_reader
from .displays import counter_box, counter_positions, render, render_counter
from .test_integration import wait_for


def cfg(**values) -> dict:
    return merge_reading(values)


# --- interpretation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "settings", "expected"),
    [
        ("74590", cfg(), 74590),
        ("0123456.7", cfg(decimals=1), 123456.7),
        ("1.6.89", cfg(mode="value", decimals=3), 1.689),  # stray dot ignored: decimals decide
        ("1,799", cfg(mode="value", decimals=3), 1.799),
        ("2:38", cfg(mode="time_left"), 158),
        ("0:07", cfg(mode="time_left"), 7),
        ("45", cfg(mode="time_left"), 45),
        ("2:75", cfg(mode="time_left"), None),  # not a time
        ("", cfg(), None),
        (".:", cfg(), None),
    ],
)
def test_parse(text, settings, expected):
    assert parse(text, settings) == expected


def test_implausible_readings():
    counter = cfg(mode="counter", max_step=10)
    assert implausible(100, None, counter) is None  # nothing to compare with yet
    assert implausible(99, 100, counter) == "went down"
    assert implausible(111, 100, counter) == "changed too much"
    assert implausible(105, 100, counter) is None
    value = cfg(mode="value")
    assert implausible(1.2, 1.9, value) is None  # values may go down, no step limit set


def test_counter_digit_count():
    counter = cfg(display="counter", digits=7)
    assert not wrong_digit_count("0089932", counter)
    assert wrong_digit_count("089932", counter)  # a wheel was missed
    assert wrong_digit_count("00899321", counter)  # a divider read as a digit
    assert not wrong_digit_count("12", cfg(digits=7))  # other displays may show any number of digits


def test_counter_line_keeps_the_middle_of_every_cell():
    # Seven cells of 20 px: white wheels with a black divider between them.
    image = Image.new("RGB", (140, 30), "black")
    for i in range(7):
        image.paste(Image.new("RGB", (14, 30), "white"), (i * 20 + 3, 0))
    line = counter_line(image, 7)
    assert line.size == (7 * 14, 30)
    assert np.asarray(line).min() == 255  # no divider left


def test_counter_positions_follow_the_wheel_rule():
    assert counter_positions(89932, 7) == [0, 0, 8, 9, 9, 3, 2]
    # While the last wheel goes 9 -> 0 the wheel to its left turns with it, and so on up the row.
    assert counter_positions(89999.5, 7) == pytest.approx([0, 0, 8.5, 9.5, 9.5, 9.5, 9.5])


def test_format_value():
    assert format_value(1.5, cfg(decimals=3)) == "1.500"
    assert format_value(158.0, cfg(mode="time_left")) == "158"
    assert format_value(None, cfg()) is None


def test_ctc_decode_merges_repeats_and_drops_blanks():
    chars = ["", "1", "2"]
    steps = [0, 1, 1, 0, 1, 2, 2, 0]  # "1", blank, "1", "2" -> "112"
    probs = np.full((len(steps), 3), 0.05)
    for i, c in enumerate(steps):
        probs[i, c] = 0.9
    text = decode(probs, chars)
    assert text.text == "112" and text.score == pytest.approx(0.9)


def test_segments_handles_flat_and_large_images():
    # One brightness level (no threshold exists) and a large image (integer overflow before).
    assert readers.segments(Image.new("RGB", (40, 20), (255, 0, 0)), light_digits=True).size == (40, 20)
    big = render("88", "led", digit_height=600)
    mask = np.asarray(readers.segments(big, light_digits=True).convert("L"))
    assert 0.05 < (mask < 128).mean() < 0.6  # the lit segments, not the whole panel


# --- Home Assistant discovery ------------------------------------------------------------------------


def test_discovery_for_reading_sensor():
    def entity(reading):
        sensor = SensorDescriptor("meter", "Meter", [], KIND_READING, [], merge_reading(reading))
        return dict(discovery_messages("homeassistant", sensor))["homeassistant/sensor/visionstate_meter/state/config"]

    counter = entity({"mode": "counter", "unit": "kWh", "device_class": "energy", "decimals": 1})
    assert counter["state_class"] == "total_increasing" and counter["unit_of_measurement"] == "kWh"
    assert counter["device_class"] == "energy" and counter["suggested_display_precision"] == 1
    timer = entity({"mode": "time_left"})
    assert (timer["device_class"], timer["unit_of_measurement"], timer["state_class"]) == (
        "duration",
        "min",
        "measurement",
    )
    price = entity({"mode": "value", "device_class": "monetary", "unit": "NOK"})
    assert "state_class" not in price  # Home Assistant rejects measurement for money


# --- with the real reader ---------------------------------------------------------------------------------


@requires_reader
@pytest.mark.parametrize(
    ("text", "style"), [("74590", "lcd"), ("0123456.7", "lcd"), ("2:38", "led"), ("0:07", "led"), ("98765", "led")]
)
def test_reader_reads_drawn_displays(text, style):
    """Guards against a broken model file and preprocessing bugs (faint unlit segments included)."""
    spec = readers.READERS[readers.DEFAULT_READER]
    reader = readers.Reader(spec, MODEL_DIR / spec.filename)
    result, _ = reader.read_display(render(text, style), "auto")
    assert result.text == text and result.score > 0.8


def counter_region(image: Image.Image, digits: int, taller: float = 0.0) -> Image.Image:
    """The counter window of a drawn meter, optionally a taller region around it."""
    box = counter_box(digits)
    top = box["y"] - box["h"] * taller / 2
    return image.crop(
        (
            round(box["x"] * image.width),
            round(top * image.height),
            round((box["x"] + box["w"]) * image.width),
            round((top + box["h"] * (1 + taller)) * image.height),
        )
    )


@requires_reader
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (89932, "0089932"),
        (7, "0000007"),
        (1234567, "1234567"),
        (89949.8, "0089950"),  # two wheels almost turned over
        (90099.9, "0090100"),  # four wheels almost turned over
    ],
)
@pytest.mark.parametrize("taller", [0.0, 0.25])
def test_reader_reads_drawn_counters(value, expected, taller):
    """Rolling digit wheels: dividers and the half digits above and below must not be read."""
    spec = readers.READERS[readers.DEFAULT_READER]
    reader = readers.Reader(spec, MODEL_DIR / spec.filename)
    result, used = reader.read_display(counter_region(render_counter(value), 7, taller), "counter", 7)
    assert result.text == expected and result.score > 0.8
    assert used.width < counter_region(render_counter(value), 7).width  # the dividers are gone


class CounterCamera:
    """A camera looking at a mechanical counter whose value the test changes."""

    def __init__(self, value: float):
        self.value = value

    async def grab(self, source_type, source):
        buf = io.BytesIO()
        render_counter(self.value).save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    async def close(self):
        pass


class DisplayCamera:
    """A camera looking at a display whose text the test changes."""

    def __init__(self, text: str, style: str = "lcd"):
        self.text, self.style = text, style

    async def grab(self, source_type, source):
        buf = io.BytesIO()
        render(self.text, self.style).save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    async def close(self):
        pass


@pytest.fixture
def settings(tmp_path):
    from visionstate.settings import Settings

    return Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        frontend_dir=tmp_path / "none",
        bundled_models_dir=MODEL_DIR,
    )


@requires_reader
def test_reading_sensor_flow(settings):
    camera = DisplayCamera("0012345.6")
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        assert rt.reader is None  # not loaded without reading sensors
        body = {
            "name": "Power meter",
            "kind": "reading",
            "source_type": "http",
            "source": "http://fake",
            "reading": {"mode": "counter", "decimals": 1, "unit": "kWh", "device_class": "energy"},
            "interval_s": 1,
            "debounce": 1,
        }
        assert client.post("/api/v1/sensors", json={**body, "reading": {"mode": "sideways"}}).status_code == 422
        assert (
            client.post("/api/v1/sensors", json={**body, "states": [{"name": "A"}, {"name": "B"}]}).status_code == 400
        )
        resp = client.post("/api/v1/sensors", json=body)
        assert resp.status_code == 201, resp.text
        sid = resp.json()["id"]

        def view():
            return client.get(f"/api/v1/sensors/{sid}").json()

        assert wait_for(lambda: view()["reading"]["value"] == "12345.6", timeout=60)
        assert view()["status"] == "ok"
        assert client.get(f"/api/v1/sensors/{sid}/reading/image").status_code == 200

        # A counter never goes down: the lower reading is rejected and the value stays.
        camera.text = "0012300.0"
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: (view()["reading"]["last"] or {}).get("reason") == "went down", timeout=30)
        assert view()["reading"]["value"] == "12345.6"

        camera.text = "0012346.1"
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: view()["reading"]["value"] == "12346.1", timeout=30)

        def history():
            return client.get(f"/api/v1/sensors/{sid}/history").json()

        # Rows are written right after a value is published, so wait for the latest one.
        assert wait_for(lambda: any(h["published_key"] == "12346.1" for h in history()), timeout=10)
        history = history()
        accepted = [h["published_key"] for h in history if h["published_key"]]
        rejected = [h["probs"]["reason"] for h in history if not h["published_key"]]
        assert accepted[:2] == ["12346.1", "12345.6"] and rejected == ["went down"]
        assert client.get("/api/v1/review").json()["total"] == 0  # readings never enter the review queue

        # Training endpoints do not apply.
        assert client.get(f"/api/v1/sensors/{sid}/quality").status_code == 400
        assert client.post(f"/api/v1/sensors/{sid}/capture", json={"state_key": "x"}).status_code == 400

        # Wizard preview: the frame, the image the reader used, the text and the value.
        camera.text, camera.style = "1:05", "led"
        preview = client.post(
            "/api/v1/preview/read",
            json={"source_type": "http", "source": "http://fake", "reading": {"mode": "time_left"}},
        ).json()
        assert preview["text"] == "1:05" and preview["value"] == "65"
        assert preview["read_image"].startswith("data:image/jpeg;base64,")

        # Export and import keep the kind and the settings.
        exported = client.get(f"/api/v1/sensors/{sid}/export")
        imported = client.post("/api/v1/import", files={"file": ("b.zip", exported.content, "application/zip")})
        assert imported.status_code == 201, imported.text
        copy = client.get(f"/api/v1/sensors/{imported.json()['id']}").json()
        assert copy["kind"] == "reading" and copy["reading"]["unit"] == "kWh"

        # The reader is released once the last reading sensor is gone.
        for sensor_id in (sid, copy["id"]):
            assert client.delete(f"/api/v1/sensors/{sensor_id}").status_code == 204
        assert rt.reader is None


@requires_reader
def test_restart_keeps_checking_against_the_last_value(settings):
    """After a restart a counter must not accept a lower value just because memory is empty."""
    camera = DisplayCamera("00500")
    body = {"name": "Water", "kind": "reading", "source_type": "http", "source": "x", "interval_s": 1, "debounce": 1}
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["reading"]["value"] == "500", timeout=60)
    camera.text = "00400"
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        assert wait_for(
            lambda: (client.get(f"/api/v1/sensors/{sid}").json()["reading"]["last"] or {}).get("reason") == "went down",
            timeout=60,
        )
        assert client.get(f"/api/v1/sensors/{sid}").json()["reading"]["value"] == "500"


@requires_reader
def test_counter_sensor_flow(settings):
    camera = CounterCamera(89932)
    reading = {"mode": "counter", "display": "counter", "digits": 7, "decimals": 3, "unit": "m³"}
    body = {
        "name": "Water meter",
        "kind": "reading",
        "source_type": "http",
        "source": "http://fake",
        "roi": counter_box(7),
        "reading": reading,
        "interval_s": 1,
        "debounce": 1,
    }
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        assert client.post("/api/v1/sensors", json={**body, "reading": {**reading, "digits": 0}}).status_code == 422
        resp = client.post("/api/v1/sensors", json=body)
        assert resp.status_code == 201, resp.text
        sid = resp.json()["id"]

        def view():
            return client.get(f"/api/v1/sensors/{sid}").json()

        assert wait_for(lambda: view()["reading"]["value"] == "89.932", timeout=60)
        assert view()["reading"]["digits"] == 7

        camera.value = 89941
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: view()["reading"]["value"] == "89.941", timeout=30)

        # Told the counter has eight wheels, the seven digits read are rejected and the value stays.
        patched = client.patch(f"/api/v1/sensors/{sid}", json={"reading": {**reading, "digits": 8}})
        assert patched.status_code == 200, patched.text
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: (view()["reading"]["last"] or {}).get("reason") == "wrong digit count", timeout=30)
        assert view()["reading"]["value"] == "89.941"

        preview = {"source_type": "http", "source": "http://fake", "roi": counter_box(7)}
        good = client.post("/api/v1/preview/read", json={**preview, "reading": reading}).json()
        assert good["text"] == "0089941" and good["value"] == "89.941" and good["wrong_digit_count"] is False
        bad = client.post("/api/v1/preview/read", json={**preview, "reading": {**reading, "digits": 8}}).json()
        assert bad["wrong_digit_count"] is True
