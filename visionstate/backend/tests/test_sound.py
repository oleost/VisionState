"""Sound sensors: the model on real recordings, levels, deciding a sound, the stream, clips,
discovery and the whole flow over the API (docs/SOUND_PLAN.md)."""

import re
import time
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from visionstate import sounds
from visionstate.engine import SoundTrack, hear
from visionstate.main import create_app
from visionstate.mqtt import SensorDescriptor, discovery_messages, entity_options, main_entities, muted_topics
from visionstate.settings import ADVICE, KIND_SOUND, SOUND, merge_sound
from visionstate.sources import AudioStream

from .conftest import MODEL_DIR, requires_sound
from .sound_stream import RATE, SoundServer, chime, noise, recording
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
        assert sound.group  # the picker lists them by group
    assert set(SPEC.popular) <= set(SPEC.sounds) and len(SPEC.popular) <= SOUND["max_sounds"]
    # Two sounds never share a class: one class heard would turn on both.
    classes = [c for sound in SPEC.sounds.values() for c in sound.classes]
    assert len(classes) == len(set(classes))
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
        body["sound"] = {"sounds": list(SPEC.sounds)[: SOUND["max_sounds"] + 1]}  # more than a sensor may have
        assert client.post("/api/v1/sensors", json=body).status_code == 422
        body["sound"] = {"sounds": ["siren"], "thresholds": {"siren": 0.4}}
        made = client.post("/api/v1/sensors", json=body).json()
        assert made["sound"]["thresholds"] == {"siren": 0.4}
        assert next(s for s in made["sound"]["live"] if s["key"] == "siren")["threshold"] == 0.4
        cleared = client.patch(f"/api/v1/sensors/{made['id']}", json={"sound": {"thresholds": {"siren": None}}})
        assert cleared.json()["sound"]["thresholds"] == {}
        # An own sound made before the model offered a sound of the same key keeps it; the model's
        # sound is then refused (the two would share an entity).
        from visionstate.db import Sensor as SensorRow

        with client.app.state.runtime.db.session() as s:
            row = s.get(SensorRow, made["id"])
            assert row is not None
            row.sound = {**(row.sound or {}), "custom": [{"key": "doorbell", "name": "Doorbell"}]}
        clash = client.patch(f"/api/v1/sensors/{made['id']}", json={"sound": {"sounds": ["siren", "doorbell"]}})
        assert clash.status_code == 400 and "forget it" in clash.json()["detail"]
        config = client.get("/api/v1/config").json()
        assert "sound" in config["sensor_kinds"] and {s["key"] for s in config["sounds"]} == set(SPEC.sounds)
        assert all(s["group"] for s in config["sounds"]) and config["sound_popular"] == list(SPEC.popular)
        assert config["sound"]["max_sounds"] == SOUND["max_sounds"]
        assert config["history_events"]["sound"] == ["heard", "quiet", "near"]
    time.sleep(0)  # the stream's thread ends on its own (closed with the runtime)


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
            # The model has a "doorbell" sound too: an own sound never takes its key.
            assert [a["label"] for a in made.json()["added"]] == ["doorbell_2"] * 5
            others = [
                ("files", (f"{n}.m4a", sounds.encode_clip(recording(n), RATE), "audio/mp4"))
                for n in ("dog_bark", "speech")
            ]
            assert (
                client.post(f"/api/v1/sensors/{sid}/sounds/upload", files=others, data={"label": "other"}).status_code
                == 201
            )
            taught = client.get(f"/api/v1/sensors/{sid}/sounds/taught").json()
            assert taught["counts"] == {"our_siren": 1, "doorbell_2": 5, "other": 2} and taught["trained"]
            assert client.get(f"/api/v1/sound-clips/{taught['clips'][0]['id']}").headers["content-type"] == "audio/mp4"

            def doorbell_on() -> bool:
                return next(s for s in view()["sound"]["live"] if s["key"] == "doorbell_2")["on"]

            # Sent at twice the speed a chime is only a few windows long: where they fall decides, so it may ring again.
            for _ in range(4):
                server.play(chime(seed=9))
                if wait_for(doorbell_on, timeout=10):
                    break
            assert doorbell_on()

            # Answering again replaces what the first answer taught.
            again = client.post(f"/api/v1/review/{item['id']}", json={"action": "sound", "state_key": "doorbell_2"})
            assert again.status_code == 200
            counts = client.get(f"/api/v1/sensors/{sid}/sounds/taught").json()["counts"]
            assert counts == {"doorbell_2": 6, "other": 2}
            # Export and import: the own sounds and their clips come along.
            exported = client.get(f"/api/v1/sensors/{sid}/export")
            imported = client.post("/api/v1/import", files={"file": ("b.zip", exported.content, "application/zip")})
            assert imported.status_code == 201, imported.text
            copy = imported.json()["id"]
            assert client.get(f"/api/v1/sensors/{copy}/sounds/taught").json()["counts"] == {"doorbell_2": 6, "other": 2}
            assert client.delete(f"/api/v1/sensors/{copy}").status_code == 204
            # Forget an own sound: its clips and its entity go.
            assert client.delete(f"/api/v1/sensors/{sid}/sounds/our_siren").status_code == 204
            assert [c["key"] for c in view()["sound"]["custom"]] == ["doorbell_2"]
            bad = client.post(f"/api/v1/sensors/{sid}/sounds/upload", files=files[:1], data={"label": "unicorn"})
            assert bad.status_code == 400
    finally:
        server.close()


