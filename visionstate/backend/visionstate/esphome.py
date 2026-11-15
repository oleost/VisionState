"""The ESPHome configuration the ESP32 guide writes for a board and a kind of device (settings.ESPHOME):
the device's whole file — our packages pulled from the repository by the firmware's tag, as
esphome/examples does it, the device's name and the wiring. The values that are the user's (the
Wi-Fi, the stream's password, the API key) are left as ``{{field}}`` for the page to fill in, in
the browser: they never reach VisionState."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket

from .settings import ESPHOME

FIELDS = ("wifi_ssid", "wifi_password", "stream_password", "ap_password", "api_key")


def field(name: str) -> str:
    return "{{" + name + "}}"


class ESPHomeError(ValueError):
    pass


def device_name(friendly_name: str) -> str:
    """ESPHome's device name: lower-case letters, digits and hyphens (also its host name)."""
    name = re.sub(r"[^a-z0-9]+", "-", friendly_name.strip().lower()).strip("-")
    return name[:31].rstrip("-") or "visionstate"


MEDIA_PLAYER = re.compile(r"media_player\.[a-z0-9_]+")


def packages(board: str, kind: str, mute: bool, assistant: bool = False) -> list[str]:
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
        if assistant:
            files.append("voice-assistant-ha-wake-word.yaml")
    return [f"esphome/packages/{f}" for f in files]


def configuration(
    board: str,
    kind: str,
    friendly_name: str,
    mute: bool,
    camera_use: str | None = None,
    assistant: str | None = None,
) -> dict:
    """The device's whole file for ESPHome, with the user's values as fields (FIELDS). ``assistant``:
    a microphone that is a voice assistant too, its answer played on that media player ("": one
    chosen later, *Answer on* on the device); None: only a microphone."""
    spec = ESPHOME["boards"].get(board)
    if spec is None:
        raise ESPHomeError(f"Unknown board {board!r}")
    if kind not in spec["kinds"]:
        raise ESPHomeError(f"A {spec['name']} cannot be a {ESPHOME['kinds'].get(kind, kind).lower()}")
    if mute and "mute_pin" not in spec["pins"]:
        raise ESPHomeError(f"A {spec['name']} has no pin for a mute switch")
    friendly = " ".join(friendly_name.split()) or "VisionState device"
    if any(c in friendly for c in '"\\\n'):
        raise ESPHomeError("The name cannot hold quotes or backslashes")
    name = device_name(friendly)
    microphone = kind in ("microphone", "camera_microphone")
    camera = kind in ("camera", "camera_microphone")
    uses = ESPHOME["camera_uses"]
    if camera_use is not None and camera_use not in uses:
        raise ESPHomeError(f"Unknown camera use {camera_use!r}")
    if assistant is not None and kind != "microphone":
        raise ESPHomeError("Only a microphone can be a voice assistant too")
    if assistant and not MEDIA_PLAYER.fullmatch(assistant):
        raise ESPHomeError("The answer plays on a media player (media_player.…)")
    lines = [
        f"# A {ESPHOME['kinds'][kind].lower()} for VisionState on a board: {spec['name']} (made by VisionState's ESP32 guide).",
        "substitutions:",
        f"  name: {name}",
        f'  friendly_name: "{friendly}"',
        f"  wifi_ssid: {field('wifi_ssid')}",
        f"  wifi_password: {field('wifi_password')}",
        f"  stream_password: {field('stream_password')}",
        "  # The fallback hotspot's, opened when it cannot reach the Wi-Fi.",
        f"  ap_password: {field('ap_password')}",
    ]
    if camera:
        lines.append("  # What it looks at (Camera use in Home Assistant, which can change it later).")
        lines.append(f'  camera_use: "{camera_use or next(iter(uses))}"')
    if microphone:
        pins = [k for k in ("mic_sck", "mic_ws", "mic_sd") if k in spec["pins"]]
        if mute:
            pins.append("mute_pin")
        if pins:
            lines.append("  # The wiring; change a pin here if you wired it differently.")
            lines += [f"  {k}: {spec['pins'][k]}" for k in pins]
    if assistant is not None:
        lines.append("  # The voice assistant's answer plays here (Answer on in Home Assistant, which can change it).")
        lines.append(f'  assistant_media_player: "{assistant}"')
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
        *(f"      - {f}" for f in packages(board, kind, mute, assistant is not None)),
        "",
        "api:",
        "  encryption:",
        f"    key: {field('api_key')}",
        "",
        "ota:",
        "  - platform: esphome",
        "    encryption: {}  # firmware over the air only from whoever has the API key",
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
        "picture": camera,
        "assistant": assistant is not None,
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


