# Sound sensors: research and plan

Hearing **what kind of sound** a microphone picks up (a siren, a smoke alarm, a dog barking,
someone speaking, our own doorbell) and turning it into Home Assistant sensors, all locally. Nothing
is built yet; this is the plan and the decisions made so far. How the app works now is in
[`SCOPE.md`](SCOPE.md), the code map in [`ARCHITECTURE.md`](ARCHITECTURE.md). Research done
2026-10-29: licences read from the model pages, prior art (Frigate's audio detection) and the
literature that day; the numbers marked *measured* come from a first look at CED-Tiny with drawn
sounds (§5), not from real recordings.

## 1. The idea in one paragraph

A new sensor kind, **sound sensors**, listens to the audio of a camera stream (most IP cameras have
a microphone) or of a small microphone of its own (an ESP32 with an I2S microphone, §10), and
recognises sound events. Like the kinds that exist for images, it comes in a ready-made and a taught
form: a **pretrained sound classifier** knows several hundred everyday sounds out of the box (the
user picks the ones they care about, as an object sensor picks classes), and **own sounds** are
taught from a handful of clips ("Our doorbell", "Washing machine done"). Each sound becomes a binary
sensor that is on while the sound is heard and goes off a while after.

## 2. What it recognises, and what we promise

Examples in the docs, the README and the UI are ones we are confident will work; phase 0 (§7)
checks each on real recordings before it is shown.

**Taught sounds: the same sound from the same place every time** (the strongest case — that the
model also learns the room and the distance helps here): the doorbell; a washing machine, dryer or
dishwasher beeping when done; a microwave or oven timer; the garage door running; a fridge or
freezer door alarm; a water leak, CO or low-battery beeper; a particular door or the letterbox flap.

**Pretrained classes** (from the AudioSet ontology the models are trained on): speech ("someone is
talking"), dog barking, baby crying, sirens (ambulance, police, fire engine), smoke and fire alarms,
glass breaking, knocking, shouting and screaming.

**Not promised:**
- telling individuals apart (which dog, which person): sound models learn categories, not
  individuals — even two doorbells lie close together (§5) — and a voice is the privacy line (§6);
- sounds drowned by a TV or music, or faint ones far from the microphone (a siren behind a closed
  window);
- every smoke alarm: most beep near 3.1 kHz in the "temporal-3" pattern (three beeps, a pause), but
  low-frequency alarms (a 520 Hz square wave, made to wake heavy sleepers and the hard of hearing)
  and CO alarms sound different. A drawn 520 Hz alarm was not recognised as a smoke alarm (§5); the
  docs tell the user to teach their own alarm as an own sound;
- a safety device: a smoke alarm sensor is an extra notification, never a replacement for a
  certified detector.

## 3. Why a new sensor kind

Faces became part of object sensors because they are a second step on boxes already found
([`FACE_RECOGNITION_PLAN.md`](FACE_RECOGNITION_PLAN.md) §2). Sound has nothing to build on:

- **No frames.** The three kinds today look at one image per check. Sound is a stream: a smoke alarm
  between two checks is missed, so a sound sensor listens all the time.
- **One source, many sounds.** One microphone feeds every sound the user picked, like one object
  sensor feeds every class: one sensor per microphone, not per sound.
- **Its own UI:** nothing to draw a region on; clips to play instead of frames to look at.

So `settings.SENSOR_KINDS` gets a fourth kind, `sound` (a lower-case key that never changes once
released). It shares the sensor list, history, review queue, teaching, the Quality tab, MQTT
discovery, export/import and backup with the others.

## 4. How listening would flow

1. **Open the stream and keep it open:** the audio track of an RTSP stream (PyAV, already bundled)
   or an HTTP audio stream, on a thread of its own as RTSP grabs are. Only audio packets are
   decoded; video packets are dropped undecoded. Unlike image sensors, which connect only for a
   check (`sources.RtspStream`, SCOPE §6), it stays connected — the price of not missing a short
   sound.
2. **Resample** to 16 kHz mono (PyAV's resampler) into a ring buffer of the last few seconds.
3. **Is it loud enough?** (the sound version of change detection) Quiet windows are not classified
   at all — a quiet house is most of the day, so this is where the CPU is saved. Frigate does the
   same (`min_volume`, an RMS level the user reads off a debug graph per camera).
   - *Relative, not absolute decibels.* Microphones are not calibrated: the level is measured
     against full scale (dBFS), not as sound pressure, so the same room gives other numbers on a
     camera and an ESP32, and a camera's automatic gain moves them. A fixed "50 dB" would mislead.
   - *Above the background by default:* the sensor keeps a slow running estimate of its quiet level
     and classifies windows a set number of dB above it — the same setting works in a quiet bedroom
     and a garage with a fan. The user can switch to a fixed level, and sees the level live in the
     Live tab next to the line it must cross.
   - *Nothing lost at the start:* the window that crosses the line is classified together with the
     seconds before it from the ring buffer.
   - *Faint sounds that matter* (a beep two rooms away) can stay below the line; the Live tab is
     where the user checks it.
4. **Classify** the newest window (about two seconds, every half second; CED takes any length).
   When the CPU falls behind, windows are dropped, never queued: sound is live. For own sounds the
   window's embedding goes to the sensor's own small head (§5).
5. **Decide with two thresholds per sound** (hysteresis): *on* when the score passed the upper
   threshold in N of the last M windows, *on* stays while it is above the lower one, *off* when it
   has not been above that for a while. The literature on sound event detection finds double
   thresholds more robust than smoothing with a median filter. **Thresholds are per sound and per
   model**, from the registry, adjustable per sensor: scores differ a lot between classes (§5), so
   one value for all (Frigate's default is 0.8) is wrong for most.
6. **Publish** only changes: on/off per sound, no scores or timestamps at every window (SCOPE §8).
7. **History and review:** a sound turning on writes a history row; when clips are on (§6) the
   seconds around it are kept with it, and an uncertain one goes to the review queue ("Was this the
   doorbell?").

**A loop that listens all day survives anything** (a camera rebooting, a stream without an audio
track, a codec change): log once, reconnect with a back-off, never end except on `CancelledError`.

**Quiet is not unavailable.** A source that sends nothing for a while is either quiet on purpose (an
ESP32 that only sends when it is loud, §10) or gone. A heartbeat tells them apart: a source that is
alive but sends no sound is *quiet* (its sounds off, the sensor available); only a source silent past
its heartbeat, or a lost connection, makes the sensor *unavailable* — reported once, not at every
reconnect.

## 5. Models

Licences as stated on the model pages on 2026-10-29. The registry rule applies: pinned by Hugging
Face revision and SHA-256, checked against real inputs in the tests.

| Model | Licence | Size | Notes |
|---|---|---|---|
| **CED-Tiny** (`mispeech/ced-tiny`) | Apache-2.0 | 5.5 M parameters; ONNX 6.7 MB (8-bit weights) | A small transformer distilled from larger AudioSet models; mAP 48.1 on AudioSet per its card. Mini (9.6 M) and Small (22 M) are a little more accurate. |
| **YAMNet** (Google; ONNX by Qualcomm AI Hub, `qualcomm/YamNet`) | Google's original Apache-2.0; Qualcomm's page says MIT | 3.7 M parameters, 14.2 MB | The classic small one (MobileNet v1, windows of 0.96 s), clearly less accurate. Qualcomm's ONNX downloads from Amazon S3, not Hugging Face: it would need a re-host or our own conversion to be pinned. The baseline. |
| PANNs CNN6 / CNN10 / CNN14 | Code MIT; re-hosts tagged Apache-2.0 by a third party | CNN6 24 MB | No ONNX on Hugging Face; older than CED and not better per parameter. |
| **CLAP** (LAION, `laion/clap-htsat-unfused`; ONNX in `Xenova/clap-htsat-unfused`) | Apache-2.0 | Audio encoder 34–118 MB, plus a text encoder | Matches a sound with a *text* ("a washing machine beeping"). Too big as a default; maybe later (phase 4). |

**CED-Tiny, measured** (its ONNX at revision `ace276d`, one thread of a 2.8 GHz Xeon server, drawn
sounds; a Raspberry Pi 4 — the minimum VisionState runs on, and a sound sensor must run there too
— is several times slower and is measured in phase 0):

- It takes the **raw 16 kHz waveform**: the spectrogram is computed inside the model, so the app
  needs no feature code. Its output is named `logits` but **already holds probabilities** (the
  graph ends in a sigmoid) — applying a sigmoid again squeezes every score to about 0.5.
- **About 8 ms per second of audio** (16 ms for a 2-s window, 73 ms for 10 s): small next to the
  image models, even listening all day.
- A drawn 3.1 kHz temporal-3 alarm comes out as *Smoke detector, smoke alarm* first, but at **0.22**;
  appliance beeps as *Beep, bleep* at 0.53; a drawn two-tone doorbell as *Music*. Scores of the
  right class can be low: thresholds must be set per class from real recordings.
- **Own sounds need a hidden layer, not the class scores.** Used as an embedding, the 527 class
  scores separate categories (a doorbell from a beep) but put two different doorbells at a cosine
  of 0.98, and five clips per sound recognised 43 of 50 test clips of five drawn sounds. The
  model's pooled hidden state (192 numbers, the input of its last layer; the graph only needs it
  added as an output) standardised over the sensor's taught clips recognised 48 of 50, with a
  median margin of 0.30 over the next sound. So the registry entry is our own copy of the ONNX with
  that second output, published pinned like the others, and own sounds get a small head per sensor
  (as state sensors do) rather than a raw nearest-clip match.

**The training data question** (as for faces): every model above is trained on AudioSet — Google's
labels are CC BY 4.0, the audio is YouTube videos not licensed for reuse. The weights are not the
clips, and the models are published under permissive licences, but the question is the
maintainer's to settle (§11, question 1).

**Leaning:** CED-Tiny for both the classes and own sounds, YAMNet as the baseline in phase 0.

## 6. Privacy and the law

Not legal advice; the points to design for, and to say plainly in DOCS.md. A microphone that
listens all day is more sensitive than a camera that takes a picture now and then.

- **Never what is said.** A sound sensor may know *that* someone speaks (the AudioSet class
  *Speech*), never *what* is said: no transcription, no keywords, no speaker recognition — a
  non-goal in SCOPE §2 (the maintainer's decision, 2026-10-29). The promise stays simple: "it hears
  what kind of sound it is, never what is said". Voice commands belong to Home Assistant's own voice
  assistant.
- **Norway:** the Penal Code punishes whoever without authorisation *secretly* listens to or records
  communication between others with a technical device (*straffeloven* § 205 a). Classifying a
  window and forgetting it records nothing, but a kept clip of people talking could be such a
  recording, and a microphone the household does not know about is secret. Hence: the household
  should know it listens (the docs say so), and the clip rules below.
- **GDPR:** the household exemption covers a home microphone only while it does not pick up public
  space or the neighbours (*Ryneš*, C-212/13, the same as for cameras).
- **EU AI Act:** recognising sound events identifies nobody and is not a high-risk use; it must not
  grow into reading people's emotions from their voices (Art. 5 prohibits that at work and in
  education) — "baby crying" is a sound event, not an emotion reading.

Design rules that follow:

1. **Created on purpose:** the wizard says what is analysed and what is kept.
2. **Windows are analysed and forgotten.** Only the decision and, when clips are on, the seconds
   around a sound that turned on are kept.
3. **Clips are in from the first release, off by default per sensor.** When on, every part of a clip in which speech was detected is
   **silenced before it is stored** — for every sound (a doorbell clip can hold a conversation). A
   *Speech* event never keeps a clip. Speech is also the class that fires most (Frigate's docs warn
   about it), another reason to keep its events lean.
4. **Clips are kept where only the app can read them** (`/data`, not `/media`, which Home
   Assistant's media browser shows to every user of the house), under the normal history limits,
   and are part of the backup (§8).
5. **Only a human adds to taught sounds**, never the model's own answers.

## 7. Plan

| Phase | What | Done when |
|---|---|---|
| **0. Spike** (local, `/VisionStateLocal/`) | Audio from common cameras: codecs (AAC, G.711, Opus), sample rates, whether PyAV gets a usable track, how many connections a camera allows. CED-Tiny and YAMNet on a Raspberry Pi 4 and an x86 machine: time per second of audio, and so how many sound sensors a Pi 4 carries beside its image sensors (the limit DOCS.md states). Scores per class on real CC0 recordings (§9), to set the registry's thresholds; false alarms over a day of a household's background and TV. Own sounds on real recordings: clips needed, the hidden-layer embedding against the class scores (§5). ESP32: which firmware to recommend, what ESPHome offers (§10). Read the weights' and data's terms. | A model and thresholds chosen with numbers, the number of sound sensors a Pi 4 carries, a verdict on licences. Or "not good enough", a valid outcome. |
| **1. Listening** | The `sound` kind; RTSP and HTTP audio streams (an ESP32 among them, §10); pretrained classes from a curated list (§2) → a binary sensor per sound; level line and live scores in the Live tab; clips around events (opt-in, speech silenced) with playback in the history. | A real CC0 siren and smoke alarm turn their sensors on in a backend test; wizard, Live tab and a clip played after a tap pass e2e on desktop, Android and iPhone. |
| **2. Review** | Review questions for uncertain sounds ("Was this the doorbell?"), history filters per sound. | e2e on every device. |
| **3. Own sounds** | Teach a sound from a clip in the history or an uploaded file; the small head (§5), its ask band and Quality tab (clips per sound, look-alikes); export/import. | Own sounds taught from five clips each are told apart in a backend test and on the spike data. |
| **4. Later** | A Home Assistant *event* entity per sound; uploads of audio and video for teaching; stock ESPHome and voice-satellite microphones (§10); CLAP sounds described by a text; a sound level entity (rounded, only on a real change). | — |

## 8. Where it would go in the code

Following the existing structure (names are proposals):

- A registry `sounds.json` next to `readers.json`, with per-class thresholds and the embedding
  output's name; loaded in `engine/models.py` on first use like the detector and reader.
- An audio stream class next to `sources.RtspStream` (open, audio only, resample, ring buffer,
  heartbeat, reconnect); `sources.check_source` checks its address the same way.
- A new engine part (a mixin, as `ObjectChecksMixin` is) with the listening loop per sensor; the
  level line and the two-threshold decision are pure logic in `logic.py`.
- Settings: a sound block in `settings.py` (window and hop, the level line, N of M, how long a sound
  stays on, clip length, the curated classes with their Home Assistant names), exposed through
  `GET /api/v1/config`.
- Data: a history row per sound event; clips as AAC in an `.m4a` file (Safari plays no Ogg and plays
  WebM Opus unreliably in an audio element; PyAV encodes AAC) in a folder under `/data` that joins
  the `history` part of `settings.BACKUP_PARTS`; taught clips as `sample` rows whose file is a clip,
  in the `samples` part. A new database version gets its kept backup in `tests/assets/backups/`.
- Entities: a `binary_sensor` per sound with device class *sound*, and *smoke* for smoke and fire
  alarms (the maintainer's decision, 2026-10-29: Home Assistant shows them as what they are; DOCS.md
  still says it is no replacement for a certified detector); new unique IDs that never change.
- UI: the wizard gets *Sound* as a kind and a stream as the source (a Home Assistant camera snapshot
  has no sound); the Live tab shows the level against its line and each sound's score against its
  thresholds; clips play only after a tap (iOS allows nothing else); no hover-only controls.
- Tests: the fake camera gets an audio stream endpoint in the format an ESP32 sends (§10), so e2e
  tests hear something through the same path a real ESP takes.

## 9. Test sounds

As for images: CC0 / public domain, or drawn by our own code.

- **Real recordings** for every pretrained class we promise: CC0 clips from Freesound (its CC0
  filter), sources in `tests/assets/README.md`. Not ESC-50 (CC BY-NC), and only the CC0 clips of
  FSD50K. Drawn sounds do not stand in for these: the pretrained model hears a drawn doorbell as
  *Music* and scores a drawn smoke alarm low (§5).
- **Drawn** (a module like `tests/displays.py`): temporal-3 alarms at 3.1 kHz and 520 Hz, two-tone
  doorbells, appliance beep patterns, background noise at chosen levels — for the pipeline (level
  line, thresholds, reconnects) and for own sounds, where telling drawn sounds apart is a fair test.
- Household recordings from the spike stay in `/VisionStateLocal/`, never in the repository, a test
  or a model.

## 10. Sources

- **Camera streams.** Many cameras ship with the microphone or the audio track off; the wizard
  checks the stream has audio, shows its codec and sample rate, and says how to turn it on. Some
  cameras allow only a few connections at once, and a sound sensor holds one all day: a go2rtc
  restream (the go2rtc app's RTSP server) shares one camera connection — Frigate recommends the
  same for its audio. Home Assistant's camera entities give the app snapshots, not sound; whether
  the stream behind one can be reached is a phase 0 question.
- **ESP32 microphones.** A cheap ESP32 with an I2S microphone (such as an INMP441) in each room is a
  strong case: camera audio is often poor (§12), and a room without a camera can still listen. 16 kHz
  16-bit mono is about 256 kbit/s, easy over Wi-Fi.
  - *A stream of its own* (firmware that sends the microphone as an HTTP stream of WAV or raw PCM, or
    over RTSP) needs nothing beyond phase 1. It is not stock ESPHome; community components exist
    (e.g. an HTTP transport in `gnumpi/esphome_audio`) and phase 0 picks one to recommend.
  - **Recommended: the ESP32 streams all the time** and VisionState decides what is loud enough
    (§4, step 3): the firmware stays simple, the level is set in the UI, not in firmware.
  - *Optional later: the ESP32 sends only when it is loud* (keeping about a second from before the
    crossing: 32 KB). VisionState still opens and holds the connection; the ESP32 sends a heartbeat
    while quiet, so the sensor shows *quiet*, not *unavailable* (§4). Worth it mainly on batteries.
  - *Stock ESPHome* sends its microphone over its native API to Home Assistant's voice assistant,
    on a wake word, and Home Assistant does not pass that audio on to apps. Listening all day needs
    an integration of its own — phase 4, once phase 0 has checked what ESPHome offers.
  - **No push** (the maintainer's decision, 2026-10-29): VisionState always opens the connection; a
    device never connects to VisionState to deliver audio, which would break the rule that the app
    is only reachable through Home Assistant Ingress.

## 11. Open questions for the maintainer

1. **Licence bar:** accept models trained on AudioSet (all candidates are), as a download on demand
   if not bundled?
2. **Sources:** RTSP and HTTP streams in phase 1, an ESP32 sending a stream as a first-class case —
   enough, or must a Home Assistant camera entity or stock ESPHome come first?
3. **The name:** VisionState that also hears — fine as it is, or a line in the README?

Decided (2026-10-29): sound sensors run on everything VisionState runs on, x86-64 and aarch64 with a
Raspberry Pi 4 as the minimum — how many a machine carries is a limit phase 0 measures, not a
reason to leave the Pi out; clips are in the first release (off by default, speech silenced, §6);
smoke and fire alarms get device class *smoke* (§8).

## 12. Pitfalls to avoid

- **Every window is another chance of a false alarm.** Two windows a second are about 170 000 a
  day; a rate that looks small per window is several false alarms a day. Hence two thresholds and N
  of M, and false alarms measured per day of real background, not per clip.
- **The TV.** Sirens, dogs and doorbells on TV are real sounds to a model. Own sounds help (they
  learn this microphone and this room); the docs say so.
- **Telephone-band audio.** Many cameras send G.711 at 8 kHz: nothing above 4 kHz survives. A
  3.1 kHz alarm does (drawn through a 3.8 kHz cut it still scored as a smoke alarm); breaking glass
  loses much of what identifies it. The wizard shows the sample rate; the docs name the trade-off.
- **Camera audio processing:** automatic gain and noise suppression change the level and can cut
  steady beeps — the level line follows the learned background for that reason.
- **Uncalibrated "decibels"** are relative (dBFS): never promise sound pressure levels, never copy a
  level from one microphone to another.
- **A model output that is not what its name says** (CED's `logits` are probabilities): every model
  gets a test with a real recording of a known class, as the registry rule asks.
- **Learning from its own answers** drifts: only human answers are labels.
- **Playback on phones:** iOS plays sound only after a tap, and not every format (§8); test on WebKit.
