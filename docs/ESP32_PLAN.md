# ESP32 microphones and cameras: the plan

Firmware of our own for cheap ESP32 boards — a microphone, a camera, or both — for VisionState's
sound and image sensors, built with ESPHome. The firmware is in [`esphome/`](../esphome/README.md);
what ESP32 microphones mean for sound sensors in general (stream all the time, no push) is in
[`SOUND_PLAN.md`](SOUND_PLAN.md) §10.

This plan keeps three things apart: what the maintainer **decided**, what is **proposed** (by
Claude, open to change), and what is **not checked** yet (claims that need a board, a Home
Assistant or a test before anything is built on them).

## 1. Decided (the maintainer, 2026-11-04)

- **Three kinds of device**: microphone, camera, camera and microphone — through **ESPHome**.
- **Cheap generic boards**: an ESP32 DevKit (ESP32-WROOM-32) and an ESP32-CAM (AI-Thinker layout).
- **The sound must be in Home Assistant**, so it can be **listened to** there, and **also have a
  direct address**, because that is fastest for VisionState.
- **A user name and password**, as cameras have, not a token or pairing.
- **A mute switch on a pin**: unwired, the sound goes through; wired, it can cut the microphone.
- **Installed through ESPHome** (the user brings our configuration into it), not an installer of
  our own.
- **Our own code under Apache-2.0**, in this repository (a folder of its own, tags `esphome-v*`,
  its own CI), on the branch `esp32` while it is built.

## 2. Built (on `esp32`, not released)

The component `visionstate_stream` and the packages in `esphome/`: the microphone as an endless
WAV over HTTP (16 kHz, mono, 16 bits), the camera's picture as a snapshot taken after the request,
both behind HTTP Basic auth on one port; the *Microphone* switch, the gain, the mute pin, the
listeners, the light while someone listens, the flash as a light. Compiled for both boards and all
three kinds (CI: `.github/workflows/esphome.yml`). What has been tried on a board is in §5.

In VisionState: the ESP32 guide (*Set one up with ESPHome*; the files are in
[`ARCHITECTURE.md`](ARCHITECTURE.md)) writes the device's file, follows it into Home Assistant
(found, waiting to be added, its address), checks it and makes the sensor. A sound sensor whose
stream is pure digital silence for `SOUND["dead_silence_s"]` says *Pure silence* (every sound
source, not only an ESP32: a camera with its audio off sends the same).

## 3. Listening in Home Assistant: open

The firmware gives the direct address. How Home Assistant plays it is **not checked**; these are
the ways to try, in this order:

| Way | What it would give | Not checked |
|---|---|---|
| **A media player** plays the address (`media_player.play_media`) | listening on a speaker, or in the browser with a browser media player | other players than Cast. A Google Cast speaker (2026-11-04) plays a WAV from Home Assistant's `/local`, also with the device's endless header, but the device never saw it as a listener. Most likely the receiver does not send the `user:password@` of an address; not proven, since the device's stream also has no length (chunked) and the file had one. Not taken further: a speaker is not the goal (the maintainer, 2026-11-04) |
| **go2rtc** (its app) takes the address and gives Home Assistant a camera with sound | a camera card with sound; for the ESP32-CAM picture and sound together | the CPU it takes, and what Home Assistant shows for sound with no picture |
| **The device serves RTSP** as well | one address like an IP camera's, for Home Assistant and VisionState | whether Home Assistant takes RTSP with sound only, or MJPEG pictures without converting them |

Known: Home Assistant has no entity for a sound stream on its own (sound comes with a camera's
stream), its camera streams in the browser need H.264 video, and the ESP32 sends JPEG pictures;
ESPHome sends a microphone to Home Assistant only for its voice assistant.

## 4. Proposed (Claude; not decided)

- **VisionState finds the device in Home Assistant** instead of the address being typed in: the
  device's *IP address* entity gives the address, so a new IP after a router restart is followed.
  Only once listening in Home Assistant (§3) is settled, since that may give an address of its own.
- **The ESPHome camera entity for image sensors** needs nothing new; the direct snapshot is there
  for speed and for a fresh picture (`fresh_snapshot`). Whether it is faster is a measurement (§5).