def device_at(host: str, entities: list[dict], states: dict[str, dict]) -> str | None:
    """The ESPHome device whose *IP address* entity shows ``host`` (an address: resolve a host name
    with ``address_of`` first), from Home Assistant's registry."""
    return next(
        (
            e["device_id"]
            for e in entities
            if e.get("platform") == "esphome"
            and e.get("original_name") == "IP address"
            and e.get("device_id")
            and (states.get(e["entity_id"]) or {}).get("state") == host
        ),
        None,
    )


def flash_of(device_id: str | None, entities: list[dict]) -> str | None:
    """A device's flash: its light named *Flash* by our firmware (esphome/packages/camera-*.yaml)."""
    if not device_id:
        return None
    return next(
        (
            e["entity_id"]
            for e in entities
            if e.get("device_id") == device_id
            and e["entity_id"].startswith("light.")
            and e.get("original_name") == ESPHOME["flash_name"]
            and not e.get("disabled_by")
        ),
        None,
    )


def own_flash(host: str, light: str, entities: list[dict], states: dict[str, dict]) -> bool:
    """Whether ``light`` is the flash of the ESPHome device at ``host``: a camera of ours with a flash
    then takes its picture with it (?flash=), no light switched through Home Assistant."""
    return bool(light) and flash_of(device_at(host, entities, states), entities) == light


