"""Reading sensors: parsing, plausibility, decoding, the reader on drawn displays, and the API."""

import io
import json
import sqlite3
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from visionstate import readers
from visionstate.db import Database, Prediction, Sensor, keep_text_reader
from visionstate.main import create_app
from visionstate.mqtt import SensorDescriptor, discovery_messages
from visionstate.readers import counter_line, decode, format_value, implausible, parse, wrong_digit_count
from visionstate.settings import KIND_READING, merge_reading

from .conftest import ASSETS, MODEL_DIR, requires_reader
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


def test_diagnostic_entities_of_reading_sensors():
    """Raw reading, problem, accepted share for every reading sensor; a rate for counters only.

    All off by default in Home Assistant, and the rate in the unit Home Assistant expects.
    """

    def configs(reading):
        sensor = SensorDescriptor("meter", "Meter", [], KIND_READING, [], merge_reading(reading))
        return {topic.split("/")[-2]: payload for topic, payload in discovery_messages("homeassistant", sensor)}

    counter = configs({"mode": "counter", "unit": "m³", "device_class": "water"})
    for key in ("raw", "problem", "accepted", "rate", "reader_image"):
        assert counter[key]["enabled_by_default"] is False, key
    assert counter["reader_image"]["image_topic"] == "visionstate/meter/reader_image"
    assert counter["reader_image"]["entity_category"] == "diagnostic"
    assert counter["problem"]["options"][0] == "ok" and counter["problem"]["device_class"] == "enum"
    assert counter["accepted"]["unit_of_measurement"] == "%"
    assert (counter["rate"]["unit_of_measurement"], counter["rate"]["device_class"]) == ("m³/h", "volume_flow_rate")
    assert "entity_category" not in counter["rate"]  # a measurement for automations, not a diagnostic
    kwh = configs({"mode": "counter", "unit": "kWh"})["rate"]
    assert (kwh["unit_of_measurement"], kwh["device_class"]) == ("kW", "power")
    assert configs({"mode": "counter", "unit": "L"})["rate"]["unit_of_measurement"] == "L/min"
    gallons = configs({"mode": "counter", "unit": "gal"})["rate"]
    assert gallons["unit_of_measurement"] == "gal/h" and "device_class" not in gallons
    for mode in ("value", "time_left"):
        assert "rate" not in configs({"mode": mode}) and "problem" in configs({"mode": mode})


def test_settling_last_wheel_and_right_values():
    counter = merge_reading({"mode": "counter", "decimals": 3})
    # One step of the last digit below the value: the last wheel turning, not a rejection.
    assert readers.settling(629.078, 629.079, counter)
    assert not readers.settling(629.077, 629.079, counter)  # two steps: a real "went down"
    assert not readers.settling(629.079, 629.079, counter) and not readers.settling(629.08, 629.079, counter)
    assert readers.settling(12344, 12345, merge_reading({"mode": "counter", "decimals": 0}))
    assert not readers.settling(5.5, 5.6, merge_reading({"mode": "value", "decimals": 1}))
    assert not readers.settling(None, 5.6, counter) and not readers.settling(5.5, None, counter)

    # The right value someone types: as written with a point or comma, digits as the meter shows them.
    assert readers.right_value("0629558", counter) == pytest.approx(629.558)
    assert readers.right_value("629558", counter) == pytest.approx(629.558)
    assert readers.right_value(" 629.558 ", counter) == pytest.approx(629.558)
    assert readers.right_value("629,558", counter) == pytest.approx(629.558)
    assert readers.right_value("12", merge_reading({"mode": "value", "decimals": 0})) == 12
    assert readers.right_value("abc", counter) is None and readers.right_value("1.2.3", counter) is None
    timer = merge_reading({"mode": "time_left"})
    assert readers.right_value("1:25", timer) == 85 and readers.right_value("85", timer) == 85
    assert readers.right_value("1:75", timer) is None and readers.right_value("1.5", timer) is None


