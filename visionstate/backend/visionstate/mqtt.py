"""MQTT bridge: Home Assistant discovery, state publishing and command handling."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from .settings import (
    APP_SLUG,
    KIND_OBJECTS,
    KIND_READING,
    KIND_STATES,
    READING_PROBLEMS,
    READING_RATE_UNITS,
    SUPERVISOR_URL,
    UNKNOWN_STATE,
    VERSION,
    Settings,
)

log = logging.getLogger(__name__)

BRIDGE_AVAILABILITY = f"{APP_SLUG}/status"
ONLINE, OFFLINE = "online", "offline"
RECONNECT_DELAY_S = 10


def topics(slug: str) -> dict[str, str]:
    """The MQTT topics of a sensor's values and commands (object classes: ``object_topics``).

    Never rename one once released: Home Assistant's entities are bound to them.
    """
    base = f"{APP_SLUG}/{slug}"
    return {
        "state": f"{base}/state",
        "attributes": f"{base}/attributes",
        "confidence": f"{base}/confidence",
        "image": f"{base}/image",
        "availability": f"{base}/availability",
        "classify": f"{base}/classify/set",
        "enabled_set": f"{base}/enabled/set",
        "enabled": f"{base}/enabled",
        # reading sensors: diagnostic entities (off by default in Home Assistant)
        "raw": f"{base}/raw",
        "problem": f"{base}/problem",
        "accepted": f"{base}/accepted",
        "rate": f"{base}/rate",
        "reader_image": f"{base}/reader_image",
    }


def object_topics(slug: str, key: str) -> dict[str, str]:
    """Topics of one object class of an object sensor."""
    base = f"{APP_SLUG}/{slug}/objects/{key}"
    return {"state": f"{base}/state", "count": f"{base}/count", "attributes": f"{base}/attributes"}


@dataclass
class SensorDescriptor:
    slug: str
    name: str
    state_keys: list[str]
    kind: str = KIND_STATES
    objects: list[tuple[str, str, str]] = field(default_factory=list)  # (key, display name, icon) per class
    reading: dict | None = None  # reading sensors: settings.READING_DEFAULTS merged
    entity_prefix: bool = False  # see db.Sensor.entity_prefix


def discovery_messages(prefix: str, sensor: SensorDescriptor) -> list[tuple[str, dict]]:
    """Home Assistant MQTT discovery configs for one sensor: one device, its entities.

    The entities of its kind (``_state_entities``, ``_object_entities``, ``_reading_entities``),
    then those every sensor has: the last frame, "check now" and the pause switch. Unique IDs
    and topics are never changed once released: Home Assistant's entities are bound to them.
    """
    d = _Discovery(prefix, sensor)
    if sensor.kind == KIND_READING:
        kind_specific = _reading_entities(d)
    elif sensor.kind == KIND_OBJECTS:
        kind_specific = _object_entities(d)
    else:
        kind_specific = _state_entities(d)
    check_now = {KIND_OBJECTS: "Detect now", KIND_READING: "Read now"}.get(sensor.kind, "Classify now")
    frame = {
        "name": "Last frame",
        **d.suggest("image", "frame"),
        "image_topic": d.t["image"],
        "content_type": "image/jpeg",
        **d.with_camera,
    }
    button = {
        "name": check_now,
        **d.suggest("button", "classify"),
        "command_topic": d.t["classify"],
        "payload_press": "PRESS",
        "icon": "mdi:camera-iris",
        **d.bridge_only,
    }
    pause = {
        "name": "Enabled",
        **d.suggest("switch", "enabled"),
        "command_topic": d.t["enabled_set"],
        "state_topic": d.t["enabled"],
        "payload_on": "ON",
        "payload_off": "OFF",
        "entity_category": "config",
        **d.bridge_only,
    }
    return [
        *kind_specific,
        d.config("image", "frame", frame),
        d.config("button", "classify", button),
        d.config("switch", "enabled", pause),
    ]


class _Discovery:
    """What every discovery config of one sensor shares: its device, topics and availability."""

    def __init__(self, prefix: str, sensor: SensorDescriptor):
        self.prefix, self.sensor = prefix, sensor
        self.t = topics(sensor.slug)
        self.uid = f"{APP_SLUG}_{sensor.slug}"
        models = {KIND_OBJECTS: "Object sensor", KIND_READING: "Reading sensor"}
        self.device = {
            "identifiers": [self.uid],
            "name": sensor.name,
            "manufacturer": "VisionState",
            "model": models.get(sensor.kind, "Image state sensor"),
            "sw_version": VERSION,
        }
        # Commands work while the app runs; values only while the camera delivers too.
        self.bridge_only = {"availability": [{"topic": BRIDGE_AVAILABILITY}]}
        self.with_camera = {
            "availability": [{"topic": BRIDGE_AVAILABILITY}, {"topic": self.t["availability"]}],
            "availability_mode": "all",
        }

    def suggest(self, component: str, object_id: str | None = None) -> dict:
        """The entity ID to suggest for a new entity (``object_id`` None: the sensor's main one).

        Older sensors keep suggesting their "visionstate_" IDs (an entity that comes back, e.g. a
        deselected object class, gets the same ID again); newer ones let Home Assistant name the
        entities after the device and the entity, like other integrations. Either way Home
        Assistant only uses it when it first creates an entity.
        """
        if not self.sensor.entity_prefix:
            return {}
        suffix = f"_{object_id}" if object_id else ""
        return {"default_entity_id": f"{component}.{self.uid}{suffix}"}

    def config(self, component: str, object_id: str, payload: dict) -> tuple[str, dict]:
        """One entity's discovery topic and config."""
        payload = {"unique_id": f"{self.uid}_{object_id}", "device": self.device, **payload}
        return f"{self.prefix}/{component}/{self.uid}/{object_id}/config", payload

    def confidence(self) -> tuple[str, dict]:
        """The confidence of the last check, in % (state and reading sensors)."""
        payload = {
            "name": "Confidence",
            **self.suggest("sensor", "confidence"),
            "state_topic": self.t["confidence"],
            "unit_of_measurement": "%",
            "state_class": "measurement",
            "entity_category": "diagnostic",
            "icon": "mdi:percent-circle-outline",
            **self.with_camera,
        }
        return self.config("sensor", "confidence", payload)


def _state_entities(d: _Discovery) -> list[tuple[str, dict]]:
    """A state sensor: its state (one of its keys, or unknown) and the confidence."""
    state = {
        "name": None,
        **d.suggest("sensor"),
        "state_topic": d.t["state"],
        "json_attributes_topic": d.t["attributes"],
        "device_class": "enum",
        "options": [*d.sensor.state_keys, UNKNOWN_STATE],
        "icon": "mdi:eye-check-outline",
        **d.with_camera,
    }
    return [d.config("sensor", "state", state), d.confidence()]


def _object_entities(d: _Discovery) -> list[tuple[str, dict]]:
    """An object sensor: per class and own label, whether it is there (on/off) and how many."""
    entities = []
    for key, name, icon in d.sensor.objects:
        ot = object_topics(d.sensor.slug, key)
        present = {
            "name": name,
            **d.suggest("binary_sensor", key),
            "state_topic": ot["state"],
            "json_attributes_topic": ot["attributes"],
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "occupancy",
            "icon": icon,  # the object itself instead of occupancy's house
            **d.with_camera,
        }
        count = {
            "name": f"{name} count",
            **d.suggest("sensor", f"{key}_count"),
            "state_topic": ot["count"],
            "state_class": "measurement",
            "icon": "mdi:counter",
            **d.with_camera,
        }
        entities += [d.config("binary_sensor", key, present), d.config("sensor", f"{key}_count", count)]
    return entities


def _reading_entities(d: _Discovery) -> list[tuple[str, dict]]:
    """A reading sensor: its value and the confidence, and diagnostics that are off by default
    (raw reading, problem, accepted share, reader image) for those who want them, e.g. for leak
    detection. A counter also gets its rate."""
    reading, t = d.sensor.reading or {}, d.t
    # Diagnostic entities, off until the user switches them on in Home Assistant.
    hidden = {"entity_category": "diagnostic", "enabled_by_default": False}
    value = {"name": None, **d.suggest("sensor"), **reading_entity(reading, t), **d.with_camera}
    raw = {
        "name": "Raw reading",
        **d.suggest("sensor", "raw"),
        "state_topic": t["raw"],
        **hidden,
        "icon": "mdi:text-recognition",
        **d.with_camera,
    }
    problem = {
        "name": "Problem",
        **d.suggest("sensor", "problem"),
        "state_topic": t["problem"],
        "device_class": "enum",
        "options": list(READING_PROBLEMS),
        **hidden,
        "icon": "mdi:alert-circle-outline",
        **d.with_camera,
    }
    accepted = {
        "name": "Accepted (24 h)",
        **d.suggest("sensor", "accepted"),
        "state_topic": t["accepted"],
        "unit_of_measurement": "%",
        "state_class": "measurement",
        **hidden,
        "icon": "mdi:check-circle-outline",
        **d.with_camera,
    }
    reader_image = {
        # What the reader saw: the region after display processing (one field per wheel).
        "name": "Reader image",
        **d.suggest("image", "reader_image"),
        "image_topic": t["reader_image"],
        "content_type": "image/jpeg",
        **hidden,
        **d.with_camera,
    }
    entities = [
        d.config("sensor", "state", value),
        d.confidence(),
        d.config("sensor", "raw", raw),
        d.config("sensor", "problem", problem),
        d.config("sensor", "accepted", accepted),
        d.config("image", "reader_image", reader_image),
    ]
    if reading.get("mode") == "counter":
        rate = {**d.suggest("sensor", "rate"), **rate_entity(reading, t), **d.with_camera}
        entities.append(d.config("sensor", "rate", rate))
    return entities


def reading_entity(reading: dict, t: dict[str, str]) -> dict:
    """Discovery fields of a reading sensor's value: unit, device class and state class.

    Counters are ``total_increasing`` so they work in the Energy dashboard; a money value may
    not have a measurement state class in Home Assistant, so it gets none.
    """
    mode = reading.get("mode", "value")
    unit = "min" if mode == "time_left" else reading.get("unit") or None
    device_class = "duration" if mode == "time_left" else reading.get("device_class") or None
    if mode == "counter":
        state_class = "total_increasing"
    elif device_class == "monetary":
        state_class = None
    else:
        state_class = "measurement"
    payload = {
        "state_topic": t["state"],
        "json_attributes_topic": t["attributes"],
        "icon": "mdi:counter" if mode == "counter" else "mdi:timer-outline" if mode == "time_left" else "mdi:numeric",
        "unit_of_measurement": unit,
        "device_class": device_class,
        "state_class": state_class,
    }
    if mode != "time_left":
        payload["suggested_display_precision"] = int(reading.get("decimals", 0))
    return {k: v for k, v in payload.items() if v is not None}


def rate_unit(reading: dict) -> tuple[str | None, str | None, float]:
    """(unit, device class, factor from "per hour") of a counter's rate, from the counter's unit."""
    unit = (reading.get("unit") or "").strip()
    if unit in READING_RATE_UNITS:
        return READING_RATE_UNITS[unit]
    return (f"{unit}/h" if unit else None), None, 1.0


def rate_entity(reading: dict, t: dict[str, str]) -> dict:
    """Discovery fields of a counter's rate: how fast it goes up (off by default in Home Assistant)."""
    unit, device_class, _ = rate_unit(reading)
    payload = {
        "name": "Rate",
        "state_topic": t["rate"],
        "unit_of_measurement": unit,
        "device_class": device_class,
        "state_class": "measurement",
        "enabled_by_default": False,
        "icon": "mdi:speedometer",
    }
    return {k: v for k, v in payload.items() if v is not None}


_TRANSLITERATE = str.maketrans({"ø": "o", "æ": "ae", "å": "a", "ß": "ss", "đ": "d", "ł": "l", "œ": "oe", "þ": "th"})


def ha_slug(text: str) -> str:
    """Close to how Home Assistant turns a name into the object ID part of an entity ID."""
    text = unicodedata.normalize("NFKD", text.lower().translate(_TRANSLITERATE))
    return re.sub(r"[^a-z0-9]+", "_", text.encode("ascii", "ignore").decode()).strip("_") or "unknown"


def main_entities(sensor: SensorDescriptor) -> list[tuple[str, str]]:
    """(unique ID, expected entity ID) of a sensor's main entities: one, or two per object class.

    The expected ID is what Home Assistant gives a new entity; the real one (it may end in _2, or
    the user changed it) is in Home Assistant's entity registry, see ha_events.fetch_entity_ids.
    """
    uid = f"{APP_SLUG}_{sensor.slug}"
    if sensor.kind != KIND_OBJECTS:
        expected = f"sensor.{uid}" if sensor.entity_prefix else f"sensor.{ha_slug(sensor.name)}"
        return [(f"{uid}_state", expected)]
    entities = []
    for key, name, _icon in sensor.objects:
        if sensor.entity_prefix:
            on, count = f"binary_sensor.{uid}_{key}", f"sensor.{uid}_{key}_count"
        else:
            on = f"binary_sensor.{ha_slug(f'{sensor.name} {name}')}"
            count = f"sensor.{ha_slug(f'{sensor.name} {name} count')}"
        entities += [(f"{uid}_{key}", on), (f"{uid}_{key}_count", count)]
    return entities


REVIEW_TOPICS = {
    "count": f"{APP_SLUG}/review/count",
    "attributes": f"{APP_SLUG}/review/attributes",
}


def hub_discovery_messages(prefix: str) -> list[tuple[str, dict]]:
    """App-wide entities (not tied to one sensor), grouped under a "VisionState" device."""
    device = {
        "identifiers": [APP_SLUG],
        "name": "VisionState",
        "manufacturer": "VisionState",
        "model": "VisionState app",
        "sw_version": VERSION,
    }
    return [
        (
            f"{prefix}/sensor/{APP_SLUG}/review_queue/config",
            {
                # Home Assistant names it sensor.visionstate_review_queue (device + entity name).
                "unique_id": f"{APP_SLUG}_review_queue",
                "name": "Review queue",
                "state_topic": REVIEW_TOPICS["count"],
                "json_attributes_topic": REVIEW_TOPICS["attributes"],
                "unit_of_measurement": "frames",
                "state_class": "measurement",
                "icon": "mdi:image-check-outline",
                "availability": [{"topic": BRIDGE_AVAILABILITY}],
                "device": device,
            },
        )
    ]


@dataclass
class MqttConfig:
    host: str
    port: int
    username: str
    password: str
    source: str  # "options" | "supervisor"


async def resolve_config(settings: Settings) -> MqttConfig | None:
    """The broker to use: the one in the app options, else the one Home Assistant's Supervisor
    offers (the Mosquitto app); None when there is none (yet)."""
    if settings.mqtt_host:
        return MqttConfig(
            settings.mqtt_host, settings.mqtt_port, settings.mqtt_username, settings.mqtt_password, "options"
        )
    if not settings.is_supervised:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{SUPERVISOR_URL}/services/mqtt",
                headers={"Authorization": f"Bearer {settings.supervisor_token}"},
            )
        if resp.status_code != 200:
            log.warning("No MQTT service from Supervisor (HTTP %s)", resp.status_code)
            return None
        data = resp.json().get("data", {})
        return MqttConfig(
            data["host"], int(data.get("port", 1883)), data.get("username", ""), data.get("password", ""), "supervisor"
        )
    except (httpx.HTTPError, KeyError, ValueError) as err:
        log.warning("Could not query MQTT service: %s", err)
        return None


