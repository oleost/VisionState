"""VisionState stream: the microphone as an endless WAV stream and the camera as a snapshot, over
HTTP on a port of its own, behind a user name and password like a camera's.

    GET /audio.wav     16 kHz mono PCM16, chunked, until the listener goes away
    GET /snapshot.jpg  a picture taken after the request (one thrown away first, see fresh_snapshot)
"""

from esphome import automation
import esphome.codegen as cg
from esphome.components import esp32_camera, microphone, sensor
from esphome.components.esp32 import include_builtin_idf_component
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_MICROPHONE, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

AUTO_LOAD = ["audio", "camera", "ring_buffer", "sensor"]
DEPENDENCIES = ["network"]
CODEOWNERS = ["@oleost"]

CONF_CAMERA = "camera"
CONF_MAX_LISTENERS = "max_listeners"
CONF_BUFFER_DURATION = "buffer_duration"
CONF_FRESH_SNAPSHOT = "fresh_snapshot"
CONF_LISTENERS = "listeners"
CONF_ON_CAMERA_USE = "on_camera_use"
CONF_MICROPHONE_SAMPLE_RATE = "microphone_sample_rate"
CONF_LEVEL = "level"
CONF_PEAK = "peak"
CONF_CLIPPED = "clipped"
CONF_DROPPED = "dropped"
CONF_DIAGNOSTICS_INTERVAL = "diagnostics_interval"

SAMPLE_RATE = 16000  # what VisionState's sound model takes, and what /audio.wav always is
# A microphone may run at its own rate: 48 kHz is filtered and taken down to 16 kHz on the device.
MICROPHONE_SAMPLE_RATES = (16000, 48000)

visionstate_stream_ns = cg.esphome_ns.namespace("visionstate_stream")
VisionStateStream = visionstate_stream_ns.class_("VisionStateStream", cg.Component)


def _both_or_neither(config):
    if (CONF_USERNAME in config) != (CONF_PASSWORD in config):
        raise cv.Invalid("Give both a username and a password, or neither")
    return config


def _consume_sockets(config):
    """One listening socket, and one per listener plus SPARE_SOCKETS (stream.cpp) for the rest."""
    from esphome.components import socket

    socket.consume_sockets(config[CONF_MAX_LISTENERS] + 2, "visionstate_stream")(config)
    socket.consume_sockets(1, "visionstate_stream", socket.SocketType.TCP_LISTEN)(config)
    return config