def test_problem_accepted_share_and_rate():
    from collections import deque

    assert readers.problem_key(None) == "ok" and readers.problem_key("went down") == "went_down"
    reads = deque([(0, True), (100, False), (200, True), (300, True)])
    assert readers.accepted_share(reads, 300, 1000) == 75.0
    assert readers.accepted_share(reads, 1150, 1000) == 100.0 and len(reads) == 2  # the old ones dropped
    assert readers.accepted_share(deque(), 0, 10) is None

    # 0.003 m³ in 15 minutes = 0.012 m³/h; the window keeps one older sample as the start.
    samples = deque([(0, 629.0), (600, 629.549), (900, 629.551), (1500, 629.552)])
    assert readers.counter_rate(samples, 1500, 900, 30) == pytest.approx(0.003 * 3600 / 900)
    assert samples[0] == (600, 629.549)
    # Readings far apart (only on triggers): the average since the previous one.
    assert readers.counter_rate(deque([(0, 10.0), (7200, 12.0)]), 7200, 900, 30) == pytest.approx(1.0)
    assert readers.counter_rate(deque([(0, 10.0), (10, 10.5)]), 10, 900, 30) is None  # too close together
    assert readers.counter_rate(deque([(0, 10.0)]), 0, 900, 30) is None


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


def wheel_logp(value: float, digits: int) -> np.ndarray:
    """Log probabilities that put every wheel exactly where ``value`` turns it."""
    logp = np.full((digits, 100), -30.0)
    for i, position in enumerate(counter_positions(value, digits)):
        logp[i, round(position * 10) % 100] = 0.0
    return logp


@pytest.mark.parametrize("value", [89932.0, 89939.5, 89949.8, 90099.9, 1234567.0, 9999999.9, 0.0, 0.3])
def test_wheel_value_follows_the_wheels(value):
    """The wheels to the left only turn while their right neighbour passes 9: the value is consistent."""
    found, score = readers.wheel_value(wheel_logp(value, 7))
    assert found == pytest.approx(value) and score == pytest.approx(0.0)


def test_wheel_value_prefers_a_consistent_reading():
    # The last wheel at 9.8 turns the second-to-last one 0.8 of the way from 4 to 5. Taken on its
    # own that wheel looks a little more like a 5 (89950 + 9.8 = too high by ten); together with
    # the last wheel it can only be 4.8.
    logp = wheel_logp(89949.8, 7)
    logp[5] = np.log(np.full(100, 1e-6))
    logp[5, 48], logp[5, 50] = np.log(0.4), np.log(0.6)
    found, _ = readers.wheel_value(logp)
    assert found == pytest.approx(89949.8)


@requires_reader
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (89932, "0089932"),
        (7, "0000007"),
        (1234567, "1234567"),
        (89949.8, "0089950"),  # two wheels almost turned over
        (90099.9, "0090100"),  # four wheels almost turned over
        (89949.3, "0089949"),  # the last wheel a third of the way
        (629999.6, "0630000"),  # five wheels past half way
    ],
)
@pytest.mark.parametrize("taller", [0.0, 0.25])
def test_wheel_reader_reads_drawn_counters(value, expected, taller):
    spec = readers.WHEEL_READERS[readers.DEFAULT_WHEEL_READER]
    reader = readers.WheelReader(spec, MODEL_DIR / spec.filename)
    result, used = reader.read_counter(counter_region(render_counter(value), 7, taller), 7)
    assert result.text == expected and result.score > 0.8
    assert used.width > used.height  # the cells side by side


@requires_reader
@pytest.mark.parametrize(
    ("asset", "expected"),
    [
        ("counter_red.jpg", "0632289"),
        # The second-to-last wheel half way from 8 to 9 while the last shows 0; read as text it was 0632550.
        ("counter_turning.jpg", "0632590"),
    ],
)
def test_wheel_reader_reads_a_real_meter(asset, expected):
    """Issue #40: an ESP32 camera with its flash on a water meter with red decimal wheels."""
    spec = readers.WHEEL_READERS[readers.DEFAULT_WHEEL_READER]
    reader = readers.WheelReader(spec, MODEL_DIR / spec.filename)
    result, _ = reader.read_counter(Image.open(ASSETS / asset).convert("RGB"), 7)
    assert result.text == expected and result.score > 0.8


