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
*Mute switch* (if one is wired), *Listeners*, *Camera* and *Flash* (ESP32-CAM), *Camera use* and
*Exposure for moving things* (a camera), *IP address*, *Wi-Fi signal*, *Uptime* and *Restart*. The
board's LED is on while someone listens or looks.

**Camera use** says what the camera looks at, and sets its picture size, JPEG quality and exposure
([`packages/camera-use.yaml`](packages/camera-use.yaml)); the guide asks, and it can be changed
later:

| Camera use | For | Picture |
|---|---|---|
| *States* | still things: a door, a gate, a light | 800×600, exposure automatic |
| *Meters and displays* | a water meter, a display: the digits | 1600×1200, less compression; the flash for the dark |
| *People, cars and animals* | things that move | 800×600, a short fixed exposure (*Exposure for moving things*) with automatic gain: sharper, but darker in poor light |

A camera for several sensors takes what the most demanding one needs: a meter, then things that
move. The snapshot is always taken after the request, so it is fresh: Home Assistant's camera
entity gives the picture the camera kept ready after the last fetch instead, as old as the time
since then.

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
that gives at least 1 A; a weak one makes it restart when the camera or Wi-Fi draws current. The
microphone takes header pins that the ESP32-CAM-MB USB board also sits on: to power the board
over USB as well, give it 5 V and GND from a USB lead or socket of its own, or use stacking
headers so the microphone's wires can stay on.

**Seeed Studio XIAO ESP32-S3 Sense**: camera, microphone or both, with nothing to wire, since
the camera and a PDM microphone are on its camera board. It powers and installs over its own USB-C.
Example: [`examples/xiao-esp32s3-sense-camera-microphone.yaml`](examples/xiao-esp32s3-sense-camera-microphone.yaml)
(for one of the two, leave out the camera's or the microphone's package). A mute switch, if you
want one, goes from D0 (GPIO1) to GND. It runs warm with the camera on. Not yet tried on a board
([`docs/ESP32_PLAN.md`](../docs/ESP32_PLAN.md) §5).

**A voice assistant that also listens for VisionState**: a Home Assistant Voice (Preview
Edition) or an M5Stack Atom VoiceS3R keeps its own firmware, and VisionState's stream is added to
it, sharing the microphone with the wake word. See [below](#on-a-voice-assistant). Not yet tried on
a device.

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

## On a voice assistant

For a **Home Assistant Voice (Preview Edition)** or an **M5Stack Atom VoiceS3R**, already set up as
a voice assistant:

1. In the ESPHome app, *Take control* of the device. ESPHome writes a file that pulls in the
   device's own configuration (`packages:`), with its name, Wi-Fi and API key.
2. Add to that file (and `vs_stream_password: <a password>` to ESPHome's secrets):

   ```yaml
   substitutions:
     stream_password: !secret vs_stream_password

   external_components:
     - source: github://oleost/VisionState@esp32  # esphome-v1 once released

   packages:
     # … the device's own package stays first …
     visionstate:
       url: https://github.com/oleost/VisionState
       ref: esp32
       files:
         - esphome/packages/voice-pe.yaml  # an Atom VoiceS3R: esphome/packages/atom-voices3r.yaml
   ```

   If `substitutions:` or `packages:` are in the file already, add the lines to them.
3. *Install*. The stream is then at the address above (`/audio.wav`), for a sound sensor in
   VisionState.

What it does on the device:

- **One listener**, VisionState, behind the password (a second one gets *409*).
- **Silence while the assistant hears a command and answers** (from the wake word until it is
  done): what is said to it is not VisionState's business. *VisionState pauses for the assistant*
  turns that off. The wake word itself is still heard. The device's own *Mute* mutes the stream
  too (VisionState shows *Pure silence*).
- *VisionState microphone gain*, and *VisionState listening* (0 or 1).
- On the **Voice PE** it takes channel 1 of the XMOS chip, the one the wake word hears: the echo of
  its own speaker cancelled and noise suppressed, without automatic gain (channel 0 has that, which
  would hide how loud a sound is). Noise suppression may soften a steady tone: whether a smoke
  alarm still comes through is not checked.
- On the **Atom VoiceS3R** it takes the microphone the assistant uses, with the echo of its own
  speaker cancelled.

It builds next to both devices' own firmware (2026-11-05). Not checked on a device: memory and CPU
next to the wake word, the gain, and how alarms sound through the
processing ([`docs/ESP32_PLAN.md`](../docs/ESP32_PLAN.md) §5).

## When something is wrong

| What you see | Why |
|---|---|
| *401* in the guide's check, or the sensor gets no sound | The password is not the one in the device's file (`stream_password`). |
| *409* | Two listeners are on already (VisionState and someone else): close one. |
| *Pure silence* on the sound sensor | The stream is nothing but zeros: the microphone is not wired (check SD, SCK, WS and L/R to GND; on a XIAO, that the camera board is on), is broken, or is muted (the *Microphone* switch is off, or the mute switch is closed). |
| Very quiet sound | Raise *Microphone gain* in Home Assistant. |
| The ESP32-CAM restarts, or the picture fails | A weak 5 V supply (see above). |
| Home Assistant does not ask to add the device | It is not on the Wi-Fi yet (ESPHome's log shows it), or Home Assistant is on another network. |

## The parts

| Package | What |
|---|---|
| [`packages/common.yaml`](packages/common.yaml) | every device: Wi-Fi (power save off: it leaves gaps in the sound), the API, updates, the stream's server and login, the light, diagnostics |
| [`packages/board-esp32-devkit.yaml`](packages/board-esp32-devkit.yaml), [`packages/board-esp32-cam.yaml`](packages/board-esp32-cam.yaml), [`packages/board-xiao-esp32s3-sense.yaml`](packages/board-xiao-esp32s3-sense.yaml) | the board and its default pins |
| [`packages/microphone.yaml`](packages/microphone.yaml) | an INMP441 (I2S) as `/audio.wav` |
| [`packages/microphone-pdm.yaml`](packages/microphone-pdm.yaml) | the XIAO ESP32-S3 Sense's PDM microphone as `/audio.wav` |
| [`packages/microphone-controls.yaml`](packages/microphone-controls.yaml) | what both microphone packages include: the *Microphone* switch and the gain |
| [`packages/mute-pin.yaml`](packages/mute-pin.yaml) | a mute switch on a pin: unwired, the microphone is on |
| [`packages/camera-esp32-cam.yaml`](packages/camera-esp32-cam.yaml) | the OV2640 and `/snapshot.jpg`, the flash as a light |
| [`packages/camera-xiao-esp32s3-sense.yaml`](packages/camera-xiao-esp32s3-sense.yaml) | the XIAO's camera and `/snapshot.jpg` (it has no flash) |
| [`packages/esp32-cam-microphone.yaml`](packages/esp32-cam-microphone.yaml) | camera and microphone on a plain ESP32: keeps the camera's I2S port free |
| [`packages/voice-pe.yaml`](packages/voice-pe.yaml), [`packages/atom-voices3r.yaml`](packages/atom-voices3r.yaml) | the stream added to a voice assistant's own configuration, on its microphone |
| [`packages/voice-assistant.yaml`](packages/voice-assistant.yaml) | what both include: one listener, silence while the assistant listens, the gain |
| [`components/visionstate_stream`](components/visionstate_stream) | the server: the stream, the snapshot and the login |

[`tests/`](tests) holds the configurations CI compiles (`.github/workflows/esphome.yml`), built from the
packages in this folder. `tests/voice-pe.yaml` and `tests/atom-voices3r.yaml` pull in the devices'
own configurations from their repositories, pinned to a release and a commit.
