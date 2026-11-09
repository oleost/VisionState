"""Licence plates on object sensors: comparing reads with the household's plates, every plate model
on a real car with a drawn plate, and a sensor that names a car by its plate and reports unknown ones
(docs/PLATE_RECOGNITION_PLAN.md)."""

import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visionstate import plates
from visionstate.db import Sensor
from visionstate.engine.plates import PlatesMixin
from visionstate.engine.plates import identified_by as plates_identified
from visionstate.main import create_app
from visionstate.mqtt import SensorDescriptor, discovery_messages, entity_options
from visionstate.settings import KIND_OBJECTS, PLATE_EVENTS, PLATES, merge_objects, plates_on

from .conftest import ASSETS, MODEL_DIR, requires_detector
from .displays import car_with_plate, licence_plate
from .test_integration import wait_for
from .test_objects import create, settings  # noqa: F401 - fixture

DEFAULT = plates.MODELS[plates.DEFAULT_MODEL]
requires_plates = pytest.mark.skipif(
    not all((MODEL_DIR / f.filename).exists() for f in DEFAULT.files),
    reason=f"Plate models not downloaded; run `python -m visionstate.backbones {MODEL_DIR} --all`",
)


# --- comparing reads with plates --------------------------------------------------------------------


def test_a_plate_is_compared_by_its_letters_and_digits():
    assert plates.normalise("vs-12 345") == "VS12345"
    assert plates.distance("VS12345", "VS 12345") == 0
    # A character a reader confuses costs half; another one a whole step.
    assert plates.distance("VS1234S", "VS12345") == plates.CONFUSED
    assert plates.distance("VS12348", "VS12345") == 1
    # The edge of the plate (a country letter read as part of it, a cut character) costs half too.
    assert plates.distance("FVS12345", "VS12345") == plates.CONFUSED
    assert plates.distance("S12345", "VS12345") == plates.CONFUSED
    assert plates.distance("VS1345", "VS12345") == 1  # inside: a whole step


def test_the_closest_car_wins_and_a_car_may_have_several_plates():
    cars = [{"key": "red", "plate": "AB 12345"}, {"key": "blue", "plate": "XY 99999, CD 55555"}]
    assert plates.match("AB12345", cars) == plates.Match("red", 0.0, "AB12345")
    assert plates.match("CD55555", cars).key == "blue"
    far = plates.match("ZZ00000", cars)
    assert far.key is not None and far.distance > PLATES["match_distance"]
    assert plates.match("AB12345", []).key is None


def test_only_a_read_with_a_digit_and_the_right_length_can_be_a_plate():
    assert plates.looks_like_plate("G756") and plates.looks_like_plate("AB 12345")
    assert not plates.looks_like_plate("EUROPE")  # no digit
    assert not plates.looks_like_plate("7")
    assert not plates.looks_like_plate("12345678901")


# --- a car's look and its plate ----------------------------------------------------------------------

CARS = [{"key": "our_car", "name": "Our car", "plate": "VS 12345"}, {"key": "van", "name": "Van", "plate": "EL 98765"}]


def name_car(det: dict, texts: list[str], look_distance: float, before: dict | None = None) -> dict:
    """One check's plate step on a vehicle box with these reads."""
    reads = [plates.PlateRead((10, 10, 60, 25), t, 0.9, 0.9) for t in texts]
    object.__new__(PlatesMixin)._name_car(det, reads, before or {}, CARS, (0, 0, 100, 100), (100, 100), look_distance)
    return det


def looks_like(key: str) -> dict:
    """A car box its taught look says is ``key`` (what teaching left on it)."""
    return {"key": "car", "box": [0, 0, 1, 1], "label": key, "match": {"id": 1, "label": key, "similarity": 0.9}}


def test_a_car_whose_look_agrees_is_named_by_a_plate_read_a_little_off():
    # One digit wrong: on its own only a candidate, asked again on the next check …
    alone = name_car({"key": "car", "box": [0, 0, 1, 1]}, ["VS12845"], 1.0)
    assert alone["plate"]["state"] == "pending" and "label" not in alone
    # … but the look says it is that car: named at once.
    both = name_car(looks_like("our_car"), ["VS12845"], 1.0)
    assert both["plate"]["state"] == "match" and both["plate"]["car"] == "our_car" and both["plate"]["look"]
    assert both["label"] == "our_car"
    # A missing digit inside the plate (a whole step) too; two steps only when the sensor allows two.
    assert name_car(looks_like("our_car"), ["VS1245"], 1.0)["plate"]["state"] == "match"
    assert name_car(looks_like("our_car"), ["VS12388"], 1.0)["plate"]["state"] != "match"
    assert name_car(looks_like("our_car"), ["VS12388"], 2.0)["plate"]["car"] == "our_car"
    # Set to 0, the look does not help the plate.
    assert name_car(looks_like("our_car"), ["VS12845"], 0.0)["plate"]["state"] == "pending"


