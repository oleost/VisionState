"""The ESP32 guide (esphome.py, api/esphome.py): the configuration it writes is the one the examples
in esphome/ use, its pins are the packages' defaults, and the check talks to a device as a sensor
will."""

import base64
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from visionstate import esphome
from visionstate.main import create_app
from visionstate.settings import CAMERA_CLOCKS_MHZ, CAMERA_GAINS, CAMERA_LIMITS, CAMERA_SIZES, ESPHOME

from .test_storage import settings  # noqa: F401 (fixture)

ESPHOME_DIR = Path(__file__).resolve().parents[3] / "esphome"
in_repo = pytest.mark.skipif(not ESPHOME_DIR.is_dir(), reason="no repository checkout")
EXAMPLES = {
    ("esp32-devkit", "microphone"): "esp32-devkit-microphone.yaml",
    ("esp32-cam", "camera"): "esp32-cam-camera.yaml",
    ("esp32-cam", "camera_microphone"): "esp32-cam-camera-microphone.yaml",
    ("xiao-esp32s3-sense", "camera_microphone"): "xiao-esp32s3-sense-camera-microphone.yaml",
    ("esp32s3-devkit", "microphone"): "esp32s3-devkit-microphone.yaml",
    ("waveshare-esp32s3-lcd2", "microphone"): "waveshare-esp32s3-lcd2-microphone.yaml",
    ("waveshare-esp32c5-kit", "microphone"): "waveshare-esp32c5-kit-microphone.yaml",
}


def files_of(yaml_text: str) -> list[str]:
    return re.findall(r"^\s+- (esphome/packages/[\w.-]+\.yaml)", yaml_text, re.MULTILINE)


def substitutions(yaml_text: str) -> dict[str, str]:
    block = yaml_text.split("substitutions:", 1)[1]
    found = {}
    for line in block.splitlines()[1:]:
        if line and not line.startswith(" "):
            break
        m = re.match(r"\s+#?\s*(\w+): (\S+)", line)
        if m:
            found[m[1]] = m[2]
    return found


@in_repo
@pytest.mark.parametrize(("board", "kind"), list(EXAMPLES))
def test_the_guide_writes_what_the_examples_use(board, kind):
    example = (ESPHOME_DIR / "examples" / EXAMPLES[(board, kind)]).read_text(encoding="utf-8")
    mute = "mute_pin" in ESPHOME["boards"][board]["pins"]
    made = esphome.configuration(board, kind, "Front door", mute=mute)
    assert files_of(made["yaml"]) == files_of(example)
    for f in files_of(made["yaml"]):  # every package it names exists
        assert (ESPHOME_DIR.parent / f).is_file(), f
    assert made["name"] == "front-door" and 'friendly_name: "Front door"' in made["yaml"]
    assert f"@{ESPHOME['ref']}" in made["yaml"]
    # The user's values are fields for the page to fill in (never sent to VisionState).
    for name in made["fields"]:
        assert "{{" + name + "}}" in made["yaml"]
    assert "!secret" not in made["yaml"]
    # Locked like ESPHome's own new devices: firmware over the air only with the API key (an open
    # OTA port takes any firmware from the network), and a password on the fallback hotspot (whose
    # captive portal takes firmware too).
    for text in (made["yaml"], example):
        # The newest of the line at every Install (ESPHome's own default, a day, built an old one).
        assert text.count(f"refresh: {ESPHOME['refresh']}") == 2
        assert "    encryption: {}" in text.split("\nota:\n", 1)[1]
        assert "  ap_password: " in text
    assert made["sound"] == ("microphone" in kind) and made["picture"] == ("camera" in kind)


@in_repo
def test_every_board_says_whether_the_firmware_has_run_on_one():
    tried = {board: spec["tried"] for board, spec in ESPHOME["boards"].items()}
    assert all(isinstance(t, bool) for t in tried.values())
    assert tried["esp32-devkit"] and tried["esp32-cam"]  # the boards checked in docs/ESP32_PLAN.md §5


