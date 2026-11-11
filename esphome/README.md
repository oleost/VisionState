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
*Mute switch* (if one is wired), *Listeners*, *Camera* and *Flash* (ESP32-CAM), *Camera use*, *Picture orientation* and
*Exposure for moving things* (a camera), *IP address*, *Wi-Fi signal*, *Uptime* and *Restart*. The
board's LED is on while someone listens or looks. VisionState shows a sound sensor's *Microphone*
and gain on its **Live** tab and sets them through Home Assistant.

**Diagnostics** (off in Home Assistant until switched on there, under the device's *Diagnostic*
entities): every 10 s while VisionState listens, *Sound level* and *Sound peak* (dBFS: 0 is the
loudest there is, a quiet room is about −60), *Samples clipped* (the gain too high, or something very
loud) and *Sound dropped* (ms the listener did not keep up with: Wi-Fi). For wiring and gain.

**A microphone at 48 kHz** (one that does not run at 16 kHz): `mic_sample_rate: "48000"` under
`substitutions`. The device filters it and takes it down to 16 kHz itself, so the stream stays the
same.

## Locked

Like ESPHome's own new devices: the Home Assistant API needs the device's encryption key,
**firmware over the air** only comes from whoever has that key (`encryption: {}` under `ota:` in
the device's file), and the **fallback hotspot** the device opens when it cannot reach its Wi-Fi
has a password (`ap_password`): its captive portal takes Wi-Fi settings and firmware. The guide and
the examples write all three. A file made before firmware 1.2.0 has neither of the last two: add
them (an `ota:` that sets a `password:` keeps it instead, as the two cannot be combined):

```yaml
substitutions:
  ap_password: <8 characters or more>

ota:
  - platform: esphome
    encryption: {}
```

The stream and the snapshot are behind `stream_password`, over plain HTTP like a camera's: anyone
who can read the network's traffic can read it.

## Updating the firmware

A device's file names the firmware's line, `esphome-v1`. Each new version of v1 is put on that
line, so updating is choosing **Install** for the device in the ESPHome app: nothing in the file
changes. ESPHome fetches the firmware again only once a day (`refresh: 1d`): an Install less than a
day after the last one may still build the version it had (*Clean build files* does not change that;
`refresh: 0s` in the file, for one Install, does). VisionState says on a sound sensor's Live tab when the device has an older
version than the app knows. Each version also has a tag of its own that never moves
(`esphome-v1.0.0`, `esphome-v1.1.0` …): name one in the file instead to stay on it. A change that
would break the files devices have gets a new line, `esphome-v2`, and stays out of v1.

**Camera use** says what the camera looks at, and sets its picture size, JPEG quality and exposure
([`packages/camera-use.yaml`](packages/camera-use.yaml)). VisionState sets it itself: a snapshot can
ask for one (`/snapshot.jpg?use=states`, `meter` or `movement`), the camera is set up so first
(the pictures before it settled thrown away) and stays so, and every snapshot says how it is set
up (an `X-VisionState-Camera` header, by which VisionState knows the camera is ours). Each sensor
asks for what its kind needs, or what is chosen in its settings (*Camera set-up*); sensors on one
camera share the most demanding, so it is not switched back and forth. The guide writes the use
it starts with, and it can be changed in Home Assistant too:

| Camera use | For | Picture |
|---|---|---|
| *States* | still things: a door, a gate, a light | 800×600, exposure automatic |
| *Meters and displays* | a water meter, a display: the digits | 1600×1200, less compression; the flash for the dark |
| *People, cars and animals* | things that move | 800×600, a short fixed exposure (*Exposure for moving things*) with automatic gain: sharper, but darker in poor light |

**Picture orientation** turns the picture 180° or mirrors it, on the camera itself: *Normal* (what
ESPHome does by default), *Mirrored*, *Upside down* or *Upside down, mirrored*. It is kept over
restarts, set in Home Assistant or in the sensor's settings in VisionState, and applies at once
(any other angle is straightened in the sensor's region). `camera_orientation` under
`substitutions` is the one a new device starts with; a file that set `vertical_flip` or
`horizontal_mirror` itself gives its orientation there instead, as the choice overrides them.

Switching takes a few pictures: a new picture size about 0.5 s, a new exposure one picture. The snapshot is always taken after the request, so it is fresh: Home Assistant's camera
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

**ESP32-S3 DevKit (ESP32-S3-DevKitC-1 and its clones) + INMP441** — [`examples/esp32s3-devkit-microphone.yaml`](examples/esp32s3-devkit-microphone.yaml)

| INMP441 | ESP32-S3 DevKit |
|---|---|
| VDD | 3V3 |
| GND | GND |
| SCK | GPIO4 |
| WS | GPIO5 |
| SD | GPIO6 |
| L/R | GND |
| *mute switch (optional)* | GPIO7 to GND |

Its RGB LED lights red while someone listens: GPIO48 on a DevKitC-1 v1.0, GPIO38 on v1.1
(`rgb_pin`). Compiled, not yet tried on a board.

**Waveshare ESP32-S3-Touch-LCD-2 + INMP441** — [`examples/waveshare-esp32s3-lcd2-microphone.yaml`](examples/waveshare-esp32s3-lcd2-microphone.yaml),
the wiring a user worked out on the V1 board ([hruba202/Waveshare-ESP32-S3-Touch-LCD-2-INMP441](https://github.com/hruba202/Waveshare-ESP32-S3-Touch-LCD-2-INMP441))

| INMP441 | ESP32-S3-Touch-LCD-2 |
|---|---|
| VDD | 3V3 (P2 header, pin 1) |
| GND | GND (P2 header, pin 2) |
| SCK | GPIO43, TX (P2 header, pin 3) |
| WS | GPIO44, RX (P2 header, pin 4) |
| SD | GPIO12 (P2 header, pin 10); with a camera in the socket GPIO18 (P1 header, pin 6) |
| L/R | GND |

GPIO12 is one of the camera's pins: with a camera in the socket, SD goes to GPIO18 and `mic_sd:
GPIO18` in your file. The log goes over USB, since the UART's pins are the microphone's; take off
any USB-to-serial adapter on them. The board has no LED, so its display is the light: a red dot
while someone listens, dark otherwise. No mute switch. Compiled, not yet tried on a board.

**Waveshare ESP32-C5-WIFI6-KIT + INMP441** (Wi-Fi on 2.4 and 5 GHz) — [`examples/waveshare-esp32c5-kit-microphone.yaml`](examples/waveshare-esp32c5-kit-microphone.yaml),
the wiring a user worked out

| INMP441 | ESP32-C5-WIFI6-KIT |
|---|---|
| VDD | 3V3 |
| GND | GND |
| SCK | GPIO8 |
| WS | GPIO9 |
| SD | GPIO10 |
| L/R | GND |
| *mute switch (optional)* | GPIO4 to GND |

Go by the GPIO labels on the board. Its RGB LED lights red while someone listens. It has a
socket for a battery, but a microphone that streams all the time wants a USB supply. Compiled,
not yet tried on a board.

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
     - source: github://oleost/VisionState@esphome-v1

   packages:
     # … the device's own package stays first …
     visionstate:
       url: https://github.com/oleost/VisionState
       ref: esphome-v1
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
- *VisionState stream*: VisionState's stream on or off on its own (off: silence), leaving the
  assistant's microphone on; and the diagnostics (above).
- On the **Voice PE** it takes channel 1 of the XMOS chip, the one the wake word hears: the echo of
  its own speaker cancelled and noise suppressed, without automatic gain (channel 0 has that, which
  would hide how loud a sound is). Noise suppression may soften a steady tone: whether a smoke
  alarm still comes through is not checked.
- On the **Atom VoiceS3R** it takes the microphone the assistant uses, with the echo of its own
  speaker cancelled.

It builds next to both devices' own firmware (2026-11-05). Not checked on a device: memory and CPU
next to the wake word, the gain, and how alarms sound through the
processing ([`docs/ESP32_PLAN.md`](../docs/ESP32_PLAN.md) §5).

## A voice assistant on a microphone of ours

A board with VisionState's microphone (a DevKit, an ESP32-CAM, an S3…) can also be a voice
assistant, with no speaker of its own:

- **The wake word is heard by Home Assistant**, so it runs on an ESP32 without PSRAM too: the
  device sends its sound to Home Assistant all the time, next to VisionState's stream. The
  assist pipeline needs a wake word engine (such as the openWakeWord app).
- **The answer plays on a media player in Home Assistant**: a Chromecast, a Music Assistant
  player… Which one is *Answer on* on the device, kept over restarts and changed in Home Assistant or
  on the sound sensor's Live tab in VisionState, without installing again (empty: no answer played);
  `assistant_media_player` is the one it starts with. On the device in Home Assistant (*Settings → Devices & services → ESPHome → the
  device → Configure*), switch on *Allow the device to perform Home Assistant actions*. A
  Chromecast fetches the answer from Home Assistant's internal URL, so it must reach that.
- From the wake word until the answer is done the stream carries silence (*VisionState pauses for
  the assistant* turns that off). *Microphone* and the mute switch mute both; *VisionState stream*
  switches off only VisionState's stream.

The ESP32 guide in VisionState writes it (*A voice assistant too*, for a microphone). By hand, add
the package after the microphone's, with the player it starts with:

```yaml
substitutions:
  assistant_media_player: media_player.living_room

packages:
  visionstate:
    # …
    files:
      # … the board, common.yaml and microphone.yaml …
      - esphome/packages/voice-assistant-ha-wake-word.yaml
```

Compiled for an ESP32 DevKit, not yet tried on a board.

## When something is wrong

| What you see | Why |
|---|---|
| *401* in the guide's check, or the sensor gets no sound | The password is not the one in the device's file (`stream_password`). |
| *409* | Two listeners are on already (VisionState and someone else): close one. |
| *Pure silence* on the sound sensor | The stream is nothing but zeros: the microphone is not wired (check SD, SCK, WS and L/R to GND; on a XIAO, that the camera board is on), is broken, or is muted (the *Microphone* switch is off, or the mute switch is closed). |
| Very quiet sound | Raise the gain (on the sensor's Live tab, or *Microphone gain* in Home Assistant). *Sound level* (a diagnostic) says how loud it arrives. |
| The device drops off the network for minutes, without restarting (its *Uptime* goes on) | Power: an ESP32-CAM on a weak USB supply does that when the camera and the Wi-Fi draw at once. A supply of at least 1 A, a short cable. |
| *Samples clipped* above 0 | The gain is too high for what is heard: lower it. |
| *Sound dropped* above 0 | The Wi-Fi does not keep up: a better signal (*Wi-Fi signal* above about −70 dBm). |
| The ESP32-CAM restarts, or the picture fails | A weak 5 V supply (see above). |
| Home Assistant does not ask to add the device | It is not on the Wi-Fi yet (ESPHome's log shows it), or Home Assistant is on another network. |

## The parts

| Package | What |
|---|---|
| [`packages/common.yaml`](packages/common.yaml) | every device: Wi-Fi (power save off: it leaves gaps in the sound), the API, updates, the stream's server and login, the light, diagnostics |
| [`packages/board-esp32-devkit.yaml`](packages/board-esp32-devkit.yaml), [`packages/board-esp32-cam.yaml`](packages/board-esp32-cam.yaml), [`packages/board-xiao-esp32s3-sense.yaml`](packages/board-xiao-esp32s3-sense.yaml), [`packages/board-esp32s3-devkit.yaml`](packages/board-esp32s3-devkit.yaml), [`packages/board-waveshare-esp32s3-lcd2.yaml`](packages/board-waveshare-esp32s3-lcd2.yaml), [`packages/board-waveshare-esp32c5-kit.yaml`](packages/board-waveshare-esp32c5-kit.yaml) | the board, its default pins and the light that shows someone listens (`vs_light`: an LED, or a display) |
| [`packages/microphone.yaml`](packages/microphone.yaml) | an INMP441 (I2S) as `/audio.wav` |
| [`packages/microphone-pdm.yaml`](packages/microphone-pdm.yaml) | the XIAO ESP32-S3 Sense's PDM microphone as `/audio.wav` |
| [`packages/microphone-controls.yaml`](packages/microphone-controls.yaml) | what both microphone packages include: the *Microphone* switch, the gain and the diagnostics |
| [`packages/diagnostics.yaml`](packages/diagnostics.yaml) | the stream's level, peak, clipped samples and dropped sound (off in Home Assistant until switched on) |
| [`packages/stream-switch.yaml`](packages/stream-switch.yaml) | *VisionState stream*: the stream on and off on its own, on a device whose microphone is shared |
| [`packages/mute-pin.yaml`](packages/mute-pin.yaml) | a mute switch on a pin: unwired, the microphone is on |
| [`packages/camera-esp32-cam.yaml`](packages/camera-esp32-cam.yaml) | the OV2640 and `/snapshot.jpg`, the flash as a light |
| [`packages/camera-xiao-esp32s3-sense.yaml`](packages/camera-xiao-esp32s3-sense.yaml) | the XIAO's camera and `/snapshot.jpg` (it has no flash) |
| [`packages/esp32-cam-microphone.yaml`](packages/esp32-cam-microphone.yaml) | camera and microphone on a plain ESP32: keeps the camera's I2S port free |
| [`packages/voice-pe.yaml`](packages/voice-pe.yaml), [`packages/atom-voices3r.yaml`](packages/atom-voices3r.yaml) | the stream added to a voice assistant's own configuration, on its microphone |
| [`packages/voice-assistant.yaml`](packages/voice-assistant.yaml) | what both include: one listener, silence while the assistant listens, the gain, *VisionState stream*, the diagnostics |
| [`packages/voice-assistant-ha-wake-word.yaml`](packages/voice-assistant-ha-wake-word.yaml) | a microphone of ours as a voice assistant too: the wake word in Home Assistant, the answer on a media player |
| [`components/visionstate_stream`](components/visionstate_stream) | the server: the stream, the snapshot and the login |

[`tests/`](tests) holds the configurations CI compiles (`.github/workflows/esphome.yml`), built from the
packages in this folder. `tests/voice-pe.yaml` and `tests/atom-voices3r.yaml` pull in the devices'
own configurations from their repositories, pinned to a release and a commit.
