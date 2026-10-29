# Sound sensors: research and plan

Hearing **what kind of sound** a microphone picks up (a siren, a smoke alarm, a dog barking,
someone speaking, our own doorbell) and turning it into Home Assistant sensors, all locally. Nothing
is built yet; this is the plan and the decisions made so far. How the app works now is in
[`SCOPE.md`](SCOPE.md), the code map in [`ARCHITECTURE.md`](ARCHITECTURE.md). Research done
2026-10-29; licence statements were read from the model pages that day (§5).

## 1. The idea in one paragraph

A new sensor kind, **sound sensors**, listens to the audio of a camera stream (most IP cameras have
a microphone) and recognises sound events. Like the two kinds that exist for images, it comes in a
ready-made and a taught form: a **pretrained sound classifier** knows several hundred everyday
sounds out of the box (the user picks the ones they care about, as an object sensor picks classes),
and **own sounds** are taught from a handful of clips ("Our doorbell", "Washing machine done"),
matched by the classifier's embedding as taught boxes are matched by DINOv2. Each sound becomes a
binary sensor that is on while the sound is heard and goes off a while after.

## 2. What it recognises, and what we promise

Examples in the docs, the README and the UI are ones we are confident will work; the spike (§7,
phase 0) checks each of them before they are shown.

**Taught sounds: the same sound from the same place every time** (the strongest case — it does
not matter that the model also learns the room and the distance, that helps here):
the doorbell; a washing machine, tumble dryer or dishwasher beeping when done; a microwave or oven
timer; the garage door running; a fridge or freezer door alarm; a water leak, CO or low-battery
beeper; a particular door or the letterbox flap.