def test_counters_made_before_the_wheel_reader_keep_the_text_reader(tmp_path):
    path = tmp_path / "old.db"
    Database(path).init()
    con = sqlite3.connect(path)
    row = (
        "INSERT INTO sensor (slug, name, kind, source_type, source, interval_s, threshold, debounce, enabled, "
        "entity_prefix, publish, created_at, reading) VALUES (?, ?, 'reading', 'http', 'x', 30, 0.7, 2, 1, 0, 1, "
        "'2026-10-01', ?)"
    )
    con.execute(row, ("old", "Old counter", json.dumps({"mode": "counter", "display": "counter", "digits": 7})))
    con.execute(
        row, ("new", "New counter", json.dumps({"display": "counter", "digits": 7, "counter_reader": "wheels"}))
    )
    con.execute(row, ("lcd", "Display", json.dumps({"mode": "counter", "display": "auto"})))
    con.execute("PRAGMA user_version = 11")  # as a database of 0.7.1b3 and older
    con.commit()
    con.close()
    db = Database(path)
    db.init()
    with db.session() as s:
        readers_by_slug = {r.slug: merge_reading(r.reading)["counter_reader"] for r in s.query(Sensor)}
    # The display keeps no stored choice: should it become a counter, it gets the wheel reader.
    assert readers_by_slug == {"old": "ocr", "new": "wheels", "lcd": "wheels"}
    assert keep_text_reader({"display": "counter"}) == {"display": "counter", "counter_reader": "ocr"}
    assert keep_text_reader(None) is None


def test_darkest_turns_coloured_digits_as_dark_as_black_ones():
    image = Image.new("RGB", (3, 1))
    image.putpixel((0, 0), (200, 30, 35))  # red digit
    image.putpixel((1, 0), (25, 25, 25))  # black digit
    image.putpixel((2, 0), (235, 235, 230))  # white wheel
    red, black, white = (readers.darkest(image).getpixel((x, 0))[0] for x in range(3))
    assert abs(red - black) < 20 and white > 200
    plain = [readers.plain(image).getpixel((x, 0))[0] for x in range(3)]
    assert plain[0] - plain[1] > 40  # in a plain grey image red stays much lighter than black