def test_a_recording_is_drawn_and_played_back_as_it_was():
    samples = np.concatenate([np.zeros(RATE // 2), 0.5 * np.ones(RATE // 4), np.zeros(RATE // 4)]).astype(np.float32)
    drawn = sounds.peaks(samples, RATE, 20)
    assert len(drawn) == 20 and max(drawn) == 1.0 and drawn[0] == 0.0 and drawn[11] == 1.0
    assert sounds.peaks(np.zeros(0, np.float32), RATE, 20) == []
    back = sounds.decode_audio(sounds.encode_wav(samples, RATE), RATE)
    assert len(back) == len(samples) and abs(float(back[RATE // 2 + 10]) - 0.5) < 0.01
    # The longest recording and the seconds after Stop fit in what the stream keeps.
    assert SOUND["record_max_s"] + SOUND["record_after_s"] < SOUND["buffer_s"]


@requires_sound
def test_an_own_sound_is_recorded_from_the_stream_and_trimmed(settings, monkeypatch):  # noqa: F811
    monkeypatch.setitem(SOUND, "record_after_s", 1.0)
    server = SoundServer(speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            body = {"name": "Hall", "kind": "sound", "source_type": "rtsp", "source": server.url}
            sid = client.post("/api/v1/sensors", json={**body, "sound": {"sounds": ["siren"]}}).json()["id"]
            base = f"/api/v1/sensors/{sid}/sounds/recording"
            assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["sound"]["line"] is not None)
            started = client.post(base)
            assert started.status_code == 201, started.text
            rec = started.json()
            assert rec["after_s"] == 1.0 and rec["max_s"] == SOUND["record_max_s"]
            server.play(chime(seed=3))
            time.sleep(1.0)
            stopped = client.post(f"{base}/{rec['id']}/stop")
            assert stopped.status_code == 200, stopped.text
            made = stopped.json()
            assert made["stop_s"] >= 1.0 and made["seconds"] >= made["stop_s"] + 0.9  # and a second after Stop
            assert abs(len(made["peaks"]) - made["seconds"] * made["peaks_per_s"]) <= 1 and max(made["peaks"]) == 1.0
            audio = client.get(f"{base}/{rec['id']}/audio")
            assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
            assert abs(len(sounds.decode_audio(audio.content, RATE)) / RATE - made["seconds"]) < 0.05
            assert client.post(f"{base}/{rec['id']}/teach", json={"start_s": 1.0, "end_s": 1.2}).status_code == 400
            taught = client.post(
                f"{base}/{rec['id']}/teach", json={"start_s": 0.0, "end_s": made["seconds"], "new_name": "Bell"}
            )
            assert taught.status_code == 201, taught.text
            assert taught.json()["label"] == "bell" and taught.json()["made"]
            clips = client.get(f"/api/v1/sensors/{sid}/sounds/taught").json()["clips"]
            assert [(c["label"], c["origin"]) for c in clips] == [("bell", "recording")]
            clip = client.get(f"/api/v1/sound-clips/{clips[0]['id']}")
            assert abs(len(sounds.decode_audio(clip.content, RATE)) / RATE - made["seconds"]) < 0.2
            # Taught once: the recording is gone.
            assert client.get(f"{base}/{rec['id']}/audio").status_code == 404
            # Stopped at once: too short. Paused: nothing to record from.
            again = client.post(base).json()
            assert client.post(f"{base}/{again['id']}/stop").status_code == 409
            assert client.patch(f"/api/v1/sensors/{sid}", json={"enabled": False}).status_code == 200
            assert wait_for(lambda: client.post(base).status_code == 409)
            assert client.post(f"/api/v1/sensors/{sid + 1}/sounds/recording").status_code == 404
    finally:
        server.close()


@requires_sound
def test_a_recording_lasts_as_long_as_it_was_held_on_a_stream_that_sends_seconds_at_once(settings, monkeypatch):  # noqa: F811
    # A Home Assistant camera's stream sends a segment of seconds at a time: between Start and Stop
    # nothing may arrive, and the recording is still as long as Start to Stop was.
    monkeypatch.setitem(SOUND, "record_after_s", 4.0)
    server = SoundServer(burst_s=3.0)
    try:
        with TestClient(create_app(settings)) as client:
            body = {"name": "Hall", "kind": "sound", "source_type": "rtsp", "source": server.url}
            sid = client.post("/api/v1/sensors", json={**body, "sound": {"sounds": ["siren"]}}).json()["id"]
            base = f"/api/v1/sensors/{sid}/sounds/recording"
            assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["sound"]["line"] is not None)
            rec = client.post(base).json()
            time.sleep(1.5)
            stopped = client.post(f"{base}/{rec['id']}/stop")
            assert stopped.status_code == 200, stopped.text
            made = stopped.json()
            assert 1.4 <= made["stop_s"] <= 2.5 and made["seconds"] >= made["stop_s"] + 3.9
    finally:
        server.close()


def test_a_built_in_sound_is_corrected_by_the_households_clips():
    threshold = 0.3
    assert sounds.lift(SOUND["own_threshold"], threshold) == pytest.approx(threshold)
    assert sounds.lift(0.0, threshold) == 0.0 and sounds.lift(1.0, threshold) == pytest.approx(1.0)
    assert sounds.correct(0.6, None, None, threshold) == (0.6, False)  # the model alone: as it was
    assert sounds.correct(0.05, 0.95, None, threshold)[0] > threshold  # its own clips hear it
    score, held = sounds.correct(0.6, 0.1, 0.9, threshold)  # clearly like a clip that is not it
    assert score < threshold and held
    assert sounds.correct(0.6, 0.1, 0.6, threshold)[1] is False  # half like it: not at first…
    assert sounds.correct(0.6, 0.1, 0.6, threshold, holding=True)[1]  # …but once held back, it stays
    assert sounds.correct(0.6, 0.1, 0.5, threshold) == (0.6, False)  # not clearly like one: the model stands
    assert sounds.correct(0.6, 0.95, 0.85, threshold)[1] is False  # more like its own clips


def test_a_taught_clip_is_learned_from_its_loud_windows():
    quiet, loud = noise(1.0) * 0.01, recording("siren")
    clip = np.concatenate([quiet, loud, quiet])
    found = sounds.loud_windows(clip, RATE)
    size = int(SOUND["window_s"] * RATE)
    assert 1 < len(found) <= SOUND["teach_windows"] and found.shape[1] == size
    levels = [sounds.level_db(w) for w in found]
    assert (
        min(levels) >= sounds.level_db(sounds.loudest_window(clip, RATE, SOUND["window_s"])) - SOUND["teach_within_db"]
    )
    assert sounds.loud_windows(loud[: RATE // 2], RATE).shape == (1, size)  # shorter than a window: padded


def test_a_threshold_is_suggested_from_the_answers():
    from visionstate import advice

    limits = (0.01, 0.99)
    assert advice.threshold_advice([0.5], [0.3], 0.25, limits)["suggested"] is None  # too few answers
    found = advice.threshold_advice([0.6, 0.7, 0.55], [0.3, 0.35, 0.4], 0.25, limits)
    assert found["current"] == {"threshold": 0.25, "yes_passed": 3, "no_passed": 3} and found["separable"]
    assert found["suggested"] == {"threshold": 0.41, "yes_passed": 3, "no_passed": 0}  # the nearest that does it
    assert advice.threshold_advice([0.6, 0.7], [0.3], 0.5, limits)["suggested"] is None  # already right
    mixed = advice.threshold_advice([0.4, 0.6], [0.5, 0.7], 0.3, limits)
    assert not mixed["separable"]
    # Limits off the hundredths: never a threshold outside them (it could not be set).
    odd = advice.threshold_advice([0.9, 0.95, 0.92], [], 0.97, (0.805, 0.995))
    assert odd["suggested"] == {"threshold": 0.9, "yes_passed": 3, "no_passed": 0}
    assert advice.threshold_advice([0.81], [0.2, 0.3], 0.85, (0.805, 0.995))["suggested"]["threshold"] == 0.81


@requires_sound
def test_a_built_in_sound_learns_from_clips_and_answers_and_what_it_is_not(settings, quick, monkeypatch):  # noqa: F811
    server = SoundServer(speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            body = {"name": "Hall", "kind": "sound", "source_type": "rtsp", "source": server.url}
            sound = {"sounds": ["doorbell", "siren"], "clips": True, "clear_after_s": 2}
            sid = client.post("/api/v1/sensors", json={**body, "sound": sound}).json()["id"]
            upload = f"/api/v1/sensors/{sid}/sounds/upload"

            def view() -> dict:
                return client.get(f"/api/v1/sensors/{sid}").json()

            def live(key: str) -> dict:
                return next(s for s in view()["sound"]["live"] if s["key"] == key)

            def taught() -> dict:
                return client.get(f"/api/v1/sensors/{sid}/sounds/taught").json()

            # The drawn chime taught as the built-in doorbell, and the siren as what the siren is not
            # (a siren on TV, say): one clip of it, as one recording of the TV would be.
            chimes = [
                ("files", (f"bell{i}.m4a", sounds.encode_clip(chime(gain=0.2 + 0.05 * i, seed=i), RATE), "audio/mp4"))
                for i in range(5)
            ]
            assert client.post(upload, files=chimes, data={"label": "doorbell"}).status_code == 201
            siren = recording("siren")
            tv = [("files", ("tv.m4a", sounds.encode_clip(siren, RATE), "audio/mp4"))]
            assert client.post(upload, files=tv, data={"label": "not:siren"}).status_code == 201
            assert client.post(upload, files=chimes[:1], data={"label": "not:dog_bark"}).status_code == 400
            built = {b["key"]: b for b in taught()["builtin"]}
            assert built["doorbell"]["clips"] == 5 and built["siren"]["not_clips"] == 1
            assert taught()["trained"] and taught()["sounds"] == []  # no own sound made

            assert wait_for(lambda: view()["sound"]["line"] is not None)
            for _ in range(4):
                server.play(chime(seed=9))
                if wait_for(lambda: live("doorbell")["on"], timeout=10):
                    break
            assert live("doorbell")["on"]
            # The siren is held back: never heard, and counted.
            server.play(siren)
            assert wait_for(lambda: taught()["builtin"][1]["held"] >= 1, timeout=20)
            time.sleep(len(siren) / RATE / 2)  # all of it sent (at twice the speed)
            heard = client.get("/api/v1/history", params={"sensor": sid, "event": "heard"}).json()["items"]
            assert all(item["state_key"] != "siren" for item in heard) and not live("siren")["on"]

            # Answers about the built-in doorbell teach its clip, and are what a threshold is suggested from.
            def rows() -> list[dict]:
                items = client.get("/api/v1/history", params={"sensor": sid, "event": "heard"}).json()["items"]
                return [i for i in items if i["state_key"] == "doorbell" and i["has_clip"]]

            assert wait_for(lambda: bool(rows()), timeout=20)
            row = rows()[0]
            assert client.post(f"/api/v1/review/{row['id']}", json={"action": "no"}).status_code == 200
            built = {b["key"]: b for b in taught()["builtin"]}
            assert built["doorbell"]["not_clips"] == 1 and built["doorbell"]["advice"]["no"] == 1
            assert client.post(f"/api/v1/review/{row['id']}", json={"action": "yes"}).status_code == 200
            built = {b["key"]: b for b in taught()["builtin"]}
            assert (built["doorbell"]["clips"], built["doorbell"]["not_clips"]) == (6, 0)
            assert built["doorbell"]["advice"]["yes"] == 1 and built["doorbell"]["advice"]["no"] == 0

            # A clip forgotten while it is embedded is left out, not a failed training.
            from visionstate.db import Embedding, Sample

            rt = client.app.state.runtime
            with rt.db.session() as s:
                s.query(Embedding).delete()
            first = taught()["clips"][0]["id"]
            real = rt.clip_embeddings

            def forgetting(model, data):  # noqa: ANN001, ANN202
                with rt.db.session() as s:
                    sample = s.get(Sample, first)
                    if sample is not None:
                        s.delete(sample)
                return real(model, data)

            monkeypatch.setattr(rt, "clip_embeddings", forgetting)
            vectors, labels = rt._sound_vectors(sid, rt.sound_model)
            assert vectors and len(vectors) == len(labels)
    finally:
        server.close()


@requires_sound
def test_a_sound_almost_heard_is_asked_about_and_a_yes_suggests_a_lower_threshold(settings, quick, monkeypatch):  # noqa: F811
    monkeypatch.setitem(ADVICE, "min_answers", 1)
    server = SoundServer(speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            body = {"name": "Hall", "kind": "sound", "source_type": "rtsp", "source": server.url}
            sound = {"sounds": ["siren"], "clips": True, "clear_after_s": 2, "thresholds": {"siren": 0.97}}
            sid = client.post("/api/v1/sensors", json={**body, "sound": sound, "review": {"cooldown_s": 0}}).json()[
                "id"
            ]

            def waiting() -> list[dict]:
                return client.get("/api/v1/review", params={"sensor": sid}).json()["items"]

            def history(event: str) -> list[dict]:
                return client.get("/api/v1/history", params={"sensor": sid, "event": event}).json()["items"]

            assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["sound"]["line"] is not None)
            # The CC0 siren scores about 0.92: never heard at 0.97, but almost.
            for _ in range(3):  # where the windows fall decides; once in a while it is not near enough
                server.play(recording("siren"))
                if wait_for(lambda: bool(waiting()), timeout=20):
                    break
            item = waiting()[0]
            assert item["review_reason"] == "near" and item["state_key"] == "siren" and item["has_clip"]
            assert 0.97 * SOUND["near_share"] <= item["confidence"] < 0.97
            assert [r["id"] for r in history("near")] == [item["id"]] and not history("heard")
            assert client.post(f"/api/v1/review/{item['id']}", json={"action": "yes"}).status_code == 200
            # A yes below the threshold: lower it.
            items = client.get(f"/api/v1/sensors/{sid}/advice").json()["items"]
            found = next(i for i in items if i["key"] == "sound:siren")
            assert found["advice"]["yes"] == 1 and found["advice"]["current"]["yes_passed"] == 0
            lower = found["advice"]["suggested"]
            assert lower is not None and lower["threshold"] < 0.97 and lower["yes_passed"] == 1
            assert client.post(f"/api/v1/sensors/{sid}/advice/sound:siren", json=lower).status_code == 200
            assert client.get(f"/api/v1/sensors/{sid}").json()["sound"]["thresholds"]["siren"] == lower["threshold"]
            # And the yes taught the clip: the siren is heard now.
            server.play(recording("siren"))
            assert wait_for(lambda: bool(history("heard")), timeout=20)
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


def test_a_sound_chosen_away_on_the_home_assistant_tab():
    """Each sound is an entity to choose; the pause switch always stays, and nothing is sent for a
    sound without its entity. Unsettled sensors keep them all."""
    sounds_of = [
        ("doorbell", "Doorbell", "mdi:ear-hearing", "sound"),
        ("dog_bark", "Dog barking", "mdi:dog", "sound"),
    ]
    descriptor = SensorDescriptor("hallway", "Hallway", [], KIND_SOUND, sounds=sounds_of, chosen={"dog_bark": False})
    assert [o["id"] for o in entity_options(descriptor)] == ["doorbell", "dog_bark"]
    assert {topic.split("/")[-2] for topic, _ in discovery_messages("ha", descriptor)} == {"doorbell", "enabled"}
    assert muted_topics(descriptor) == {"visionstate/hallway/sounds/dog_bark/state"}
    assert [uid for uid, _ in main_entities(descriptor)] == ["visionstate_hallway_doorbell"]
    unsettled = SensorDescriptor("hallway", "Hallway", [], KIND_SOUND, sounds=sounds_of, chosen=None)
    assert {o["id"] for o in entity_options(unsettled) if unsettled.entity_on(o["id"], o["default"])} == {
        "doorbell",
        "dog_bark",
    }
    assert discovery_messages("ha", SensorDescriptor("h", "H", [], KIND_SOUND, sounds=sounds_of, in_ha=False)) == []


def test_a_short_gap_in_the_sound_is_not_unavailable(monkeypatch):
    stream = AudioStream("http://127.0.0.1:1/never")  # never connects: fed by hand
    try:
        assert not stream.delivering  # nothing came yet
        stream._append(np.zeros(100, dtype=np.float32))
        stream.connected = False  # the stream dropped: opened again in a moment
        assert stream.delivering
        monkeypatch.setitem(SOUND, "offline_after_s", 0.0)
        assert not stream.delivering
    finally:
        stream.close()


def test_a_stream_that_ends_at_once_is_not_asked_again_at_the_shortest_pause(monkeypatch):
    """Sound arrived, but only for a moment: the pause goes on doubling."""
    monkeypatch.setitem(SOUND, "reconnect_min_s", 0.05)
    monkeypatch.setitem(SOUND, "reconnect_max_s", 10.0)
    opened = []

    def ends_at_once(self):
        opened.append(time.monotonic())
        return True

    monkeypatch.setattr(AudioStream, "_listen", ends_at_once)
    stream = AudioStream("http://127.0.0.1:1/never")
    try:
        time.sleep(1.0)
    finally:
        stream.close()
    gaps = [b - a for a, b in zip(opened, opened[1:], strict=False)]
    assert len(gaps) >= 3 and gaps[2] > gaps[0] * 2


def test_video_times_that_start_again_leave_the_old_video_behind():
    from fractions import Fraction
    from types import SimpleNamespace

    stream = AudioStream("http://127.0.0.1:1/never", video=True)
    try:

        def packet(at: float):
            return SimpleNamespace(pts=int(at * 1000), dts=None, size=1, time_base=Fraction(1, 1000), is_keyframe=True)

        class Bytes(SimpleNamespace):
            def __bytes__(self):
                return b"x"

        for at in (40.0, 41.0, 40.5, 42.0):  # a little out of order is normal
            stream._keep_packet(Bytes(**vars(packet(at))))
        assert len(stream._packets) == 4
        stream._keep_packet(Bytes(**vars(packet(0.0))))
        assert [p.time for p in stream._packets] == [0.0]
    finally:
        stream.close()


@requires_sound
def test_a_paused_sensor_hears_nothing(settings, quick):  # noqa: F811
    server = SoundServer(speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            body = {
                "name": "Hallway",
                "kind": "sound",
                "source_type": "rtsp",
                "source": server.url,
                "sound": {"sounds": ["siren"]},
            }
            sid = client.post("/api/v1/sensors", json=body).json()["id"]

            def on() -> bool:
                return next(iter(client.get(f"/api/v1/sensors/{sid}").json()["sound"]["live"]))["on"]

            assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["sound"]["line"] is not None)
            server.play(recording("siren"))
            assert wait_for(on)
            assert client.patch(f"/api/v1/sensors/{sid}", json={"enabled": False}).status_code == 200
            assert wait_for(lambda: not on())
            rows = client.get("/api/v1/history", params={"sensor": sid, "event": "quiet"}).json()["items"]
            assert rows and rows[0]["state_key"] == "siren"
            # Listening again in quiet: it does not come back on the windows from before the pause.
            assert client.patch(f"/api/v1/sensors/{sid}", json={"enabled": True}).status_code == 200
            time.sleep(1.5)
            assert not on()
    finally:
        server.close()


@requires_sound
def test_a_stream_of_pure_digital_silence_is_said_to_be(settings, monkeypatch):  # noqa: F811
    """An ESP32 without its microphone (or muted) sends zeros: the sensor is "ok", and deaf."""
    monkeypatch.setitem(SOUND, "dead_silence_s", 2.0)
    server = SoundServer(background=np.zeros(RATE, dtype=np.float32), speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            body = {"name": "Hall", "kind": "sound", "source_type": "rtsp", "source": server.url}
            sid = client.post("/api/v1/sensors", json={**body, "sound": {"sounds": ["siren"]}}).json()["id"]

            def view() -> dict:
                return client.get(f"/api/v1/sensors/{sid}").json()

            assert not view()["sound"]["dead_silent"]
            assert wait_for(lambda: view()["sound"]["dead_silent"])
            assert view()["status"] == "ok"  # it hears its stream: only what is in it is wrong
            server.play(noise(1.0))  # any sound at all, the quietest room's included
            assert wait_for(lambda: not view()["sound"]["dead_silent"])
    finally:
        server.close()


@requires_sound
def test_it_goes_on_listening_while_a_clip_waits_for_its_video(settings, quick, monkeypatch):  # noqa: F811
    from concurrent.futures import Future

    monkeypatch.setitem(SOUND, "video_wait_s", 30.0)
    monkeypatch.setattr(AudioStream, "video_clip", lambda self, samples, start: Future())  # never comes
    server = SoundServer(speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            body = {
                "name": "Hallway",
                "kind": "sound",
                "source_type": "rtsp",
                "source": server.url,
                "sound": {"sounds": ["siren", "dog_bark"], "clips": True, "clear_after_s": 2},
            }
            sid = client.post("/api/v1/sensors", json=body).json()["id"]

            def on(key: str) -> bool:
                view = client.get(f"/api/v1/sensors/{sid}").json()
                return next(s for s in view["sound"]["live"] if s["key"] == key)["on"]

            assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["sound"]["line"] is not None)
            server.play(recording("siren"))
            assert wait_for(lambda: on("siren"))
            time.sleep((SOUND["clip_after_s"] + 1) / 2)  # its clip is due now, and waits
            server.play(recording("dog_bark"))
            assert wait_for(lambda: on("dog_bark"), timeout=15)
    finally:
        server.close()


@requires_sound
def test_review_asks_once_the_clip_is_there_and_an_answer_without_one_stands(settings, monkeypatch):  # noqa: F811
    monkeypatch.setitem(SOUND, "clip_before_s", 1.0)
    monkeypatch.setitem(SOUND, "clip_after_s", 8.0)  # the clip comes well after the sound went off
    monkeypatch.setitem(SOUND, "unsure_share", 1.0)  # always asked: no test on a score near a line
    server = SoundServer(speed=2.0)
    try:
        with TestClient(create_app(settings)) as client:
            body = {
                "name": "Hallway",
                "kind": "sound",
                "source_type": "rtsp",
                "source": server.url,
                "review": {"cooldown_s": 0},
                "sound": {"sounds": ["siren"], "clips": True, "clear_after_s": 1, "thresholds": {"siren": 0.5}},
            }
            sid = client.post("/api/v1/sensors", json=body).json()["id"]
            assert wait_for(lambda: client.get(f"/api/v1/sensors/{sid}").json()["sound"]["line"] is not None)

            def waiting() -> list[dict]:
                return client.get("/api/v1/review", params={"sensor": sid}).json()["items"]

            server.play(recording("siren"))
            assert wait_for(waiting, timeout=60)
            item = waiting()[0]
            assert item["has_clip"]
            # The clip goes (the history's limits): "new" cannot teach it, and the answer is kept.
            from visionstate.db import Prediction

            with client.app.state.runtime.db.session() as s:
                row = s.get(Prediction, item["id"])
                assert row is not None
                row.frame = None
            answer = client.post(f"/api/v1/review/{item['id']}", json={"action": "new", "value": "Ours"})
            assert answer.status_code == 200
            assert not waiting()
    finally:
        server.close()


def test_only_a_stream_address_is_listened_to(settings):  # noqa: F811
    """FFmpeg opens local files too: refused when saved, and by the stream itself (an older sensor)."""
    with TestClient(create_app(settings)) as client:
        body = {"name": "Hall", "kind": "sound", "source_type": "rtsp", "sound": {"sounds": ["siren"]}}
        for bad in ("file:///data/options.json", "concat:/etc/passwd", "/data/options.json"):
            assert client.post("/api/v1/sensors", json={**body, "source": bad}).status_code == 400
        sid = client.post("/api/v1/sensors", json={**body, "source": "rtsp://cam/live"}).json()["id"]
        assert client.patch(f"/api/v1/sensors/{sid}", json={"source": "file:///etc/passwd"}).status_code == 400
        assert client.get(f"/api/v1/sensors/{sid}").json()["source"] == "rtsp://cam/live"
    stream = AudioStream("file:///etc/passwd")
    try:
        assert wait_for(lambda: "Unsupported address" in stream.error, timeout=5)
    finally:
        stream.close()


def test_a_sensor_needs_something_to_listen_for(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        body = {"name": "Hall", "kind": "sound", "source_type": "rtsp", "source": "rtsp://cam/live"}
        assert client.post("/api/v1/sensors", json={**body, "sound": {"sounds": []}}).status_code == 400
        sid = client.post("/api/v1/sensors", json={**body, "sound": {"sounds": ["siren"]}}).json()["id"]
        assert client.patch(f"/api/v1/sensors/{sid}", json={"sound": {"sounds": []}}).status_code == 400


# --- a Home Assistant camera's own sound --------------------------------------------------------------


def test_a_home_assistant_camera_gives_its_stream_address(settings):  # noqa: F811
    """camera/stream over the WebSocket API: the address of its HLS stream, asked with the token."""
    import asyncio
    import json

    from websockets.asyncio.server import serve

    from visionstate.sources import HomeAssistant, SourceError

    async def handler(ws):  # noqa: ANN001
        await ws.send(json.dumps({"type": "auth_required"}))
        assert json.loads(await ws.recv())["access_token"] == "token"
        await ws.send(json.dumps({"type": "auth_ok"}))
        request = json.loads(await ws.recv())
        assert request["type"] == "camera/stream" and request["format"] == "hls"
        if request["entity_id"] == "camera.snapshots":
            error = {"code": "home_assistant_error", "message": "camera.snapshots does not support play stream service"}
            await ws.send(json.dumps({"id": request["id"], "type": "result", "success": False, "error": error}))
            return
        result = {"url": "/api/hls/abc123/master_playlist.m3u8"}
        await ws.send(json.dumps({"id": request["id"], "type": "result", "success": True, "result": result}))

    async def scenario() -> None:
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            ha = HomeAssistant(replace(settings, ha_url=f"http://127.0.0.1:{port}", ha_token="token"))
            try:
                url, headers = await ha.stream_address("camera.front_door")
                assert url == f"http://127.0.0.1:{port}/api/hls/abc123/master_playlist.m3u8"
                assert headers == {"Authorization": "Bearer token"}
                with pytest.raises(SourceError, match="does not support play stream"):
                    await ha.stream_address("camera.snapshots")
            finally:
                await ha.close()

    asyncio.run(scenario())


def test_cameras_say_whether_they_stream(settings):  # noqa: F811
    """Only a camera with a stream in Home Assistant has sound to listen to (CameraEntityFeature.STREAM)."""
    import asyncio

    import httpx

    from visionstate.sources import HomeAssistant

    states = [
        {"entity_id": "camera.door", "state": "idle", "attributes": {"friendly_name": "Door", "supported_features": 2}},
        {"entity_id": "camera.snap", "state": "idle", "attributes": {"friendly_name": "Snap", "supported_features": 0}},
        {"entity_id": "camera.old", "state": "idle", "attributes": {"friendly_name": "Old"}},
        {"entity_id": "light.hall", "state": "on", "attributes": {}},
    ]

    async def scenario() -> list[dict]:
        ha = HomeAssistant(replace(settings, ha_url="http://ha", ha_token="token"))
        ha._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=states)))
        try:
            return await ha.cameras()
        finally:
            await ha.close()

    assert {c["entity_id"]: c["stream"] for c in asyncio.run(scenario())} == {
        "camera.door": True,
        "camera.snap": False,
        "camera.old": False,
    }


def test_a_camera_stream_is_asked_for_each_time_it_is_opened(monkeypatch):
    """The address holds a token that ends with Home Assistant's stream: asked for again at every
    reconnect (a failure is shown and tried again), and opened with the headers it came with."""
    import asyncio

    from visionstate.sources import SourceError, open_audio

    monkeypatch.setitem(SOUND, "reconnect_min_s", 0.1)
    server = SoundServer()
    asked: list[str] = []

    class FakeHa:
        async def stream_address(self, entity_id: str) -> tuple[str, dict[str, str]]:
            asked.append(entity_id)
            if len(asked) == 1:
                raise SourceError(f"{entity_id}: the camera is starting")
            return server.url, {"Authorization": "Bearer token"}

    async def scenario() -> None:
        stream = open_audio(FakeHa(), "ha_camera", "camera.front_door")  # type: ignore[arg-type]
        try:
            for _ in range(200):
                if stream.total:
                    break
                await asyncio.sleep(0.05)
            assert stream.total and stream.info["codec"] == "pcm_s16le"
        finally:
            stream.close()

    try:
        asyncio.run(scenario())
        assert asked[:2] == ["camera.front_door", "camera.front_door"]
        assert server.headers and server.headers[0]["Authorization"] == "Bearer token"
    finally:
        server.close()


def test_a_sound_sensor_can_listen_to_a_home_assistant_camera(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        body = {"name": "Door", "kind": "sound", "source_type": "ha_camera", "sound": {"sounds": ["siren"]}}
        assert client.post("/api/v1/sensors", json={**body, "source": "not an entity"}).status_code == 400
        made = client.post("/api/v1/sensors", json={**body, "source": "camera.front_door"})
        assert made.status_code == 201, made.text
        sid = made.json()["id"]
        # No Home Assistant here: said on the sensor, as any stream that does not open.
        assert wait_for(lambda: "not configured" in client.get(f"/api/v1/sensors/{sid}").json()["live"]["error"])
        moved = client.patch(f"/api/v1/sensors/{sid}", json={"source_type": "rtsp", "source": "rtsp://cam/live"})
        assert moved.status_code == 200 and moved.json()["source_type"] == "rtsp"
        assert client.patch(f"/api/v1/sensors/{sid}", json={"source_type": "ha_camera"}).status_code == 400
        assert client.patch(f"/api/v1/sensors/{sid}", json={"source_type": "http"}).status_code == 400
        preview = {"source_type": "http", "source": "http://cam/snap.jpg"}
        assert client.post("/api/v1/sounds/preview", json=preview).status_code == 400


@requires_sound
def test_a_sound_in_the_middle_of_a_burst_is_heard(settings):  # noqa: F811
    """A Home Assistant camera's stream (HLS) delivers seconds of sound at once: every window in it
    is analysed, not only the newest (a siren in the middle of a segment was missed)."""
    server = SoundServer(burst_s=8.0, speed=2.0)
    siren = recording("siren")[: 3 * RATE]  # shorter than a burst
    try:
        with TestClient(create_app(settings)) as client:
            body = {"name": "Door", "kind": "sound", "source_type": "rtsp", "source": server.url}
            sid = client.post("/api/v1/sensors", json={**body, "sound": {"sounds": ["siren"]}}).json()["id"]

            def view() -> dict:
                return client.get(f"/api/v1/sensors/{sid}").json()

            assert wait_for(lambda: view()["sound"]["line"] is not None)
            heard = {"sensor": sid, "event": "heard"}
            # Played while the server waits between bursts, it begins the next one and is followed by
            # quiet: the burst's newest window never has it. A sound is now and then not heard at all
            # (one run in CI): played again, up to three times.
            for _ in range(3):
                server.play(np.concatenate([siren, np.zeros(4 * RATE, dtype=np.float32)]))
                found = wait_for(lambda: client.get("/api/v1/history", params=heard).json()["items"], timeout=15)
                if found:
                    break
            sound = view()["sound"]
            assert found, (sound["line"], sound["stream"], [round(x) for x in sound["levels"]], view()["live"])
    finally:
        server.close()