**Pretrained classes** (from the AudioSet ontology the models are trained on): speech ("someone is
talking"), dog barking, baby crying, sirens (ambulance, police, fire engine), smoke and fire alarms,
glass breaking, knocking, shouting and screaming.

**Not promised:**
- telling individuals apart (which dog, which person): general sound models learn categories, not
  individuals, and a voice is the privacy line drawn in §6;
- sounds drowned by a TV or music, or faint ones far from the microphone (a siren behind a closed
  window);
- a smoke alarm sensor as a safety device: it is an extra notification, never a replacement for a
  certified smoke detector.

## 3. Why a new sensor kind

Faces became part of object sensors because they are a second step on boxes already found
([`FACE_RECOGNITION_PLAN.md`](FACE_RECOGNITION_PLAN.md) §2). Sound has nothing to build on:

- **No frames.** The three kinds today look at one image per check. Sound is a stream: a smoke alarm
  between two checks is missed, so a sound sensor listens all the time.
- **One source, many sounds.** One microphone feeds every sound the user picked, like one object
  sensor feeds every class; one sensor per microphone, not per sound.
- **Its own UI:** nothing to draw a region on; clips to play instead of frames to look at.

So `settings.SENSOR_KINDS` gets a fourth kind, `sound` (a lower-case key; it never changes once
released). What it shares with the others: the sensor list, history, review queue, teaching, the
Quality tab, MQTT discovery, export/import and backup.

## 4. How listening would flow

1. **Open the stream and keep it open.** The audio track of an RTSP stream (PyAV, already bundled)
   or an HTTP audio stream. Only audio packets are decoded; video packets are thrown away undecoded,
   so a camera stream costs little more than its network traffic. This differs from image sensors,
   which connect only for a check (`sources.RtspStream`, SCOPE §6), and is the price of not missing
   a short sound. Its own thread, as RTSP grabs have.
2. **Resample** to the model's rate (16 kHz mono for the candidates in §5) with PyAV's resampler,
   into a ring buffer of the last few seconds.
3. **Windows:** every hop (about half a second) the newest window (about one second for YAMNet,
   one or more for CED) goes to the model; at most one window waits, the rest are dropped when the
   CPU falls behind — sound is live, a backlog is useless.
4. **Quiet is cheap:** a window below a loudness floor is not classified at all (a quiet house is
   most of the day). The floor is a setting, learned per sensor later if needed.
5. **Classify:** scores for every class the user picked; for own sounds, the window's embedding is
   compared with the taught clips (§8).
6. **Decide:** a sound is *on* after it scored above its threshold in enough of the last windows
   (N of M, the sound version of debounce), *off* when it has not been heard for a while
   (`settings.OBJECT_DEFAULTS` has the same idea as `clear_after_s`). Thresholds belong to the model
   (in its registry entry), as for faces.
7. **Publish** only changes: on/off per sound. No scores or timestamps at every window (SCOPE §8:
   Home Assistant stores every change as a row).
8. **History and review:** a sound turning on writes a history row; when clips are on (§6) the few
   seconds around it are kept with it, and an uncertain one goes to the review queue ("Was this the
   doorbell?").

A loop that listens all day must survive anything (a camera rebooting, a stream with no audio
track, a codec change): log once, reconnect with a back-off, never end except on `CancelledError`.

## 5. Models found (licences as stated on 2026-10-29)

The registry rule applies: pinned by Hugging Face revision and SHA-256, checked against real
inputs in the tests.

| Model | Licence (model page) | Size | Notes |
|---|---|---|---|
| **CED-Tiny** (`mispeech/ced-tiny`) | Apache-2.0 | 5.5 M parameters; ONNX 6.7 MB in the repository | A small transformer distilled from larger AudioSet models; its card reports mAP 48.1 on AudioSet (AS-2M) and says it runs on a CPU as fast as a comparable MobileNetV3. Takes inputs of any length. Mini (9.6 M) and Small (22 M) exist with a little more accuracy. |
| **YAMNet** (Google; ONNX by Qualcomm AI Hub, `qualcomm/YamNet`) | Google's original Apache-2.0; Qualcomm's page says MIT | 3.7 M parameters, 14.2 MB float | The classic small one (MobileNet v1, windows of 0.96 s). Clearly less accurate than CED. Qualcomm's ONNX is downloaded from Amazon S3, not Hugging Face: it would have to be re-hosted (or converted by us) to be pinned as the rule asks. A good baseline. |
| PANNs CNN6 / CNN10 / CNN14 | Code MIT; re-hosts tagged Apache-2.0 by a third party (`nicofarr/panns_*`) | CNN6 safetensors 24 MB | No ONNX on Hugging Face; would need a conversion checked against real sounds. Older than CED and not better per parameter. |
| **CLAP** (LAION, `laion/clap-htsat-unfused`; ONNX in `Xenova/clap-htsat-unfused`) | Apache-2.0 | Audio encoder 34 MB (8-bit) to 118 MB (fp32), plus a text encoder | Compares a sound with a *text* ("a washing machine beeping"): sounds without examples. Too big as a default; an optional download later (§7, phase 4). |

**The training data question** (the same kind as for faces): every model above is trained on
AudioSet — Google's labels are CC BY 4.0, the audio is YouTube videos that are not licensed for
reuse. A model's weights are not the clips, and the models are published under permissive licences
for that reason, but the question is the maintainer's to settle (§11, question 1), as it was for
faces.

**Leaning:** **CED-Tiny** as the classifier and the embedding for own sounds, YAMNet as the light
baseline in the spike. The ONNX in `mispeech/ced-tiny` gives class scores; whether those (527
numbers) are a good enough embedding for own sounds, or a hidden layer must be exported too (a
conversion of our own, checked against real sounds and published pinned like the others), is
measured in phase 0.

## 6. Privacy and the law

Not legal advice; the points to design for, and to say plainly in DOCS.md. A microphone that
listens all day is more sensitive than a camera that takes a picture now and then.

- **Never what is said.** A sound sensor may know *that* someone speaks (the AudioSet class
  *Speech*), never *what* is said: no transcription, no keywords, no speaker recognition. This is a
  non-goal in SCOPE §2 (the maintainer's decision, 2026-10-29). It keeps the promise simple: "it
  hears what kind of sound it is, never what is said". Voice commands belong to Home Assistant's own
  voice assistant.
- **Norway:** the Penal Code makes secretly recording a conversation between others an offence
  (*straffeloven* § 205), and Datatilsynet's guidance for private cameras treats sound recording as
  far more intrusive than images. Classifying a window and forgetting it is not a recording; a kept
  clip of people talking may be. Hence the clip rules below.
- **GDPR:** the household exemption covers a home microphone only while it does not pick up public
  space or the neighbours (*Ryneš*, C-212/13, the same as for cameras). The docs say where to point
  a microphone and that the household should know it listens.
- **EU AI Act:** recognising sound events identifies nobody (no biometrics) and is not among the
  high-risk uses; it must not grow into emotion recognition from voices (an Art. 5 prohibition at
  work and in education) — "baby crying" is a sound event, not a reading of a person's emotions.

Design rules that follow:

1. **Off unless asked:** a sound sensor is created on purpose; the wizard says what is analysed and
   what is kept.
2. **Windows are analysed and forgotten.** Only the decision (on/off) and, when clips are on, the
   seconds around a sound that turned on are kept.
3. **Clips are off by default.** When switched on, any part of a clip in which speech was detected
   is **silenced before it is stored** — for every sound, not only *Speech* (a doorbell clip can
   hold a conversation). A *Speech* event itself never keeps a clip.
4. **Clips are kept where only the app can read them** (`/data`, not `/media`, which Home
   Assistant's media browser shows to every user of the house), under the normal history limits,
   and are part of the backup (§8).
5. **Only a human adds to taught sounds**, never the model's own answers (the rule from the wheel
   reader and faces).
6. **Not a safety function:** the docs say a smoke alarm sensor is a notification on top of a
   certified detector, never instead of one.

## 7. Plan

| Phase | What | Done when |
|---|---|---|
| **0. Spike** (local, `/VisionStateLocal/`) | Audio from real cameras of the household: which codecs they send (AAC, G.711, Opus), at which sample rate, whether PyAV gets a usable track. Time per second of audio for CED-Tiny and YAMNet on a desktop CPU and a Raspberry Pi 4. Hit rates on the CC0 and drawn sounds (§9); false alarms over a day of a household's background and TV. Own sounds: how many clips a doorbell or an appliance beep needs, class scores vs a hidden layer as the embedding. Read the weights' and data's terms. | A model chosen with numbers, thresholds proposed, and a written verdict on CPU (does it fit beside the image sensors on a Pi 4?) and licences. Or "not good enough", which is a valid outcome. |
| **1. Listening** | The `sound` kind; RTSP audio and HTTP audio streams as sources; pretrained classes picked from a curated list (§2) → a binary sensor per sound; the Live tab shows what it hears now; no clips. | A drawn smoke alarm and a CC0 siren turn their sensors on in a backend test, and the wizard and Live tab pass e2e on desktop, Android and iPhone. |
| **2. Clips and review** | Clips around events (opt-in, speech silenced), history with playback, review questions for uncertain sounds, history filters. | e2e: a clip plays after a tap on every device. |
| **3. Own sounds** | Teach a sound from a clip in the history or an uploaded file; matching with threshold, margin and ask band as taught boxes; a Quality tab (clips per sound, look-alikes); export/import. | An own sound taught from five drawn clips is recognised in a backend test and on the spike data. |
| **4. Later** | A Home Assistant *event* entity per sound for automations; uploads of audio and video files for teaching; ESPHome and voice-satellite microphones; CLAP sounds described by a text; a sound level entity (rounded, only on a real change). | — |

## 8. Where it would go in the code

Following the existing structure (names are proposals):

- A registry `sounds.json` next to `readers.json`, pinned the same way, with the model's thresholds
  and window length; loaded in `engine/models.py` on first use like the detector and reader.
- An audio stream class next to `sources.RtspStream` (open, audio only, resample, ring buffer,
  reconnect); `sources.check_source` checks its address the same way.
- A new engine part (a mixin, as `ObjectChecksMixin` is) with the listening loop per sensor and the
  N-of-M decision; pure decisions go into `logic.py`.
- Settings: a sound block in `settings.py` (hop, loudness floor, N of M, how long a sound stays on,
  clip length, the curated class list with their Home Assistant names) exposed through
  `GET /api/v1/config`.
- Data: a history row per sound event; clips as small Opus or FLAC files in a folder under `/data`
  that joins the `history` part of `settings.BACKUP_PARTS`; taught clips as `sample` rows whose file
  is a clip, in the `samples` part. A new database version gets its kept backup in
  `tests/assets/backups/`.
- Entities: a `binary_sensor` per sound with device class *sound* (perhaps *smoke* for smoke and fire
  alarms, §11), new unique IDs that never change.
- UI: the wizard gets *Sound* as a kind and a stream as the source (a Home Assistant camera
  snapshot has no sound); the Live tab shows the sounds picked with how close each is to its
  threshold; clips play only after a tap (iOS allows nothing else); no hover-only controls.
- Tests: the fake camera gets an audio stream endpoint (drawn sounds on request), so the e2e tests
  can make a sound sensor hear something.

## 9. Test sounds

As for images: CC0 / public domain, or drawn by our own code.

- **Drawn** (a module like `tests/displays.py`): a smoke alarm's three-beep pattern near 3 kHz, a
  two-tone doorbell, appliance beeps, background noise, all at chosen loudness and mixed with noise.
- **Recorded:** CC0 clips from Freesound (its CC0 filter) for sirens, dogs, glass, a crying baby;
  sources listed in `tests/assets/README.md`. Not ESC-50 (CC BY-NC) and only the CC0 clips of
  FSD50K.
- Household recordings from the spike stay in `/VisionStateLocal/`, never in the repository, a test
  or a model.

## 10. Home Assistant side

- What is sent changes only when the result does: on/off per sound, no scores in attributes at every
  window.
- The source must be a stream with audio. Many cameras ship with the microphone or the audio track
  turned off; the wizard checks the stream has audio and says how to turn it on when not.
- Home Assistant's own camera entities give the app snapshots, not sound; whether the stream behind
  a camera entity can be reached for audio is a question for the spike (§11, question 2).

## 11. Open questions for the maintainer

1. **Licence bar:** accept models trained on AudioSet (all candidates are), as a download on demand
   if not bundled?
2. **Sources:** RTSP and HTTP streams in phase 1 — is that enough, or must a Home Assistant camera
   entity (its stream through Home Assistant) or an ESPHome microphone come first?
3. **Raspberry Pi 4:** must a sound sensor run there beside the image sensors, or is "a stronger
   machine recommended" fine? Phase 0 gives the numbers.
4. **Clips:** off by default with speech silenced (§6) — or no clips at all in the first release?
5. **Smoke and fire alarms:** device class *smoke* (Home Assistant treats it as a safety sensor) or
   only *sound*, to keep the "not a safety device" line clear?
6. **The name:** VisionState that also hears — fine as it is, or does the README need a line on it?

## 12. Pitfalls to avoid

- **Every window is another chance of a false alarm.** Two windows a second are 170 000 a day; a
  false-positive rate that looks small per window is several false alarms a day. Hence N of M,
  thresholds measured per day of real background, not per clip.
- **The TV.** Sirens, dogs and doorbells on TV are real sounds to a model. Own sounds help (they
  learn this microphone); the docs say so.
- **Telephone-band audio.** Many cameras send G.711 at 8 kHz: nothing above 4 kHz survives. A smoke
  alarm near 3 kHz does; breaking glass loses much of what identifies it. The spike measures per
  codec, and the wizard shows the stream's sample rate.
- **Camera audio processing.** Automatic gain and noise suppression in the camera change the level
  and can cut steady beeps; the loudness floor must cope with it.
- **A stream with no audio, or one that comes and goes.** A clear message in the Live tab and
  Status, never a loop that stops (CLAUDE.md).
- **Always-open streams cost.** Each sound sensor keeps a connection to its camera all day; several
  sensors on one camera share one stream.
- **Learning from its own answers** drifts (only human answers are labels), and **one threshold for
  every model** is wrong (thresholds live with the registry entry).
- **Playback on phones.** iOS plays sound only after a tap and may need the clip in a format it
  supports; test on WebKit.
