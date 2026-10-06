"""Teaching object sensors: matching taught boxes, own labels, missed boxes and the API."""

import time

import numpy as np
from fastapi.testclient import TestClient

from visionstate import teach
from visionstate.engine import ObjectTrack, SensorConfig, update_tracks
from visionstate.main import create_app
from visionstate.mqtt import main_entities
from visionstate.settings import KIND_OBJECTS, NONE_LABEL, TEACH, active_custom, merge_objects

from .conftest import requires_detector
from .test_integration import wait_for
from .test_objects import PhotoCamera, create, settings  # noqa: F401 - fixture

# --- matching (no models) ---------------------------------------------------------------------


def unit(*values: float) -> np.ndarray:
    v = np.array(values, dtype=np.float32)
    return v / np.linalg.norm(v)


def index(entries: list[tuple[str, str | None, np.ndarray]], parents=None) -> teach.TaughtIndex:
    """A taught index from (label, detected, vector) entries."""
    return teach.build(
        "test",
        list(range(1, len(entries) + 1)),
        [e[0] for e in entries],
        [e[1] for e in entries],
        np.stack([e[2] for e in entries]),
        parents or {},
    )


STATUE = unit(1, 0, 0, 0)
PERSON = unit(0, 1, 0, 0)
OUR_CAR = unit(0, 0, 1, 0)
OTHER_CAR = unit(0, 0, 0.6, 0.8)


def det(key: str, score: float = 0.9, box=(0.1, 0.1, 0.3, 0.5)) -> dict:
    return {"key": key, "score": score, "box": list(box)}


def test_nearest_needs_a_clear_close_match():
    idx = index([(NONE_LABEL, "person", STATUE), ("person", "person", PERSON)])
    match = teach.nearest(idx, STATUE, TEACH["match_similarity"])
    assert match == teach.Match(NONE_LABEL, 1.0, 1)
    # Far from everything taught: the detector decides.
    assert teach.nearest(idx, unit(1, 1, 1, 1), TEACH["match_similarity"]) is None
    # Equally close to two labels: the detector decides.
    both = index([(NONE_LABEL, "person", STATUE), ("person", "person", STATUE)])
    assert teach.nearest(both, STATUE, TEACH["match_similarity"]) is None


def test_apply_filters_relabels_and_gives_own_labels():
    parents = {"our_car": "car"}
    idx = index(
        [
            (NONE_LABEL, "person", STATUE),
            ("person", "person", PERSON),
            ("our_car", "car", OUR_CAR),
            ("cat", "dog", unit(0.5, 0.5, 0.5, 0.5)),
        ],
        parents,
    )
    found = [det("person"), det("person", 0.8), det("car"), det("car", 0.7), det("dog")]
    vectors = np.stack([STATUE, PERSON, OUR_CAR, OTHER_CAR, unit(0.5, 0.5, 0.5, 0.5)])
    checked, rescue = teach.plan(found, [], idx)
    assert sorted(checked) == [0, 1, 2, 3, 4] and rescue == []
    by_index = {i: v for i, v in zip(range(5), vectors, strict=True)}
    result = teach.apply(
        found,
        checked,
        np.stack([by_index[i] for i in checked]),
        [],
        np.zeros((0, 4)),
        idx,
        ["person", "car", "cat"],
        parents,
    )
    statue, person, our_car, other_car, cat = result
    assert statue["filtered"] and statue["match"]["label"] == NONE_LABEL
    assert "filtered" not in person and "match" not in person  # taught as right: unchanged
    assert our_car["label"] == "our_car" and our_car["key"] == "car"
    assert "label" not in other_car  # not close enough to the taught car
    assert cat["key"] == "cat" and cat["was"] == "dog"
    assert found[0].get("filtered") is None  # the input is not changed


def toward(target: np.ndarray, other: np.ndarray, similarity: float) -> np.ndarray:
    """A unit vector with exactly ``similarity`` to ``target`` (``other`` orthogonal to it)."""
    return (similarity * target + np.sqrt(1 - similarity**2) * other).astype(np.float32)