@pytest.mark.parametrize("board", list(ESPHOME["boards"]))
def test_the_boards_pins_are_the_packages_defaults(board):
    package = (ESPHOME_DIR / "packages" / f"board-{board}.yaml").read_text(encoding="utf-8")
    defaults = substitutions(package)
    for key, pin in ESPHOME["boards"][board]["pins"].items():
        assert defaults[key] == pin, (board, key)


@in_repo
def test_the_firmware_takes_every_camera_setting_the_app_offers():
    # A sensor's settings (settings.CAMERA_*) are what the snapshot can ask for (visionstate_stream.cpp).
    component = (ESPHOME_DIR / "components" / "visionstate_stream" / "visionstate_stream.cpp").read_text(
        encoding="utf-8"
    )
    sizes = re.findall(r'\{"(\d+x\d+)", FRAMESIZE_', component)
    assert sizes == list(CAMERA_SIZES)
    clocks = {int(mhz) for mhz in re.findall(r"mhz == (\d+)", component)}
    assert clocks == set(CAMERA_CLOCKS_MHZ)
    assert re.search(r"quality >= (\d+) && quality <= (\d+)", component).groups() == ("6", "63")
    assert CAMERA_LIMITS["quality"][0] >= 6 and CAMERA_LIMITS["quality"][1] <= 63
    assert max(CAMERA_GAINS) == 128 and all(g & (g - 1) == 0 for g in CAMERA_GAINS)  # 2 << GAINCEILING_*
    assert int(re.search(r"ms >= 0 && ms <= (\d+)", component)[1]) >= CAMERA_LIMITS["exposure_ms"][1]
    # The camera starts at its largest size, so every size asked for fits its buffers.
    for package in ("camera-esp32-cam.yaml", "camera-xiao-esp32s3-sense.yaml"):
        text = (ESPHOME_DIR / "packages" / package).read_text(encoding="utf-8")
        assert substitutions(text)["camera_resolution"] == CAMERA_SIZES[-1]


def test_a_mute_switch_only_where_asked_and_a_board_only_for_what_it_can_be():
    assert "mute-pin.yaml" not in esphome.configuration("esp32-devkit", "microphone", "Hall", mute=False)["yaml"]
    assert "mute_pin: GPIO32" in esphome.configuration("esp32-devkit", "microphone", "Hall", mute=True)["yaml"]
    with pytest.raises(esphome.ESPHomeError):
        esphome.configuration("esp32-devkit", "camera", "Hall", mute=False)
    with pytest.raises(esphome.ESPHomeError):
        esphome.configuration("esp32-s9", "microphone", "Hall", mute=False)
    with pytest.raises(esphome.ESPHomeError):
        esphome.configuration("esp32-cam", "camera", 'Say "hi"', mute=False)
    # The XIAO's microphone is on the board: no wiring to write, and no I2S port kept for its camera.
    xiao = esphome.configuration("xiao-esp32s3-sense", "microphone", "Hall", mute=False)["yaml"]
    assert "microphone-pdm.yaml" in xiao and "mic_" not in xiao and "The wiring" not in xiao
    both = files_of(esphome.configuration("xiao-esp32s3-sense", "camera_microphone", "Hall", mute=True)["yaml"])
    assert not any(f.endswith("-microphone.yaml") for f in both)
    # How a camera is set up is each sensor's (its settings in VisionState), not the device's file.
    assert "camera_use" not in esphome.configuration("esp32-cam", "camera", "Hall", mute=False)["yaml"]
    # A board with no pin for a mute switch takes none; its wiring is the user's (an INMP441 on its headers).
    with pytest.raises(esphome.ESPHomeError):
        esphome.configuration("waveshare-esp32s3-lcd2", "microphone", "Hall", mute=True)
    waveshare = esphome.configuration("waveshare-esp32s3-lcd2", "microphone", "Hall", mute=False)["yaml"]
    assert "mic_sck: GPIO43" in waveshare and "mute" not in waveshare
    assert "mute_pin: GPIO7" in esphome.configuration("esp32s3-devkit", "microphone", "Hall", mute=True)["yaml"]
    assert esphome.device_name("  Garasje / Bod 2 ") == "garasje-bod-2"
    assert esphome.device_name("???") == "visionstate"


