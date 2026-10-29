"""Sound sensors: the model on real recordings, levels, deciding a sound, the stream, clips,
discovery and the whole flow over the API (docs/SOUND_PLAN.md)."""

import re
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from visionstate import sounds
from visionstate.engine import SoundTrack, hear
from visionstate.main import create_app
from visionstate.mqtt import SensorDescriptor, discovery_messages, main_entities
from visionstate.settings import KIND_SOUND, SOUND, merge_sound
from visionstate.sources import AudioStream

from .conftest import MODEL_DIR, requires_sound
from .sound_stream import RATE, SoundServer, noise, recording
from .test_integration import settings, wait_for  # noqa: F401 - the settings fixture

SPEC = sounds.MODELS[sounds.DEFAULT_MODEL]


@pytest.fixture(scope="module")
def model() -> sounds.SoundModel:
    return sounds.SoundModel(SPEC, MODEL_DIR / SPEC.filename)


def windows(samples: np.ndarray, hop: float = 0.5) -> np.ndarray:
    size, step = int(SOUND["window_s"] * RATE), int(hop * RATE)
    return np.stack([samples[i : i + size] for i in range(0, len(samples) - size + 1, step)])


# --- registry -------------------------------------------------------------------------------------


def test_registry_is_pinned_and_its_sounds_are_safe():
    assert SPEC.id == sounds.DEFAULT_MODEL and SPEC.rate == RATE and not SPEC.bundled
    assert re.fullmatch(r"[0-9a-f]{64}", SPEC.sha256) and SPEC.size > 0
    assert SPEC.url == "" or "/resolve/" in SPEC.url  # published: pinned to a revision
    assert SPEC.license == "Apache-2.0" and "AudioSet" in SPEC.data
    for key, sound in SPEC.sounds.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key)  # safe in entity ids
        assert sound.classes and all(0 <= c < 527 for c in sound.classes)
        assert 0 < sound.threshold < 1
        assert re.fullmatch(r"mdi:[a-z0-9-]+", sound.icon)
        assert sound.device_class in ("sound", "smoke")
    assert SPEC.sounds["smoke_alarm"].device_class == "smoke"
    assert SOUND["speech_key"] in SPEC.sounds


# --- the model on real recordings (CC0, tests/assets/README.md) -----------------------------------


@requires_sound
@pytest.mark.parametrize("name", ["siren", "dog_bark", "smoke_alarm", "speech"])
def test_the_model_hears_real_recordings(model, name):
    scores, embedding = model.run(windows(recording(name)))
    assert embedding.shape == (len(scores), 576) and np.isfinite(embedding).all()
    heard = [model.sound_scores(row, list(SPEC.sounds)) for row in scores]
    above = sum(window[name] >= SPEC.sounds[name].threshold for window in heard)
    assert above >= SOUND["on_windows"], [round(w[name], 2) for w in heard]
    # The scores are probabilities already (a second sigmoid would put everything near 0.5).
    assert scores.min() >= 0 and scores.max() <= 1 and np.median(scores) < 0.05


@requires_sound
def test_the_model_hears_nothing_in_a_quiet_room(model):
    scores, _ = model.run(windows(noise(4.0)))
    for row in scores:
        heard = model.sound_scores(row, list(SPEC.sounds))
        assert all(heard[k] < SPEC.sounds[k].threshold for k in heard), heard


@requires_sound
def test_speech_is_silenced_in_a_clip_and_a_siren_is_not(model):
    speech = recording("speech")
    quiet, muted = model.silence_speech(speech, SOUND["speech_mute"])
    assert muted >= len(speech) / RATE * 0.6 and sounds.level_db(quiet) < sounds.level_db(speech) - 10
    siren = recording("siren")
    kept, muted = model.silence_speech(siren, SOUND["speech_mute"])
    assert muted <= 1 and sounds.level_db(kept) > sounds.level_db(siren) - 3