@requires_reader
def test_reader_reads_red_wheels_of_a_real_meter():
    """A water meter's red decimal wheels (issue #40): the "9"s were read as "5"s in plain grey."""
    spec = readers.READERS[readers.DEFAULT_READER]
    reader = readers.Reader(spec, MODEL_DIR / spec.filename)
    result, _ = reader.read_display(Image.open(ASSETS / "counter_red.jpg").convert("RGB"), "counter", 7)
    assert result.text == "0632289"


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
        sent: dict[str, object] = {}  # what goes to MQTT, last payload per topic

        async def publish(topic, payload, retain=False):
            sent[topic] = payload

        rt.mqtt.publish = publish
        resp = client.post("/api/v1/sensors", json=body)
        assert resp.status_code == 201, resp.text
        sid = resp.json()["id"]
        base = "visionstate/power_meter"
        assert client.get(f"/api/v1/sensors/{sid}/reading-export").status_code == 404  # nothing verified yet

        def view():
            return client.get(f"/api/v1/sensors/{sid}").json()

        assert wait_for(lambda: view()["reading"]["value"] == "12345.6", timeout=60)
        assert wait_for(lambda: sent.get(f"{base}/problem") == "ok", timeout=10)
        assert sent[f"{base}/raw"] == "12345.6" and sent[f"{base}/accepted"] == "100"
        assert sent[f"{base}/reader_image"][:2] == b"\xff\xd8"  # what the reader saw, as a JPEG
        assert view()["status"] == "ok"
        assert client.get(f"/api/v1/sensors/{sid}/reading/image").status_code == 200

        # One step of the last digit lower: the last wheel turning. The value stays, without a
        # rejection, a history row or a review item.
        camera.text = "0012345.5"
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: (view()["reading"]["last"] or {}).get("settling"), timeout=30)
        assert view()["reading"]["last"]["reason"] is None and view()["reading"]["value"] == "12345.6"
        assert client.get("/api/v1/review").json()["total"] == 0
        assert client.get(f"/api/v1/sensors/{sid}/reading-quality").json()["periods"][0]["rejected"] == 0
        assert sent[f"{base}/problem"] == "ok"

        # A counter never goes down: the lower reading is rejected and the value stays.
        camera.text = "0012300.0"
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: (view()["reading"]["last"] or {}).get("reason") == "went down", timeout=30)
        assert view()["reading"]["value"] == "12345.6"
        assert wait_for(lambda: sent.get(f"{base}/problem") == "went_down", timeout=10)
        assert sent[f"{base}/raw"] == "12300.0" and float(sent[f"{base}/accepted"]) < 100

        camera.text = "0012346.1"
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: view()["reading"]["value"] == "12346.1", timeout=30)
        # The counter went up 0.5 kWh: a rate in kW once two accepted readings are far enough apart.
        rt.live[sid].rate_samples[0] = (rt.live[sid].rate_samples[0][0] - 3600, 12345.6)
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: f"{base}/rate" in sent, timeout=30)
        assert 0 < float(sent[f"{base}/rate"]) <= 0.5

        def history():
            return client.get("/api/v1/history", params={"sensor": sid, "limit": 200}).json()["items"]

        # Rows are written right after a value is published, so wait for the latest one.
        assert wait_for(lambda: any(h["published_key"] == "12346.1" for h in history()), timeout=10)
        history = history()
        accepted = [h["published_key"] for h in history if h["published_key"]]
        rejected = [h["probs"]["reason"] for h in history if not h["published_key"]]
        assert accepted[:2] == ["12346.1", "12345.6"] and rejected == ["went down"]

        # Every rejected reading waits in the review queue; the answer says whether the reader misread.
        queue = client.get("/api/v1/review").json()
        assert queue["total"] == 1
        item = queue["items"][0]
        assert item["review_reason"] == "rejected" and item["sensor"]["kind"] == "reading"
        assert item["sensor"]["reading"]["unit"] == "kWh"
        assert client.post(f"/api/v1/review/{item['id']}", json={"action": "confirm"}).status_code == 400
        assert client.post(f"/api/v1/review/{item['id']}", json={"action": "misread", "value": "x"}).status_code == 400
        answer = client.post(f"/api/v1/review/{item['id']}", json={"action": "misread", "value": "12300,04"})
        assert answer.status_code == 200, answer.text
        assert client.get("/api/v1/review").json()["total"] == 0

        # The quality tab: counts per period and per day, the reasons, and what was verified.
        quality = client.get(f"/api/v1/sensors/{sid}/reading-quality").json()
        today = quality["periods"][0]
        assert today["days"] == 1 and today["rejected"] == 1 and today["by_reason"] == {"went down": 1}
        assert today["reads"] == today["accepted"] + 1 and today["reads"] >= 3
        assert len(quality["daily"]) == 30 and quality["daily"][-1]["rejected"] == 1
        assert quality["verified"]["misread_rejected"] == 1 and quality["verified"]["waiting"] == 0
        verified = next(i for i in quality["items"] if i["id"] == item["id"])
        assert verified["read_ok"] is False and verified["correct_value"] == "12300.0"

        # Export to share: only the region of each verified reading, what was read, the answer, CC0.
        export = client.get(f"/api/v1/sensors/{sid}/reading-export")
        assert export.status_code == 200
        assert 'filename="visionstate-readings-power_meter.zip"' in export.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            assert {"README.txt", "LICENSE.txt", "readings.json"} <= set(archive.namelist())
            assert "CC0" in archive.read("LICENSE.txt").decode()
            manifest = json.loads(archive.read("readings.json"))
            assert manifest["settings"]["unit"] == "kWh" and "source" not in manifest["settings"]
            [entry] = manifest["readings"]
            assert (entry["read"], entry["rejected"], entry["answer"], entry["right_value"]) == (
                "0012300.0",
                "went down",
                "misread",
                "12300.0",
            )
            assert Image.open(archive.open(entry["file"])).format == "JPEG"
        assert client.get(f"/api/v1/sensors/{sid}/quality").status_code == 400  # the state sensors' tab

        # "Dismiss all" empties one sensor's part of the queue; the counts and earlier answers stay.
        camera.text = "0012300.0"
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: client.get("/api/v1/review").json()["total"] >= 1, timeout=30)
        camera.text = "0012346.1"
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: (view()["reading"]["last"] or {}).get("reason") is None, timeout=30)
        queue = client.get("/api/v1/review").json()
        assert queue["sensors"] == [{"id": sid, "name": "Power meter", "count": queue["total"]}]
        assert client.post("/api/v1/review/sensors/9999/dismiss").status_code == 404
        dismissed = client.post(f"/api/v1/review/sensors/{sid}/dismiss").json()["dismissed"]
        assert dismissed == queue["total"]
        assert client.get("/api/v1/review").json() == {"total": 0, "items": [], "sensors": []}
        quality = client.get(f"/api/v1/sensors/{sid}/reading-quality").json()
        assert quality["periods"][0]["rejected"] == 1 + dismissed
        assert quality["verified"]["misread_rejected"] == 1 and quality["verified"]["waiting"] == 0

        # A verified reading is kept like a training image, whatever the history limits say.
        rt.set_storage_limits({"history_days": 1, "history_max_gb": 0.000001})
        rt.cleanup_history()
        kept = [h["id"] for h in client.get("/api/v1/history", params={"sensor": sid, "limit": 200}).json()["items"]]
        assert kept == [item["id"]]
        rt.set_storage_limits({})

        # Training endpoints do not apply.
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
    """A counter read as text (OCR): a wrong number of digits is rejected, the export, the preview."""
    camera = CounterCamera(89932)
    reading = {
        "mode": "counter",
        "display": "counter",
        "digits": 7,
        "decimals": 3,
        "unit": "m³",
        "counter_reader": "ocr",
    }
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

        # The export to share holds only the counter's window (plus a margin), not the whole frame.
        # The review item is stored after the rejection is published: wait for it.
        assert wait_for(lambda: client.get("/api/v1/review").json()["total"] == 1, timeout=30)
        item = client.get("/api/v1/review").json()["items"][0]
        client.post(f"/api/v1/review/{item['id']}", json={"action": "read_ok"})
        export = client.get(f"/api/v1/sensors/{sid}/reading-export")
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            [entry] = json.loads(archive.read("readings.json"))["readings"]
            assert entry["answer"] == "read correctly" and entry["rejected"] == "wrong digit count"
            crop = Image.open(archive.open(entry["file"]))
        frame = render_counter(89941)
        box = counter_box(7)
        assert crop.width < frame.width * box["w"] * 1.4 and crop.height < frame.height * box["h"] * 1.4
        assert entry["region"] == "as read"

        # Moving the region later does not change how earlier readings are cut: they keep theirs.
        assert item["probs"]["roi"] == view()["roi"]
        moved = {**box, "x": 0.0, "w": box["w"] / 2}
        assert client.patch(f"/api/v1/sensors/{sid}", json={"roi": moved}).status_code == 200

        def export_entry():
            with zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/sensors/{sid}/reading-export").content)) as archive:
                [entry] = json.loads(archive.read("readings.json"))["readings"]
                return entry, Image.open(archive.open(entry["file"])).size

        assert export_entry() == ({**entry}, crop.size)
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            manifest = json.loads(archive.read("readings.json"))
        assert manifest["reader"] == readers.DEFAULT_READER and manifest["settings"]["counter_reader"] == "ocr"
        # A reading from before the region was kept is cut with the region the sensor has now.
        with client.app.state.runtime.db.session() as s:
            row = s.get(Prediction, item["id"])
            assert row is not None
            row.probs = {k: v for k, v in row.probs.items() if k != "roi"}
        old, size = export_entry()
        assert old["region"] == "current" and size[0] < crop.size[0]
        assert client.patch(f"/api/v1/sensors/{sid}", json={"roi": box}).status_code == 200

        preview = {"source_type": "http", "source": "http://fake", "roi": counter_box(7)}
        good = client.post("/api/v1/preview/read", json={**preview, "reading": reading}).json()
        assert good["text"] == "0089941" and good["value"] == "89.941" and good["wrong_digit_count"] is False
        bad = client.post("/api/v1/preview/read", json={**preview, "reading": {**reading, "digits": 8}}).json()
        assert bad["wrong_digit_count"] is True