def test_a_microphone_can_be_a_voice_assistant_too_its_answer_on_a_media_player():
    made = esphome.configuration("esp32-devkit", "microphone", "Hall", mute=True, assistant="media_player.kitchen")
    files = files_of(made["yaml"])
    # After the microphone and its mute switch, so they are there for it.
    assert files[-2:] == ["esphome/packages/mute-pin.yaml", "esphome/packages/voice-assistant-ha-wake-word.yaml"]
    assert 'assistant_media_player: "media_player.kitchen"' in made["yaml"] and made["assistant"]
    later = esphome.configuration("esp32-devkit", "microphone", "Hall", mute=False, assistant="")
    assert 'assistant_media_player: ""' in later["yaml"]  # chosen later: Answer on
    only = esphome.configuration("esp32-devkit", "microphone", "Hall", mute=False)
    assert "voice-assistant" not in only["yaml"] and not only["assistant"]
    with pytest.raises(esphome.ESPHomeError, match="Only a microphone"):
        esphome.configuration("esp32-cam", "camera_microphone", "Hall", mute=False, assistant="")
    with pytest.raises(esphome.ESPHomeError, match="media player"):
        esphome.configuration("esp32-devkit", "microphone", "Hall", mute=False, assistant='x"\nevil: 1')


def test_the_addresses_carry_the_login_and_refuse_what_would_break_them():
    found = esphome.addresses(" 192.168.1.50/ ", "abc123", sound=True, picture=True)
    assert found == {
        "sound": "http://visionstate:abc123@192.168.1.50:8081/audio.wav",
        "picture": "http://visionstate:abc123@192.168.1.50:8081/snapshot.jpg",
    }
    with pytest.raises(esphome.ESPHomeError):
        esphome.addresses("192.168.1.50", "a@b", sound=True, picture=False)
    with pytest.raises(esphome.ESPHomeError):
        esphome.addresses("cam; rm", "abc", sound=True, picture=False)


class FakeDevice(BaseHTTPRequestHandler):
    """A device as the firmware answers: the login, a WAV header, a JPEG."""

    password = "secret"

    def log_message(self, *args):  # noqa: ANN002
        pass

    def do_GET(self):  # noqa: N802
        login = base64.b64encode(f"visionstate:{self.password}".encode()).decode()
        if self.headers.get("Authorization") != f"Basic {login}":
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="VisionState"')
            self.end_headers()
            return
        if self.path == "/audio.wav":
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.end_headers()
            self.wfile.write(b"RIFF\xff\xff\xff\xffWAVEfmt " + bytes(32) + bytes(3200))
        elif self.path == "/snapshot.jpg":
            body = b"\xff\xd8" + bytes(100)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def device(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDevice)
    monkeypatch.setitem(ESPHOME, "port", server.server_address[1])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def test_the_check_tells_a_right_password_from_a_wrong_one(settings, device):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        body = {"host": "127.0.0.1", "password": "secret", "sound": True, "picture": True}
        got = client.post("/api/v1/esphome/check", json=body).json()
        assert got["sound"]["ok"] and got["picture"]["ok"], got
        assert got["addresses"]["sound"].endswith("/audio.wav")
        wrong = client.post("/api/v1/esphome/check", json={**body, "password": "nope"}).json()
        assert wrong["sound"] == {"ok": False, "status": 401} and wrong["picture"]["status"] == 401
        made = client.post(
            "/api/v1/esphome/configuration", json={"board": "esp32-cam", "kind": "camera", "name": "Garage"}
        )
        assert made.status_code == 200 and made.json()["picture"]
        bad = client.post("/api/v1/esphome/configuration", json={"board": "esp32-cam", "kind": "x", "name": "G"})
        assert bad.status_code == 400
        assert set(client.get("/api/v1/config").json()["esphome"]["boards"]) == set(ESPHOME["boards"])