def test_a_car_says_whether_it_is_known_by_its_plate_its_look_or_both():
    plate = {"state": "match", "car": "tesla", "text": "EL11111"}
    look = {"id": 1, "label": "tesla", "similarity": 0.9}
    box = {"key": "car", "box": [0, 0, 1, 1], "label": "tesla"}
    assert plates_identified([{**box, "match": look}], "tesla") == "look"  # parked side-on
    assert plates_identified([{**box, "plate": plate}], "tesla") == "plate"
    assert plates_identified([{**box, "plate": plate, "match": look}], "tesla") == "plate_and_look"
    # The surest box wins; a filtered box and another car's box do not count.
    assert plates_identified([{**box, "match": look}, {**box, "plate": plate}], "tesla") == "plate"
    assert plates_identified([{**box, "match": look, "filtered": True}], "tesla") is None
    assert plates_identified([{**box, "label": "our_car", "match": look}], "tesla") is None


def test_a_plate_decides_over_the_look():
    # Read exactly as another car: that car, whatever it looks like.
    other = name_car(looks_like("our_car"), ["EL98765"], 2.0)
    assert other["plate"]["car"] == "van" and other["label"] == "van"
    # A plate far from the car's, read once: the look keeps naming it; read twice the same way, it
    # is another car that looks the same.
    once = name_car(looks_like("our_car"), ["ZX55555"], 1.0)
    assert once["plate"]["state"] == "reading" and once["label"] == "our_car"
    twice = name_car(looks_like("our_car"), ["ZX55555"], 1.0, before=once["plate"])
    assert twice["plate"]["state"] == "unknown" and "label" not in twice


# --- plates seen at an angle ------------------------------------------------------------------------


def on_grey(plate: Image.Image) -> Image.Image:
    background = Image.new("RGB", (plate.width + 60, plate.height + 50), (90, 90, 90))
    background.paste(plate, (30, 25))
    return background


@pytest.mark.parametrize(("turn", "shear"), [(12, 0.0), (-20, 0.0), (0, 0.3), (8, -0.25)])
def test_a_plate_seen_at_an_angle_is_straightened(turn, shear):
    level = on_grey(licence_plate("VS 12345").resize((200, 43)))
    assert plates.straighten(level) is level  # seen straight on: left as it is
    seen = level.rotate(turn, Image.Resampling.BICUBIC, fillcolor=(90, 90, 90))
    if shear:
        seen = seen.transform(seen.size, Image.Transform.AFFINE, (1, shear, -shear * seen.height / 2, 0, 1, 0))
    straight = plates.straighten(seen)
    assert straight is not seen and straight.size == seen.size
    # Straightened, it is level and upright again: straightening it once more changes nothing.
    assert plates.straighten(straight) is straight


def test_plates_are_on_only_for_a_sensor_that_looks_for_a_vehicle():
    on = merge_objects({"classes": ["car"], "plates": {"enabled": True}})
    assert plates_on(on) and on["plates"] == {"enabled": True, "unknown": False, "look_distance": 1.0, "learn": True}
    assert not plates_on(merge_objects({"classes": ["person"], "plates": {"enabled": True}}))
    assert not plates_on(merge_objects({"classes": ["car"]}))


def test_a_sensor_that_reads_plates_has_a_plate_event_entity():
    sensor = SensorDescriptor("drive", "Drive", [], KIND_OBJECTS, [("car", "Car", "mdi:car")], plates=True)
    configs = {topic: payload for topic, payload in discovery_messages("homeassistant", sensor)}
    event = configs["homeassistant/event/visionstate_drive/plate/config"]
    assert event["event_types"] == list(PLATE_EVENTS) and event["state_topic"] == "visionstate/drive/plate"
    assert any(o["id"] == "plate" for o in entity_options(sensor))
    without = SensorDescriptor("drive", "Drive", [], KIND_OBJECTS, [("car", "Car", "mdi:car")])
    assert not any("/event/" in topic for topic, _ in discovery_messages("homeassistant", without))


# --- the models -------------------------------------------------------------------------------------------


