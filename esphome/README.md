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

**With VisionState's guide** (*Set one up with ESPHome*, under a new sensor's sound or image
source):

1. Pick the board and what it is (microphone, camera, both), its name and whether a mute switch is
   wired. The guide shows the wiring.
2. Type the Wi-Fi's name and password (the guide makes up the password for VisionState). These stay
   in the browser: VisionState never receives them.
3. *Download* the device's file. In the ESPHome app: *New device* → *Advanced set up options* →
   *Import from file*. Nothing in the file needs editing.
4. *Install*: the first time over USB, then over Wi-Fi. The first build takes several minutes.
5. Home Assistant finds the device (*Settings → Devices & services → Discovered*). *Add* asks for the
   device's **encryption key**: the guide shows it, and it is in the file under `api: encryption:
   key:`. It is not the password for VisionState.
6. The guide sees the device in Home Assistant, fills in its address, checks the sound and the
   picture with the password, and makes the sensor (named after the device, *… sound* or
   *… camera*; an ESP32-CAM's flash is offered as the sensor's light).

**By hand, with the examples:**

1. In the ESPHome app, make a new device for an ESP32. ESPHome writes its name, the Wi-Fi and an
   API key.
2. Open the example for your board, and copy its lines from `substitutions:` down into the
   device's file in place of what ESPHome wrote below `captive_portal:`, keeping ESPHome's `api:`
   key.
3. Add the stream's password to ESPHome's secrets: `vs_stream_password: <a password>`.
4. *Install*: the first time over USB, then over Wi-Fi. Home Assistant finds the device and asks to
   add it.
5. In VisionState, a sound sensor with the stream address above as its *RTSP or stream* source, or
   an image sensor with the snapshot address as its *HTTP snapshot URL*.

## When something is wrong

| What you see | Why |
|---|---|
| *401* in the guide's check, or the sensor gets no sound | The password is not the one in the device's file (`stream_password`). |
| *409* | Two listeners are on already (VisionState and someone else): close one. |
| *Pure silence* on the sound sensor | The stream is nothing but zeros: the microphone is not wired (check SD, SCK, WS and L/R to GND), is broken, or is muted (the *Microphone* switch is off, or the mute switch is closed). |
| Very quiet sound | Raise *Microphone gain* in Home Assistant. |
| The ESP32-CAM restarts, or the picture fails | A weak 5 V supply (see above). |
| Home Assistant does not ask to add the device | It is not on the Wi-Fi yet (ESPHome's log shows it), or Home Assistant is on another network. |

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