def test_the_password_is_asked_of_the_device_named_like_its_address_entity_first():
    services = ["garage_cam_stream_password", "hall_stream_password", "hall_restart", "front_door_stream_password"]
    assert esphome.password_services("sensor.front_door_ip_address", services) == [
        "front_door_stream_password",
        "garage_cam_stream_password",
        "hall_stream_password",
    ]
    assert esphome.password_services("sensor.cellar_ip_address", ["hall_restart"]) == []


def test_the_device_tells_its_password_through_home_assistant(settings, device):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        asked = []
        answers = {
            # Another device of ours, named alike: its address shows it is not this one.
            "front_door_stream_password": {"password": "other", "address": "10.0.0.8"},
            "front_door_2_stream_password": {"password": "secret", "address": "127.0.0.1"},
        }

        async def raw_states():
            return {"sensor.front_door_ip_address": {"state": "127.0.0.1"}}

        async def service_names(domain):
            assert domain == "esphome"
            return ["front_door_restart", *answers]

        async def call_service_response(domain, service, data):
            asked.append(service)
            return answers[service]

        rt.ha.raw_states, rt.ha.service_names, rt.ha.call_service_response = (
            raw_states,
            service_names,
            call_service_response,
        )
        body = {"entity_id": "sensor.front_door_ip_address", "sound": True, "picture": True}
        got = client.post("/api/v1/esphome/connect", json=body).json()
        assert asked == ["front_door_stream_password", "front_door_2_stream_password"]
        assert got["told"] and got["sound"]["ok"] and got["picture"]["ok"], got
        assert "visionstate:secret@127.0.0.1" in got["addresses"]["sound"]

        answers.clear()  # an older firmware: nothing tells it, so it is typed
        missing = client.post("/api/v1/esphome/connect", json=body).json()
        assert not missing["told"] and "1.3.0" in missing["detail"] and missing["addresses"] == {}


