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
from visionstate.settings import CAMERA_USES, ESPHOME

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
    assert made["sound"] == ("microphone" in kind) and made["picture"] == ("camera" in kind)


@in_repo
@pytest.mark.parametrize("board", list(ESPHOME["boards"]))
def test_the_boards_pins_are_the_packages_defaults(board):
    package = (ESPHOME_DIR / "packages" / f"board-{board}.yaml").read_text(encoding="utf-8")
    defaults = substitutions(package)
    for key, pin in ESPHOME["boards"][board]["pins"].items():
        assert defaults[key] == pin, (board, key)


@in_repo
def test_the_camera_uses_are_the_devices_options():
    # What the guide offers is what Camera use (esphome/packages/camera-use.yaml) can be set to.
    package = (ESPHOME_DIR / "packages" / "camera-use.yaml").read_text(encoding="utf-8")
    options = re.findall(r"^      - (.+)$", package.split("options:", 1)[1].split("initial_option", 1)[0], re.MULTILINE)
    assert options == list(ESPHOME["camera_uses"])
    assert substitutions(package)["camera_use"] == next(iter(ESPHOME["camera_uses"]))
    # The firmware's keys (?use=) and names are the app's (settings.CAMERA_USES).
    component = (ESPHOME_DIR / "components" / "visionstate_stream" / "visionstate_stream.cpp").read_text(
        encoding="utf-8"
    )
    names = re.findall(r'"([^"]+)"', re.search(r"CAMERA_USE_NAMES\[CAMERA_USES\] = \{([^}]*)\}", component)[1])
    keys = re.findall(r'"([^"]+)"', re.search(r"CAMERA_USE_KEYS\[CAMERA_USES\] = \{([^}]*)\}", component)[1])
    assert dict(zip(keys, names, strict=True)) == {k: CAMERA_USES[k] for k in keys}
    assert set(keys) == set(CAMERA_USES) and set(names) == set(ESPHOME["camera_uses"])


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
    # A camera says what it looks at (Camera use on the device); a microphone has none.
    assert 'camera_use: "States"' in esphome.configuration("esp32-cam", "camera", "Hall", mute=False)["yaml"]
    meter = esphome.configuration("esp32-cam", "camera", "Hall", mute=False, camera_use="Meters and displays")
    assert 'camera_use: "Meters and displays"' in meter["yaml"]
    assert "camera_use" not in esphome.configuration("esp32-devkit", "microphone", "Hall", mute=False)["yaml"]
    with pytest.raises(esphome.ESPHomeError):
        esphome.configuration("esp32-cam", "camera", "Hall", mute=False, camera_use="Fish")
    # A board with no pin for a mute switch takes none; its wiring is the user's (an INMP441 on its headers).
    with pytest.raises(esphome.ESPHomeError):
        esphome.configuration("waveshare-esp32s3-lcd2", "microphone", "Hall", mute=True)
    waveshare = esphome.configuration("waveshare-esp32s3-lcd2", "microphone", "Hall", mute=False)["yaml"]
    assert "mic_sck: GPIO43" in waveshare and "mute" not in waveshare
    assert "mute_pin: GPIO7" in esphome.configuration("esp32s3-devkit", "microphone", "Hall", mute=True)["yaml"]
    assert esphome.device_name("  Garasje / Bod 2 ") == "garasje-bod-2"
    assert esphome.device_name("???") == "visionstate"


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

        rt.ha.entities = entities
        rt.ha.discovered = discovered
        got = client.get("/api/v1/esphome/devices").json()
        assert got["waiting"] == ["vs-garage"]  # found, not added yet
        assert got["devices"] == [
            {
                "entity_id": "sensor.front_door_ip_address",
                "name": "Front door",
                "address": "10.0.0.7",
                "light": "light.front_door_flash",
            }
        ]


def test_discovered_esphome_devices_are_read_from_home_assistants_flows(monkeypatch):
    import asyncio

    from visionstate import ha_events

    async def commands(url, token, cmds):
        assert cmds == [{"type": "config_entries/flow/progress"}]
        return [
            [
                {"handler": "esphome", "context": {"source": "zeroconf", "title_placeholders": {"name": "vs-hall"}}},
                {"handler": "hue", "context": {"source": "zeroconf", "title_placeholders": {"name": "Bridge"}}},
                {"handler": "esphome", "context": {"source": "user"}},  # someone adding one by hand
            ]
        ]

    monkeypatch.setattr(ha_events, "ws_commands", commands)
    assert asyncio.run(ha_events.discovered("ws://ha", "t", "esphome")) == ["vs-hall"]


@in_repo
def test_the_firmwares_version_and_controls_are_the_apps():
    # The version a device reports is the one the app compares with (settings.ESPHOME).
    common = (ESPHOME_DIR / "packages" / "common.yaml").read_text(encoding="utf-8")
    assert re.search(r'^    version: "([^"]+)"', common, re.MULTILINE)[1] == ESPHOME["firmware_version"]
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
    assert [c["entity_id"] for c in found["controls"]] == ["switch.hall_microphone", "number.hall_microphone_gain"]
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
            calls.append((f"{domain}.{service}", data["entity_id"], data["value"]))

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
        assert len(got["device"]["controls"]) == 2
        url = f"/api/v1/sensors/{sid}/device"
        assert client.post(url, json={"entity_id": "switch.hall_microphone", "value": False}).status_code == 200
        assert client.post(url, json={"entity_id": "number.hall_microphone_gain", "value": 8}).status_code == 200
        assert calls == [
            ("switch", "switch.hall_microphone", False),
            ("number.set_value", "number.hall_microphone_gain", 8.0),
        ]
        # Only this device's controls, and the gain within its range.
        assert client.post(url, json={"entity_id": "switch.other_microphone", "value": True}).status_code == 404
        assert client.post(url, json={"entity_id": "light.kitchen", "value": True}).status_code == 404
        assert client.post(url, json={"entity_id": "number.hall_microphone_gain", "value": 99}).status_code == 400
        # A state sensor has no such device; Home Assistant gone: no controls, not an error page.
        state = client.post(
            "/api/v1/sensors",
            json={
                "name": "Door",
                "source_type": "http",
                "source": "http://x/s.jpg",
                "states": [{"name": "Open"}, {"name": "Closed"}],
            },
        ).json()["id"]
        assert client.get(f"/api/v1/sensors/{state}/device").status_code == 400

        async def gone():
            raise RuntimeError("no route")

        rt.ha.registries = gone
        assert client.get(f"/api/v1/sensors/{sid}/device").json() == {"device": None, "error": "no route"}
