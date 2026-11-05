"""The ESPHome configuration the ESP32 guide writes for a board and a kind of device (settings.ESPHOME):
the device's whole file — our packages pulled from the repository by the firmware's tag, as
esphome/examples does it, the device's name and the wiring. The values that are the user's (the
Wi-Fi, the stream's password, the API key) are left as ``{{field}}`` for the page to fill in, in
the browser: they never reach VisionState."""

from __future__ import annotations

import re

from .settings import ESPHOME

FIELDS = ("wifi_ssid", "wifi_password", "stream_password", "api_key")


def field(name: str) -> str:
    return "{{" + name + "}}"


class ESPHomeError(ValueError):
    pass


def device_name(friendly_name: str) -> str:
    """ESPHome's device name: lower-case letters, digits and hyphens (also its host name)."""
    name = re.sub(r"[^a-z0-9]+", "-", friendly_name.strip().lower()).strip("-")
    return name[:31].rstrip("-") or "visionstate"


def packages(board: str, kind: str, mute: bool) -> list[str]:
    """The package files, in the order they must be merged (the I2S port kept for the camera before
    the microphone)."""
    spec = ESPHOME["boards"][board]
    files = [f"board-{board}.yaml", "common.yaml"]
    if kind in ("camera", "camera_microphone"):
        files.append(f"camera-{board}.yaml")
    if kind == "camera_microphone" and spec.get("camera_i2s"):
        files.append(f"{board}-microphone.yaml")
    if kind in ("microphone", "camera_microphone"):
        files.append("microphone-pdm.yaml" if spec.get("microphone") == "built_in" else "microphone.yaml")
        if mute:
            files.append("mute-pin.yaml")
    return [f"esphome/packages/{f}" for f in files]


def configuration(board: str, kind: str, friendly_name: str, mute: bool) -> dict:
    """The device's whole file for ESPHome, with the user's values as fields (FIELDS)."""
    spec = ESPHOME["boards"].get(board)
    if spec is None:
        raise ESPHomeError(f"Unknown board {board!r}")
    if kind not in spec["kinds"]:
        raise ESPHomeError(f"A {spec['name']} cannot be a {ESPHOME['kinds'].get(kind, kind).lower()}")
    friendly = " ".join(friendly_name.split()) or "VisionState device"
    if any(c in friendly for c in '"\\\n'):
        raise ESPHomeError("The name cannot hold quotes or backslashes")
    name = device_name(friendly)
    microphone = kind in ("microphone", "camera_microphone")
    lines = [
        f"# A {ESPHOME['kinds'][kind].lower()} for VisionState on a board: {spec['name']} (made by VisionState's ESP32 guide).",
        "substitutions:",
        f"  name: {name}",
        f'  friendly_name: "{friendly}"',
        f"  wifi_ssid: {field('wifi_ssid')}",
        f"  wifi_password: {field('wifi_password')}",
        f"  stream_password: {field('stream_password')}",
    ]
    if microphone:
        pins = [k for k in ("mic_sck", "mic_ws", "mic_sd") if k in spec["pins"]]
        if mute:
            pins.append("mute_pin")
        if pins:
            lines.append("  # The wiring; change a pin here if you wired it differently.")
            lines += [f"  {k}: {spec['pins'][k]}" for k in pins]
    lines += [
        "",
        "external_components:",
        f"  - source: github://{ESPHOME['repository'].removeprefix('https://github.com/')}@{ESPHOME['ref']}",
        f"    refresh: {ESPHOME['refresh']}",
        "",
        "packages:",
        "  visionstate:",
        f"    url: {ESPHOME['repository']}",
        f"    ref: {ESPHOME['ref']}",
        f"    refresh: {ESPHOME['refresh']}",
        "    files:",
        *(f"      - {f}" for f in packages(board, kind, mute)),
        "",
        "api:",
        "  encryption:",
        f"    key: {field('api_key')}",
        "",
    ]
    return {
        "name": name,
        "friendly_name": friendly,
        "yaml": "\n".join(lines),
        "fields": list(FIELDS),
        "port": ESPHOME["port"],
        "username": ESPHOME["username"],
        "sound": microphone,
        "picture": kind in ("camera", "camera_microphone"),
    }


def addresses(host: str, password: str, *, sound: bool, picture: bool) -> dict[str, str]:
    """The addresses VisionState takes the device's sound and pictures from, with the login."""
    host = host.strip().removeprefix("http://").rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", host):
        raise ESPHomeError("Give the device's IP address or host name")
    if any(c in password for c in "@/:?#%"):
        raise ESPHomeError("The password cannot hold @ / : ? # or %")
    base = f"http://{ESPHOME['username']}:{password}@{host}:{ESPHOME['port']}"
    found = {}
    if sound:
        found["sound"] = f"{base}/audio.wav"
    if picture:
        found["picture"] = f"{base}/snapshot.jpg"
    return found