def test_every_plate_model_is_pinned_and_described():
    assert DEFAULT.open, "the default must be usable without accepting anything"
    for spec in plates.MODELS.values():
        assert spec.name and spec.description and spec.license and spec.data and spec.source
        assert spec.footprint.ram_mb > 0 and spec.footprint.time_ms > 0 and 1 <= spec.footprint.accuracy <= 5
        for file in spec.files:
            assert re.fullmatch(r"[0-9a-f]{64}", file.sha256) and file.size > 0
            # Pinned: a Hugging Face revision, or a release asset checked by its SHA-256.
            assert "/resolve/" in file.url or "/releases/download/" in file.url


def on_disk(spec):
    return pytest.param(
        spec,
        id=spec.id,
        marks=pytest.mark.skipif(
            not all((MODEL_DIR / f.filename).exists() for f in spec.files),
            reason=f"{spec.id} not downloaded (backbones models --all)",
        ),
    )


def load(spec: plates.PlateModelSpec) -> plates.PlateModel:
    return plates.PlateModel(
        spec,
        plates.Finder(spec.finder, MODEL_DIR / spec.finder.filename),
        plates.PlateReader(spec.reader, MODEL_DIR / spec.reader.filename),
    )


@pytest.mark.parametrize("spec", [on_disk(s) for s in plates.MODELS.values()])
@pytest.mark.parametrize("text", ["VS 12345", "AB 1234", "KY58 YMJ"])
def test_every_plate_model_reads_a_plate_on_a_real_car(spec, text):
    with Image.open(ASSETS / "plate_car.jpg") as photo:
        image = car_with_plate(text, photo)
    reads = plates.read_plates(load(spec), image)
    assert reads and reads[0].text == plates.normalise(text), [r.text for r in reads]


@pytest.mark.parametrize("spec", [on_disk(s) for s in plates.MODELS.values()])
def test_a_car_without_a_readable_plate_reads_no_plate(spec):
    with Image.open(ASSETS / "cat.jpg") as photo:
        assert not plates.read_plates(load(spec), photo.convert("RGB"))


# --- a sensor that reads plates -------------------------------------------------------------------------


class PlateCamera:
    """The CC0 car with a plate of our own text, which a test can change."""

    def __init__(self, text: str):
        self.text = text

    async def grab(self, source_type, source):
        with Image.open(ASSETS / "plate_car.jpg") as photo:
            buf = io.BytesIO()
            car_with_plate(self.text, photo).save(buf, format="JPEG", quality=92)
        return buf.getvalue()

    async def close(self):
        pass


def detections(client, sid):
    return client.get(f"/api/v1/sensors/{sid}").json()["objects"]["detections"]


def plate_of(client, sid) -> dict:
    return next((d.get("plate") or {} for d in detections(client, sid) if d["key"] == "car"), {})


