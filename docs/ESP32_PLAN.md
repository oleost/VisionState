# ESP32 microphones and cameras: the plan

Firmware of our own for cheap ESP32 boards — a microphone, a camera, or both — for VisionState's
sound and image sensors, built with ESPHome. The firmware is in [`esphome/`](../esphome/README.md);
what ESP32 microphones mean for sound sensors in general (stream all the time, no push) is in
[`SOUND_PLAN.md`](SOUND_PLAN.md) §10.

Testing on real boards and feedback since the release: issue
[#86](https://github.com/oleost/VisionState/issues/86) (#75 was the pull request it was built in).

This plan keeps three things apart: what the maintainer **decided**, what is **proposed** (by
Claude, open to change), and what is **not checked** yet (claims that need a board, a Home
Assistant or a test before anything is built on them).

## 1. Decided (the maintainer, 2026-11-04)

- **Three kinds of device**: microphone, camera, camera and microphone — through **ESPHome**.
- **Cheap generic boards**: an ESP32 DevKit (ESP32-WROOM-32) and an ESP32-CAM (AI-Thinker layout).
- **The Seeed Studio XIAO ESP32-S3 Sense as well** (2026-11-05), for a user who has one: camera and
  microphone (PDM) on the board, nothing to solder. A user had found that a microphone on the
  ESP32-CAM's pins leaves no room for its USB board.
- **An ESP32-S3 DevKit and the Waveshare ESP32-S3-Touch-LCD-2 as well** (2026-11-05), microphone
  only, for a user (#75) who had wired an INMP441 to the Waveshare with firmware of their own: our
  firmware on it, with their wiring.
- **The Waveshare ESP32-C5-WIFI6-KIT as well** (2026-11-06), microphone only, with the wiring the
  same user found. From their own firmware (#75) we took the idea of showing on the LCD-2's display
  that someone listens; not its token in the address (we have a password), its fixed WAV length,
  or its level sensors every second (VisionState measures the level itself, and Home Assistant
  would store each one).
- **The sound must be in Home Assistant**, so it can be **listened to** there, and **also have a
  direct address**, because that is fastest for VisionState.
- **A user name and password**, as cameras have, not a token or pairing.
- **A mute switch on a pin**: unwired, the sound goes through; wired, it can cut the microphone.
- **Installed through ESPHome** (the user brings our configuration into it), not an installer of
  our own.
- **Our own code under Apache-2.0**, in this repository (a folder of its own, tags `esphome-v*`,
  its own CI), on the branch `esp32` while it is built.

## 2. Built (released on the line `esphome-v1`)

The component `visionstate_stream` and the packages in `esphome/`: the microphone as an endless
WAV over HTTP (16 kHz, mono, 16 bits), the camera's picture as a snapshot taken after the request,
both behind HTTP Basic auth on one port; the *Microphone* switch, the gain, the mute pin, the
listeners, the light while someone listens, the flash as a light, and each sensor's camera settings
(§7: picture size, quality, clock, exposure and gain, sent with its snapshot; until firmware 1.5.0 a
*Camera use* on the device that VisionState set with `?use=`). Compiled for both boards and all
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
  ESP32-S3-BOX that is a voice assistant and hears smoke alarms too). Built for the Home Assistant
  Voice PE and the M5Stack Atom VoiceS3R (the maintainer, 2026-11-05: ready for someone who has one
  to try; [`esphome/README.md`](../esphome/README.md)), as below: one listener, silence while the
  assistant listens (a switch), the stream on the device's own microphone. On the Voice PE that is
  XMOS channel 1, which `voice_kit` sets to echo cancelled, interference cancelled and noise
  suppressed (its default `channel_1_stage`), without automatic gain; the firmware can also give
  the raw microphone or echo cancellation alone, but the wake word hears the same channel, so it is
  left as it is. What follows is how it was proposed. ESPHome lets several parts
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
- **A voice assistant on a microphone of ours** (the maintainer, 2026-11-10, for the DevKit with an
  INMP441: the wake word not by a button, the answer on a Chromecast or Music Assistant player):
  `esphome/packages/voice-assistant-ha-wake-word.yaml`. The wake word is Home Assistant's
  (`use_wake_word`), since `micro_wake_word` on an ESP32 without PSRAM is not known to work; the
  answer goes to a media player through a Home Assistant action. The stream pauses from the wake
  word (not from the pipeline's start: with the wake word in Home Assistant the pipeline runs all
  the time). *Not checked*: on a board.
- **The guide from start to finish on a real Home Assistant (2026-11-11, 0.15.0b11):** a new
  ESP32-CAM made with the guide, imported into the ESPHome app (*Import from file*), installed over
  USB from the browser, added in Home Assistant and found by the guide. Found and fixed: the guide
  forgot its stream password when the page was left to install (it is kept for the tab now), a
  discovered device with a friendly name shows as *Name (device)* in Home Assistant (the device
  name is taken from the parentheses), Home Assistant takes the key from its own ESPHome app without
  asking, and a board Home Assistant was once told to ignore stays ignored under a new name (it
  matches by MAC; the guide now says where to stop ignoring it). Direction from the maintainer:
  build for new devices; what older ones need goes in an issue, not into the code or the docs.
- **Locked (2026-11-11, found reviewing the firmware with the maintainer), firmware 1.2.0:** an
  OTA port without `encryption:` took plaintext firmware from anyone on the network (ESPHome only
  offers the API key's encryption then), and the fallback hotspot was open, its captive portal
  taking firmware too. The guide and the examples now write `encryption: {}` under `ota:` and an
  `ap_password` (`common.yaml` keeps it empty by default, and the encryption out, so files made
  before still build, one with an OTA password among them).
- **A voice assistant from the guide, its speaker on the device (the maintainer's wish, 2026-11-11),
  built in firmware 1.2.0:** the guide offers *A voice assistant too* for a microphone, with a media
  player from Home Assistant (or one chosen later). The player is *Answer on* on the device (a text
  entity kept over restarts, `assistant_media_player` its first value), set in Home Assistant or on
  the Live tab: a new speaker, or one renamed in Home Assistant, needs no new install. The list
  names each player's integration (one speaker is often there through Google Cast, Music Assistant
  and DLNA alike: the maintainer could not tell them apart by name). Tried 2026-11-11 on the
  WROOM-32: "Okay Nabu" heard by Home Assistant, the answer played on the Google Cast speaker chosen.
- **The assistant heard its own answer (the maintainer, 2026-11-12):** when the answer ends with a
  question, Home Assistant continues the conversation, and the device listened again 0.1 s after the
  answer's text was ready, seconds before the Google Cast speaker started playing it: the answer
  was taken for a new command, round after round. It is the device that starts listening again
  (ESPHome's `voice_assistant` with no output of its own goes straight on), and the device never
  said when the answer was played, so the satellite stayed *responding*. Muting the microphone
  meanwhile was not chosen: Home Assistant's time to answer (about 15 s) would run while the
  speaker plays. Built instead: an answer player on the device (`components/visionstate_answer`),
  the voice assistant's `media_player`. It plays the answer on *Answer on* and stays announcing
  until that player in Home Assistant has gone from playing to anything else (it follows the
  player's state, also after *Answer on* names another; limits for a player that never starts or
  plays on). `voice_assistant` waits for it, tells Home Assistant the answer is done and only then
  listens for the follow-up. VisionState's stream stays paused until then too, and pauses again for
  the follow-up (it starts without a wake word). A player that plays an announcement over its music
  (Sonos, Music Assistant) may show no start or end in its state: then the answer's length, guessed
  from its text, says when it is done. The player tells Home Assistant of a new *Answer on* through
  ESPHome's API from the inside (`active_clients`, a subscription message): ESPHome sends its list of
  subscriptions only once a connection, so a change in ESPHome there shows in the weekly CI build
  against the latest ESPHome. Found trying it: an error in a continued conversation (speech-to-text
  failed) left ESPHome starting one follow-up after another without a wake word, each taking
  whatever was said in the room; and an answer the player could not play (its integration was
  down) let the conversation go on unheard. Both now end the conversation and go back to the wake
  word (built, not yet seen happen on the device). Tried 2026-11-12 on the WROOM-32 with a Google Cast speaker: answers in a row, follow-ups
  without the wake word, none of them heard back; *Answer on* changed while running (Home
  Assistant sends the new player's state at once); a player that never started (the guess from the
  text ended the wait). Speech-to-text that makes words out of silence at the end of a follow-up
  (Whisper: "what are you doing?") keeps a conversation going, which no firmware can tell from
  speech. *Not checked*: a player that plays over its music, other boards than the WROOM-32
  (built for the ESP32-CAM with its camera, the S3 and the C5).
- **Dark pictures and a colour cast with the flash (#86, pedromfa's export, 2026-11-12), firmware
  1.4.0:** after moving his water meter to our firmware, some checks were rejected: pictures
  black because the flash could not be switched on through Home Assistant (the device was away
  from it for a moment), and pictures pink or purple in the dark, the dial then read the other way
  round now and then. His log showed the flash fading in over a second (ESPHome's default
  transition): pictures taken meanwhile had a light that kept changing. A longer wait helped him.
  Not seen on the maintainer's two ESP32-CAMs, close up and in the dark (colours steady from the
  first lit picture). Built: the flash full at once (`default_transition_length: 0s`; with Home
  Assistant switching it, lit 0.6 s after instead of 1.0-1.1 s), and `/snapshot.jpg?flash=<ms>`:
  the camera switches its own flash for the picture, throws away the pictures of that time, and
  switches it off again (the `X-VisionState-Flash` header says it can). VisionState asks so when a
  sensor's light is that camera's own flash, and a sensor's light can be switched on only when it
  is dark (the maintainer's wish: flash off, on or auto). For the camera's own flash the camera
  decides that itself (`&dark=<0-255>`, the maintainer's idea: it looks all the time): from the
  picture it keeps ready, decoded at 1/8 of its size, or from the next one after a snapshot with
  the flash (the one kept then was taken while it was still on, and made a dark place look light).
  Another light is judged by VisionState from a frame taken without it first. Measured on the test ESP32-CAM: lit
  pictures from `flash=0` on (the fresh snapshot's thrown-away picture is time enough), a flash
  already on left on. The dial reader no longer fails on a needle it loses when it looks round its
  hub (it raised an error on one of his pictures). Not hidden in the dial reader (the
  maintainer's choice): numbers that pass for red in a colour cast.
- **The stream password told through Home Assistant (the maintainer's wish, 2026-11-12), built in
  firmware 1.3.0:** looking for `stream_password` in a device's file to make a sensor from it was
  the worst step. An entity holding it was not chosen: Home Assistant records every state, keeps it
  in its backups and shows it to every user. The device has an action instead
  (`esphome/packages/stream-password.yaml`, `esphome.<device>_stream_password`) that answers with the
  password and its address; VisionState calls it when the device is picked as a source and takes
  the answer only when the address is that device's (Home Assistant names the action after the
  device's name in its file, not shown anywhere VisionState can match, so the one named like its
  *IP address* entity is asked first). A device with an older firmware asks for the password as
  before. Anyone who may call actions in Home Assistant can ask for it; anyone who may install the
  firmware can read it already. Tried 2026-11-12 on the WROOM-32 through the main Home Assistant:
  the password and address came back, and the stream answered with it.
- **Picture orientation (the maintainer's wish, 2026-11-10), built in firmware 1.2.0:** a camera
  of ours turns its picture 180° and mirrors it on the chip (`camera-orientation.yaml`), chosen in Home
  Assistant or in a camera sensor's settings (through Home Assistant, like the microphone's
  controls). Found switching the maintainer's water meter ESP32-CAM to our firmware: its own file
  had `vertical_flip: false`, which the packages left at ESPHome's default. Tried on the test
  ESP32-CAM (2026-11-11): *Upside down* and *Mirrored* set from a sensor's settings give the picture
  turned 180° and mirrored (compared with the one before); the picture follows a few seconds after
  the choice. On a weak power supply the same board dropped off the Wi-Fi for minutes without
  restarting (60–100 % of pings lost, with VisionState listening or not); on a power bank 0 of 600
  were lost, with ESPHome's own camera firmware and with ours alike.
- **From #75 and #85 (hruba202, 2026-11-10), built in firmware 1.1.0:** microphones at 48 kHz
  filtered and taken down to 16 kHz on the device (`mic_sample_rate`; a 48-tap windowed sinc, edge at
  7.2 kHz), *VisionState stream* on a shared microphone (`stream-switch.yaml`), the stream's
  diagnostics (`diagnostics.yaml`: level, peak, clipped, dropped; off in Home Assistant until
  switched on), and the device's *Microphone* and gain on a sound sensor's Live tab. Not built: an
  allowed subnet (the password is the lock; a subnet check is easy to get wrong behind NAT and
  Home Assistant's own network). The firmware is now a line that moves (`docs/RELEASING.md`, decided
  with the maintainer 2026-11-10): updating is *Install*, not editing the file.
  Tried 2026-11-10 on the WROOM-32: with ESPHome's fetch older than its refresh (a day), *Install*
  fetched the moved `esphome-v1` (1.1.0) with the device's file unchanged. With `refresh: 1d` an
  Install less than a day after the last fetch built the old version (the maintainer's devices,
  2026-11-11, right after 1.2.0), so the guide and the examples write `refresh: 0s` since 0.15.0b10:
  a shallow fetch at every Install, and the copy it has when GitHub cannot be reached.
- ***Only when loud*** (the device sends only above VisionState's line, with a heartbeat): not
  built. On mains power the only gain is privacy; it costs the background VisionState learns and
  every sound below the line. Left until someone asks for it.
- **Ideas not taken further**: the device's LED or display showing what VisionState heard (ESPHome
  can import a Home Assistant entity, no change in VisionState); a sound that starts an image
  check (VisionState's trigger entities may do it already). Left out: classifying on the device,
  recording on the device, a speaker, a Home Assistant integration of our own, battery power
  (listening all day keeps the ESP32 awake).

## 5. Not checked yet, and how

On the maintainer's boards (an ESP32-WROOM-32 and an ESP32-CAM, and an INMP441 since 2026-11-10):

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
      it in VisionState works, the flash as its light. Home Assistant's camera entity answers faster (55-110 ms), but
      with the picture ESPHome kept ready, taken right after the last fetch: switched on 1.5 s
      before, the flash was dark in it every time, and light in the snapshot (2026-11-05; as old as
      the time since the last check, so a water meter checked every minute is read a minute late).
      A snapshot took 200-230 ms, 100 of them waiting for the camera's next picture at ESPHome's
      default 10 fps; at max_framerate 25 fps it takes 137-159 ms (the fresh picture after 42-59 ms).
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
- [x] Camera use on the ESP32-CAM (2026-11-05): each choice sets its size at once and keeps it over
      restarts. States 800x600, 12-15 kB, 138-145 ms; Meters and displays 1600x1200, 53-57 kB,
      266-285 ms; People, cars and animals 800x600 with exposure 250, 11-14 kB, 125-133 ms, and
      darker (52 against 90 with the flash on a wall). Not checked: how sharp something moving is
      at that exposure, how a meter's digits read at 1600x1200 against 800x600, and the XIAO.
- [ ] The mute pin: GPIO12 on the ESP32-CAM is a strapping pin — does the board still start with
      the switch closed and open?
- [x] The INMP441 on an ESP32 D1 mini (WROOM-32), wired as the DevKit's defaults (2026-11-10):
      VisionState's sensor takes the sound (room noise about −50 to −25 dB at gain 4), and a smoke
      alarm played from a speaker in the room was heard (about 30 s, one short drop in a pause of
      its beeps).
- [ ] With the INMP441: the level of a smoke alarm at a few metres and the gain it needs, how long
      until VisionState hears a sound (against a camera's RTSP and its Home Assistant stream), gaps
      over 24 hours, and on the ESP32-CAM gaps in the sound while a picture is sent.
- [ ] Listening in Home Assistant (§3).
- [ ] The Voice PE and the Atom VoiceS3R with VisionState's stream: it builds next to their own
      firmware (2026-11-05, Voice PE 26.9.0 with RAM 44 % and flash 36 %, M5Stack's file at one
      commit with RAM 41 % and flash 55 %), but is not tried on a device:
      memory and CPU next to the wake word, the gain, whether a smoke alarm comes through the noise
      suppression, and the pause while the assistant listens.
- [ ] The ESP32-S3 DevKit and the Waveshare ESP32-S3-Touch-LCD-2 (compiled, not tried on a board):
      the INMP441 on an S3, the DevKit's RGB LED, and on the Waveshare the microphone on the
      UART's pins with the log on USB, SD on GPIO18 with a camera in the socket, and its display as
      the light (that the dot is red, not blue: the colour order is taken from the user's firmware).
- [ ] The Waveshare ESP32-C5-WIFI6-KIT (compiled, not tried on a board): the INMP441, its RGB LED,
      and Wi-Fi on 5 GHz.
- [ ] The XIAO ESP32-S3 Sense (compiled, not tried on a board): the camera's picture, the PDM
      microphone's channel (left is assumed) and the gain it needs, its LED, and how warm it runs
      with the camera on.

## 6. Into beta: the steps (the maintainer, 2026-11-06)

No alpha channel: a device's file names the branch its firmware comes from, so devices set up from
an alpha would keep following that branch after the app moved on to beta or stable. The work goes
from `esp32` straight into `beta`. Looked through for existing users: Camera use only asks cameras
that answered with `X-VisionState-Camera`; no new database version; the new `camera_use` in a
sensor's triggers is empty by default and ignored by older versions (exports and backups both
ways); the guide does nothing until opened.

- [x] Fix `test_every_kept_backup_restores[backup-format1-schema15.zip]` on `beta` (not from this
      work): the kept backup's history is older than `history_days`, so the app deletes it at
      start and the test fails by today's date (it passed CI on 2026-11-05). It would stop the next
      beta tag.
- [x] *Pure silence* is new for every sound sensor, not only ESP32s: 60 s of exact digital zeros
      (`SOUND["dead_silence_s"]`) gives a log warning and the chip on the dashboard. A camera that
      sends true zeros in a quiet room (a noise gate) would show it every night. Check on the
      maintainer's own sound sensors on the test Home Assistant before the beta. (2026-11-10: the
      doorbell's RTSP stream, down to −86 dB in a quiet room, was not taken for it.)
- [x] Merge `esp32` into `beta`; tag `esphome-v1` and set `settings.ESPHOME["ref"]` to the tag and
      `refresh` to `1d` in the same beta (`docs/RELEASING.md`), so beta users' devices stay on a
      fixed firmware. A later fix that needs a change in users' files gets `esphome-v2`.
- [x] The usual checks, the changelog entry shown to the maintainer, and their "go" (2026-11-10:
      released in 0.15.0b7).

## 7. Camera settings per sensor (the maintainer, 2026-11-16)

Prompted by issue #86 (pink pictures from one ESP32-CAM, and the wish to try a slower camera
clock): *Camera use* on the device is replaced by six settings that belong to each sensor.

- **The sensor owns them:** picture size, JPEG quality, camera clock (20 or 10 MHz), exposure
  (automatic, or fixed in milliseconds), and the gain ceiling. They are kept in the sensor's
  triggers and sent with each snapshot; the firmware changes only what differs from the last
  picture and throws away the pictures taken while the camera settles.
- **Presets only fill them in:** *States*, *Meters and displays*, *People, cars and animals*. A new
  sensor starts from its kind's preset. The name shown is worked out from the values: a preset's
  name when they match one, *Custom* when they do not; nothing else is stored.
- **Not in Home Assistant:** *Camera use* and *Exposure for moving things* go; *Picture
  orientation* stays (how the camera hangs, not what a sensor wants). Breaking inside the 0.15.0
  betas is fine (the maintainer).
- **Two sensors on one camera with other settings:** the camera switches between them before each
  picture (correct, a little slower); they share a picture only when their settings are the same,
  and the sensor's settings say so when another sensor on the camera differs.
- **Milliseconds:** the firmware turns them into the sensor's lines by the picture size and clock;
  shown as "about", since only one chip (the test ESP32-CAM's OV3660) is measured.
- **Older firmware** (1.0–1.4): asked with `?use=` as before, when the values are a preset's; a
  custom one is not sent (the camera stays as it is) and the sensor's settings say it needs 1.5.0.
- **When:** before the stable 0.15.0 (the maintainer).
- **Tried on the test ESP32-CAM (2026-11-16, firmware 1.5.0 built from the working copy; its chip
  is an OV3660, which the snapshot now says: `X-VisionState-Camera-Chip`):** every
  setting taken, the clock switched 20 → 10 → 20 MHz and the size back and forth twelve times while
  it ran, every picture whole and its colour unchanged, nothing in its log. A 1600×1200 picture
  every 70 ms at 20 MHz and every 143 ms at 10 MHz (the line time the milliseconds are worked out
  by). Right after a change the camera hands out the one or two pictures it kept from before (after
  7 and 42 ms): they are thrown away as well. With the flash, the same exposure in milliseconds was
  about two thirds as bright at 10 MHz as at 20 MHz, so the milliseconds stay "about".
- **Issue #86:** the maintainer's two ESP32-CAMs and the water meter's own history showed no pink
  pictures (2026-11-16: 80 flash pictures in the dark, 268 readings since October). The user whose
  camera does (an OV2640, they believe; not the test camera's OV3660) had run it at 20 MHz before
  our firmware, at 1024×768, JPEG quality 12, `ae_level: 1` and ESPHome's single frame buffer (ours
  keeps two, so the camera takes pictures all the time). The first three are now their sensor's to
  try; the frame buffers are not a setting.
- **No picture after an install (issue #86, firmware 1.5.0):** that OV2640 gave no picture after
  every install of our firmware ("Got invalid frame from camera" from the start) until the device
  was restarted; not with their old firmware, and not with one frame buffer either. The
  maintainer's two OV3660s never did it. A restart and the restart after an install are the same
  (no power cut); what differs is the install before it, written to flash while the camera keeps
  taking pictures. Firmware 1.5.2: the camera is powered down while firmware is installed
  (`camera_power_down_pin`), and a camera without a picture 30 s after start-up has the device
  restart once (remembered in memory that survives a restart, so never in a loop). Not seen here;
  that user confirmed on 2026-11-16 that the camera now comes up after an install (0.15.0b22).
  Not known yet which of the two did it: the device's log after an install says so ("The camera
  gave no picture … restarting once" is the safety net; without it, the power-down was enough).

**Measured on an OV3660 (2026-11-17), changed in firmware 1.5.3 and 0.15.0b23:**
- **The gain ceiling did nothing:** the camera driver's `set_gainceiling` writes the gainceiling_t
  (0–6) as it is into the OV3660's and OV5640's ceiling register (0x3A18-19), which holds the gain in
  1/16, so every choice was a ceiling under 1x (as dark at 2x as at 128x). The firmware writes the
  ceiling itself on those two chips; afterwards 15 ms gave 5 / 12 / 14.5 (mean grey) at 2x / 16x /
  128x in the dark, automatic exposure 11 / 25 / 30 at 4x / 16x / 128x. The OV2640 (its own scale)
  goes through the driver.
- **Thin lines across large pictures** (a few rows, full width, often green): at 20 MHz in 14 of 20
  detailed 1600×1200 pictures and 4 of 10 at 1280×1024, none at 1024×768 or smaller; at 10 MHz none
  in 30 at 1600×1200. Fewer with more compression (JPEG quality 20: 4 of 10, 30: none), so the chip's
  JPEG output not keeping up. A dark, flashed meter picture had none in 40. *Meters and displays*
  moved to 10 MHz.
- **The people preset:** a fixed 15 ms was nearly black in a dim room at any gain, and with a fixed
  exposure the automatic gain swung from picture to picture (once a picture whose lower part was
  washed out); automatic exposure was as sharp in a lit room and steady. *People, cars and animals*
  moved to automatic exposure, gain up to 32. Sensors on the old values of either preset follow it.
- A fixed exposure is at most one picture's time (the driver clamps it to the frame length: about
  70 ms at 1600×1200 and 20 MHz).

**What is left (2026-11-16), in order:**
1. Issue #86: ask for the log after an install, to know whether the power-down is enough (the
   restart then never happens) or the safety net carries it.
2. Issue #86: the pink pictures. The user tries JPEG quality 12 (as their old firmware) and reports
   overnight; if still pink, the camera clock at 10 MHz. Their export after 0.15.0b22 (2026-11-17):
   160 readings, none wrong, 2 rejected; 6 had a green tint (one so strong it was rejected). Their pink pictures were darker as well,
   as if taken before the camera settled. Next they run the camera at 10 MHz for a day (asked 2026-11-17). Still open if neither helps: the exposure level
   (`ae_level: 1` in their old file, not a setting here), and settling until the colour is steady
   instead of a fixed time (proposed, not built).
3. The dial misreads that the pink pictures cause, in the reader: `POINTER_DIAL_PLAN.md` §10,
   after the stable release (the maintainer).
4. Before the stable 0.15.0: the maintainer's own cameras on 1.5.3 (*Install* in the Builder).

## 8. Flash strength (the maintainer, 2026-11-20)

The flash was always full. The maintainer's own water meter came out somewhat overexposed with
it, and a weaker flash saves power and heat, which matters with the weak power supplies many
ESP32-CAMs run on.

- **Decided:** each sensor sets how strong its camera's own flash is (*Flash strength*, percent,
  `triggers.flash_level`), default half; the firmware (1.6.0) lights it at half too unless a
  snapshot asks otherwise (`&flash_level=`). Not kept back for old sensors (the maintainer: no
  backward compatibility here): they get half as well. A light switched through Home Assistant is
  not dimmed.
- **Why half (looked up 2026-11-20):** AI-on-the-edge-device, the widely used ESP32-CAM meter
  reader, starts its flash at 50 of 100 ([its parameters](https://jomjol.github.io/AI-on-the-edge-device-docs/Parameters/))
  and lists a lower flash among the cures for reflections ([reference image](https://jomjol.github.io/AI-on-the-edge-device-docs/Reference-Image/));
  its users report pictures spoiled by a flash too strong for the plastic cover
  ([#499](https://github.com/jomjol/AI-on-the-edge-device/issues/499)), and one of its collaborators
  advises a higher gain ceiling with a weaker flash against reflections, warning that with a very
  high ceiling the flash's strength hardly shows, since the exposure makes up for it
  ([#3360](https://github.com/jomjol/AI-on-the-edge-device/issues/3360)). At full the ESP32-CAM's
  LED gets "worryingly hot" ([arduino-esp32 #7742](https://github.com/espressif/arduino-esp32/issues/7742)),
  and switching it on is a known cause of the board's brown-outs. No measurements of its current
  per strength were found.
- **Built:** the flash's LED output at 40 kHz (ESPHome's 1 kHz would put stripes in a dimmed
  picture, which the camera reads a line at a time, about 56 µs a line) and `gamma_correct: 1.0`
  (ESPHome's curve for eyes would give about a seventh of the light at half). Two snapshots that
  ask for different strengths while it is on: the last one asked.
- **Measured on the maintainer's water meter (2026-11-20, an ESP32-CAM with an OV3660, the meter a
  few centimetres away, *Meters and displays*, automatic exposure):** the share of burnt-out pixels
  (a channel at 250 or more) in the counter's region was 86 % at full, 85 % at 75 %, 62 % at half
  and none at 25 % and 10 %, the same in a second round; the automatic exposure did not make up
  for it (the frame round the meter is dark). By eye: at full the digits are faint on a white
  wash, at half better, at 25 % the clearest, at 10 % clear but greyer. The reader read 750.826
  at every strength, sure 0.83 at full and 0.97-0.98 at 50, 25 and 10 %. No stripes at any
  strength at 40 kHz. In steps of 5 between 25 % and half (two rounds each, the same to within a
  point): burnt out none at 25 and 30 %, 4 % at 35 %, 30 % at 40 %, 51 % at 45 %, 63 % at half;
  read the same, sure 0.96-0.98 at each. By eye the digits are clear at all of them; from 35 %
  the white of the face starts to burn out. For this meter 30 % is the most light without loss.
