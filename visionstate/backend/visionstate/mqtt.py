"""MQTT bridge: Home Assistant discovery, state publishing and command handling."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from .settings import APP_SLUG, KIND_OBJECTS, KIND_STATES, SUPERVISOR_URL, UNKNOWN_STATE, VERSION, Settings

log = logging.getLogger(__name__)

BRIDGE_AVAILABILITY = f"{APP_SLUG}/status"
ONLINE, OFFLINE = "online", "offline"
RECONNECT_DELAY_S = 10


def topics(slug: str) -> dict[str, str]:
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
    objects: list[tuple[str, str]] = field(default_factory=list)  # (key, display name) per class


def discovery_messages(prefix: str, sensor: SensorDescriptor) -> list[tuple[str, dict]]:
    """Home Assistant MQTT discovery configs for one sensor (one device, several entities)."""
    t = topics(sensor.slug)
    uid = f"{APP_SLUG}_{sensor.slug}"
    objects = sensor.kind == KIND_OBJECTS
    device = {
        "identifiers": [uid],
        "name": sensor.name,
        "manufacturer": "VisionState",
        "model": "Object sensor" if objects else "Image state sensor",
        "sw_version": VERSION,
    }
    bridge_only = {"availability": [{"topic": BRIDGE_AVAILABILITY}]}
    with_camera = {
        "availability": [{"topic": BRIDGE_AVAILABILITY}, {"topic": t["availability"]}],
        "availability_mode": "all",
    }

    def config(component: str, object_id: str, payload: dict) -> tuple[str, dict]:
        payload = {"unique_id": f"{uid}_{object_id}", "device": device, **payload}
        return f"{prefix}/{component}/{uid}/{object_id}/config", payload

    if objects:
        kind_specific = []
        for key, name in sensor.objects:
            ot = object_topics(sensor.slug, key)
            kind_specific += [
                config(
                    "binary_sensor",
                    key,
                    {
                        "name": name,
                        "default_entity_id": f"binary_sensor.{uid}_{key}",
                        "state_topic": ot["state"],
                        "json_attributes_topic": ot["attributes"],
                        "payload_on": "ON",
                        "payload_off": "OFF",
                        "device_class": "occupancy",
                        **with_camera,
                    },
                ),
                config(
                    "sensor",
                    f"{key}_count",
                    {
                        "name": f"{name} count",
                        "default_entity_id": f"sensor.{uid}_{key}_count",
                        "state_topic": ot["count"],
                        "state_class": "measurement",
                        "icon": "mdi:counter",
                        **with_camera,
                    },
                ),
            ]
    else:
        kind_specific = [
            config(
                "sensor",
                "state",
                {
                    "name": None,
                    "default_entity_id": f"sensor.{uid}",
                    "state_topic": t["state"],
                    "json_attributes_topic": t["attributes"],
                    "device_class": "enum",
                    "options": [*sensor.state_keys, UNKNOWN_STATE],
                    "icon": "mdi:eye-check-outline",
                    **with_camera,
                },
            ),
            config(
                "sensor",
                "confidence",
                {
                    "name": "Confidence",
                    "default_entity_id": f"sensor.{uid}_confidence",
                    "state_topic": t["confidence"],
                    "unit_of_measurement": "%",
                    "state_class": "measurement",
                    "entity_category": "diagnostic",
                    "icon": "mdi:percent-circle-outline",
                    **with_camera,
                },
            ),
        ]
    return [
        *kind_specific,
        config(
            "image",
            "frame",
            {
                "name": "Last frame",
                "default_entity_id": f"image.{uid}_frame",
                "image_topic": t["image"],
                "content_type": "image/jpeg",
                **with_camera,
            },
        ),
        config(
            "button",
            "classify",
            {
                "name": "Detect now" if objects else "Classify now",
                "default_entity_id": f"button.{uid}_classify",
                "command_topic": t["classify"],
                "payload_press": "PRESS",
                "icon": "mdi:camera-iris",
                **bridge_only,
            },
        ),
        config(
            "switch",
            "enabled",
            {
                "name": "Enabled",
                "default_entity_id": f"switch.{uid}_enabled",
                "command_topic": t["enabled_set"],
                "state_topic": t["enabled"],
                "payload_on": "ON",
                "payload_off": "OFF",
                "entity_category": "config",
                **bridge_only,
            },
        ),
    ]


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
                "unique_id": f"{APP_SLUG}_review_queue",
                "default_entity_id": f"sensor.{APP_SLUG}_review_queue",
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
            finally:
                self.connected = False
                self._client = None
            await asyncio.sleep(RECONNECT_DELAY_S)

    async def _dispatch(self, topic: str, payload: bytes | bytearray | str) -> None:
        text = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
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
        for key, _ in sensor.objects:
            await self.remove_object_class(sensor.slug, key, discovery=False)

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