@requires_plates
def test_a_car_is_named_by_its_plate_and_unknown_plates_make_events(settings, monkeypatch):  # noqa: F811
    camera = PlateCamera("VS 12345")
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        sent: list[tuple[str, object]] = []
        original = rt.mqtt.publish

        async def publish(topic, payload, retain=False):
            sent.append((topic, payload))
            await original(topic, payload, retain=retain)

        monkeypatch.setattr(rt.mqtt, "publish", publish)
        monkeypatch.setitem(PLATES, "event_cooldown_s", 0)

        # The household's cars: validated, their keys never clash with an object or another car.
        assert client.post("/api/v1/cars", json={"name": "Car", "plate": "x"}).status_code == 422
        assert client.post("/api/v1/cars", json={"name": "Van", "plate": "AB 1", "parent": "cat"}).status_code == 422
        car = client.post("/api/v1/cars", json={"name": "Car", "plate": "VS 12345"}).json()
        assert car["key"] != "car" and car["parent"] == "car"
        same = client.post("/api/v1/cars", json={"name": "Other", "plate": "vs-12345"})
        assert same.status_code == 400 and "has that plate" in same.json()["detail"]

        sid = create(client, name="Drive", objects={"classes": ["car"], "clear_after_s": 0}, interval_s=1).json()["id"]
        # Off by default: no plate model, no plate entity, the car is no own label.
        sensor = client.get(f"/api/v1/sensors/{sid}").json()
        assert sensor["objects"]["plates"] == {"enabled": False, "unknown": False, "look_distance": 1.0, "learn": True}
        assert rt.plate_model is None and not any(o["id"] == "plate" for o in sensor["ha_entities"])

        objects = {"classes": ["car"], "clear_after_s": 0, "plates": {"enabled": True}}
        assert client.patch(f"/api/v1/sensors/{sid}", json={"objects": objects}).status_code == 200
        assert wait_for(lambda: client.get("/api/v1/status").json()["plates"]["loaded"], timeout=120)
        sensor = client.get(f"/api/v1/sensors/{sid}").json()
        assert any(c["key"] == car["key"] and c.get("car") for c in sensor["objects"]["custom"])
        assert {o["id"] for o in sensor["ha_entities"]} >= {"plate", car["key"]}

        # Read exactly: the car is named at once, and its coming is an event and a history row.
        assert wait_for(lambda: plate_of(client, sid).get("state") == "match", timeout=60)
        assert plate_of(client, sid)["car"] == car["key"] and plate_of(client, sid)["text"] == "VS12345"

        # Its entities say how it is known: by its plate (its look is not taught yet).
        def identified() -> list:
            topic = f"/drive/objects/{car['key']}/attributes"
            return [p.get("identified_by") for t, p in sent if t.endswith(topic) and isinstance(p, dict)]

        assert wait_for(lambda: "plate" in identified(), timeout=30)
        # A sure plate on a car whose look it does not know yet: that box is learned as the car,
        # once (not again at every check while it stays).
        assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["objects"]["taught"] == 1, timeout=60)
        learned = client.get(f"/api/v1/sensors/{sid}/taught").json()["examples"]
        assert [(x["label"], x["origin"]) for x in learned] == [(car["key"], "plate")]
        assert wait_for(lambda: any(t.endswith("/drive/plate") for t, _ in sent), timeout=30)
        events = [p for t, p in sent if t.endswith("/drive/plate")]
        assert events[0]["event_type"] == "known_car" and events[0]["car_name"] == "Car"
        rows = client.get("/api/v1/history", params={"sensor": sid, "event": "plate_seen"}).json()
        assert rows["total"] == 1 and rows["items"][0]["state_key"] == car["key"]
        # Once per visit: the car staying makes no more events.
        before = len(events)
        wait_for(lambda: False, timeout=3)
        assert len([p for t, p in sent if t.endswith("/drive/plate")]) == before

        # Another plate: not the car's, so after two reads an unknown plate; an event only when the
        # sensor reports unknown plates.
        camera.text = "ZX 98765"
        assert wait_for(lambda: plate_of(client, sid).get("state") == "unknown", timeout=60)
        assert plate_of(client, sid)["text"] == "ZX98765"
        assert not any(p.get("event_type") == "unknown_plate" for t, p in sent if t.endswith("/drive/plate"))
        objects["plates"]["unknown"] = True
        assert client.patch(f"/api/v1/sensors/{sid}", json={"objects": objects}).status_code == 200
        assert wait_for(
            lambda: any(
                isinstance(p, dict) and p.get("event_type") == "unknown_plate" and p["plate"] == "ZX98765"
                for t, p in sent
                if t.endswith("/drive/plate")
            ),
            timeout=60,
        )

        # A car renamed keeps its key; forgotten, it is no own label any more.
        renamed = client.put(f"/api/v1/cars/{car['key']}", json={"name": "Our car", "plate": "VS 12345"}).json()
        assert renamed["key"] == car["key"] and renamed["name"] == "Our car"
        # A car can be taught by its look too (a box of this sensor); forgetting it forgets those.
        view = client.get(f"/api/v1/sensors/{sid}").json()
        box = next(d for d in view["objects"]["detections"] if d["key"] == "car")
        body = {"frame_id": view["live"]["frame_id"], "box": box["box"], "detected": "car", "score": box["score"]}
        assert client.post(f"/api/v1/sensors/{sid}/taught", json={**body, "label": car["key"]}).status_code == 201
        assert client.get(f"/api/v1/sensors/{sid}").json()["objects"]["taught"] == 2
        assert client.delete(f"/api/v1/cars/{car['key']}").status_code == 204
        assert client.get(f"/api/v1/sensors/{sid}").json()["objects"]["taught"] == 0
        sensor = client.get(f"/api/v1/sensors/{sid}").json()
        assert not any(c.get("car") for c in sensor["objects"]["custom"])
        assert client.get("/api/v1/cars").json() == []