def test_devices_are_the_ip_address_entities_home_assistant_has(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime

        async def entities():
            return [
                {"entity_id": "sensor.front_door_ip_address", "name": "Front door IP address", "state": "10.0.0.7"},
                {"entity_id": "light.front_door_flash", "name": "Front door Flash", "state": "off"},
                {"entity_id": "sensor.old_ip_address", "name": "Old IP address", "state": "unavailable"},
                {"entity_id": "sensor.temperature", "name": "Temperature", "state": "21"},
            ]

        async def discovered(domain):
            assert domain == "esphome"
            return ["vs-garage"]

        async def registries():
            # The front door is a camera of ours (offered as a sensor's source with its password).
            # Its flash is named after its room, not after its IP address entity: found by its device.
            return [
                {"entity_id": "sensor.front_door_ip_address", "device_id": "d1", "original_name": "IP address"},
                {"entity_id": "camera.front_door_camera", "device_id": "d1", "original_name": "Camera"},
                {"entity_id": "light.hall_1_front_door_flash", "device_id": "d1", "original_name": "Flash"},
            ], [{"id": "d1", "model": ESPHOME["model"]}]

        rt.ha.entities = entities
        rt.ha.discovered = discovered
        rt.ha.registries = registries
        got = client.get("/api/v1/esphome/devices").json()
        assert got["waiting"] == ["vs-garage"]  # found, not added yet
        assert got["devices"] == [
            {
                "entity_id": "sensor.front_door_ip_address",
                "name": "Front door",
                "address": "10.0.0.7",
                "light": "light.hall_1_front_door_flash",
                "ours": True,
                "camera": True,
                "microphone": False,
            }
        ]

        async def no_websocket():
            raise RuntimeError("no WebSocket")

        rt.ha.registries = no_websocket  # listed all the same, none as ours, the flash by its name
        device = client.get("/api/v1/esphome/devices").json()["devices"][0]
        assert not device["ours"] and device["light"] == "light.front_door_flash"


def test_discovered_esphome_devices_are_read_from_home_assistants_flows(monkeypatch):
    import asyncio

    from visionstate import ha_events

    async def commands(url, token, cmds):
        assert cmds == [{"type": "config_entries/flow/progress"}]
        return [
            [
                {"handler": "esphome", "context": {"source": "zeroconf", "title_placeholders": {"name": "vs-hall"}}},
                # With a friendly name ESPHome shows "Friendly (device-name)": the device name is what counts.
                {
                    "handler": "esphome",
                    "context": {"source": "zeroconf", "title_placeholders": {"name": "Test cam (test-cam)"}},
                },
                {"handler": "hue", "context": {"source": "zeroconf", "title_placeholders": {"name": "Bridge"}}},
                {"handler": "esphome", "context": {"source": "user"}},  # someone adding one by hand
            ]
        ]

    monkeypatch.setattr(ha_events, "ws_commands", commands)
    assert asyncio.run(ha_events.discovered("ws://ha", "t", "esphome")) == ["vs-hall", "test-cam"]


@in_repo
def test_the_firmwares_version_and_controls_are_the_apps():
    # The version a device reports is the one the app compares with (settings.ESPHOME).
    common = (ESPHOME_DIR / "packages" / "common.yaml").read_text(encoding="utf-8")
    assert re.search(r'^    version: "([^"]+)"', common, re.MULTILINE)[1] == ESPHOME["firmware_version"]
    # The flash: its light's name in the camera package, and how long the camera keeps it on at most.
    camera = (ESPHOME_DIR / "packages" / "camera-esp32-cam.yaml").read_text(encoding="utf-8")
    # The pin the camera is powered down by while firmware is installed is the camera's own.
    assert re.search(r"power_down_pin: GPIO(\d+)", camera)[1] == re.search(r"camera_power_down_pin: (\d+)", camera)[1]
    assert re.search(r"id: vs_flash\n\s+name: (\S+)", camera)[1] == ESPHOME["flash_name"]
    stream = (ESPHOME_DIR / "components" / "visionstate_stream" / "visionstate_stream.cpp").read_text(encoding="utf-8")
    assert int(re.search(r"FLASH_SETTLE_MAX_MS = (\d+);", stream)[1]) == ESPHOME["flash_max_s"] * 1000
    project = re.search(r"^    name: (\S+)", common, re.MULTILINE)[1]
    assert project.split(".")[-1] == ESPHOME["model"]
    # Every control the Live tab shows is an entity of that name and kind in a package.
    packages = {p.name: p.read_text(encoding="utf-8") for p in (ESPHOME_DIR / "packages").glob("*.yaml")}
    for name, kind in ESPHOME["controls"].items():
        assert any(
            re.search(rf"^{kind}:\n(?:  .*\n|\n)*?    name: {re.escape(name)}$", text, re.MULTILINE)
            for text in packages.values()
        ), (name, kind)


def test_firmware_versions_compare_with_dev_before_the_release():
    assert esphome.firmware_version("1.1.0 (ESPHome 2026.10.2)") == "1.1.0"
    assert esphome.firmware_version(None) is None
    assert esphome.outdated("1.0.0-dev", "1.0.0") and esphome.outdated("1.0.0", "1.1.0")
    assert not esphome.outdated("1.1.0", "1.1.0") and not esphome.outdated("1.2.0", "1.1.0")
    assert not esphome.outdated(None, "1.1.0")


ORIENTATIONS = ["Normal", "Mirrored", "Upside down", "Upside down, mirrored"]


def registries(sw_version="1.0.0-dev (ESPHome 2026.10.2)", model="visionstate"):
    """Home Assistant's registries and states for one ESP32 microphone of ours, and another device."""

    def entity(entity_id, name, device="dev1", **extra):
        return {
            "entity_id": entity_id,
            "original_name": name,
            "name": None,
            "platform": "esphome",
            "device_id": device,
            **extra,
        }

    entities = [
        entity("sensor.hall_ip_address", "IP address"),
        entity("number.hall_microphone_gain", "Microphone gain"),
        entity("switch.hall_microphone", "Microphone"),
        entity("select.hall_picture_orientation", "Picture orientation"),
        entity("text.hall_answer_on", "Answer on"),
        entity("sensor.hall_uptime", "Uptime"),
        entity("switch.hall_visionstate_stream", "VisionState stream", disabled_by="user"),
        entity("sensor.other_ip_address", "IP address", device="dev2"),
        entity("switch.other_microphone", "Microphone", device="dev2"),
    ]
    devices = [
        {"id": "dev1", "name": "hall", "name_by_user": None, "model": model, "sw_version": sw_version},
        {"id": "dev2", "name": "other", "model": "visionstate", "sw_version": "1.1.0 (ESPHome 2026.10.2)"},
    ]
    states = {
        "sensor.hall_ip_address": {"state": "10.0.0.9"},
        "number.hall_microphone_gain": {"state": "4.0", "attributes": {"min": 1, "max": 64, "step": 1}},
        "switch.hall_microphone": {"state": "on"},
        "select.hall_picture_orientation": {"state": "Normal", "attributes": {"options": ORIENTATIONS}},
        "text.hall_answer_on": {"state": "media_player.kitchen"},
        "sensor.other_ip_address": {"state": "10.0.0.10"},
        "switch.other_microphone": {"state": "off"},
    }
    return entities, devices, states


def test_a_sound_sensors_device_is_found_by_its_address_with_its_own_controls():
    entities, devices, states = registries()
    found = esphome.device_controls("10.0.0.9", entities, devices, states)
    assert found["name"] == "hall" and found["ours"] and found["firmware"] == "1.0.0-dev"
    assert found["outdated"] and found["latest"] == ESPHOME["firmware_version"]
    # Its own controls only (not the other device's, not one switched off in Home Assistant), in order.
    assert [c["entity_id"] for c in found["controls"]] == [
        "switch.hall_microphone",
        "number.hall_microphone_gain",
        "select.hall_picture_orientation",
        "text.hall_answer_on",
    ]
    assert found["controls"][2]["options"] == ORIENTATIONS
    assert found["controls"][1] | {} == {
        "entity_id": "number.hall_microphone_gain", "kind": "number", "name": "Microphone gain",
        "state": "4.0", "min": 1, "max": 64, "step": 1,
    }  # fmt: skip
    assert esphome.device_controls("10.0.0.99", entities, devices, states) is None
    # Another firmware (a voice assistant's own) has the controls, but no version of ours to compare.
    other = esphome.device_controls("10.0.0.9", entities, registries(model="home-assistant-voice")[1], states)
    assert not other["ours"] and not other["outdated"] and other["firmware"] is None


def test_the_live_tab_sets_the_devices_controls_through_home_assistant(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        entities, devices, states = registries()
        calls = []

        async def regs():
            return entities, devices

        async def raw_states():
            return states

        async def switch(entity_id, on):
            calls.append(("switch", entity_id, on))

        async def call_service(domain, service, data):
            calls.append((f"{domain}.{service}", data["entity_id"], data.get("value", data.get("option"))))

        rt.ha.registries, rt.ha.raw_states, rt.ha.switch, rt.ha.call_service = regs, raw_states, switch, call_service
        body = {
            "name": "Hall",
            "kind": "sound",
            "source_type": "rtsp",
            "source": "http://visionstate:pw@10.0.0.9:8081/audio.wav",
        }
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        got = client.get(f"/api/v1/sensors/{sid}/device").json()
        assert got["error"] is None and got["device"]["outdated"]
        assert len(got["device"]["controls"]) == 4
        url = f"/api/v1/sensors/{sid}/device"
        assert client.post(url, json={"entity_id": "switch.hall_microphone", "value": False}).status_code == 200
        assert client.post(url, json={"entity_id": "number.hall_microphone_gain", "value": 8}).status_code == 200
        orientation = {"entity_id": "select.hall_picture_orientation", "value": "Upside down, mirrored"}
        assert client.post(url, json=orientation).status_code == 200
        answer = {"entity_id": "text.hall_answer_on", "value": "media_player.living_room"}
        assert client.post(url, json=answer).status_code == 200
        assert calls == [
            ("switch", "switch.hall_microphone", False),
            ("number.set_value", "number.hall_microphone_gain", 8.0),
            ("select.select_option", "select.hall_picture_orientation", "Upside down, mirrored"),
            ("text.set_value", "text.hall_answer_on", "media_player.living_room"),
        ]
        # Only this device's controls, and the gain within its range.
        assert client.post(url, json={"entity_id": "switch.other_microphone", "value": True}).status_code == 404
        assert client.post(url, json={"entity_id": "light.kitchen", "value": True}).status_code == 404
        assert client.post(url, json={"entity_id": "number.hall_microphone_gain", "value": 99}).status_code == 400
        assert client.post(url, json={"entity_id": "number.hall_microphone_gain", "value": "8"}).status_code == 400
        assert client.post(url, json={**orientation, "value": "Sideways"}).status_code == 400
        assert client.post(url, json={**answer, "value": "light.kitchen"}).status_code == 400
        assert client.post(url, json={**answer, "value": True}).status_code == 400
        # A camera's device is found by its address too (the picture's orientation in its settings);
        # a camera Home Assistant has no ESPHome device for has none. Home Assistant gone: no
        # controls, not an error page.
        state = client.post(
            "/api/v1/sensors",
            json={
                "name": "Door",
                "source_type": "http",
                "source": "http://x/s.jpg",
                "states": [{"name": "Open"}, {"name": "Closed"}],
            },
        ).json()["id"]
        assert client.get(f"/api/v1/sensors/{state}/device").json() == {"device": None, "error": None}

        async def gone():
            raise RuntimeError("no route")

        rt.ha.registries = gone
        assert client.get(f"/api/v1/sensors/{sid}/device").json() == {"device": None, "error": "no route"}


def test_media_players_say_which_integration_each_comes_through(settings):  # noqa: F811
    # One speaker three times (Google Cast, Music Assistant, DLNA) is told apart by its integration.
    entities = [
        {"entity_id": "media_player.living_room", "platform": "cast"},
        {"entity_id": "media_player.living_room_2", "platform": "music_assistant"},
        {"entity_id": "media_player.living_room_3", "platform": "dlna_dmr"},
        {"entity_id": "light.hall", "platform": "hue"},
    ]
    states = {
        "media_player.living_room_3": {"state": "unavailable", "attributes": {"friendly_name": "Living room"}},
        "media_player.living_room": {"state": "idle", "attributes": {"friendly_name": "Living room"}},
        "media_player.living_room_2": {"state": "idle", "attributes": {"friendly_name": "Living room"}},
        "media_player.kitchen": {"state": "off", "attributes": {}},  # not in the registry (YAML)
        "light.hall": {"state": "on", "attributes": {"friendly_name": "Hall"}},
    }
    names = {"cast": "Google Cast", "music_assistant": "Music Assistant", "dlna_dmr": "DLNA Digital Media Renderer"}
    players = esphome.media_players(entities, states, names)
    assert [(p["name"], p["integration"]) for p in players] == [
        ("Living room", "DLNA Digital Media Renderer"),
        ("Living room", "Google Cast"),
        ("Living room", "Music Assistant"),
        ("media_player.kitchen", ""),
    ]
    assert players[0]["state"] == "unavailable"

    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        asked = []

        async def regs():
            return entities, []

        async def raw_states():
            return states

        async def integration_names(domains):
            asked.append(domains)
            return names

        rt.ha.registries, rt.ha.raw_states, rt.ha.integration_names = regs, raw_states, integration_names
        got = client.get("/api/v1/esphome/media-players").json()
        assert got["error"] is None and len(got["players"]) == 4
        assert asked == [["cast", "dlna_dmr", "music_assistant"]]  # only the media players' integrations

        async def gone():
            raise RuntimeError("no route")

        rt.ha.registries = gone
        assert client.get("/api/v1/esphome/media-players").json() == {"players": [], "error": "no route"}


def camera_registries(sw_version="1.4.0 (ESPHome 2026.10.2)"):
    """A camera of ours with its flash at 10.0.0.7 (and its camera entity), and a lamp elsewhere."""

    def entity(entity_id, name, device="cam", platform="esphome"):
        return {"entity_id": entity_id, "original_name": name, "platform": platform, "device_id": device}

    entities = [
        entity("sensor.cam_ip_address", "IP address"),
        entity("camera.cam_camera", "Camera"),
        entity("light.garage_cam_flash", "Flash"),
        entity("light.garage_lamp", "Lamp", device="lamp", platform="hue"),
    ]
    devices = [
        {
            "id": "cam",
            "name": "cam",
            "name_by_user": "Water meter",
            "model": ESPHOME["model"],
            "sw_version": sw_version,
        },
        {"id": "lamp", "name": "Garage lamp", "model": "Hue"},
    ]
    return entities, devices, {"sensor.cam_ip_address": {"state": "10.0.0.7"}}


def test_a_sensors_light_says_whether_its_camera_takes_the_picture_with_it():
    """The light in a sensor's settings: its camera's flash offered, and whether the light chosen is
    switched by the camera itself or through Home Assistant, and why."""
    entities, devices, states = camera_registries()

    def light(source_type="http", host="10.0.0.7", camera=None, chosen="light.garage_cam_flash", **registries):
        e, d, s = registries.get("reg") or (entities, devices, states)
        return esphome.camera_light(source_type, host, camera, chosen, e, d, s)

    # The camera's own flash, on a firmware that takes the picture with it.
    own = light()
    assert own["flash"] == "light.garage_cam_flash" and own["device"] == "Water meter"
    assert own["light"] == "own" and own["light_device"] == "Water meter"
    # No light yet: the flash is offered.
    assert light(chosen="") == {"flash": "light.garage_cam_flash", "device": "Water meter", "light": None}
    # A lamp is a light like any other.
    assert light(chosen="light.garage_lamp")["light"] is None
    # An older firmware switches it through Home Assistant.
    old = light(reg=camera_registries("1.3.0 (ESPHome 2026.10.2)"))
    assert old["light"] == "firmware" and old["firmware"] == "1.3.0"
    # Looking through the device's camera entity: through Home Assistant too.
    through = light(source_type="ha_camera", host=None, camera="camera.cam_camera")
    assert through["light"] == "camera_entity" and through["flash"] == "light.garage_cam_flash"
    # Pictures from another address (a name that did not resolve, another camera): not that camera.
    other = light(host="watermeter.local")
    assert other["light"] == "other_source" and other["flash"] is None and other["light_device"] == "Water meter"
    # Not a camera of ours: no flash offered.
    foreign = [{**d, "model": "esp32cam"} if d["id"] == "cam" else d for d in devices]
    assert light(chosen="", reg=(entities, foreign, states))["flash"] is None


def test_a_camera_reached_by_its_name_is_found_by_its_address():
    import asyncio

    assert asyncio.run(esphome.address_of("10.0.0.7")) == "10.0.0.7"
    assert asyncio.run(esphome.address_of("localhost")) in ("127.0.0.1", "::1")
    assert asyncio.run(esphome.address_of("no-such-camera.invalid")) == "no-such-camera.invalid"


def test_the_light_endpoint_looks_the_source_up_in_home_assistant(settings):  # noqa: F811
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        entities, devices, states = camera_registries()

        async def registries():
            return entities, devices

        async def raw_states():
            return states

        rt.ha.registries, rt.ha.raw_states = registries, raw_states
        body = {"source_type": "http", "source": "http://visionstate:secret@10.0.0.7:8081/snapshot.jpg"}
        got = client.post("/api/v1/camera-light", json={**body, "light_entity": "light.garage_cam_flash"}).json()
        assert got["light"] == "own" and got["flash"] == "light.garage_cam_flash" and got["error"] is None
        none = client.post("/api/v1/camera-light", json={"source_type": "rtsp", "source": "", "light_entity": ""})
        assert none.json()["flash"] is None