@requires_reader
def test_wheel_counter_sensor_flow(settings):
    """A counter read with the wheel reader (the default): turning wheels, the preview, the export."""
    camera = CounterCamera(89949.8)
    reading = {"mode": "counter", "display": "counter", "digits": 7, "decimals": 3, "unit": "m³"}
    body = {
        "name": "Gas meter",
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
        bad = client.post("/api/v1/sensors", json={**body, "reading": {**reading, "counter_reader": "abacus"}})
        assert bad.status_code == 422
        assert client.get("/api/v1/config").json()["counter_readers"] == ["wheels", "ocr"]
        resp = client.post("/api/v1/sensors", json=body)
        assert resp.status_code == 201, resp.text
        sid = resp.json()["id"]

        def view():
            return client.get(f"/api/v1/sensors/{sid}").json()

        # Two wheels almost turned over: the wheels together read 89.950.
        assert wait_for(lambda: view()["reading"]["value"] == "89.950", timeout=60)
        assert view()["reading"]["counter_reader"] == "wheels"
        status = client.get("/api/v1/status").json()
        assert status["wheel_reader"] == readers.DEFAULT_WHEEL_READER and status["wheel_reader_error"] == ""
        assert status["reader"] is None  # only a counter read with the wheel reader: no text reader loaded
        models = client.get("/api/v1/settings").json()
        assert models["wheel_reader"] == readers.DEFAULT_WHEEL_READER
        assert [m["id"] for m in models["wheel_readers"]] == list(readers.WHEEL_READERS)
        assert models["wheel_readers"][0]["installed"] and models["wheel_readers"][0]["license"] == "Apache-2.0"
        image = client.get(f"/api/v1/sensors/{sid}/reading/image")
        assert image.status_code == 200 and Image.open(io.BytesIO(image.content)).width > 400

        preview = {"source_type": "http", "source": "http://fake", "roi": counter_box(7)}
        camera.value = 90099.9
        got = client.post("/api/v1/preview/read", json={**preview, "reading": reading}).json()
        assert got["text"] == "0090100" and got["value"] == "90.100" and got["wrong_digit_count"] is False

        # Shared exports name the wheel reader.
        client.post(f"/api/v1/sensors/{sid}/classify")
        assert wait_for(lambda: view()["reading"]["value"] == "90.100", timeout=30)
        with client.app.state.runtime.db.session() as s:
            row = s.scalars(select(Prediction).where(Prediction.sensor_id == sid)).first()
            assert row is not None
            row.read_ok = True
        with zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/sensors/{sid}/reading-export").content)) as archive:
            manifest = json.loads(archive.read("readings.json"))
        assert manifest["reader"] == readers.DEFAULT_WHEEL_READER
        assert manifest["settings"]["counter_reader"] == "wheels"

        # A sensor export keeps the choice; one made before the wheel reader keeps the text reader.
        exported = client.get(f"/api/v1/sensors/{sid}/export").content

        def imported_reader(content: bytes) -> str:
            new_id = client.post("/api/v1/import", files={"file": ("b.zip", content)}).json()["id"]
            client.patch(f"/api/v1/sensors/{new_id}", json={"enabled": False})
            return client.get(f"/api/v1/sensors/{new_id}").json()["reading"]["counter_reader"]

        assert imported_reader(exported) == "wheels"
        old = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(exported)) as source, zipfile.ZipFile(old, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "manifest.json":
                    content = json.loads(data)
                    del content["sensor"]["reading"]["counter_reader"]
                    data = json.dumps(content).encode()
                target.writestr(name, data)
        assert imported_reader(old.getvalue()) == "ocr"


@requires_reader
def test_a_wheel_reader_that_does_not_load_is_shown(settings, monkeypatch):
    def broken(*_args):
        raise RuntimeError("model file damaged")

    monkeypatch.setattr(readers, "WheelReader", broken)
    body = {
        "name": "Water meter",
        "kind": "reading",
        "source_type": "http",
        "source": "http://fake",
        "roi": counter_box(7),
        "reading": {"mode": "counter", "display": "counter", "digits": 7},
        "interval_s": 1,
    }
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = CounterCamera(1234)
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        assert wait_for(lambda: client.get("/api/v1/status").json()["wheel_reader_error"] == "model file damaged")
        assert client.get("/api/v1/status").json()["wheel_reader"] is None
        assert "model file damaged" in client.get(f"/api/v1/sensors/{sid}").json()["live"]["error"]


@requires_reader
def test_spot_checks_of_accepted_readings(settings):
    camera = DisplayCamera("00500")
    body = {
        "name": "Gas",
        "kind": "reading",
        "source_type": "http",
        "source": "x",
        "interval_s": 3600,
        "debounce": 1,
        "reading": {"spot_rate": 1.0},
    }
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = camera
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["reading"]["value"] == "500", timeout=60)
        assert client.get("/api/v1/review").json()["total"] == 0  # a new value is not a spot check
        client.post(f"/api/v1/sensors/{sid}/classify")  # the same value again: checked at random (here always)
        assert wait_for(lambda: client.get("/api/v1/review").json()["total"] == 1, timeout=30)
        item = client.get("/api/v1/review").json()["items"][0]
        assert item["review_reason"] == "spot_check" and item["published_key"] == "500"
        assert client.post(f"/api/v1/review/{item['id']}", json={"action": "read_ok"}).status_code == 200
        verified = client.get(f"/api/v1/sensors/{sid}/reading-quality").json()["verified"]
        assert verified["right_accepted"] == 1 and verified["misread_accepted"] == 0