async def address_of(host: str) -> str:
    """The address of a host name (an ESP32 is often reached as watermeter.local), for finding its
    device in Home Assistant, which shows its address; an address, or a name that does not resolve
    in ESPHOME["resolve_timeout_s"], as it is."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        found = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), ESPHOME["resolve_timeout_s"]
        )
    except OSError, TimeoutError:
        return host
    # Home Assistant shows an ESPHome device's IPv4 address: that one first.
    found = sorted(found, key=lambda info: info[0] != socket.AF_INET)
    return str(found[0][4][0]) if found else host


def camera_light(
    source_type: str,
    host: str | None,
    camera_entity: str | None,
    light: str,
    entities: list[dict],
    devices: list[dict],
    states: dict[str, dict],
) -> dict:
    """What a sensor's light is to the camera it takes its pictures from, for its settings:

    - ``flash``: the flash of the ESP32 of ours that is the sensor's source (offered as its light),
      and ``device`` its name;
    - ``light``: when the light chosen is the flash of an ESPHome device, how it is switched —
      ``own`` (the camera takes the picture with it itself), ``firmware`` (that camera, but its
      firmware is older than ESPHOME["flash_firmware"]: through Home Assistant), ``camera_entity``
      (the sensor looks through that device's camera entity in Home Assistant: through Home
      Assistant), ``other_source`` (the sensor's pictures come from another address); None for any
      other light. ``light_device``: the device whose flash it is."""
    by_id = {d.get("id"): d for d in devices}

    def name(device_id: str | None) -> str | None:
        device = by_id.get(device_id) or {}
        return device.get("name_by_user") or device.get("name")

    def ours(device_id: str | None) -> bool:
        return (by_id.get(device_id) or {}).get("model") == ESPHOME["model"]

    if source_type == "http" and host:
        source_device = device_at(host, entities, states)
    elif source_type == "ha_camera" and camera_entity:
        source_device = next((e.get("device_id") for e in entities if e["entity_id"] == camera_entity), None)
    else:
        source_device = None
    flash = flash_of(source_device, entities) if source_device and ours(source_device) else None
    found: dict = {"flash": flash, "device": name(source_device) if flash else None, "light": None}
    light_device = next(
        (
            e.get("device_id")
            for e in entities
            if light and e["entity_id"] == light and e.get("platform") == "esphome" and e.get("device_id")
        ),
        None,
    )
    if light_device is None or flash_of(light_device, entities) != light:
        return found  # not the flash of an ESPHome device: a light like any other
    found["light_device"] = name(light_device)
    if light_device != source_device:
        found["light"] = "other_source"
    elif source_type == "ha_camera":
        found["light"] = "camera_entity"
    else:
        version = firmware_version((by_id.get(light_device) or {}).get("sw_version")) if ours(light_device) else None
        found["light"] = "firmware" if version is None or outdated(version, ESPHOME["flash_firmware"]) else "own"
        found["firmware"] = version
    return found


def password_services(ip_entity_id: str, services: list[str]) -> list[str]:
    """The ESPHome services that may answer with this device's stream password: every device's
    (settings.ESPHOME["password_action"]), the one named like its *IP address* entity first. Home
    Assistant names it after the device's name in its file, which the entity often shares; the
    answer's address says which device answered."""
    suffix = f"_{ESPHOME['password_action']}"
    own = ip_entity_id.removeprefix("sensor.").removesuffix("_ip_address") + suffix
    found = [name for name in services if name.endswith(suffix)]
    return sorted(found, key=lambda name: name != own)


def firmware_version(sw_version: str | None) -> str | None:
    """Our firmware's version from what the device reports ("1.1.0 (ESPHome 2026.10.2)")."""
    if not sw_version:
        return None
    return sw_version.split(" (", 1)[0].strip() or None


def _version_key(version: str) -> tuple:
    """Sortable: "1.0.0-dev" comes before "1.0.0"."""
    main, _, pre = version.partition("-")
    numbers = tuple(int(n) if n.isdigit() else 0 for n in main.split("."))
    return (*numbers, 0 if pre else 1)


def outdated(version: str | None, latest: str) -> bool:
    return version is not None and _version_key(version) < _version_key(latest)


def device_parts(ip_entity_id: str, entities: list[dict], devices: list[dict]) -> dict:
    """What the ESPHome device behind an *IP address* entity is, from Home Assistant's registries:
    our firmware (offered as a source with its stream's password), with a camera, a microphone."""
    device_id = next((e.get("device_id") for e in entities if e["entity_id"] == ip_entity_id), None)
    if not device_id:
        return {"ours": False, "camera": False, "microphone": False}
    device = next((d for d in devices if d.get("id") == device_id), {})
    own = [e for e in entities if e.get("device_id") == device_id]
    return {
        "ours": device.get("model") == ESPHOME["model"],
        "camera": any(e["entity_id"].startswith("camera.") for e in own),
        "microphone": any(e.get("original_name") == "Microphone" for e in own),
    }


def media_players(entities: list[dict], states: dict[str, dict], names: dict[str, str]) -> list[dict]:
    """Home Assistant's media players, for a voice assistant's *Answer on*: each with the integration
    it comes through (one speaker is often there several times: Google Cast, Music Assistant, DLNA…),
    sorted by name, then integration."""
    platform = {e["entity_id"]: e.get("platform") for e in entities}
    found = []
    for entity_id, state in states.items():
        if not entity_id.startswith("media_player."):
            continue
        domain = platform.get(entity_id) or ""
        found.append(
            {
                "entity_id": entity_id,
                "name": (state.get("attributes") or {}).get("friendly_name") or entity_id,
                "integration": names.get(domain, domain),
                "state": state.get("state"),
            }
        )
    return sorted(found, key=lambda p: (p["name"].lower(), p["integration"].lower(), p["entity_id"]))


def device_controls(host: str, entities: list[dict], devices: list[dict], states: dict[str, dict]) -> dict | None:
    """The ESPHome device whose *IP address* entity shows this address, with the controls it has
    (settings.ESPHOME["controls"]) and, for our firmware, whether a newer one is out. None: no such
    device in Home Assistant."""
    device_id = device_at(host, entities, states)
    if device_id is None:
        return None
    device = next((d for d in devices if d.get("id") == device_id), {})
    found: list[tuple[int, dict]] = []
    for e in entities:
        kind = ESPHOME["controls"].get(e.get("original_name") or "")
        if e.get("device_id") != device_id or kind is None or not e["entity_id"].startswith(f"{kind}."):
            continue
        if e.get("disabled_by"):
            continue
        state = states.get(e["entity_id"]) or {}
        attrs = state.get("attributes") or {}
        control = {
            "entity_id": e["entity_id"],
            "kind": kind,
            "name": e.get("name") or e.get("original_name"),
            "state": state.get("state"),
        }
        if kind == "number":
            control.update(min=attrs.get("min"), max=attrs.get("max"), step=attrs.get("step"))
        elif kind == "select":
            control["options"] = list(attrs.get("options") or [])
        found.append((list(ESPHOME["controls"]).index(e["original_name"]), control))
    controls = [c for _, c in sorted(found, key=lambda ic: ic[0])]  # in the order of settings
    ours = device.get("model") == ESPHOME["model"]
    version = firmware_version(device.get("sw_version")) if ours else None
    return {
        "name": device.get("name_by_user") or device.get("name"),
        "address": host,
        "ours": ours,
        "firmware": version,
        "latest": ESPHOME["firmware_version"] if ours else None,
        "outdated": ours and outdated(version, ESPHOME["firmware_version"]),
        "controls": controls,
    }
