"""Recognising people by their face: the face pipeline on real photos, matching, and the sensor flow.

The photos are NASA portraits (public domain, tests/assets/README.md): Suni Williams in 2006 and
2018, Butch Wilmore, the two of them together, and a crew of nine with both of them in it.
"""

import asyncio
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageFilter
from sqlalchemy import delete, func, select

from visionstate import detectors, faces
from visionstate.db import FaceEmbedding
from visionstate.main import create_app
from visionstate.settings import FACE_UNKNOWN_KEY, FACES, merge_objects

from .conftest import ASSETS, MODEL_DIR, requires_faces
from .test_integration import wait_for
from .test_objects import PhotoCamera, create, settings  # noqa: F401 - fixture

# --- geometry and matching (no models) ---------------------------------------------------------


FRAME = (640, 480)  # the frame the drawn faces are in


def face_at(points, score=0.9) -> faces.Face:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return faces.Face((min(xs) - 10, min(ys) - 20, max(xs) + 10, max(ys) + 10), tuple(points), score)


def test_align_puts_the_five_points_on_the_template():
    # The template turned by 20°, scaled by 2.5 and moved: aligning must undo exactly that.
    angle = np.radians(20)
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    points = (faces.TEMPLATE_112 @ rot.T) * 2.5 + np.array([200, 150])
    m = faces.similarity_transform(points, faces.TEMPLATE_112)
    mapped = points @ m[:, :2].T + m[:, 2]
    assert np.allclose(mapped, faces.TEMPLATE_112, atol=1e-6)
    # Drawn dots at the points land on the template's points in the aligned face.
    image = Image.new("RGB", (500, 500))
    for x, y in points:
        image.paste((255, 255, 255), (round(x) - 5, round(y) - 5, round(x) + 6, round(y) + 6))
    aligned = np.asarray(faces.align(image, face_at([tuple(p) for p in points])).convert("L"))
    for x, y in faces.TEMPLATE_112:
        assert aligned[round(y), round(x)] > 128


