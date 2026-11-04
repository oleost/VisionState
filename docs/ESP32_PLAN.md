# ESP32 microphones and cameras: the plan

Our own firmware for a cheap ESP32 board that VisionState listens to (an I2S microphone such as the
INMP441), looks through (an OV2640 camera), or both: one firmware with three profiles — microphone,
camera, camera and microphone — set up through ESPHome and found by VisionState through Home
Assistant. This
is a plan, nothing of it is built. What VisionState already decided about ESP32 microphones (stream
all the time, no push) is in [`SOUND_PLAN.md`](SOUND_PLAN.md) §10; this plan builds on it. Written
2026-11-04, before the maintainer's boards (ESP32, ESP32-CAM, INMP441) arrived: every claim about
the hardware marked *to check* is checked on them in phase 0.

## 1. Why

- **Camera sound is often poor** (`SOUND_PLAN.md` §12): a camera's microphone sits behind the
  lens housing, its codec is chosen for speech, and Home Assistant's HLS stream brings it 7–12 s
  late. A microphone of its own, in the room, over a direct stream, is heard about as soon as the
  sound is made.
- **Rooms without a camera** can listen: a smoke alarm in the basement, the washing machine, the
  freezer alarm. An ESP32 with an INMP441 costs a few euros.
- **A face or state sensor and a sound sensor from one box** at the front door or in the garage.
- Others are building it already (a community user's ESPHome setup streams 16 kHz WAV over HTTP):
  there is demand, and a tested firmware made for VisionState is better than each user writing
  their own. Ours is written from scratch, under the repository's Apache-2.0 licence (the
  maintainer's decision, 2026-11-04).

## 2. What there is to build on

- **VisionState already listens to an HTTP audio stream** (a sound sensor's *RTSP or stream* source
  takes `http://`): firmware that serves an endless WAV stream works today, with no change to the
  app. The fake camera's `/sound.wav` is such a stream, and the tests use it.
- **The model takes 16 kHz mono**; the app resamples anything else. 16-bit PCM at 16 kHz is
  256 kbit/s: nothing for Wi-Fi.
- **A token in the address stays hidden**: `?token=` is among the keys the app masks in logs,
  errors and exports (`SECRET_QUERY_KEYS` in `settings.py`).
- **ESPHome** has the I2S microphone (`i2s_audio`), the camera (`esp32_camera`, also a snapshot and
  MJPEG server, `esp32_camera_web_server`), user-defined actions Home Assistant can call, entities
  it imports from Home Assistant, OTA updates, Improv Wi-Fi set-up and remote packages pulled from
  GitHub. Stock ESPHome sends its microphone only to Home Assistant's voice assistant, on a wake
  word, and Home Assistant does not pass that sound on (`SOUND_PLAN.md` §10): the stream itself
  needs an external component of ours.
- **Image sensors on an ESPHome camera work today**: it is a `camera.*` entity in Home Assistant,
  and image sensors take a snapshot of any camera entity.

## 3. The device

**One firmware, three profiles**, made from ESPHome packages so a user's YAML is a few lines:

| Profile | For | Package |
|---|---|---|
| **Microphone** | a room without a camera: the basement, the laundry | mic |
| **Camera** | image sensors only (a meter, a door), like an ESP32-CAM today, but tuned for VisionState | camera |
| **Camera and microphone** | a door, a garage: states, faces and sounds from one box | camera-mic |

```yaml
packages:
  visionstate: github://<repository>/camera-mic.yaml@v1   # or mic.yaml, camera.yaml
substitutions:
  mic_sck: GPIO14
  mic_ws: GPIO15
  mic_sd: GPIO13
```

The profiles are made of the same parts (a common part, a microphone part, a camera part), so a fix
in one reaches every profile that has it, and CI builds all three.

**Every profile:**

- **Home Assistant entities** (through the ESPHome integration): Wi-Fi signal, uptime, firmware
  version; restart and *identify* (blink the LED) buttons.
- **The addresses for VisionState** as text entities (§5), and pairing.
- **Wi-Fi power save off** (`power_save_mode: none`): modem sleep gives gaps in the sound and slow
  snapshots.
- **One web server, one port**, for the stream and the snapshot, guarded by the same token and
  subnet.

**The microphone part:**

- **The stream** (the external component): `GET /audio.wav` on its own port, an endless WAV
  (16 kHz, mono, PCM16, a header with an unknown length), sent in chunks of about 32 ms. A token
  and an allowed subnet guard it (401, 403), and the number of listeners is limited (409) — two:
  VisionState and someone listening in with VLC, so a test never stops the sensor.
- **No automatic gain**: an AGC changes the level all the time, and VisionState decides what is
  loud by the background it learns (`SOUND_PLAN.md` §4). A fixed gain, set once in Home Assistant
  (a number entity, kept over restarts).
- **Home Assistant entities**: *Microphone* (a switch, **kept
  over restarts**: a stream that is off after every power cut shows *No sound* until someone
  notices), *Microphone gain*, *Sound level* (dBFS, every 10–30 s and only when it changes by a few
  dB: Home Assistant stores every change, `SCOPE.md` §8), *Listeners* (how many are connected).
- **A privacy light**: the status LED is on while a listener is connected.
- **A mute switch the user can wire in, or not** (the maintainer's wish). Unwired, the microphone is
  on. Two ways, both in the docs:
  - *In the microphone's power* (recommended, and the only way on an ESP32-CAM, which has no pin to
    spare): a switch between 3V3 and the INMP441's VDD. No software can switch the microphone back
    on. The firmware sees it: the data pin has a pull-down, so a microphone without power gives
    only zeros, and after a second of zeros the device reports *Microphone: muted* and sends
    silence, so the sensor shows *muted*, not *No sound* and not a fault. The same check catches a
    loose wire.
  - *On a free pin* (a DevKit has plenty): a switch to GND on a pin with the internal pull-up —
    unwired the pin is high and the microphone is on, closed it is low and the device stops
    sending. Simpler to wire, but software decides, so the power switch is the one to trust.
  - Either way, a binary sensor in Home Assistant, and the stream says so, so VisionState shows
    *Muted on the device* on the sensor's Live tab.

**The camera part** — what VisionState learned from users' ESP32 cameras goes into the firmware:

- **The ESPHome camera entity** (image sensors pick it as any Home Assistant camera, which works
  today) and **a direct snapshot** (`GET /snapshot.jpg`, the same token): faster than going through
  Home Assistant, and found the same way as the stream (§5).
- **A fresh picture when asked.** ESPHome keeps one picture ready, taken right after the last one
  was fetched, up to 10 s earlier, and the sensor adjusts its exposure only between pictures: the
  first picture after a light came on was dark (issue #32, the light before a check in `SCOPE.md`).
  The snapshot takes a new picture when it is asked for (one thrown away first after the light
  changed, so the exposure has caught up).
- **The same picture size after every restart**: a camera that came back at its default size moved
  a reading sensor's region (issue #40). The size is fixed in the firmware and reported as an
  entity, so a change is a firmware change, not a surprise.
- **The flash LED as a light entity** (dimmable, off by default), so a sensor's light before a
  check can switch it — and never on by itself: an ESP32-CAM's flash is bright and warm.
- **Picture settings as entities**: resolution, JPEG quality, exposure and white balance (auto or
  fixed), mirror and flip, kept over restarts. Fixed exposure helps a meter read at night.
- **No MJPEG stream by default**: VisionState takes snapshots, and a stream someone forgot open
  keeps the camera and the Wi-Fi busy; it can be switched on for setting up the camera.

**Boards: the cheap generic ones** (the maintainer's decision, 2026-11-04) — what most users already
have or buy for a few euros. *To check* in phase 0; the docs list only boards that were tried.

| Board | Profiles | Notes |
|---|---|---|
| A generic ESP32 DevKit (ESP32-WROOM-32, 30 or 38 pins) + INMP441 | microphone | the simplest start: 5 wires, pins to spare (a mute switch) |
| A generic ESP32-CAM (AI-Thinker layout, the clones) | camera; camera and microphone with an INMP441 on the SD card's pins (13, 14, 15) | the camera driver on the plain ESP32 uses I2S0, so the microphone must get I2S1 (*to check* that ESPHome can be told so); GPIO12 is a strapping pin (a pull-up there at boot sets the wrong flash voltage) and stays free; with the microphone no pin is left, so mute is the power switch; clones differ (PSRAM, OV2640 or OV3660, the antenna): the firmware reports what it found |

*Tried, not promised* (the docs name them once tried): ESP32-S3 camera boards (the microphone built
in on some, I2S free because the S3 has a camera interface of its own: the way out if camera and
microphone together do not work on the ESP32-CAM), Ethernet / PoE boards for a fixed place, and
later the ESP32-P4 (§9).

## 4. Getting it onto the board

The aim: from a board in its bag to a sensor in VisionState without writing YAML — but never at the
cost of the app's principles (no extra privileges, nothing that phones home).

**Through ESPHome** (the maintainer's choice, 2026-11-04): the ESPHome Device Builder app is where
Home Assistant users build and install ESP32 firmware already, and it does well what we would
otherwise have to build: compiling, installing over USB (through its ESPHome Web page) or over
Wi-Fi, the Wi-Fi secrets, the API encryption key, handing the device to Home Assistant, and
updates when ESPHome or our firmware moves on. VisionState gives the configuration; ESPHome does
the rest.

1. **New sensor → *An ESP32 microphone or camera*** in VisionState: pick the board, the profile and
   a name; the pins are filled in for the board and can be changed (a pin the board cannot use, or
   a strapping pin such as GPIO12 on an ESP32-CAM, is refused with the reason). It shows the wiring
   for those pins.
2. **The configuration**: a short YAML — the device's name, the board, the pins as substitutions,
   our package and external component pulled from this repository by the firmware's tag
   (`github://`, §12), the Wi-Fi as `!secret` (ESPHome's own secrets file) and a fresh API
   encryption key. *Copy* (for ESPHome's *Empty configuration*: paste — works on a tablet) or
   *Download* (for *Import from file*). It holds no VisionState secret: the stream's token comes
   with pairing (§5), so the file can be kept, shared or posted in an issue.
3. **In ESPHome**: import or paste, *Install* (the first time over USB, then over Wi-Fi). ESPHome
   hands the device to Home Assistant with its key (*to check* at the version we pin).
4. **Back in VisionState**, the new sensor's source step waits for the device: once it is in Home
   Assistant, it shows up by itself ("Hallway microphone found"), one tap pairs it (§5), and the
   sensor is made — the "add the sensor right there" the maintainer asked for.

The YAML stays the user's: they can add their own sensors to the same board (a temperature
sensor, a button), and a new version of our firmware is a new tag in it (or `@esphome-v1`, which
follows the latest of version 1).

**What it costs the user**: the ESPHome app (one more app, if they do not have it), and the first
compile takes minutes — on a Raspberry Pi or a Home Assistant Green longer, and it needs memory.
DOCS.md says so. Later compiles are fast.

**What it costs to build**: the generator in VisionState (a template filled in: small), the
package and the external component (needed anyway), CI that compiles every board and profile with
the ESPHome versions the docs name, and the finding and pairing (§5, needed anyway).

**Not done:**

- **An Install button inside VisionState.** Installing from a browser uses Web Serial: only Chrome,
  Edge and Opera on a computer (not Firefox, not Safari, not the Home Assistant app on a phone), only
  on an HTTPS page (many Home Assistants are opened over plain `http://`), and not inside an iframe
  that does not allow it — and VisionState runs in Home Assistant's Ingress iframe. ESPHome has
  solved this already (its ESPHome Web page), so we use it.
- **Prebuilt images on a web page of our own** (ESP Web Tools): no ESPHome needed, but pins fixed
  at our wiring, a shared image cannot carry a key of its own, and we would do the updates. Only if
  users without ESPHome ask for it.
- **Flashing from the Home Assistant server**: the app would need the host's USB ports (a lower
  security rating in Home Assistant for every user), and that machine is often in a cupboard.

## 5. In Home Assistant, and found by VisionState

The maintainer's wish: the microphone (and the camera) is picked in VisionState as a Home Assistant
device, not by typing an address — but VisionState listens to it and fetches its pictures directly,
because that is fastest.

Home Assistant cannot carry the sound itself: it has no entity for a live audio stream, and a camera
stream needs H.264 video, which an ESP32 cannot encode (§9 for the P4). So Home Assistant carries
**how to reach it**, and VisionState opens the stream itself:

1. The device has a text entity, *VisionState stream*, whose value says what it is and where:
   `vsmic/1 http://192.168.1.50:8081/audio.wav` (no token); a camera has *VisionState snapshot*,
   `vscam/1 http://192.168.1.50:8081/snapshot.jpg`. The prefix finds it however the user renames
   the entity, and the version lets the protocol change later.
2. **A new sound source type, *ESPHome microphone***: the picker lists every entity whose value
   starts with `vsmic/`, by the device's name. The sensor stores the entity ID, as a camera source
   does, and **reads the address each time it opens the stream**: a new IP address after a router
   restart is followed without the user doing anything.
3. **Pairing**: when the sensor is made, VisionState makes a random token, sends it to the device
   through Home Assistant (the device's action, a service like `esphome.<device>_set_stream_token`,
   stored in flash) and keeps it with the sensor. The token never appears in a Home Assistant
   entity or its history. A device paired again (another VisionState, a reset) gets a new token,
   and the old one stops working.
4. **The camera the same way**: image sensors get the source type *ESPHome camera* (`vscam/`
   entities): the direct snapshot, its address read each time, and the same pairing — one token per
   device, for its stream and its snapshot. The ESPHome camera entity keeps working as a plain Home
   Assistant camera.
5. **On the same device**: the picker knows which camera and microphone belong together, so making
   an image sensor suggests the camera of the device that already listens, and the other way round.

The plain address keeps working for users without the new source type, or outside Home Assistant:
the stream is an ordinary HTTP stream.

## 6. Should the ESP32 decide when to send?

The maintainer's question: VisionState already decides what is loud (*above the background* or
*above a fixed level*); could the ESP32 stay quiet until a sound crosses that line, and send a
heartbeat meanwhile?

**What it would win:**

| | Mains power | Battery |
|---|---|---|
| Wi-Fi | 256 kbit/s saved per device: nothing on a home network; a little air time on a crowded 2.4 GHz | the radio is the biggest draw: sending only now and then would matter |
| VisionState's CPU | almost nothing: it already decodes the stream cheaply and runs the model only on loud windows | — |
| Privacy | **real**: nothing leaves the device while it is quiet, quiet speech included | the same |

**What it would cost:**

- **Sounds below the gate are lost for good.** VisionState's line can be lowered in the UI after the
  fact; a gate on the device cannot bring back what it never sent. The quiet beep two rooms away is
  exactly the case the docs warn about.
- **VisionState learns the background from the sound** (`SOUND_PLAN.md` §4): without it, *above the
  background* does not work. The device would have to send its level instead.
- **The start of the sound**: the device keeps about a second from before the crossing (32 KB of
  RAM) and sends it first, so the clip and the first window are whole.
- **Recording own sounds** (*Record* on Own sounds) and the live level need sound while it is quiet:
  VisionState would have to open the gate while they are on.
- A second protocol (sound frames, level frames, a heartbeat), on both sides, to test and keep.

**Battery is not realistic for listening all day**: the microphone and the I2S clock keep the ESP32
out of deep sleep, so even with the radio mostly off it draws tens of mA — days on a battery, not
months (*to check*: measure it in phase 0). Listening needs mains power, USB or PoE; a battery
device would be a different product (wake on a level from a separate comparator), not this one.

**Recommendation: stream all the time (as `SOUND_PLAN.md` §10 decided), and keep the gate as an
optional *only when loud* mode for later** — only if users ask for it, because the only real gain on mains
power is privacy. If it is built, it is built the way that keeps what VisionState does:

- The device keeps the connection open and sends **level frames** while quiet (the level every
  100 ms: a few bytes; they are the heartbeat and what VisionState learns the background from, so
  *quiet*, *No sound* and the live meter keep working).
- **VisionState sets the gate**: it sends its line (minus a margin of a few dB) to the device
  through the device's action, every time the line changes. The device never decides by a level of
  its own the user cannot see.
- **VisionState opens the gate** while Record is on, while someone watches Live, and for a few
  seconds every few minutes (to check that the device hears what VisionState thinks).
- The stream says it is gated (a header), and the UI shows *Only sound above the line
  leaves the device*.

## 7. Other ideas, beyond the brief

- **The device shows what VisionState heard**, with no change to VisionState: ESPHome can import a
  Home Assistant entity (the sound sensor's binary sensor), so the LED blinks or a display shows
  *Smoke alarm* when it is heard. A cheap way to see that it works, and a visual alarm for people
  who do not hear well.
- **A sound starts an image check** (a VisionState feature, for every kind of source, not just
  ESP32): when a door slam is heard, the door's state sensor is checked now instead of at its next
  interval. The trigger entities already do this for Home Assistant entities, so a sound sensor's
  own entity may be enough; check that before building anything.
- **The level as a sensor of its own**: *Sound level* in Home Assistant is useful without
  VisionState (a noise graph, *it is quiet in the house*), and it helps set the gain.
- **µ-law (8-bit) instead of PCM16** halves the data; FFmpeg reads it. Not worth it: the quality and
  the level suffer, and 256 kbit/s is nothing (keep as an option only if a user's Wi-Fi needs it).
- **Two microphones for direction** (the time difference between them): fun, not useful enough for a
  home sensor. Left out.
- **A speaker** (the board as an intercom): out of scope — VisionState only listens.
- **Health in VisionState**: the device's uptime and Wi-Fi signal next to *No sound* in the sensor's
  status, so "the stream stopped" can be told apart from "the device restarted" and "weak Wi-Fi".

## 8. Not to do

- **No sound classification on the ESP32.** VisionState does it, and it is what makes own sounds,
  review and threshold advice possible; the device stays a microphone.
- **No push to VisionState** (`SOUND_PLAN.md` §10: the app is only reachable through Ingress).
- **No Home Assistant integration of our own** for this: the text entity and the action (§5) give
  what an integration would, with stock ESPHome.
- **No recording on the device** and no cloud: it streams to whoever is allowed to listen, nothing
  else.

## 9. Later: an ESP32-P4 camera with sound

The ESP32-P4 encodes H.264 in hardware, so it can serve RTSP with video and sound like an IP camera.
Then Home Assistant can stream it (a Generic camera with its RTSP address, go2rtc), and a sound
sensor picks it as a *Home Assistant camera* — that works in VisionState today. Worth watching, not
building, until ESPHome or a community firmware offers it.

## 10. Phases

0. **Try the hardware** (when the boards arrive): an INMP441 on the ESP32 and on the ESP32-CAM with
   the community component or a first sketch of ours. Measure: noise floor and level of a smoke
   alarm at a few metres, how long from a sound until VisionState hears it (against a camera's RTSP
   and its Home Assistant stream), gaps over 24 h, the camera and microphone together (I2S1 on the
   ESP32-CAM: does the sound get gaps while a picture is taken?), how fast a fresh snapshot is,
   memory and heat, the power draw. Decide the boards (§3).
1. **The firmware, microphone and camera profiles**: the external component (stream and fresh
   snapshot), the common, microphone and camera parts, the entities, the privacy light, CI that
   compiles every profile. Works with VisionState as it is, with the addresses typed in.
2. **Camera and microphone together**, on the boards phase 0 picked.
3. **The configuration from VisionState** (§4): board, profile, pins and name to a YAML for ESPHome.
4. **Found through Home Assistant** (§5): the source types *ESPHome microphone* and *ESPHome
   camera* and pairing, in VisionState; DOCS.md gets the devices and how to set them up.
5. Only if asked for: *only when loud* (§6).

## 11. How it is tested

- **VisionState side**: the fake camera plays the device: its stream and snapshot, the text
  entities (`vsmic/1`, `vscam/1`), a token, 401/409 answers, a device that changes address — so the
  source types and pairing are tested without hardware, in the backend tests and the e2e.
- **Firmware**: CI compiles every board and profile with the ESPHome version the docs name;
  a script on a real board streams for an hour and counts gaps (by the sample count against the
  clock).
- **By hand**: on the maintainer's boards and the test Home Assistant, through the dev app.

## 12. Decisions and open questions

Decided by the maintainer (2026-11-04):

- **The firmware lives in this repository**, in a folder of its own: the stream's protocol, the fake
  camera that plays the device and the source types change together, in one commit and one CI run,
  and the docs stay in one place. It gets tags of its own (`esphome-v1`, so the app's release
  workflow, which runs on `v*` tags, leaves them alone), a CI job that runs only when the folder
  changes, and users pull it with `github://` and that tag (the checkout is about 8 MB, once).
- **Our own code, under Apache-2.0** (§1).
- **The cheap generic boards** (§3): an ESP32 DevKit and an ESP32-CAM.
- **A mute switch** the user can wire in or leave out, from the start (§3).
- **Installed through ESPHome**, with the configuration made in VisionState (§4).

Open:

- **Only when loud** (§6): left out until asked for, as recommended?