def test_an_object_that_stays_keeps_its_taught_answer():
    """A parked car is "Our car" at 0.90 similarity; in the next light it is 0.84 — still ours."""
    parents = {"our_car": "car"}
    idx = index([("our_car", "car", OUR_CAR)], parents)
    side = unit(0, 0, 0, 1)
    here, elsewhere = (0.1, 0.1, 0.3, 0.5), (0.6, 0.5, 0.9, 0.9)

    def check(similarity, box, previous=None):
        found = [det("car", box=box)]
        return teach.apply(
            found,
            [0],
            np.stack([toward(OUR_CAR, side, similarity)]),
            [],
            np.zeros((0, 4)),
            idx,
            ["car"],
            parents,
            previous,
        )[0]

    first = check(0.90, here)
    assert first["label"] == "our_car" and "kept" not in first
    assert "label" not in check(0.84, here)  # a new object needs match_similarity
    kept = check(0.84, here, [first])
    assert kept["label"] == "our_car" and kept["kept"]
    assert check(0.84, (0.11, 0.1, 0.31, 0.5), [kept])["label"] == "our_car"  # and it goes on, also moved a bit
    assert "label" not in check(0.84, elsewhere, [first])  # another place: another object
    assert "label" not in check(0.70, here, [first])  # clearly something else now


def test_a_box_that_may_be_an_own_label_is_asked_about():
    parents = {"our_car": "car"}
    idx = index([("our_car", "car", OUR_CAR)], parents)
    side = unit(0, 0, 0, 1)

    def check(similarity, key="car"):
        found = [det(key)]
        return teach.apply(
            found,
            [0],
            np.stack([toward(OUR_CAR, side, similarity)]),
            [],
            np.zeros((0, 4)),
            idx,
            ["car", "dog"],
            parents,
        )[0]

    assert check(0.90)["label"] == "our_car" and "ask" not in check(0.90)  # clear: no question
    asked = check(0.80)
    assert "label" not in asked and asked["ask"]["label"] == "our_car" and asked["ask"]["similarity"] == 0.8
    assert "ask" not in check(0.70)  # clearly not ours: no question either
    assert "ask" not in check(0.80, key="dog")  # a dog is never "Our car"
    assert teach.unsure_for(asked, "our_car") and not teach.counts_for(asked, "our_car")


def test_apply_filters_a_class_the_sensor_no_longer_has():
    idx = index([("cat", "dog", STATUE)])
    result = teach.apply([det("dog")], [0], np.stack([STATUE]), [], np.zeros((0, 4)), idx, ["dog"], {})
    assert result[0]["filtered"]


def test_unsure_boxes_are_rescued_only_when_a_missed_box_was_taught():
    found = [det("car", box=(0.6, 0.1, 0.9, 0.5))]
    candidates = [det("person", 0.3), det("truck", 0.4, box=(0.6, 0.1, 0.9, 0.5))]  # the truck is the counted car
    without = index([("person", "person", PERSON)])
    assert teach.plan(found, candidates, without) == ([], [])
    missed = index([("person", None, PERSON)])
    checked, rescue = teach.plan(found, candidates, missed)
    assert checked == [] and rescue == [candidates[0]]
    result = teach.apply(found, checked, np.zeros((0, 4)), rescue, np.stack([PERSON]), missed, ["person", "car"], {})
    assert result[1]["key"] == "person" and result[1]["rescued"]
    # Not close enough for an unsure box (stricter than for a counted one).
    near = unit(0.52, 1, 0, 0)  # similarity ~0.89
    assert TEACH["match_similarity"] <= float(near @ PERSON) < TEACH["rescue_similarity"]
    result = teach.apply(found, checked, np.zeros((0, 4)), rescue, np.stack([near]), missed, ["person", "car"], {})
    assert len(result) == 1


def test_plan_compares_only_taught_classes_and_bounds_the_work():
    idx = index([(NONE_LABEL, "person", STATUE)])
    found = [det("dog")] + [det("person", 0.5 + i / 100) for i in range(TEACH["max_checked"] + 3)]
    checked, _ = teach.plan(found, [], idx)
    assert len(checked) == TEACH["max_checked"] and 0 not in checked
    assert checked[0] == len(found) - 1  # most certain first


def test_tracks_skip_filtered_boxes_and_count_own_labels():
    tracks: dict[str, ObjectTrack] = {}
    found = [
        {**det("person"), "filtered": True},
        {**det("car"), "label": "our_car"},
        det("car"),
    ]
    update_tracks(tracks, found, ["person", "car", "our_car"], 1, 0, now=1.0)
    assert not tracks["person"].on
    assert tracks["car"].count == 2 and tracks["our_car"].count == 1


