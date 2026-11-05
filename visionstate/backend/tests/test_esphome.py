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
    made = esphome.configuration(board, kind, "Front door", mute=True)
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