def test_a_plate_model_without_a_stated_licence_must_be_accepted(settings):  # noqa: F811
    closed = next(m for m in plates.MODELS.values() if not m.open)
    with TestClient(create_app(settings)) as client:
        current = client.get("/api/v1/settings").json()
        assert current["plate_model"] == plates.DEFAULT_MODEL
        listed = {m["id"]: m for m in current["plate_models"]}
        assert listed[plates.DEFAULT_MODEL]["open"] and not listed[closed.id]["open"]
        body = {"backbone": current["backbone"], "plate_model": closed.id}
        refused = client.put("/api/v1/settings", json=body)
        assert refused.status_code == 400 and "accept" in refused.json()["detail"]
        chosen = client.put("/api/v1/settings", json={**body, "plate_model_accepted": True})
        assert chosen.status_code == 200 and chosen.json()["plate_model"] == closed.id


@requires_detector
def test_a_named_vehicle_is_a_car_of_the_household(settings):  # noqa: F811
    """One list of cars: an own label of a vehicle kind becomes a car (keeping its key, boxes and
    entities), a vehicle named while teaching is a car, and a sensor's export takes its cars along."""
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = PlateCamera("VS 12345")
        sid = create(client, name="Drive", objects={"classes": ["car", "dog"], "clear_after_s": 0}, interval_s=1).json()
        sid = sid["id"]
        assert wait_for(lambda: any(d["key"] == "car" for d in detections(client, sid)), timeout=60)
        view = client.get(f"/api/v1/sensors/{sid}").json()
        box = next(d for d in view["objects"]["detections"] if d["key"] == "car")
        body = {"frame_id": view["live"]["frame_id"], "box": box["box"], "detected": "car", "score": box["score"]}

        # Named while teaching (plates are off here): a car of the household, without a plate,
        # an entity of this sensor because its look was taught here.
        taught = client.post(f"/api/v1/sensors/{sid}/taught", json={**body, "new_label": "Tesla"})
        assert taught.status_code == 201, taught.text
        cars = client.get("/api/v1/cars").json()
        assert [(c["name"], c["plate"]) for c in cars] == [("Tesla", "")]
        assert cars[0]["taught"] == [{"sensor": sid, "name": "Drive", "boxes": 1}]
        tesla = cars[0]["key"]
        view = client.get(f"/api/v1/sensors/{sid}").json()
        assert any(c["key"] == tesla and c.get("car") for c in view["objects"]["custom"])
        assert tesla in {o["id"] for o in view["ha_entities"]}
        # Its plates added later (several: one car, more than one plate).
        assert (
            client.put(f"/api/v1/cars/{tesla}", json={"name": "Tesla", "plate": "EL 11111, EL 22222"}).status_code
            == 200
        )

        # An own label of a vehicle kind from before cars were shared moves into the list with its
        # key; one of another kind stays the sensor's.
        with rt.db.session() as s:
            row = s.get(Sensor, sid)
            row.objects = {
                **row.objects,
                "custom": [
                    {"key": "our_car", "name": "Our car", "parent": "car"},
                    {"key": "rex", "name": "Rex", "parent": "dog"},
                ],
            }
        assert rt.adopt_car_labels() == [sid]
        assert rt.adopt_car_labels() == []  # once
        assert [c["key"] for c in client.get("/api/v1/cars").json()] == [tesla, "our_car"]
        with rt.db.session() as s:
            assert [c["key"] for c in merge_objects(s.get(Sensor, sid).objects)["custom"]] == ["rex"]

        # Exported, the sensor takes the car taught on it along; imported here, it is the same car.
        exported = client.get(f"/api/v1/sensors/{sid}/export")
        imported = client.post("/api/v1/import", files={"file": ("b.zip", exported.content, "application/zip")})
        assert imported.status_code == 201, imported.text
        copy = client.get(f"/api/v1/sensors/{imported.json()['id']}").json()
        assert copy["objects"]["taught"] == 1 and any(c["key"] == tesla for c in copy["objects"]["custom"])
        assert len(client.get("/api/v1/cars").json()) == 2


def test_a_car_overlapping_another_vehicle_is_not_learned_from():
    from visionstate.engine.plates import _overlaps_vehicle

    car = {"key": "car", "box": [0.1, 0.1, 0.5, 0.5]}
    assert not _overlaps_vehicle(car, [car, {"key": "truck", "box": [0.6, 0.1, 0.9, 0.5]}])
    assert _overlaps_vehicle(car, [car, {"key": "truck", "box": [0.4, 0.3, 0.9, 0.6]}])
    assert not _overlaps_vehicle(car, [car, {"key": "person", "box": [0.2, 0.2, 0.3, 0.6]}])