CommandHandler = Callable[[str, str, str], Awaitable[None]]  # (slug, command, payload)
ConnectHandler = Callable[[], Awaitable[None]]


class MqttBridge:
    def __init__(self, settings: Settings, on_command: CommandHandler, on_connect: ConnectHandler):
        self.settings = settings
        self.on_command = on_command
        self.on_connect = on_connect
        self.connected = False
        self.config: MqttConfig | None = None
        self.last_error = ""
        self._client = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="mqtt")

    async def stop(self) -> None:
        if self._client is not None and self.connected:
            try:
                await self._client.publish(BRIDGE_AVAILABILITY, OFFLINE, retain=True)
            except Exception:  # noqa: BLE001
                pass
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        import aiomqtt

        while True:
            self.config = await resolve_config(self.settings)
            if self.config is None:
                self.last_error = "No MQTT broker configured"
                await asyncio.sleep(RECONNECT_DELAY_S * 6)
                continue
            try:
                async with aiomqtt.Client(
                    hostname=self.config.host,
                    port=self.config.port,
                    username=self.config.username or None,
                    password=self.config.password or None,
                    identifier=f"{APP_SLUG}-{VERSION}",
                    will=aiomqtt.Will(BRIDGE_AVAILABILITY, OFFLINE, qos=1, retain=True),
                ) as client:
                    self._client = client
                    self.connected = True
                    self.last_error = ""
                    log.info("Connected to MQTT %s:%s", self.config.host, self.config.port)
                    await client.subscribe(f"{APP_SLUG}/+/classify/set")
                    await client.subscribe(f"{APP_SLUG}/+/enabled/set")
                    await client.subscribe(f"{self.settings.discovery_prefix}/status")
                    await client.publish(BRIDGE_AVAILABILITY, ONLINE, retain=True)
                    await self.on_connect()
                    async for message in client.messages:
                        await self._dispatch(str(message.topic), message.payload)
            except aiomqtt.MqttError as err:
                self.last_error = str(err)
                log.warning("MQTT connection lost: %s", err)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - a bug must not end the bridge: reconnect
                self.last_error = str(err) or type(err).__name__
                log.exception("MQTT bridge failed, reconnecting")
            finally:
                self.connected = False
                self._client = None
            await asyncio.sleep(RECONNECT_DELAY_S)

    async def _dispatch(self, topic: str, payload: bytes | bytearray | str) -> None:
        text = payload.decode(errors="replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        if topic == f"{self.settings.discovery_prefix}/status":
            if text == ONLINE:
                await self.publish(BRIDGE_AVAILABILITY, ONLINE, retain=True)
                await self.on_connect()
            return
        parts = topic.split("/")
        if len(parts) == 4 and parts[0] == APP_SLUG and parts[3] == "set":
            try:
                await self.on_command(parts[1], parts[2], text)
            except Exception:  # noqa: BLE001
                log.exception("Command %s failed", topic)

    async def publish(self, topic: str, payload: str | bytes | dict, retain: bool = False) -> None:
        client = self._client
        if client is None or not self.connected:
            return
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        try:
            await client.publish(topic, payload, qos=0, retain=retain)
        except Exception as err:  # noqa: BLE001
            log.debug("Publish to %s failed: %s", topic, err)

    async def publish_discovery(self, sensor: SensorDescriptor) -> None:
        for topic, payload in discovery_messages(self.settings.discovery_prefix, sensor):
            await self.publish(topic, payload, retain=True)

    async def publish_hub_discovery(self) -> None:
        for topic, payload in hub_discovery_messages(self.settings.discovery_prefix):
            await self.publish(topic, payload, retain=True)

    async def remove_discovery(self, sensor: SensorDescriptor) -> None:
        for topic, _ in discovery_messages(self.settings.discovery_prefix, sensor):
            await self.publish(topic, "", retain=True)
        for topic in topics(sensor.slug).values():
            await self.publish(topic, "", retain=True)
        for key, *_ in sensor.objects:
            await self.remove_object_class(sensor.slug, key, discovery=False)

    async def remove_rate(self, slug: str) -> None:
        """Forget the rate entity of a reading sensor that is no longer a counter."""
        uid = f"{APP_SLUG}_{slug}"
        await self.publish(f"{self.settings.discovery_prefix}/sensor/{uid}/rate/config", "", retain=True)
        await self.publish(topics(slug)["rate"], "", retain=True)

    async def remove_object_class(self, slug: str, key: str, discovery: bool = True) -> None:
        """Forget one class of an object sensor (deselected): its entities and retained values."""
        if discovery:
            uid = f"{APP_SLUG}_{slug}"
            for component, object_id in (("binary_sensor", key), ("sensor", f"{key}_count")):
                await self.publish(
                    f"{self.settings.discovery_prefix}/{component}/{uid}/{object_id}/config", "", retain=True
                )
        for topic in object_topics(slug, key).values():
            await self.publish(topic, "", retain=True)