def test_even_light_spreads_a_dark_face_over_the_full_range():
    # A face in front of a bright sky: dark and flat. Evened out it uses the full range, its
    # colours kept (a red face stays red), at the same size.
    dark = Image.effect_noise((112, 112), 20).point(lambda v: 20 + v // 8).convert("RGB")
    dark = Image.merge("RGB", (dark.getchannel(0).point(lambda v: v + 10), dark.getchannel(1), dark.getchannel(2)))
    even = faces.even_light(dark)
    low, high = even.convert("L").getextrema()
    assert even.size == dark.size and low < 40 and high > 215
    r, g, b = (np.asarray(even.getchannel(c), dtype=np.float32).mean() for c in range(3))
    assert r > g and r > b


def test_turn_eye_distance_and_quality_gate():
    straight = face_at([(40, 50), (80, 50), (60, 70), (45, 90), (75, 90)])  # 60 px wide
    half = face_at([(40, 50), (80, 50), (75, 70), (45, 90), (75, 90)])  # turned, but can be named
    sideways = face_at([(40, 50), (80, 50), (85, 70), (45, 90), (75, 90)])
    assert straight.eye_distance == 40 and straight.width == 60
    assert faces.turn(straight) == 0 and faces.turn(sideways) > FACES["max_turn"]
    sharp = Image.effect_noise((112, 112), 60).convert("RGB")
    assert faces.problem(straight, sharp, 30, FRAME) is None
    assert faces.problem(straight, sharp, 61, FRAME) == "small"  # the face's width, not its eyes
    assert faces.problem(half, sharp, 30, FRAME) is None
    assert faces.problem(sideways, sharp, 30, FRAME) == "turned"
    # A profile: eyes close together for the width of the face is turned, however big it is.
    profile = faces.Face((10, 10, 70, 80), ((40, 40), (44, 40), (30, 55), (38, 65), (42, 65)), 0.9)
    assert faces.problem(profile, sharp, 30, FRAME) == "turned"
    assert faces.problem(profile, sharp, 61, FRAME) == "small"
    assert faces.problem(face_at(straight.points, score=0.5), sharp, 30, FRAME) == "unsure"
    assert faces.problem(straight, Image.new("RGB", (112, 112), (90, 90, 90)), 30, FRAME) == "blurry"
    # Matching nobody makes a stranger only of a face seen clearly: big enough and nearly straight.
    assert faces.clearly_seen(straight, 30) and not faces.clearly_seen(straight, 45)
    assert not faces.clearly_seen(half, 30)
    assert not faces.clearly_seen(face_at(straight.points, score=0.8), 30)
    # Cut by the edge of the frame (a person leaning into the camera): the eyes are guessed.
    assert faces.problem(straight, sharp, 30, (200, 85)) == "cut"
    top = faces.Face((143, 0, 258, 74), ((174, -4), (225, 0), (198, 17), (175, 35), (214, 38)), 0.79)
    assert faces.cut_off(top, (480, 640)) and not faces.cut_off(straight, FRAME)


def test_head_region_is_the_top_of_a_standing_person_and_all_of_a_close_up():
    standing = faces.head_region((0.4, 0.1, 0.5, 0.9), 1000, 1000)  # 100 × 800 px
    assert standing[1] < 100 and standing[3] < 100 + 1.3 * 100 + 20
    close_up = faces.head_region((0.2, 0.2, 0.6, 0.6), 1000, 1000)  # 400 × 400 px: taken whole
    assert close_up[3] >= 600


def unit(*values):
    v = np.array(values, dtype=np.float32)
    return v / np.linalg.norm(v)


def index(entries) -> faces.FaceIndex:
    return faces.FaceIndex(
        "test", list(range(1, len(entries) + 1)), [e[0] for e in entries], np.stack([e[1] for e in entries])
    )


LIMITS = faces.RECOGNISERS[faces.DEFAULT_RECOGNISER].thresholds


def test_a_person_is_the_mean_of_their_closest_faces():
    kari, eva = unit(1, 0, 0), unit(0, 1, 0)
    # One of Kari's faces was taught as Eva by mistake: Eva's score is her mean, not that face.
    idx = index([("kari", kari), ("kari", kari), ("kari", kari), ("eva", eva), ("eva", eva), ("eva", kari)])
    who = faces.identify(idx, kari)
    assert who.label == "kari" and who.similarity == 1.0 and who.margin == pytest.approx(1 - 1 / 3, abs=1e-3)
    assert faces.decide(who, LIMITS) == "match"
    assert faces.decide(faces.identify(idx, unit(0, 0, 1)), LIMITS) == "unknown"
    assert faces.decide(faces.Identity(None, 0, 0, None), LIMITS) == "unknown"
    between = faces.Identity("kari", (LIMITS["ask_similarity"] + LIMITS["match_similarity"]) / 2, 0.3, 1)
    assert faces.decide(between, LIMITS) == "ask"
    # Close to two people at once: not a match.
    assert faces.decide(faces.Identity("kari", 0.9, 0.01, 1), LIMITS) == "ask"
    # A person who stays keeps their name down to keep_similarity.
    staying = faces.Identity("kari", LIMITS["keep_similarity"] + 0.01, 0.3, 1)
    assert faces.decide(staying, LIMITS, kept="kari") == "match"
    assert faces.decide(staying, LIMITS) != "match"


def test_only_a_clearly_seen_face_that_matches_nobody_is_a_stranger():
    from visionstate.engine.faces import FacesMixin

    idx = index([("kari", unit(1, 0, 0))])
    box = [0.1, 0.1, 0.5, 0.9]

    def check(clear: bool, before: list[dict]) -> dict:
        det = {"key": "person", "box": box, "face": {"problem": None}}
        face_settings = {"ask_unknown": False}
        FacesMixin._name_face(None, det, before, idx, unit(0, 0, 1), LIMITS, face_settings, clear)  # type: ignore[arg-type]
        return det

    # Turned or small: matching nobody may be the angle, so it never counts towards a stranger.
    det = check(False, [])
    assert det["face"]["state"] == "unclear" and "label" not in det
    det = check(False, [det])
    assert "label" not in det
    # Seen clearly twice: a stranger, who stays one while turned away.
    det = check(True, [check(True, [])])
    assert det["label"] == FACE_UNKNOWN_KEY
    assert check(False, [det])["label"] == FACE_UNKNOWN_KEY


def test_every_face_model_is_pinned_and_described():
    for spec in (faces.DETECTOR, *faces.RECOGNISERS.values()):
        assert "/resolve/" in spec.url and len(spec.sha256) == 64 and spec.size > 0
        assert spec.description and spec.license and spec.data and spec.source
        assert not spec.bundled  # downloaded only when a sensor turns faces on
    for spec in faces.RECOGNISERS.values():
        t = spec.thresholds
        assert t["ask_similarity"] < t["keep_similarity"] < t["match_similarity"] < t["sure_similarity"]
        assert spec.footprint.ram_mb > 0 and spec.footprint.time_ms > 0
    ranks = [s.footprint.accuracy for s in sorted(faces.RECOGNISERS.values(), key=lambda s: s.footprint.time_ms)]
    assert ranks == sorted(ranks)


# --- the models on real photos ---------------------------------------------------------------------


def on_disk(spec):
    return pytest.param(
        spec,
        id=spec.id,
        marks=pytest.mark.skipif(
            not all((MODEL_DIR / s.filename).exists() for s in (spec, faces.DETECTOR)),
            reason=f"{spec.id} not downloaded (backbones models --all)",
        ),
    )


def photo_faces(recogniser, name):
    """(x of the person box, embedding, face) of every usable face in a photo."""
    image = Image.open(ASSETS / f"{name}.jpg").convert("RGB")
    spec = detectors.DETECTORS[detectors.DEFAULT_DETECTOR]
    people = [d for d in detectors.Detector(spec, MODEL_DIR / spec.filename).detect(image, 0.6) if d.key == "person"]
    finder = faces.FaceDetector(faces.DETECTOR, MODEL_DIR / faces.DETECTOR.filename)
    regions = [faces.head_region(p.box, image.width, image.height) for p in people]
    result = []
    for person, region, face in zip(people, regions, finder.detect([image.crop(r) for r in regions]), strict=True):
        assert face is not None, f"no face in a person box of {name}"
        moved = face.moved(region[0], region[1], 1.0)
        aligned = faces.align(image, moved)
        assert faces.problem(moved, aligned, 30, image.size) is None
        result.append((person.box[0], recogniser.embed([aligned])[0], moved))
    return result


@pytest.mark.parametrize("spec", [on_disk(s) for s in faces.RECOGNISERS.values()])
def test_loading_a_face_model_keeps_the_log_quiet(spec, capfd):
    """SFace's file lists its weights as graph inputs: ONNX Runtime wrote 174 warnings per load."""
    faces.Recogniser(spec, MODEL_DIR / spec.filename)
    assert "onnxruntime" not in capfd.readouterr().err.replace("\x00", "")


@pytest.mark.parametrize("spec", [on_disk(s) for s in faces.RECOGNISERS.values()])
def test_every_face_model_tells_people_apart_in_real_photos(spec):
    recogniser = faces.Recogniser(spec, MODEL_DIR / spec.filename)
    limits = spec.thresholds
    [(_, williams_2006, _)] = photo_faces(recogniser, "face_williams_2006")
    [(_, williams_2018, _)] = photo_faces(recogniser, "face_williams_2018")
    [(_, wilmore, _)] = photo_faces(recogniser, "face_wilmore")
    assert williams_2006 @ williams_2018 >= limits["match_similarity"]  # twelve years apart
    assert williams_2006 @ wilmore < limits["ask_similarity"]
    idx = index([("williams", williams_2006), ("williams", williams_2018), ("wilmore", wilmore)])
    # Both of them together (2024): each found, the right way round.
    crew = sorted(photo_faces(recogniser, "face_crew"), key=lambda c: c[0])
    assert [faces.identify(idx, v).label for _, v, _ in crew] == ["williams", "wilmore"]
    assert all(faces.decide(faces.identify(idx, v), limits) == "match" for _, v, _ in crew)
    # A crew of nine: the two of them, and nobody else is taken for them.
    named = [faces.decide(faces.identify(idx, v), limits) for _, v, _ in photo_faces(recogniser, "face_group")]
    assert named.count("ask") == 0 and named.count("match") >= 2


@requires_faces
def test_a_blurred_or_small_face_is_no_face():
    image = Image.open(ASSETS / "face_williams_2018.jpg").convert("RGB")
    finder = faces.FaceDetector(faces.DETECTOR, MODEL_DIR / faces.DETECTOR.filename)
    [face] = finder.detect([image])
    assert face is not None and faces.problem(face, faces.align(image, face), 30, image.size) is None
    blurred = image.filter(ImageFilter.GaussianBlur(6))
    [soft] = finder.detect([blurred])
    assert soft is None or faces.problem(soft, faces.align(blurred, soft), 30, blurred.size) in ("blurry", "unsure")
    tiny = image.resize((image.width // 8, image.height // 8))
    [small] = finder.detect([tiny])
    assert small is None or faces.problem(small, faces.align(tiny, small), 30, tiny.size) == "small"


# --- the sensor --------------------------------------------------------------------------------------


def view(client, sid):
    return client.get(f"/api/v1/sensors/{sid}").json()


def live(client, sid):
    return {c["key"]: c for c in view(client, sid)["objects"]["live"]}


def person_with_face(client, sid):
    """The frame of the last check and its person box with a usable face (or None)."""
    v = view(client, sid)
    for d in v["objects"]["detections"]:
        if d["key"] == "person" and (d.get("face") or {}).get("box") and not d["face"].get("problem"):
            return v["live"]["frame_id"], d
    return None


def faces_patch(enabled, **extra):
    return {"objects": {"classes": ["person"], "clear_after_s": 0, "faces": {"enabled": enabled, **extra}}}


@requires_faces
def test_people_are_taught_recognised_forgotten_exported(settings):  # noqa: F811
    camera = PhotoCamera("face_williams_2006")
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        sid = create(client, objects={"classes": ["person"], "clear_after_s": 0}, interval_s=1).json()["id"]
        # Off by default: no model, no face search, no "unknown person".
        assert view(client, sid)["objects"]["faces"]["enabled"] is False
        assert wait_for(lambda: live(client, sid)["person"]["on"], timeout=60)
        assert rt.face_model is None and FACE_UNKNOWN_KEY not in live(client, sid)
        body = {"frame_id": view(client, sid)["live"]["frame_id"], "box": [0, 0, 1, 1], "new_person": "Suni"}
        assert client.post(f"/api/v1/sensors/{sid}/faces", json=body).status_code == 400

        # Turned on: the model loads, faces are looked for, and "unknown person" is an entity.
        assert client.patch(f"/api/v1/sensors/{sid}", json=faces_patch(True)).status_code == 200
        assert wait_for(lambda: client.get("/api/v1/status").json()["faces"]["loaded"], timeout=60)
        assert "binary_sensor.beach_unknown_person" in view(client, sid)["entity_ids"]
        assert wait_for(lambda: person_with_face(client, sid), timeout=30)
        stats = view(client, sid)["objects"]["face_stats"]
        assert stats["looked"] >= 1 and stats["usable"] >= 1
        with Image.open(ASSETS / "face_williams_2006.jpg") as photo:
            assert stats["frame"] == list(photo.size)
        # A face nobody is named for keeps its frame in the history (once, not every check).
        seen = {"sensor": sid, "event": "face_seen", "limit": 10}
        assert wait_for(lambda: client.get("/api/v1/history", params=seen).json()["total"] == 1, timeout=30)
        row = client.get("/api/v1/history", params=seen).json()["items"][0]
        assert row["published_key"] == "face" and row["has_frame"] and not row["is_change"]

        def teach(name):
            frame_id, person = person_with_face(client, sid)
            body = {"frame_id": frame_id, "box": person["box"], "score": person["score"], "new_person": name}
            resp = client.post(f"/api/v1/sensors/{sid}/faces", json=body)
            assert resp.status_code == 201, resp.text
            return resp.json()

        suni = teach("Suni")
        assert suni["label"] == "suni" and suni["faces"] == 1
        assert "binary_sensor.beach_suni" in view(client, sid)["entity_ids"]
        assert wait_for(lambda: live(client, sid).get("suni", {}).get("on"), timeout=30)
        # A box without a face cannot be taught: a back or a jacket never becomes someone.
        frame_id = view(client, sid)["live"]["frame_id"]
        check = client.post(f"/api/v1/sensors/{sid}/faces/check", json={"frame_id": frame_id, "box": [0, 0.8, 0.3, 1]})
        assert check.status_code == 200 and check.json()["problem"] == "none" and check.json()["message"]
        nobody = {"frame_id": frame_id, "box": [0, 0.8, 0.3, 1], "person": "suni"}
        assert client.post(f"/api/v1/sensors/{sid}/faces", json=nobody).status_code == 400

        camera.name = "face_wilmore"
        assert wait_for(lambda: not live(client, sid)["suni"]["on"] and person_with_face(client, sid), timeout=30)
        teach("Butch")

        # Both together, and in a crew of nine: each is named, the others are unknown people.
        camera.name = "face_crew"
        assert wait_for(lambda: live(client, sid)["suni"]["on"] and live(client, sid)["butch"]["on"], timeout=30)
        camera.name = "face_group"
        assert wait_for(lambda: live(client, sid)[FACE_UNKNOWN_KEY]["on"], timeout=60)
        assert live(client, sid)[FACE_UNKNOWN_KEY]["count"] <= 7
        assert wait_for(lambda: live(client, sid)["suni"]["on"] and live(client, sid)["butch"]["on"], timeout=30)

        people = client.get("/api/v1/people").json()
        assert [p["name"] for p in people["people"]] == ["Suni", "Butch"] and people["sensors"] == [sid]
        face_id = people["people"][0]["faces"][0]["id"]
        first = people["people"][0]["faces"][0]
        assert first["suspect"] is None and first["sensor_id"] == sid and people["look_alikes"] == []
        # Embeddings are cached per model and version (an older version is recomputed).
        with rt.db.session() as s:
            keys = set(s.scalars(select(FaceEmbedding.model)))
        assert keys == {f"{faces.DEFAULT_RECOGNISER}/{faces.EMBEDDING_VERSION}"}
        # A check and a request building the index at once store each face's embedding once.
        for _ in range(5):
            with rt.db.session() as s:
                s.execute(delete(FaceEmbedding))
            with ThreadPoolExecutor(8) as pool:
                built = list(pool.map(lambda _: rt._build_face_index(rt.face_model, 0), range(8)))
            assert all(len(index.ids) == 2 for index in built)
        with rt.db.session() as s:
            assert s.scalar(select(func.count()).select_from(FaceEmbedding)) == 2
        # Embedding the taught faces again (a new model) shows how far it is, and nothing when done.
        with rt.db.session() as s:
            s.execute(delete(FaceEmbedding))
        progress, embed = [], rt._embed_taught
        rt._embed_taught = lambda *args: progress.append(rt.face_status()["embedding"]) or embed(*args)
        try:
            assert len(rt._build_face_index(rt.face_model, 0).ids) == 2
        finally:
            del rt._embed_taught
        assert progress == [{"done": 0, "total": 2}, {"done": 1, "total": 2}]
        assert rt.face_status()["embedding"] is None
        # A newly loaded model embeds them before it is used: the checks go on without faces meanwhile.
        with rt.db.session() as s:
            s.execute(delete(FaceEmbedding))
        unused = []
        rt._embed_taught = lambda *args: unused.append(rt.face_model is None) or embed(*args)

        async def reload():
            rt.face_model, rt.face_index, rt._face_task = None, None, asyncio.current_task()
            await rt._load_faces()

        try:
            client.portal.call(reload)
        finally:
            del rt._embed_taught
        assert unused == [True, True] and rt.face_model is not None and rt.face_index is not None
        assert len(rt.face_index.ids) == 2
        # Each person's own entity says where they are seen: on this sensor now.
        assert people["people"][0]["seen"]["now"] == "beach" and people["people"][0]["seen"]["sensor_id"] == sid
        image = client.get(f"/api/v1/faces/{face_id}/image")
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        # Faces are kept in the app's own storage, never in the media folder.
        assert list((settings.data_dir / "faces").glob("*.jpg"))
        assert not list((settings.media_dir / "samples").rglob("*.jpg"))
        # Not among the boxes taught by their look.
        assert client.get(f"/api/v1/sensors/{sid}/taught").json()["examples"] == []
        assert view(client, sid)["objects"]["taught"] == 0 and view(client, sid)["objects"]["taught_faces"] == 2

        # Shared: another sensor that recognises faces knows them without being taught.
        other = create(client, name="Door", objects={"classes": ["person"], "clear_after_s": 0}, interval_s=1)
        door = other.json()["id"]
        assert client.patch(f"/api/v1/sensors/{door}", json=faces_patch(True)).status_code == 200
        assert "binary_sensor.door_suni" in view(client, door)["entity_ids"]
        assert wait_for(lambda: live(client, door).get("suni", {}).get("on"), timeout=30)
        # Deleting a sensor keeps the faces taught on it.
        assert client.delete(f"/api/v1/sensors/{door}").status_code == 204

        # Export: the faces taught on this camera only when asked for, with their people's names.
        plain = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/sensors/{sid}/export").content))
        manifest = json.loads(plain.read("manifest.json"))
        assert manifest["samples"] == [] and manifest["faces"] == []
        with_faces = client.get(f"/api/v1/sensors/{sid}/export", params={"faces": True}).content
        stored = json.loads(zipfile.ZipFile(io.BytesIO(with_faces)).read("manifest.json"))["faces"]
        assert sorted(f["person"] for f in stored) == ["butch", "suni"]
        imported = client.post("/api/v1/import", files={"file": ("b.zip", with_faces, "application/zip")})
        assert imported.status_code == 201, imported.text
        copy = imported.json()["id"]
        assert view(client, copy)["objects"]["faces"]["enabled"] is False  # turned on there, by the user
        assert imported.json()["faces_off"] is True  # so the UI says to turn it on
        # The same people (no copies of them), each with the face again, from the new sensor.
        people = client.get("/api/v1/people").json()["people"]
        assert [(p["key"], len(p["faces"])) for p in people] == [("suni", 2), ("butch", 2)]

        # Renamed: the key (and entity IDs) stay. A face under the wrong name is moved; forgetting
        # a person removes their faces and entities everywhere.
        assert client.patch("/api/v1/people/suni", json={"name": "Sunita"}).json() == {"key": "suni", "name": "Sunita"}
        assert client.patch("/api/v1/people/suni", json={"name": "butch"}).status_code == 400  # taken
        assert client.patch(f"/api/v1/faces/{face_id}", json={"person": "butch"}).status_code == 200
        assert client.delete(f"/api/v1/faces/{face_id}").status_code == 204
        assert client.delete("/api/v1/people/suni").status_code == 204
        assert "binary_sensor.beach_suni" not in view(client, sid)["entity_ids"]
        assert client.delete("/api/v1/people").status_code == 204
        assert client.get("/api/v1/people").json()["people"] == []
        assert not list((settings.data_dir / "faces").glob("*.jpg"))

        # Turned off everywhere: the face model is let go; the files can then be deleted.
        assert client.delete(f"/api/v1/models/face_models/{faces.DEFAULT_RECOGNISER}").status_code == 409
        assert client.patch(f"/api/v1/sensors/{sid}", json=faces_patch(False)).status_code == 200
        assert wait_for(lambda: rt.face_model is None, timeout=10)
        assert FACE_UNKNOWN_KEY not in live(client, sid)


@requires_faces
def test_a_face_question_is_answered_in_the_review_queue(settings):  # noqa: F811
    camera = PhotoCamera("face_williams_2006")
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.grabber = camera
        patch = faces_patch(True, ask_unknown=True)["objects"]
        sid = create(client, objects=patch, interval_s=1).json()["id"]
        assert wait_for(lambda: person_with_face(client, sid), timeout=90)
        frame_id, person = person_with_face(client, sid)
        body = {"frame_id": frame_id, "box": person["box"], "new_person": "Suni"}
        assert client.post(f"/api/v1/sensors/{sid}/faces", json=body).status_code == 201
        # Butch is nobody yet: "Who is this?" (asked about faces nobody was taught for).
        camera.name = "face_wilmore"

        def question():
            items = client.get("/api/v1/review").json()["items"]
            return next((i for i in items if (i["probs"].get("ask") or {}).get("face")), None)

        assert wait_for(question, timeout=60)
        item = question()
        assert item["probs"]["ask"]["label"] == FACE_UNKNOWN_KEY and item["probs"]["ask"]["face"]
        assert {"key": "suni", "name": "Suni", "parent": "person", "face": True} in item["sensor"]["labels"]
        bad = client.post(f"/api/v1/review/{item['id']}", json={"action": "new"})
        assert bad.status_code == 400
        resp = client.post(f"/api/v1/review/{item['id']}", json={"action": "new", "value": "Butch"})
        assert resp.status_code == 200, resp.text
        people = client.get("/api/v1/people").json()["people"]
        assert {p["name"]: len(p["faces"]) for p in people} == {"Suni": 1, "Butch": 1}
        assert wait_for(lambda: live(client, sid).get("butch", {}).get("on"), timeout=30)
        assert merge_objects(None)["faces"]["ask_unknown"] is False  # off by default
        # The question's frame in the history now shows who it was: taught from it.
        rows = client.get("/api/v1/history", params={"sensor": sid, "limit": 100}).json()["items"]
        row = next(r for r in rows if r["id"] == item["id"])
        assert [d["label"] for d in row["detections"] if d.get("taught")] == ["butch"]
    # After an update or restart, when each person was last seen comes back from the history.
    with TestClient(create_app(settings)) as client:
        # (Butch was waited for until seen; Suni may have been taught and gone before a check saw her.)
        assert live(client, sid)["butch"]["last_seen"]
        seen = {p["key"]: p["seen"]["at"] for p in client.get("/api/v1/people").json()["people"]}
        assert seen["butch"]


def test_a_face_model_that_cannot_be_downloaded_leaves_the_sensor_working(settings, monkeypatch, tmp_path):  # noqa: F811
    from visionstate import backbones

    def offline(*_args, **_kwargs):
        raise OSError("no internet")

    settings.bundled_models_dir = tmp_path / "empty"
    monkeypatch.setattr(backbones, "download", offline)
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        assert client.post("/api/v1/faces/retry").status_code == 200
        # The error is set inside the loading task, which ends just after it.
        assert wait_for(lambda: rt.face_status()["error"] and not rt.face_status()["loading"], timeout=10)
        error = rt.face_status()["error"]
        assert faces.DETECTOR.filename in error and "by hand" in error
        assert rt.face_model is None and not rt.face_status()["loading"]
        # No retry by itself: only when asked.
        assert rt.wanted_faces() is None and rt.face_status()["error"] == error
        assert client.post("/api/v1/faces/retry").status_code == 200
        # The error is set inside the loading task, which ends just after it.
        assert wait_for(lambda: rt.face_status()["error"] and not rt.face_status()["loading"], timeout=10)
        settings_view = client.get("/api/v1/settings").json()
        assert [m["id"] for m in settings_view["face_models"]] == list(faces.RECOGNISERS)
        assert all(not m["installed"] and m["data"] for m in settings_view["face_models"])
        assert (
            client.put(
                "/api/v1/settings", json={"backbone": settings_view["backbone"], "face_model": "nope"}
            ).status_code
            == 400
        )
        resp = client.put("/api/v1/settings", json={"backbone": settings_view["backbone"], "face_model": "auraface-v1"})
        assert resp.status_code == 200 and resp.json()["face_model"] == "auraface-v1"


def test_face_limits_global_and_per_sensor(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        view = client.get("/api/v1/face-limits").json()
        defaults = faces.tunable_defaults(view["model"])
        assert view["defaults"] == defaults == view["effective"] and not any(view["values"].values())
        assert defaults["match_similarity"] == faces.recogniser_spec(view["model"]).match_similarity
        assert set(client.get("/api/v1/config").json()["face_limits"]) == set(defaults)

        # Global: one set, the rest follow the face model; nonsense is refused.
        view = client.put("/api/v1/face-limits", json={"min_face_px": 40}).json()
        assert view["effective"]["min_face_px"] == 40 and rt.global_faces == {"min_face_px": 40}
        assert client.put("/api/v1/face-limits", json={"ask_similarity": 0.9}).status_code == 400  # above match
        assert client.put("/api/v1/face-limits", json={"min_face_px": 5}).status_code == 422

        # A sensor follows the global value until it has its own.
        sid = create(client, objects={"classes": ["person"]}).json()["id"]
        assert rt.face_limits(rt.load_sensor(sid))["min_face_px"] == 40
        on = {"objects": {"faces": {"enabled": False, "ask_unknown": False, "min_face_px": 24}}}
        assert client.patch(f"/api/v1/sensors/{sid}", json=on).status_code == 200
        assert rt.face_limits(rt.load_sensor(sid))["min_face_px"] == 24
        assert client.get(f"/api/v1/sensors/{sid}").json()["objects"]["faces"]["min_face_px"] == 24
        bad = {"objects": {"faces": {"match_similarity": 0.9, "sure_similarity": 0.8}}}
        assert client.patch(f"/api/v1/sensors/{sid}", json=bad).status_code == 400  # match above sure
        back = {"objects": {"faces": {"enabled": False, "ask_unknown": False}}}
        client.patch(f"/api/v1/sensors/{sid}", json=back)
        assert rt.face_limits(rt.load_sensor(sid))["min_face_px"] == 40


def test_a_downloaded_model_not_in_use_can_be_deleted(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        info = client.get("/api/v1/settings").json()
        in_use = next(b for b in info["backbones"] if b["id"] == info["backbone"])
        assert in_use["in_use"] and not in_use["downloaded"]  # included in the app, and in use
        assert client.delete(f"/api/v1/models/backbones/{info['backbone']}").status_code == 409
        other = next(d for d in info["detectors"] if d["id"] != info["detector"])
        assert not other["in_use"]
        assert client.delete(f"/api/v1/models/detectors/{other['id']}").status_code == 404  # not downloaded
        assert client.delete("/api/v1/models/detectors/nope").status_code == 404

        # Downloaded and not in use: deleted.
        spec = detectors.DETECTORS[other["id"]]
        settings.models_dir.mkdir(parents=True, exist_ok=True)
        (settings.models_dir / spec.filename).write_bytes(b"model")
        assert next(d for d in client.get("/api/v1/settings").json()["detectors"] if d["id"] == spec.id)["downloaded"]
        resp = client.delete(f"/api/v1/models/detectors/{spec.id}")
        assert resp.status_code == 200 and not (settings.models_dir / spec.filename).exists()

        # A face model goes with the face detector once it is the last one.
        recogniser = faces.RECOGNISERS["auraface-v1"]
        for name in (recogniser.filename, faces.DETECTOR.filename):
            (settings.models_dir / name).write_bytes(b"model")
        assert client.delete("/api/v1/models/face_models/auraface-v1").status_code == 200
        assert not (settings.models_dir / recogniser.filename).exists()
        assert not (settings.models_dir / faces.DETECTOR.filename).exists()


def test_each_person_has_an_entity_saying_where_they_are():
    from visionstate.mqtt import person_discovery_messages, person_topics

    [(topic, config), (stamp_topic, stamp)] = person_discovery_messages("homeassistant", [("ole", "Ole")])
    # And one saying when they were last seen, as a timestamp ("3 minutes ago" in Home Assistant).
    assert stamp_topic == "homeassistant/sensor/visionstate/person_ole_last_seen/config"
    assert stamp["default_entity_id"] == "sensor.visionstate_ole_last_seen" and stamp["name"] == "Ole last seen"
    assert stamp["device_class"] == "timestamp" and stamp["unique_id"] == "visionstate_person_ole_last_seen"
    assert stamp["state_topic"] == person_topics("ole")["last_seen"] == "visionstate/people/ole/last_seen"
    assert topic == "homeassistant/sensor/visionstate/person_ole/config"
    assert config["default_entity_id"] == "sensor.visionstate_ole_seen_by" and config["name"] == "Ole seen by"
    assert config["unique_id"] == "visionstate_person_ole" and config["device"]["identifiers"] == ["visionstate"]
    assert config["state_topic"] == person_topics("ole")["state"] == "visionstate/people/ole/state"


@requires_faces
def test_people_are_taught_from_photos(settings):  # noqa: F811
    from visionstate.uploads import zip_bytes

    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        photo = (ASSETS / "face_williams_2006.jpg").read_bytes()
        files = [("files", ("suni.jpg", photo, "image/jpeg"))]
        # Only once a sensor recognises faces (its confirmation fetches the face model).
        assert client.post("/api/v1/people/photos", data={"new_person": "Suni"}, files=files).status_code == 400
        create(client, objects=faces_patch(True)["objects"])
        assert wait_for(lambda: rt.face_model is not None, timeout=60)
        blank = io.BytesIO()
        Image.new("RGB", (640, 480), (90, 90, 90)).save(blank, "JPEG")
        files = [("files", ("suni.jpg", photo, "image/jpeg")), ("files", ("wall.jpg", blank.getvalue(), "image/jpeg"))]
        made = client.post("/api/v1/people/photos", data={"new_person": "Suni"}, files=files)
        assert made.status_code == 201, made.text
        body = made.json()
        assert body["label"] == "suni" and body["added"] == 1 and body["faces"] == 1
        assert body["skipped"] == [{"file": "wall.jpg", "message": "No face found in this photo."}]
        # More of an existing person, from a ZIP (as from a state sensor's upload).
        archive = zip_bytes({"a.jpg": (ASSETS / "face_williams_2018.jpg").read_bytes()})
        more = client.post(
            "/api/v1/people/photos", data={"person": "suni"}, files=[("files", ("p.zip", archive, "application/zip"))]
        )
        assert more.status_code == 201 and more.json()["faces"] == 2
        [suni] = client.get("/api/v1/people").json()["people"]
        assert {f["origin"] for f in suni["faces"]} == {"upload"} and all(f["sensor_id"] is None for f in suni["faces"])
        # No usable face: nobody is made.
        empty = [("files", ("wall.jpg", blank.getvalue(), "image/jpeg"))]
        nobody = client.post("/api/v1/people/photos", data={"new_person": "Nobody"}, files=empty).json()
        assert nobody["added"] == 0 and len(client.get("/api/v1/people").json()["people"]) == 1
        assert client.post("/api/v1/people/photos", files=empty).status_code == 400  # who is it?


def test_questions_about_a_face_come_in_a_burst_and_an_answer_opens_the_next(settings):  # noqa: F811
    """Someone walking up to a doorbell shows several faces worth teaching: up to
    FACES["ask_burst"] questions about one person per review cooldown, FACES["ask_gap_s"] apart,
    and an answer lets the next one come right away. An own label is asked about one at a time."""
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sid = create(client, objects={"classes": ["person", "car"]}, enabled=False).json()["id"]
        cfg = rt.load_sensor(sid)
        image = Image.new("RGB", (64, 48))
        kari = {"key": "person", "score": 0.9, "box": [0.1, 0.1, 0.5, 0.9], "face": {"box": [0.2, 0.1, 0.3, 0.2]}}
        kari["ask"] = {"id": None, "label": "kari", "similarity": 0.4, "face": True}
        car = {"key": "car", "score": 0.9, "box": [0.5, 0.5, 0.9, 0.9], "ask": {"label": "our_car", "similarity": 0.8}}

        def waiting(label):
            items = client.get("/api/v1/review").json()["items"]
            return [i for i in items if i["probs"]["ask"]["label"] == label]

        start = 1_000_000.0
        for n in range(8):  # a check every 2 s: a question at most every FACES["ask_gap_s"]
            client.portal.call(rt._ask_unsure, cfg, image, [kari, car], start + 2 * n)
        assert len(waiting("kari")) == 4  # 16 s of checks every 2 s: at 0, 4, 8 and 12 s
        assert len(waiting("our_car")) == 1
        for n in range(8, 20):
            client.portal.call(rt._ask_unsure, cfg, image, [kari, car], start + 2 * n)
        assert len(waiting("kari")) == FACES["ask_burst"]  # then it waits for the cooldown
        # An answer opens the next question at once (no 5 minutes to wait in front of the camera).
        first = waiting("kari")[0]["id"]
        assert client.post(f"/api/v1/review/{first}", json={"action": "skip"}).status_code == 200
        client.portal.call(rt._ask_unsure, cfg, image, [kari, car], start + 41)
        assert len(waiting("kari")) == FACES["ask_burst"] and waiting("kari")[0]["id"] != first
        assert len(waiting("our_car")) == 1


def test_a_person_taught_from_a_frame_was_last_seen_then(settings):  # noqa: F811
    """Answering "yes, Kari" about a frame from 17:28 means Kari was there at 17:28: their entity
    says so at once (last_seen), while its value stays where they are now."""
    from datetime import UTC, datetime

    from visionstate.mqtt import person_topics

    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sent: dict[str, object] = {}

        async def publish(topic, payload, retain=False):
            sent[topic] = payload

        rt.mqtt.publish = publish
        at = datetime(2026, 10, 12, 15, 28, tzinfo=UTC).timestamp()
        rt.seen_earlier(1, "kari", at, person=True)
        client.portal.call(rt.person_last_seen_changed, "kari")
        topics = person_topics("kari")
        assert sent[topics["state"]] == "not_seen"
        assert sent[topics["attributes"]]["last_seen"] == "2026-10-12T15:28:00+00:00"
        assert sent[topics["last_seen"]] == "2026-10-12T15:28:00+00:00"  # the timestamp entity
        # An older frame does not move it back.
        rt.seen_earlier(1, "kari", at - 3600, person=True)
        client.portal.call(rt.person_last_seen_changed, "kari")
        assert sent[topics["attributes"]]["last_seen"] == "2026-10-12T15:28:00+00:00"


def test_what_is_taught_from_a_frame_shows_on_the_same_moments_entries(settings):  # noqa: F811
    """A face seen and the question about it are two history entries of one moment: answering
    the question marks both (where the box is the same), not an entry of another moment."""
    from datetime import timedelta

    from visionstate.api.teach import remember_taught
    from visionstate.db import Prediction, utcnow
    from visionstate.settings import HISTORY

    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        sid = create(client, objects={"classes": ["person"]}, enabled=False).json()["id"]
        box = [0.4, 0.2, 0.6, 0.9]
        person = {"key": "person", "score": 0.9, "box": box}
        now = utcnow()
        later = now + timedelta(seconds=HISTORY["same_moment_s"] * 4)
        rows = [("ask", now), ("face", now + timedelta(seconds=1)), ("face", later)]
        with rt.db.session() as s:
            made = [
                Prediction(
                    sensor_id=sid,
                    state_key="person",
                    published_key=k,
                    confidence=0.9,
                    probs={},
                    created_at=at,
                    detections=[dict(person)],
                    is_change=False,
                    reviewed=True,
                )
                for k, at in rows
            ]
            s.add_all(made)
            s.flush()
            ids = [m.id for m in made]
        remember_taught(rt, sid, ids[0], box, "kari", "person", person=True)
        with rt.db.session() as s:
            taught = [[d.get("label") for d in s.get(Prediction, i).detections if d.get("taught")] for i in ids]
        assert taught == [["kari"], ["kari"], []]