def test_a_clip_is_aac_in_mp4_and_reads_back():
    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(3 * RATE) / RATE)).astype(np.float32)
    data = sounds.encode_clip(tone, RATE)
    assert data[4:8] == b"ftyp" and len(data) < 40_000  # small: about 48 kbit/s
    back = sounds.decode_audio(data, RATE)
    assert abs(len(back) - len(tone)) < 0.1 * RATE
    assert abs(sounds.level_db(back) - sounds.level_db(tone)) < 1.0


# --- levels and deciding -----------------------------------------------------------------------------


def test_levels_and_the_line():
    assert sounds.level_db(np.zeros(100)) == sounds.SILENCE_DB
    assert sounds.level_db(np.full(100, 0.1)) == pytest.approx(-20.0, abs=0.01)
    auto = merge_sound({"level": "auto", "above_db": 12})
    assert sounds.line_db(auto, None) is None  # nothing learned yet
    assert sounds.line_db(auto, -50.0) == pytest.approx(-38.0)
    assert sounds.line_db(auto, -100.0) == SOUND["floor_db"]  # never below the floor
    assert sounds.line_db(merge_sound({"level": "fixed", "fixed_db": -30}), None) == -30
    levels = [-60.0] * 80 + [-20.0] * 20  # a loud fifth does not move the background
    assert sounds.background_db(levels) == pytest.approx(-60.0)


def test_a_sound_needs_two_windows_and_stays_through_its_pauses():
    track, now = SoundTrack(), 1000.0
    assert not hear(track, 0.9, 0.3, now, 10.0)  # one loud window is not enough
    assert not hear(track, 0.1, 0.3, now + 0.5, 10.0)
    assert hear(track, 0.5, 0.3, now + 1.0, 10.0) and track.on  # two of the last three
    # A smoke alarm pauses between beeps: above half the threshold, or quiet for less than clear_after_s.
    assert not hear(track, 0.2, 0.3, now + 1.5, 10.0)
    assert not hear(track, 0.0, 0.3, now + 9.0, 10.0) and track.on
    assert hear(track, 0.0, 0.3, now + 11.6, 10.0) and not track.on
    assert track.best == pytest.approx(0.5)
    assert not hear(track, 0.9, 0.3, now + 12.0, 10.0)  # off again: two windows to come back


def test_the_stream_buffer_hands_out_what_is_new():
    stream = AudioStream("http://127.0.0.1:1/never")  # never connects: fed by hand
    try:
        size = len(stream._buf)
        stream._append(np.arange(1000, dtype=np.float32))
        new, total = stream.since(0)
        assert total == 1000 and new[0] == 0 and new[-1] == 999
        stream._append(np.arange(1000, size + 500, dtype=np.float32))  # wraps around
        new, total = stream.since(1000)
        assert total == size + 500 and new[0] == 1000 and new[-1] == size + 499
        assert stream.segment(total - 10, total).tolist() == list(range(size + 490, size + 500))
        assert stream.segment(0, 10) is None  # no longer in the buffer
        assert stream.segment(total - 10, total + 1) is None  # not received yet
        assert stream.tail(1 / RATE).tolist() == [size + 499]
        assert stream.connected
    finally:
        stream.close()


# --- Home Assistant ----------------------------------------------------------------------------------


def test_discovery_of_a_sound_sensor():
    descriptor = SensorDescriptor(
        "hallway",
        "Hallway",
        [],
        KIND_SOUND,
        sounds=[("smoke_alarm", "Smoke or fire alarm", "mdi:smoke-detector-variant-alert", "smoke")]
        + [("doorbell", "Doorbell", "mdi:ear-hearing", "sound")],
    )
    messages = dict(discovery_messages("homeassistant", descriptor))
    alarm = messages["homeassistant/binary_sensor/visionstate_hallway/smoke_alarm/config"]
    assert alarm["device_class"] == "smoke" and alarm["state_topic"] == "visionstate/hallway/sounds/smoke_alarm/state"
    assert alarm["unique_id"] == "visionstate_hallway_smoke_alarm"
    assert messages["homeassistant/binary_sensor/visionstate_hallway/doorbell/config"]["device_class"] == "sound"
    assert alarm["device"]["model"] == "Sound sensor"
    # It listens all the time: no frame, no "check now"; it can be paused.
    assert set(messages) == {
        "homeassistant/binary_sensor/visionstate_hallway/smoke_alarm/config",
        "homeassistant/binary_sensor/visionstate_hallway/doorbell/config",
        "homeassistant/switch/visionstate_hallway/enabled/config",
    }
    assert main_entities(descriptor) == [
        ("visionstate_hallway_smoke_alarm", "binary_sensor.hallway_smoke_or_fire_alarm"),
        ("visionstate_hallway_doorbell", "binary_sensor.hallway_doorbell"),
    ]