def _something_to_serve(config):
    if CONF_MICROPHONE not in config and CONF_CAMERA not in config:
        raise cv.Invalid("Give a microphone, a camera or both")
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(VisionStateStream),
            cv.Optional(CONF_PORT, default=8081): cv.port,
            cv.Optional(CONF_USERNAME): cv.string_strict,
            cv.Optional(CONF_PASSWORD): cv.string_strict,
            cv.Optional(CONF_MICROPHONE): microphone.microphone_source_schema(
                min_bits_per_sample=16, max_bits_per_sample=16
            ),
            cv.Optional(CONF_CAMERA): cv.use_id(esp32_camera.ESP32Camera),
            cv.Optional(CONF_MAX_LISTENERS, default=2): cv.int_range(min=1, max=4),
            # Kept per listener: a listener that falls behind (Wi-Fi) loses the oldest sound, not the newest.
            cv.Optional(CONF_BUFFER_DURATION, default="1s"): cv.All(
                cv.positive_time_period_milliseconds,
                cv.Range(min=cv.TimePeriod(milliseconds=200), max=cv.TimePeriod(seconds=10)),
            ),
            cv.Optional(CONF_FRESH_SNAPSHOT, default=True): cv.boolean,
            cv.Optional(CONF_LISTENERS): sensor.sensor_schema(accuracy_decimals=0, icon="mdi:ear-hearing"),
            cv.Optional(CONF_MICROPHONE_SAMPLE_RATE, default=SAMPLE_RATE): cv.one_of(*MICROPHONE_SAMPLE_RATES, int=True),
            # Diagnostics while someone listens, for wiring and gain: the level and the loudest sample
            # (dBFS), samples that clipped, and sound dropped because a listener did not keep up (ms).
            cv.Optional(CONF_LEVEL): sensor.sensor_schema(
                unit_of_measurement="dBFS", accuracy_decimals=0, icon="mdi:waveform"
            ),
            cv.Optional(CONF_PEAK): sensor.sensor_schema(
                unit_of_measurement="dBFS", accuracy_decimals=0, icon="mdi:chart-bell-curve"
            ),
            cv.Optional(CONF_CLIPPED): sensor.sensor_schema(accuracy_decimals=0, icon="mdi:waveform"),
            cv.Optional(CONF_DROPPED): sensor.sensor_schema(
                unit_of_measurement="ms", accuracy_decimals=0, icon="mdi:timer-sand-empty"
            ),
            cv.Optional(CONF_DIAGNOSTICS_INTERVAL, default="10s"): cv.All(
                cv.positive_time_period_milliseconds, cv.Range(min=cv.TimePeriod(seconds=1))
            ),
            # The camera use changed (by Home Assistant, or a snapshot that asked for another), with its name.
            cv.Optional(CONF_ON_CAMERA_USE): automation.validate_automation(single=True),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    _both_or_neither,
    _something_to_serve,
    cv.only_on_esp32,
    _consume_sockets,
)

def FINAL_VALIDATE_SCHEMA(config):  # noqa: N802 - the name ESPHome looks for
    """The microphone must run at the rate given (16 kHz, or 48 kHz taken down to it here)."""
    rate = config.get(CONF_MICROPHONE_SAMPLE_RATE, SAMPLE_RATE)
    return cv.Schema(
        {cv.Optional(CONF_MICROPHONE): microphone.final_validate_microphone_source_schema("visionstate_stream", sample_rate=rate)},
        extra=cv.ALLOW_EXTRA,
    )(config)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_max_listeners(config[CONF_MAX_LISTENERS]))
    cg.add(var.set_buffer_duration(config[CONF_BUFFER_DURATION]))
    cg.add(var.set_fresh_snapshot(config[CONF_FRESH_SNAPSHOT]))
    if CONF_USERNAME in config:
        cg.add(var.set_credentials(config[CONF_USERNAME], config[CONF_PASSWORD]))
    if CONF_MICROPHONE in config:
        cg.add_define("USE_VISIONSTATE_MICROPHONE")
        source = await microphone.microphone_source_to_code(config[CONF_MICROPHONE])
        cg.add(var.set_microphone_source(source))
        cg.add(var.set_input_rate(config[CONF_MICROPHONE_SAMPLE_RATE]))
        cg.add(var.set_diagnostics_interval(config[CONF_DIAGNOSTICS_INTERVAL]))
        for key, setter in (
            (CONF_LEVEL, var.set_level_sensor),
            (CONF_PEAK, var.set_peak_sensor),
            (CONF_CLIPPED, var.set_clipped_sensor),
            (CONF_DROPPED, var.set_dropped_sensor),
        ):
            if key in config:
                cg.add(setter(await sensor.new_sensor(config[key])))
    if CONF_CAMERA in config:
        cg.add_define("USE_VISIONSTATE_CAMERA")
        cam = await cg.get_variable(config[CONF_CAMERA])
        cg.add(var.set_camera(cam))
    if CONF_ON_CAMERA_USE in config:
        await automation.build_automation(
            var.get_camera_use_trigger(), [(cg.std_string, "x")], config[CONF_ON_CAMERA_USE]
        )
    if CONF_LISTENERS in config:
        sens = await sensor.new_sensor(config[CONF_LISTENERS])
        cg.add(var.set_listeners_sensor(sens))
    include_builtin_idf_component("esp_http_server")