def test_own_labels_are_entities_while_their_class_is_selected():
    objects = merge_objects(
        {
            "classes": ["car"],
            "custom": [
                {"key": "our_car", "name": "Our car", "parent": "car"},
                {"key": "rex", "name": "Rex", "parent": "dog"},
            ],
        }
    )
    assert [c["key"] for c in active_custom(objects)] == ["our_car"]
    cfg = SensorConfig(
        id=1,
        slug="drive",
        name="Drive",
        source_type="http",
        source="x",
        roi=None,
        interval_s=10,
        threshold=0.6,
        debounce=1,
        enabled=True,
        states=[],
        kind=KIND_OBJECTS,
        objects=objects,
    )
    assert cfg.object_keys == ["car", "our_car"] and cfg.parents == {"our_car": "car"}
    entities = [entity for _, entity in main_entities(cfg.descriptor)]
    assert "binary_sensor.drive_our_car" in entities and "sensor.drive_our_car_count" in entities
    assert not any("rex" in e for e in entities)


class FakeEmbedder:
    """Every image gets the same vector: enough to see which taught boxes an index holds."""

    class spec:  # noqa: N801 - mimics BackboneSpec
        id = "fake"

    def embed(self, images):
        return np.stack([unit(1, 0, 0, 0) for _ in images])


def test_an_index_built_before_an_own_label_existed_is_rebuilt(settings):  # noqa: F811
    """A check that loaded its settings just before a new own label was added must not keep an
    index without it (the label would never be used until a restart)."""
    import asyncio

    from PIL import Image

    from visionstate.db import Database, Sensor
    from visionstate.engine import Runtime

    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.init()
    rt = Runtime(settings, db)
    rt.embedder = FakeEmbedder()
    rex = {"key": "rex", "name": "Rex", "parent": "dog"}
    with db.session() as s:
        row = Sensor(slug="beach", name="Beach", kind=KIND_OBJECTS, source_type="http", source="x", interval_s=5,
                     threshold=0.6, debounce=1, objects={"classes": ["dog"], "custom": [rex]})  # fmt: skip
        s.add(row)
        s.flush()
        sid = row.id
        stale = SensorConfig.from_row(row)
    stale.objects = merge_objects({"classes": ["dog"]})  # as loaded before the label was made
    rt.add_object_example(sid, Image.new("RGB", (64, 64)), [0.1, 0.1, 0.5, 0.5], "rex", "dog", 0.9, "live")
    rt.taught_changed(sid)
    assert asyncio.run(rt.taught_index(stale)) is None  # Rex means nothing to it
    current = rt.load_sensor(sid)
    index = asyncio.run(rt.taught_index(current))
    assert index is not None and index.labels == ["rex"]
    # A box taught while an index was being built: the next check rebuilds.
    rt.add_object_example(sid, Image.new("RGB", (64, 64)), [0.5, 0.5, 0.9, 0.9], NONE_LABEL, "dog", 0.7, "live")
    rt._taught_generation[sid] += 1  # what taught_changed does, without dropping the index
    assert asyncio.run(rt.taught_index(current)).labels == ["rex", NONE_LABEL]
    db.engine.dispose()


# --- the API, with the real detector and backbone on CC0 photos ----------------------------


def sensor(client, sid):
    return client.get(f"/api/v1/sensors/{sid}").json()


def live(client, sid):
    return {o["key"]: o for o in sensor(client, sid)["objects"]["live"]}


def teach_box(client, sid, detection, frame_id, **fields):
    body = {"frame_id": frame_id, "box": detection["box"], "detected": detection["key"], "score": detection["score"]}
    return client.post(f"/api/v1/sensors/{sid}/taught", json={**body, **fields})