# --- the whole flow -----------------------------------------------------------------------------------


@pytest.fixture
def quick(monkeypatch):
    """Clips cut sooner, so the test need not wait for them."""
    monkeypatch.setitem(SOUND, "clip_before_s", 1.0)
    monkeypatch.setitem(SOUND, "clip_after_s", 1.0)


@requires_sound
def test_a_sound_sensor_hears_a_siren_and_keeps_a_clip(settings, quick):  # noqa: F811
    server = SoundServer(speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            resp = client.post(
                "/api/v1/sensors",
                json={
                    "name": "Hallway",
                    "kind": "sound",
                    "source_type": "rtsp",
                    "source": server.url,
                    "sound": {"sounds": ["siren", "dog_bark"], "clips": True, "clear_after_s": 2},
                },
            )
            assert resp.status_code == 201, resp.text
            sensor = resp.json()
            sid = sensor["id"]
            assert sensor["sound"]["sounds"] == ["siren", "dog_bark"] and sensor["roi"] is None
            assert sensor["entity_ids"] == ["binary_sensor.hallway_siren", "binary_sensor.hallway_dog_barking"]

            def view() -> dict:
                return client.get(f"/api/v1/sensors/{sid}").json()

            assert wait_for(lambda: view()["sound"]["line"] is not None)  # the background is learned
            assert view()["status"] == "ok" and view()["sound"]["stream"]["sample_rate"] == RATE
            server.play(recording("siren"))
            assert wait_for(lambda: next(s for s in view()["sound"]["live"] if s["key"] == "siren")["on"])
            assert not next(s for s in view()["sound"]["live"] if s["key"] == "dog_bark")["on"]

            def heard() -> list[dict]:
                params = {"sensor": sid, "event": "heard"}
                return client.get("/api/v1/history", params=params).json()["items"]

            assert wait_for(lambda: heard() and heard()[0]["has_clip"])
            row = heard()[0]
            assert row["state_key"] == "siren" and row["confidence"] >= SPEC.sounds["siren"].threshold
            clip = client.get(f"/api/v1/history/{row['id']}/clip")
            assert clip.status_code == 200 and clip.headers["content-type"] == "audio/mp4"
            back = sounds.decode_audio(clip.content, RATE)
            assert 2.0 <= len(back) / RATE <= 6.0  # a second before, the sound, a second after
            assert client.get(f"/api/v1/history/{row['id']}/image").status_code == 404
            # Quiet again: off after clear_after_s, and a "quiet" row.
            assert wait_for(lambda: not next(s for s in view()["sound"]["live"] if s["key"] == "siren")["on"])
            quiet = client.get("/api/v1/history", params={"sensor": sid, "event": "quiet"}).json()["items"]
            assert quiet and quiet[0]["state_key"] == "siren"
            # No longer listening for the siren: it leaves the sensor's sounds.
            patched = client.patch(f"/api/v1/sensors/{sid}", json={"sound": {"sounds": ["dog_bark"]}}).json()
            assert [s["key"] for s in patched["sound"]["live"]] == ["dog_bark"]
            assert patched["sound"]["clips"] is True  # what was not sent stays
            assert client.delete(f"/api/v1/sensors/{sid}").status_code == 204
            assert not (settings.clips_dir / str(sid)).exists()
    finally:
        server.close()


def test_a_sound_sensor_listens_to_a_stream_only(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        body = {"name": "Hallway", "kind": "sound", "source_type": "http", "source": "http://cam/snap.jpg"}
        assert client.post("/api/v1/sensors", json=body).status_code == 400
        body = {**body, "source_type": "rtsp", "source": "rtsp://cam/live", "sound": {"sounds": ["unicorn"]}}
        assert client.post("/api/v1/sensors", json=body).status_code == 422
        body["sound"] = {"sounds": ["siren"], "thresholds": {"siren": 1.5}}
        assert client.post("/api/v1/sensors", json=body).status_code == 422
        body["sound"] = {"sounds": ["siren"], "thresholds": {"siren": 0.4}}
        made = client.post("/api/v1/sensors", json=body).json()
        assert made["sound"]["thresholds"] == {"siren": 0.4}
        assert next(s for s in made["sound"]["live"] if s["key"] == "siren")["threshold"] == 0.4
        cleared = client.patch(f"/api/v1/sensors/{made['id']}", json={"sound": {"thresholds": {"siren": None}}})
        assert cleared.json()["sound"]["thresholds"] == {}
        config = client.get("/api/v1/config").json()
        assert "sound" in config["sensor_kinds"] and {s["key"] for s in config["sounds"]} == set(SPEC.sounds)
        assert config["history_events"]["sound"] == ["heard", "quiet"]
    time.sleep(0)  # the stream's thread ends on its own (closed with the runtime)


def chime(seconds: float = 3.0, gain: float = 0.3, seed: int = 0) -> np.ndarray:
    """A drawn two-tone doorbell (ding-dong) with a little noise."""
    t = np.arange(int(seconds * RATE)) / RATE
    out = np.zeros_like(t)
    for start, freq in ((0.3, 784.0), (1.0, 622.0)):
        m = t >= start
        decay = np.exp(-(t[m] - start) * 2.5)
        out[m] += gain * decay * (np.sin(2 * np.pi * freq * t[m]) + 0.3 * np.sin(2 * np.pi * 2 * freq * t[m]))
    return (out + noise(seconds, 0.002, seed)).astype(np.float32)


@requires_sound
def test_own_sounds_are_taught_and_heard_and_review_asks(settings, quick, monkeypatch):  # noqa: F811
    monkeypatch.setitem(SOUND, "unsure_share", 0.99)  # every sound is asked about
    server = SoundServer(speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            sensor = client.post(
                "/api/v1/sensors",
                json={
                    "name": "Hall",
                    "kind": "sound",
                    "source_type": "rtsp",
                    "source": server.url,
                    "sound": {"sounds": ["siren"], "clips": True, "clear_after_s": 2},
                },
            ).json()
            sid = sensor["id"]

            def view() -> dict:
                return client.get(f"/api/v1/sensors/{sid}").json()

            def waiting() -> list[dict]:
                return client.get("/api/v1/review", params={"sensor": sid}).json()["items"]

            assert wait_for(lambda: view()["sound"]["line"] is not None)
            server.play(recording("siren"))
            assert wait_for(lambda: bool(waiting()), timeout=40)
            item = waiting()[0]
            assert item["state_key"] == "siren" and item["review_reason"] == "unsure" and item["has_clip"]
            assert item["sensor"]["sounds"] == [{"key": "siren", "name": "Siren", "own": False}]
            # "That was our own siren": a new own sound from the clip.
            answer = client.post(f"/api/v1/review/{item['id']}", json={"action": "new", "value": "Our siren"})
            assert answer.status_code == 200, answer.text
            assert [c["key"] for c in view()["sound"]["custom"]] == ["our_siren"]
            assert view()["entity_ids"][-1] == "binary_sensor.hall_our_siren"

            # Teach a doorbell from recordings (drawn), and what other sounds are.
            files = [
                ("files", (f"bell{i}.m4a", sounds.encode_clip(chime(gain=0.2 + 0.05 * i, seed=i), RATE), "audio/mp4"))
                for i in range(5)
            ]
            made = client.post(f"/api/v1/sensors/{sid}/sounds/upload", files=files, data={"new_name": "Doorbell"})
            assert made.status_code == 201, made.text
            assert [a["label"] for a in made.json()["added"]] == ["doorbell"] * 5
            others = [
                ("files", (f"{n}.m4a", sounds.encode_clip(recording(n), RATE), "audio/mp4"))
                for n in ("dog_bark", "speech")
            ]
            assert (
                client.post(f"/api/v1/sensors/{sid}/sounds/upload", files=others, data={"label": "other"}).status_code
                == 201
            )
            taught = client.get(f"/api/v1/sensors/{sid}/sounds/taught").json()
            assert taught["counts"] == {"our_siren": 1, "doorbell": 5, "other": 2} and taught["trained"]
            assert client.get(f"/api/v1/sound-clips/{taught['clips'][0]['id']}").headers["content-type"] == "audio/mp4"

            server.play(chime(seed=9))
            assert wait_for(lambda: next(s for s in view()["sound"]["live"] if s["key"] == "doorbell")["on"])

            # Answering again replaces what the first answer taught.
            again = client.post(f"/api/v1/review/{item['id']}", json={"action": "sound", "state_key": "doorbell"})
            assert again.status_code == 200
            counts = client.get(f"/api/v1/sensors/{sid}/sounds/taught").json()["counts"]
            assert counts == {"doorbell": 6, "other": 2}
            # Export and import: the own sounds and their clips come along.
            exported = client.get(f"/api/v1/sensors/{sid}/export")
            imported = client.post("/api/v1/import", files={"file": ("b.zip", exported.content, "application/zip")})
            assert imported.status_code == 201, imported.text
            copy = imported.json()["id"]
            assert client.get(f"/api/v1/sensors/{copy}/sounds/taught").json()["counts"] == {"doorbell": 6, "other": 2}
            assert client.delete(f"/api/v1/sensors/{copy}").status_code == 204
            # Forget an own sound: its clips and its entity go.
            assert client.delete(f"/api/v1/sensors/{sid}/sounds/our_siren").status_code == 204
            assert [c["key"] for c in view()["sound"]["custom"]] == ["doorbell"]
            bad = client.post(f"/api/v1/sensors/{sid}/sounds/upload", files=files[:1], data={"label": "unicorn"})
            assert bad.status_code == 400
    finally:
        server.close()


@requires_sound
def test_a_camera_with_sound_keeps_clips_with_its_video(settings, quick):  # noqa: F811
    server = SoundServer(speed=1.0, video=True)
    try:
        with TestClient(create_app(settings)) as client:
            body = {"sounds": ["siren"], "clips": True, "clear_after_s": 2}
            made = client.post(
                "/api/v1/sensors",
                json={"name": "Porch", "kind": "sound", "source_type": "rtsp", "source": server.url, "sound": body},
            ).json()
            sid = made["id"]

            def view() -> dict:
                return client.get(f"/api/v1/sensors/{sid}").json()

            assert wait_for(lambda: view()["sound"]["line"] is not None)
            assert view()["sound"]["stream"]["video"] == {"codec": "h264", "width": 320, "height": 180}
            server.play(recording("siren"))

            def heard() -> list[dict]:
                return client.get("/api/v1/history", params={"sensor": sid, "event": "heard"}).json()["items"]

            assert wait_for(lambda: heard() and heard()[0]["has_clip"], timeout=40)
            row = heard()[0]
            assert row["clip_video"]
            clip = client.get(f"/api/v1/history/{row['id']}/clip")
            assert clip.headers["content-type"] == "video/mp4"
            import io

            import av

            with av.open(io.BytesIO(clip.content), mode="r") as container:
                kinds = sorted(s.type for s in container.streams)
                frames = sum(1 for _ in container.decode(video=0))
            assert kinds == ["audio", "video"] and frames >= 10  # at least a second of the camera
            assert 1.0 <= len(sounds.decode_audio(clip.content, RATE)) / RATE <= 6.0
            # Sound only, when asked: the next clip has no video.
            client.patch(f"/api/v1/sensors/{sid}", json={"sound": {**body, "video": False}})
            assert wait_for(lambda: not next(s for s in view()["sound"]["live"] if s["key"] == "siren")["on"])
            server.play(recording("siren"))
            assert wait_for(lambda: len(heard()) == 2 and heard()[0]["has_clip"], timeout=40)
            assert not heard()[0]["clip_video"]
    finally:
        server.close()
