# ESPHome firmware for VisionState

Firmware for cheap ESP32 boards, built with ESPHome: a **microphone**, a **camera**, or **both**,
for VisionState's sound and image sensors. The device serves its sound and its pictures straight
to whoever is allowed to fetch them (VisionState, VLC, go2rtc), behind a user name and password
like a camera's, and shows up in Home Assistant through the ESPHome integration.

Not released yet: this is being built and tried on the boards below. The plan, the decisions and
what is still to check are in [`docs/ESP32_PLAN.md`](../docs/ESP32_PLAN.md).

## What it serves

| Address | What |
|---|---|
| `http://visionstate:<password>@<device>:8081/audio.wav` | the microphone: an endless WAV stream, 16 kHz, mono, 16 bits |
| `http://visionstate:<password>@<device>:8081/snapshot.jpg` | the camera: a picture taken after the request |

In VisionState, a sound sensor takes the first as its *RTSP or stream* source, and an image
sensor the second as its *HTTP snapshot URL*. The password is hidden in VisionState's logs and
exports, like a camera's. Two listeners at a time (VisionState and someone listening in);
a third gets *409*.

In Home Assistant the device has: *Microphone* (on/off, kept over restarts), *Microphone gain*,
*Mute switch* (if one is wired), *Listeners*, *Camera* and *Flash* (ESP32-CAM), *IP address*,
*Wi-Fi signal*, *Uptime* and *Restart*. The board's LED is on while someone listens or looks.

## Boards and wiring

**ESP32 DevKit (ESP32-WROOM-32) + INMP441** — [`examples/esp32-devkit-microphone.yaml`](examples/esp32-devkit-microphone.yaml)

| INMP441 | ESP32 DevKit |
|---|---|
| VDD | 3V3 |
| GND | GND |
| SCK | GPIO26 |
| WS | GPIO25 |
| SD | GPIO33 |
| L/R | GND |
| *mute switch (optional)* | GPIO32 to GND |

**ESP32-CAM (AI-Thinker layout)** — camera only: [`examples/esp32-cam-camera.yaml`](examples/esp32-cam-camera.yaml);
with an INMP441 on the SD card's pins (no SD card then): [`examples/esp32-cam-camera-microphone.yaml`](examples/esp32-cam-camera-microphone.yaml)

| INMP441 | ESP32-CAM |
|---|---|
| VDD | 3V3 |
| GND | GND |
| SCK | GPIO14 |
| WS | GPIO15 |
| SD | GPIO13 |
| L/R | GND |
| *mute switch (optional)* | GPIO12 to GND |

The pins can be changed under `substitutions` in your own file. An ESP32-CAM wants a 5 V supply
that gives at least 1 A; a weak one makes it restart when the camera or Wi-Fi draws current.

## Install it

The easiest way: VisionState's ESP32 guide (*Set one up with ESPHome*, under a new sensor's sound or
image source). It shows the wiring and writes the device's whole file, Wi-Fi and password included,
to paste into ESPHome's *Empty configuration*. By hand, with the examples:

1. In the ESPHome app, make a new device for an ESP32. ESPHome writes its name, the Wi-Fi and an
   API key.
2. Open the example for your board, and copy its lines from `substitutions:` down into the
   device's file in place of what ESPHome wrote below `captive_portal:`, keeping ESPHome's `api:`
   key.
3. Add the stream's password to ESPHome's secrets: `vs_stream_password: <a password>`.
4. *Install*: the first time over USB, then over Wi-Fi. Home Assistant finds the device and asks to
   add it.

## The parts

| Package | What |
|---|---|
| [`packages/common.yaml`](packages/common.yaml) | every device: Wi-Fi (power save off: it leaves gaps in the sound), the API, updates, the stream's server and login, the light, diagnostics |
| [`packages/board-esp32-devkit.yaml`](packages/board-esp32-devkit.yaml), [`packages/board-esp32-cam.yaml`](packages/board-esp32-cam.yaml) | the board and its default pins |
| [`packages/microphone.yaml`](packages/microphone.yaml) | the INMP441 and `/audio.wav`, the *Microphone* switch and the gain |
| [`packages/mute-pin.yaml`](packages/mute-pin.yaml) | a mute switch on a pin: unwired, the microphone is on |
| [`packages/camera-esp32-cam.yaml`](packages/camera-esp32-cam.yaml) | the OV2640 and `/snapshot.jpg`, the flash as a light |
| [`packages/esp32-cam-microphone.yaml`](packages/esp32-cam-microphone.yaml) | camera and microphone on a plain ESP32: keeps the camera's I2S port free |
| [`components/visionstate_stream`](components/visionstate_stream) | the server: the stream, the snapshot and the login |

[`tests/`](tests) holds the configurations CI compiles (`.github/workflows/esphome.yml`), built from the
packages in this folder.