- **A voice assistant that also listens for VisionState** (the maintainer's idea, 2026-11-04: an
  ESP32-S3-BOX that is a voice assistant and hears smoke alarms too). ESPHome lets several parts
  share one microphone: the I2S microphone counts who has started it (micro_wake_word,
  voice_assistant and sound_level already share it), so `visionstate_stream` can be one more,
  running alongside the wake word instead of switching over. Proposed: an add-on package for an
  existing voice-assistant configuration (the stream on the device's own microphone, with
  `max_listeners: 1` and the password, so only VisionState hears it); a choice to send silence
  while the assistant listens and while its speaker plays (VisionState keeps no speech, but a
  spoken command and the box's own answers are not VisionState's business, and the speaker would
  be heard as sound). *Not checked*: the S3-BOX's microphone set-up (two channels, which one to
  take), memory and CPU next to the wake word, and the mute: the microphone's mute is shared, so the
  pause must be the stream's own. The same may work on other ESPHome voice devices (Home
  Assistant Voice PE: its sound is processed first, with automatic gain — to check).
- ***Only when loud*** (the device sends only above VisionState's line, with a heartbeat): not
  built. On mains power the only gain is privacy; it costs the background VisionState learns and
  every sound below the line. Left until someone asks for it.
- **Ideas not taken further**: the device's LED or display showing what VisionState heard (ESPHome
  can import a Home Assistant entity, no change in VisionState); a sound that starts an image
  check (VisionState's trigger entities may do it already). Left out: classifying on the device,
  recording on the device, a speaker, a Home Assistant integration of our own, battery power
  (listening all day keeps the ESP32 awake).

## 5. Not checked yet, and how

On the maintainer's boards (an ESP32-WROOM-32 and an ESP32-CAM; the INMP441 has not arrived):

- [x] Both boards start, join the Wi-Fi and show up in Home Assistant, installed through the guide
      (2026-11-04).
- [x] `/audio.wav` answers with the login and refuses without it (401; a wrong path 404), a third
      listener gets 409, and VisionState's sound sensor takes the stream: 16 kHz mono, about
      32 kB/s, all zeros without a microphone (2026-11-04, the DevKit). The *Microphone* switch off
      gives silence without breaking the stream; after *Restart* the device is back in about 7 s with
      the switch as it was, and VisionState listens again by itself.
- [x] `/snapshot.jpg` on the ESP32-CAM: the picture comes, fresh, also with no one else looking
      (2026-11-04; at first it did not: ESPHome's camera forgets a request made while it hands out
      the last picture, so the component asks again until one comes). An image sensor (objects) on
      it in VisionState works, the flash as its light. A snapshot takes about 180 ms (median of 15;
      at most 300), Home Assistant's camera entity about 80 ms, but that is the picture ESPHome
      kept ready, taken after the last fetch.
- [x] Clients that keep their connection open (HTTP keep-alive) do not lock the device out
      (2026-11-04). They did: the Cast speaker's attempt, and VisionState's own snapshots, held the
      one spare socket, and no new connection was taken until a restart, while the open streams
      went on. Every answer that is not a stream now closes its connection, and two sockets are
      spare; tried on both boards with keep-alive clients on 401, 404, the snapshot and 409.
- [x] Gaps in the sound without a microphone (2026-11-04, both boards, 55 minutes, VisionState and
      a second listener on each, snapshots every 10 s from two clients on the ESP32-CAM): the 21
      quiet minutes at the end had no gap over 250 ms on either board, snapshots included. Before
      that, short bursts of 250–730 ms gaps came only while devices were being tested, installed
      or restarted. A day's run, and gaps with a real microphone, are still to do (below).
- [x] The ESP32-CAM with the camera and the microphone's I2S running together (the empty I2S port
      in `esp32-cam-microphone.yaml`): the camera works (2026-11-04, no microphone wired yet). The
      flash must stay off the camera's LEDC channel 0 (`camera-esp32-cam.yaml` uses channel 2).
- [ ] The mute pin: GPIO12 on the ESP32-CAM is a strapping pin — does the board still start with
      the switch closed and open?
- [ ] With the INMP441: the level of a smoke alarm at a few metres and the gain it needs, how long
      until VisionState hears a sound (against a camera's RTSP and its Home Assistant stream), gaps
      over 24 hours, and on the ESP32-CAM gaps in the sound while a picture is sent.
- [ ] Listening in Home Assistant (§3).