@requires_detector
def test_teaching_filters_boxes_and_adds_own_labels(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = PhotoCamera("beach")
        sid = create(client, objects={"classes": ["dog", "person"], "clear_after_s": 0}, interval_s=1).json()["id"]
        assert wait_for(lambda: live(client, sid)["person"]["on"] and live(client, sid)["dog"]["on"], timeout=60)
        view = sensor(client, sid)
        assert view["objects"]["taught"] == 0 and view["objects"]["taught_keys"] == []
        frame_id = view["live"]["frame_id"]
        person = next(d for d in view["objects"]["detections"] if d["key"] == "person")
        dog = max((d for d in view["objects"]["detections"] if d["key"] == "dog"), key=lambda d: d["score"])
        dogs = live(client, sid)["dog"]["count"]

        # Validation.
        assert teach_box(client, sid, person, "gone", label=NONE_LABEL).status_code == 409
        assert teach_box(client, sid, person, frame_id, label="unicorn").status_code == 400
        assert teach_box(client, sid, person, frame_id, label="car").status_code == 400  # not one of its objects
        missed = {"frame_id": frame_id, "box": person["box"], "detected": None, "label": NONE_LABEL}
        assert client.post(f"/api/v1/sensors/{sid}/taught", json=missed).status_code == 400
        assert teach_box(client, sid, person, frame_id, new_label="Postman", parent="car").status_code == 400

        # "Not a person": the person is filtered away, still shown, and kept in the history once.
        resp = teach_box(client, sid, person, frame_id, label=NONE_LABEL)
        assert resp.status_code == 201, resp.text
        assert wait_for(lambda: not live(client, sid)["person"]["on"], timeout=30)
        view = sensor(client, sid)
        assert view["objects"]["taught"] == 1 and view["objects"]["taught_keys"] == ["person"]
        shown = next(d for d in view["objects"]["detections"] if d["key"] == "person")
        assert shown["filtered"] and shown["match"]["similarity"] >= TEACH["match_similarity"]

        def filtered_rows():
            return [h for h in client.get(f"/api/v1/sensors/{sid}/history").json() if h["published_key"] == "filtered"]

        assert wait_for(lambda: len(filtered_rows()) == 1, timeout=10)

        # An own label for one dog: its own entities, and only that dog counts for it.
        resp = teach_box(client, sid, dog, sensor(client, sid)["live"]["frame_id"], new_label="Rex")
        assert resp.status_code == 201, resp.text
        assert resp.json()["label"] == "rex"
        assert "binary_sensor.beach_rex" in sensor(client, sid)["entity_ids"]
        assert wait_for(lambda: live(client, sid).get("rex", {}).get("on"), timeout=30)
        assert live(client, sid)["rex"]["count"] == 1 and live(client, sid)["rex"]["parent"] == "dog"
        assert live(client, sid)["dog"]["count"] == dogs  # Rex is still a dog
        taught = client.get(f"/api/v1/sensors/{sid}/taught").json()
        assert [x["label"] for x in taught["examples"]] == ["rex", NONE_LABEL]
        assert taught["labels"] == [{"key": "rex", "name": "Rex", "parent": "dog", "active": True}]
        assert len(taught["filtered"]) == 1

        # Turned off, the detector alone decides; saving the settings keeps the own labels.
        patch = {"objects": {"classes": ["dog", "person"], "clear_after_s": 0, "min_size": 0, "use_taught": False}}
        assert client.patch(f"/api/v1/sensors/{sid}", json=patch).status_code == 200
        assert wait_for(lambda: live(client, sid)["person"]["on"] and not live(client, sid)["rex"]["on"], timeout=30)
        patch["objects"]["use_taught"] = True
        client.patch(f"/api/v1/sensors/{sid}", json=patch)
        assert wait_for(lambda: not live(client, sid)["person"]["on"], timeout=30)

        # Export and import take what was taught along.
        exported = client.get(f"/api/v1/sensors/{sid}/export")
        imported = client.post("/api/v1/import", files={"file": ("b.zip", exported.content, "application/zip")})
        assert imported.status_code == 201, imported.text
        copy = sensor(client, imported.json()["id"])
        assert copy["objects"]["taught"] == 2 and [c["key"] for c in copy["objects"]["custom"]] == ["rex"]
        assert wait_for(lambda: live(client, copy["id"]).get("rex", {}).get("on"), timeout=60)
        client.delete(f"/api/v1/sensors/{copy['id']}")

        # Removing the label removes its boxes and entities; forgetting all clears the rest.
        assert client.delete(f"/api/v1/sensors/{sid}/labels/rex").status_code == 204
        view = sensor(client, sid)
        assert "rex" not in live(client, sid) and view["objects"]["taught"] == 1
        assert not any("rex" in e for e in view["entity_ids"])
        assert client.delete(f"/api/v1/sensors/{sid}/labels/rex").status_code == 404
        assert client.delete(f"/api/v1/sensors/{sid}/taught").status_code == 204
        assert sensor(client, sid)["objects"]["taught"] == 0
        assert wait_for(lambda: live(client, sid)["person"]["on"], timeout=30)


@requires_detector
def test_a_missed_box_is_found_again_among_unsure_boxes(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = PhotoCamera("beach")
        # So strict that the detector counts nothing on this photo (its best boxes are ~0.94).
        sid = create(
            client, objects={"classes": ["dog", "person"], "clear_after_s": 0}, interval_s=1, threshold=0.99
        ).json()["id"]
        # The frame of a check (last_run is set when a check starts, before its frame is taken).
        assert wait_for(lambda: sensor(client, sid)["live"]["frame_id"], timeout=60)
        assert not live(client, sid)["person"]["on"]
        frame_id = sensor(client, sid)["live"]["frame_id"]
        # The person's box, as the user would draw it (from the wizard preview, which sees everything).
        preview = client.post("/api/v1/preview/detect", json={"source_type": "http", "source": "x", "roi": None}).json()
        person = next(d for d in preview["detections"] if d["key"] == "person")
        drawn = {"frame_id": frame_id, "box": person["box"], "detected": None, "label": "person"}
        resp = client.post(f"/api/v1/sensors/{sid}/taught", json=drawn)
        assert resp.status_code == 201, resp.text
        assert resp.json()["seen"] is True  # the detector does see something there
        assert wait_for(lambda: live(client, sid)["person"]["on"], timeout=30)
        rescued = [d for d in sensor(client, sid)["objects"]["detections"] if d.get("rescued")]
        assert [d["key"] for d in rescued] == ["person"]
        assert not live(client, sid)["dog"]["on"]  # the dogs are not like the person


@requires_detector
def test_an_unsure_own_label_is_asked_about_and_the_answer_taught(settings, monkeypatch):  # noqa: F811
    """A dog that may be Rex goes to the review queue; "yes" teaches it as Rex, "no" as a dog."""
    with TestClient(create_app(settings)) as client:
        client.app.state.runtime.grabber = PhotoCamera("beach")
        sid = create(client, objects={"classes": ["dog"], "clear_after_s": 0}, interval_s=1).json()["id"]
        assert wait_for(lambda: live(client, sid)["dog"]["on"], timeout=60)
        view = sensor(client, sid)
        dog = max((d for d in view["objects"]["detections"] if d["key"] == "dog"), key=lambda d: d["score"])
        assert teach_box(client, sid, dog, view["live"]["frame_id"], new_label="Rex").status_code == 201
        assert wait_for(lambda: live(client, sid).get("rex", {}).get("on"), timeout=30)

        # Nothing is ever this sure: Rex's own box is now "may be Rex". Rex stays on (unsure holds
        # it), and the sensor asks once — not again while the question waits.
        monkeypatch.setitem(TEACH, "match_similarity", 1.01)
        monkeypatch.setitem(TEACH, "keep_similarity", 1.01)

        def questions():
            return [i for i in client.get("/api/v1/review").json()["items"] if i["review_reason"] == "ask"]

        assert wait_for(lambda: len(questions()) == 1, timeout=30)
        item = questions()[0]
        assert item["state_key"] == "rex" and item["probs"]["ask"]["detected"] == "dog"
        assert item["sensor"]["labels"] == [{"key": "rex", "name": "Rex", "parent": "dog"}]
        assert live(client, sid)["rex"]["on"]
        time.sleep(2)  # a few more checks
        assert len(questions()) == 1

        def taught():
            return [x["label"] for x in client.get(f"/api/v1/sensors/{sid}/taught").json()["examples"]]

        assert client.post(f"/api/v1/review/{item['id']}", json={"action": "maybe"}).status_code == 400

        def after_a_check():
            # A check reads the new taught box (Windows can not delete a file while it is read).
            start = sensor(client, sid)["live"]["last_run"]
            assert wait_for(lambda: sensor(client, sid)["live"]["last_run"] > start + 0.5, timeout=30)

        assert client.post(f"/api/v1/review/{item['id']}", json={"action": "yes"}).status_code == 200
        assert sorted(taught()) == ["rex", "rex"] and questions() == []
        after_a_check()
        # Answered again: the first answer's box is replaced, not added to.
        assert client.post(f"/api/v1/review/{item['id']}", json={"action": "no"}).status_code == 200
        assert sorted(taught()) == ["dog", "rex"]
        after_a_check()
        assert client.post(f"/api/v1/review/{item['id']}", json={"action": "skip"}).status_code == 200
        assert taught() == ["rex"]
