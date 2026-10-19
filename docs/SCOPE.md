# Scope & Design Decisions

> The design as built, the decisions behind it, and the roadmap. Update it in the same change as
> the decision. How to use the app: [`visionstate/DOCS.md`](../visionstate/DOCS.md); where the code
> is: [`ARCHITECTURE.md`](ARCHITECTURE.md); channels and releases: [`RELEASING.md`](RELEASING.md).
> Setting names below are keys in `settings.py`, which holds their values.

## 1. Vision

A Home Assistant app (formerly "add-on") that lets anyone teach a local AI to recognise
**states** in camera images — e.g. a garage door being `open`, `closed` or `partial` —
and exposes the result as a regular Home Assistant sensor. Training happens by clicking
the matching state on a live image, or by bulk-uploading images/video. Everything runs
locally, on any CPU, with a polished UI inside Home Assistant.

A second sensor kind, **object sensors**, finds common objects (people, cars, animals …) with a
pretrained detector, and a third, **reading sensors**, reads a number from a display or the wheels
of a meter. Neither needs training.

## 2. Goals / Non-goals

**Goals**
- Generic: works with any camera Home Assistant knows about, plus direct URLs.
- Runs on Intel/AMD (amd64) and Raspberry Pi 4/5 and other aarch64 boards, CPU only — any
  64-bit x86 CPU (x86-64-v1), so also virtual machines with a generic CPU type.
- Very low effort to train: good results with ~10–30 images per state.
- Simple, polished UI via HA Ingress.
- Public, well-documented, configurable, multi-arch builds.
- Import/export of sensors with their training images.

**Non-goals (for now)**
- Tracking objects across frames (identities, paths, line crossing), zones within one sensor,
  custom-trained detectors.
- Multi-label sensors (the data model allows this later, see §10).
- Reading pointer dials (needle gauges, the small red hands on some water meters) — see §14.
- Cloud training or cloud inference; telemetry of any kind.
- armv7 / i386 (deprecated by Home Assistant).

## 3. Terminology

| Term | Meaning |
|---|---|
| **Sensor** | One thing to recognise, e.g. "Garage door". Becomes one HA device with entities. Its **kind** is `single_state` (learned states), `objects` (detector) or `reading` (a number), fixed at creation. |
| **State** | One possible value of a sensor. Has a display name ("Open") and a key (`open`, derived from the name, used in HA). |
| **Source** | Where images come from: HA camera entity, HTTP snapshot URL, RTSP stream, or upload. |
| **ROI** | Region of interest — a rectangle, or a polygon (up to `ROI_MAX_POINTS` corners), cropped from the frame before classification; outside a polygon is filled with a neutral colour. |
| **Sample** | A stored full frame with a state label; the ROI is applied when embedding, so moving the ROI never loses data. |
| **Backbone** | Pre-trained vision model that turns an image into an embedding vector. |
| **Head** | Small per-sensor classifier trained on embeddings. |
| **Trigger** | Something that makes a sensor check its camera besides the regular interval. |
| **Detector** | Pretrained object detector used by object sensors (registry `detectors.json`). |
| **Class** | One object type an object sensor looks for (a COCO label, e.g. `person`). |
| **Reader** | Text recognizer used by reading sensors (registry `readers.json`). |
| **Wheel reader** | Model that reads how far each wheel of a mechanical counter has turned (registry `readers.json`, `wheel_readers`). |

## 4. Platform & deployment

- Home Assistant app with **Ingress** (sidebar panel, HA handles authentication); requests
  from anything other than the Ingress proxy are refused.
- Architectures: `amd64`, `aarch64`. Prebuilt images on GHCR, pulled by Home Assistant.
- Two release channels, stable and beta ([`RELEASING.md`](RELEASING.md)).
- Also runnable as a plain Docker container / Python process for development, configured via
  environment variables and a long-lived HA token (no login outside HA).
- MQTT broker discovered automatically through the Supervisor services API
  (`services: mqtt:want`); manual override via app options.

## 5. ML approach

**Embedding + lightweight head** (transfer learning without fine-tuning):

1. Grab frame → crop ROI → letterbox to a square → normalise.
2. Backbone (ONNX Runtime) → L2-normalised embedding (CLS token + mean of patch tokens).
3. Per-sensor head: scikit-learn logistic regression (`C=30`, class-balanced) → probability per state.
4. Post-processing → published state (§7).

Why: training takes about a second even on a Raspberry Pi, works with few samples, and every
new label retrains immediately. Embeddings are cached per sample (keyed by backbone and ROI),
so retraining only runs the backbone on new or changed samples.

**Backbones** (registry: `backend/visionstate/backbones.json`):

| ID | Model | Use |
|---|---|---|
| `dinov2-small-q8` | DINOv2 ViT-S/14, 8-bit quantised (bundled) | Default everywhere |
| `dinov2-small` | DINOv2 ViT-S/14, fp32 (downloaded on demand, SHA-256 checked) | Optional, slightly more accurate |
| `dinov2-base` | DINOv2 ViT-B/14, fp32 (downloaded on demand, 347 MB) | Fast PCs; separates objects best |

- Each registry entry carries a rough footprint for **Compare and manage the models** in Settings
  (`backbones.Footprint`: extra memory, time per check on 4 threads of a desktop CPU, an accuracy
  rank within its kind), measured when the model was added. Texts and footprint may be updated in a
  released entry; its file and inputs never change.
- DINOv2 base, evaluated (2026-10-11) with the crops of the 14 objects in the CC0 test photos, each
  also changed in light, framing, blur, JPEG quality and night colours: the gap between the same
  object changed (5th percentile) and other objects of its kind (90th percentile) was 0.12 for base
  fp32, 0.08 for small fp32, 0.06 for small 8-bit — and 0.04 for base 8-bit, whose quantisation
  squeezes everything together (other objects of a kind around 0.82), so base is offered in fp32
  only; fp16 saved disk but used more memory on CPU. Base needs slightly stricter teaching limits
  (`settings.TEACH_BY_BACKBONE`).

- MobileNet was dropped: the available ONNX exports only expose classification logits.
- Switching backbone retrains every sensor from its stored samples.
- Runs on the CPU only (`backbones.cpu_session`; ONNX Runtime's other providers, such as its
  Azure provider, are never offered or used). GPU/Coral
  were assessed and deliberately not pursued (little gain for this workload; Coral cannot run
  the transformer model and is often in use by other software).
- Heads trained by another scikit-learn version, or for another backbone, are retrained
  automatically at startup.
- **Quality check:** after each training, cross-validated predictions give the accuracy, the
  confusion matrix and a list of *possibly mislabelled* samples (label ≠ out-of-fold prediction).
- Not implemented: training-time augmentation (not needed so far), fine-tuning.

**Object sensors** use a pretrained detector instead (registry `backend/visionstate/detectors.json`,
with the 80 COCO labels, their keys, groups and the popular ones):

| ID | Model | Use |
|---|---|---|
| `dfine-s-coco` | D-FINE S, COCO, fp32 ONNX (bundled, 42 MB) | Default |
| `dfine-n-coco` | D-FINE N, COCO, fp32 ONNX (downloaded on demand, 15 MB) | Faster, misses more |
| `dfine-m-coco` | D-FINE M, COCO, fp32 ONNX (downloaded on demand, 79 MB) | Fast PCs; finds more small and hidden objects |
| `dfine-l-coco` | D-FINE L, COCO, fp32 ONNX (downloaded on demand, 125 MB) | Fast PCs; the most accurate |

D-FINE X (251 MB) was left out: twice the size of L for little more. At the default 60 % threshold
M and L find the same objects in the test photos as S, with fewer weak false boxes, so the
threshold is the same for every detector.

1. Crop the ROI's bounding box plus a margin (`DETECTION["context_margin"]`) so edge objects are
   seen whole; stretch to 640×640 (no letterbox), scale to 0–1.
2. DETR-style outputs (300 queries × 80 class logits, cx/cy/w/h boxes) → sigmoid → per-class
   non-maximum suppression → boxes mapped back to the frame.
3. An object counts when its bottom centre is inside the ROI (polygon aware), its score is at
   least the sensor threshold (default 60 %) and it is at least `min_size` of the region.
4. Per class: on after *N* checks in a row (`debounce`, default 1); off after `clear_after_s`
   (default 30 s) without it.

**Teaching object sensors** (`teach.py`, `api/teach.py`, tunables `settings.TEACH`). The detector
is never retrained (that needs a GPU and many labels, and a few examples make it forget what it
knew). Instead a second step compares boxes with boxes the user taught:

- A taught box is a `sample` row (the crop of the box plus `crop_margin`) with `object_label`
  (`none` = not what the detector said, a class, or an own label), `detected` (the detector's
  class; `None` = a box it missed, drawn by the user), `box` and `score`. Own labels live in
  `sensor.objects["custom"]` as `{key, name, parent}`; each active one (its parent class still
  selected) gets the two entities a class gets, and counts for its parent too.
- Each check: the counted boxes of every class that has taught boxes (at most `max_checked`, most
  certain first) are embedded with the DINOv2 backbone (always loaded) and compared with the taught
  boxes (cosine similarity, best per label). A box takes the closest label only when it is at
  least `match_similarity` (0.88) and beats the next label by `margin`; otherwise the detector's
  answer stands. Measured on CC0 photos: the same object in other light or framing scores
  0.87–0.97, other objects of the same kind mostly below 0.7.
- An object that stays (its box overlaps a box of the last check by `keep_iou`, 0.6) keeps the
  taught answer it had while it is at least `keep_similarity` (0.8) to that label: a parked car
  measured 0.94 between two checks seven minutes apart, and a 2 % shift of the detector's box
  alone costs 0.04 — enough to flip it around 0.88 every few checks. Such boxes carry `kept`.
- Unsure (`ask_similarity` 0.75 up to `match_similarity`, to an own label of the box's class): the
  box carries `ask`, counts neither for nor against that label (it keeps its published state), and
  goes to the review queue ("Is this Our car?", a history row `published_key = "ask"`, reason
  `ask`) — one waiting question per label, at most one per review cooldown, none with review off;
  about a face up to `FACES["ask_burst"]` (waiting, and per cooldown), `FACES["ask_gap_s"]` apart,
  and an answer resets that person's cooldown.
  Yes/no teach the box from that history frame (as the label / as its class); answering again
  replaces that taught box. Below 0.75 it is simply not that label.
- Switching off needs `DETECTION["clear_misses"]` (2) checks in a row without the object as well
  as `clear_after_s`; a missing object is looked for again after `recheck_s` (5 s) instead of at
  the next interval (`LiveState.recheck_at`).
- Results: `filtered` (not counted, still shown dashed; a history row "filtered" when a class
  starts being filtered, at most every `filtered_record_cooldown_s`), another class (`was` keeps
  the detector's), or an own `label`.
- Once a missed box was taught, the detector also returns boxes down to `rescue_floor`; up to
  `max_rescue` of those that overlap no counted box are compared and counted (`rescued`) when at
  least `rescue_similarity` (0.9) to a taught box. Drawing a box tells whether the detector sees
  anything there at all (`seen`).
- Nothing changes for a sensor until something is taught; **Use what you taught** off skips it.
  The analysed frames of the last checks are kept (`frames_kept`) so a box can be taught a while
  after it was shown. Export/import carries taught boxes and own labels.
- **Tunable** (`settings.TEACH_TUNABLE`): `match_similarity`, `keep_similarity`, `ask_similarity`,
  `rescue_similarity` and `rescue_floor` can be set by the user, globally (DB setting `teach`,
  `GET/PUT /api/v1/teach-limits`) and per sensor (`objects["teach"]`); unset values inherit.
  `settings.merge_teach`: the backbone's defaults (`TEACH`, with `TEACH_BY_BACKBONE` on top) <
  global < sensor. Asking and keeping must not start above a match (refused by the API). A user's
  value stays when the backbone changes; the UI says to look at it again.

**Recognising people by their face** (`faces.py`, `engine/faces.py`, `api/faces.py`; registry
`faces.json`, tunables `settings.FACES`, per sensor `objects["faces"]` with `settings.FACE_DEFAULTS`;
the user's limits `settings.FACE_TUNABLE`).
Why it is a part of object sensors and not a kind, the research, the privacy rules and what is
left are in [`FACE_RECOGNITION_PLAN.md`](FACE_RECOGNITION_PLAN.md). In short:

- Off by default; on only for a sensor that looks for `person` (its Settings tab, or the wizard),
  after a confirmation that names what is stored and downloaded. The face models are never
  bundled: the first sensor that turns it on downloads them (`backbones.download` with progress)
  and loads them in the background — the sensor runs without faces meanwhile; a failed download waits for **Try again**. The last sensor
  turning it off lets the model go; its files can then be deleted (like any downloaded model not
  in use: `DELETE /api/v1/models/{kind}/{id}`, *Compare and manage the models*).
- The user's limits (`settings.FACE_TUNABLE`: the smallest face and the four similarities) work
  like the teaching limits: defaults (`FACE_DEFAULTS`, the similarities from the face model) <
  global (Settings, DB setting `faces`) < the sensor's own (`objects["faces"]`); an unset value
  inherits.
- Each check: person boxes (at most `FACES["max_faces"]`, new boxes first, then the ones searched
  longest ago) are cropped from the full frame in their upper part and tiled
  four to one YuNet run (640 × 640). One face counts for one person box. The quality gate (not
  cut by the frame's edge — the detector then guesses the eyes —, the face's width `min_face_px` —
  not the distance between the eyes, which shrinks as a head turns —, detector score, how far it
  is turned, sharpness) makes a bad face *no face*, never *unknown*. A usable face is aligned on
  its five points (Umeyama + Pillow, no OpenCV), its brightness evened out (`faces.even_light`: a
  face in front of a bright sky is nearly black) and embedded; cached embeddings carry
  `faces.EMBEDDING_VERSION`, so a change to that step recomputes them.
- A person's score is the mean similarity to their `top_k` closest taught faces. Thresholds belong
  to the recogniser's registry entry (cosine values of one model mean nothing for another): a
  match needs `match_similarity` and `FACES["margin"]` over the next person, and is published at
  once from `sure_similarity`, else when a second check of the same box agrees; between
  `ask_similarity` and a match the box is `ask` (review: "Is this Kari?"); a box that stays keeps
  its name while no usable face is seen or the face is above `keep_similarity`. A sure name is
  carried for `recheck_every` checks without searching again. A box whose usable face matched
  nobody `unknown_after` checks in a row and never had a name counts for `unknown_person`
  (`settings.FACE_UNKNOWN_KEY`, its own entities). Only faces seen clearly count:
  `FACES["unknown_size"]` times the smallest face, turned at most `FACES["unknown_max_turn"]`,
  detector score at least `FACES["unknown_min_score"]`; a smaller, more turned or less certain face
  can be named but is otherwise *not sure who*, as not looking like anyone may only be the angle.
  Measured on the NASA test photos: the same person 0.64–0.95 (SFace) and 0.59–0.93 (AuraFace),
  different people at most 0.30 and 0.22.
- The people are shared by every sensor (a household, not a camera): `person` rows, each with
  `face` rows (`db.Face`: {box, points, score} in the crop, and the sensor it was taught on, null
  once that sensor is deleted — the face stays). The crop (the face with a margin, not aligned, so
  another recogniser can align it its own way) lives in `<data>/faces/`, not in the media folder;
  embeddings are cached per face model (`face_embedding`), deleted with the face, never exported.
  A sensor that recognises faces gets every person as an own label (`engine.with_people`, not
  stored on the sensor): its entities, and a name its faces can get. One index of all taught
  faces serves every sensor. Only a human adds a face (teach sheet, review answer), on any sensor.
- Each person also has two entities of their own on the app's device: `sensor.visionstate_<key>_seen_by`
  (`mqtt.person_discovery_messages`): the slug of the sensor that sees them now — the one that saw
  them last when several do — or `settings.PERSON_NOT_SEEN`; when and where they were last seen
  are attributes, and when also a timestamp entity, `sensor.visionstate_<key>_last_seen` (a frame
  confirmed afterwards moves it: `engine.objects.seen_earlier`, published at once). Kept in memory, and at start read back from the history
  (`engine.objects.restore_last_seen`: the last "on", an "off" less `clear_after_s`, a box taught
  from a frame), so "last seen" survives a restart while the history keeps it. A box taught from
  a history frame or a review answer is written into that row (`api.teach.remember_taught`: its
  label, `taught`), so the History shows what was taught from it.
- `GET /people` and `/faces/{id}` (`api/faces.py`) list, rename, move and forget for all sensors;
  teaching stays on a sensor (`/sensors/{id}/faces`), or comes from photos (`POST /people/photos`:
  read like a state sensor's upload, `uploads.frames_from_file`; the best face of each picture
  through the same gate, at most `FACES["max_photos"]`; origin `upload`, no sensor). Export carries the faces taught on that
  sensor, with their people's keys and names, only with `?faces=true`; import adds them to the
  person with that key or name (or a new one) and turns face recognition off.

- Weights: D-FINE (Apache-2.0, COCO-trained; not the Objects365 variants, which carry other
  terms), ONNX conversions from Hugging Face pinned by revision and SHA-256. One conversion
  of D-FINE N was found broken during evaluation; tests with real CC0 photos
  (`tests/assets`, `tests/test_models.py`) now guard every model file. 8-bit variants were slower and worse on CPU.
**Reading sensors** use a CTC text recognizer (registry `backend/visionstate/readers.json`):

| ID | Model | Use |
|---|---|---|
| `ppocrv6-tiny` | PP-OCRv6 tiny rec, ONNX (bundled, 4.5 MB) | Default |
| `ppocrv6-small` | PP-OCRv6 small rec, ONNX (downloaded on demand, 21 MB) | Unusual fonts |

PP-OCRv6 medium (77 MB) was evaluated and left out (2026-10-11): on 280 drawn LCD/LED displays
(clean, blurred, low resolution, low contrast, noisy, tilted, heavy JPEG) and 49 drawn counters
it read 297 of 329 right, tiny 304 and small 316; it took 9 for 3 or 8 on seven-segment digits.

1. Crop the ROI's bounding box (no margin, no mask). Display *auto*: autocontrast greyscale;
   when the read is unsure (< `READING["segments_fallback_below"]`) also try the digits' own
   segments (two-stage Otsu on brightness for light-on-dark, on darkness for dark-on-light,
   removing faint unlit segments) and keep the more confident read. *led* / *lcd* force that.
   Display *counter* (mechanical counter, rolling digit wheels): the crop is split into
   `digits` equal cells, the middle `READING["counter_cell_share"]` of each cell is pasted into
   one line (no dividers), turned grey by each pixel's darkest colour channel (`readers.darkest`:
   red decimal wheels as dark as black ones), and several row bands of that line are read; the
   most confident read with exactly `digits` digits wins. That is the *text reader*
   (`counter_reader: "ocr"`); by default a counter is read by the **wheel reader** (below).
2. Resize to height 48, BGR, scale to −1…1; greedy CTC decoding **limited to** `0-9 . , : -`
   (the class indices are stored in the registry, so the dictionary file is not needed).
3. Parse: only digits count, the configured `decimals` place the decimal point; `time_left`
   reads `h:mm` → minutes. Reject when empty, below the threshold (default 70 %), a counter
   going down, a mechanical counter read with another number of digits than it has wheels, a
   counter going up faster than its limit (below), or another value changing more than
   `max_step`; otherwise publish after `debounce` equal reads (new sensors publish the first
   one: the checks above are what catch misreads — a misread from glare or a wheel between two
   digits comes back the same in the next reading, so equal reads let it through anyway — while a
   counter in use changes every reading, and waiting for two equal ones published nothing until
   the water stopped).
   A counter read exactly
   one step of its last digit below the value (`readers.settling`) is the last wheel turning: the
   value stays, but it is no rejection (no history row, no review item, counted as accepted) —
   seen in a user's export, where one too-high value made every right reading after it a "went
   down" rejection. A right value typed in the review (`readers.right_value`) is taken as written
   with a point or comma, and digits only are placed with the configured decimals.
   The last published value is restored from the history after a restart (or from
   `reading_watch`, when that is newer: a counter's, or a value confirmed in Review).

**Counter limit** (`change_limit.py`, `settings.COUNTER_LIMIT`): how much a counter may go up
since a reading last confirmed its published value is a rate times the time it was *not seen*,
plus two steps of the last digit. (Confirmed, not merely accepted: a reading still waiting for
its debounce leaves the published value older than it, and during a long shower that is minutes.) A fixed `max_step` between two readings (what counters had until then) was both
too loose and too tight: issue #40's water meter (0.04 m³ per one-minute reading = 2.4 m³/h, four
times its busiest shower) rejected right readings after a 35-minute update.
- *Not seen* is time the app was down (a pause longer than `gap_checks` intervals), and time the
  readings were unsure. Once a reading was read with confidence over the limit (`held`), normal
  pauses stop counting: otherwise a wheel misread over and over would get through after a while,
  as the allowed rise grows. This is the reason the limit is not simply "rate × time since the
  last accepted value".
- *Learn it* (`auto`, the default for new counters): per local day `reading_stat` keeps the
  largest rise over `window_s` (600 s) and the day's rise, from the accepted readings (each with
  the lower of its value and the next one's, so one read too high adds nothing; published values
  would put a whole shower into one step) — only over stretches where the counter was read all
  the time, so a rise over a pause is never learned. The limit takes the
  second-largest day of the last `learn_days`, so one misread that slipped through does not count,
  times `safety`; for a long pause it is capped by that many busy days. It needs `min_days` days
  (a whole week: wash day and weekend). On issue #40's data, ×2 left room above every right
  reading (the largest was 1.1 × the learned rate). Replayed three times over with readings every
  minute, 14 times away (35 min to two days), 15 stretches of unsure readings and six wheels read
  a digit too high for 20–120 min: the fixed 0.04 per reading kept the value behind the meter for
  528 hours; learned, 32 hours (the debounce during long showers, as with a fixed rate), it
  learned 0.82 m³/h for a meter that ran at 0.75 at most, and after its first week no wrong
  value was published. A wheel read too high right after a long time away does get through —
  nothing tells it from water used meanwhile — and then the "went down" lock below applies.
  Until it learned, a rate applies: `max_rate`, or when that is 0 `standard_steps_per_min` steps
  of the last digit a minute (issue #40's 0.04 m³ a minute, three decimals). It once set no limit
  until then; on issue #40's meter, switched to *Learn it*, a wheel read two digits off went
  through as +2.09 m³ in its first week. The UI shows the rate in use, and fills in the standard
  one when *Fixed rate* or *Fixed per reading* is chosen without a value; 0 there stays "no
  limit", as it was before.
- *Fixed per reading* (`step`): `max_step` for every reading, however long since the last one —
  the limit counters had before the rate, which some users find easier to tune ("how many
  decimals may move between two checks"). Counters from before keep it unchanged (migration 14,
  `db.keep_change_step`); after a restart a real rise waits for Review.
- Readings over the limit are never accepted by themselves, however many agree: only an answer in
  Review (below) moves the counter on. A beta accepted a jump once readings agreed for five
  minutes, unless it looked like one misread wheel; the maintainer took it out again —
  the limit is there so that nothing overrides it without a person, and a camera that moved can
  misread the same wrong value over and over (issue #40). A real jump while the readings were
  unsure, or a counter that goes faster than it ever did, waits for Review.
- Confirmed in Review (*read correctly* for an unsure or implausible reading, or a typed right
  value) on a rejected reading newer than the last accepted one: that value becomes the last one
  (`engine.reading.anchor_reading`). It is also the way out for a counter stuck above its real
  value, the "went down" lock — never automatic, as Home Assistant takes a counter going down
  for a reset.
- The limit's state (`reading_watch`) is stored with every reading, so a restart knows how long
  the app was away. Counters made before the limit (schema v14, and importing an older export:
  `db.keep_change_step`) keep theirs as `fixed`: the old `max_step` per check interval, as a rate.
- **Camera changed** (reading sensors with a region): the region is a share of the picture, so a
  camera back from a restart with another size reads something else (issue #40: an ESP32 camera
  whose picture moved, nobody near it). The first frame after the region or source changed sets
  `db.Sensor.frame_size`; a frame of another size is not read (`engine.reading.CAMERA_CHANGED`):
  the value stays, the frame is kept once in the history (not in Review: the card on the sensor
  asks), and Home Assistant gets a notification (`READING["camera_notification_id"]`). It ends
  when the size is back, or when the user says the region is right (`POST
  /sensors/{id}/accept-picture`) or draws a new one (both clear the size). Held rather than
  paused, so a camera that comes back at its old size needs nothing. A camera that moved without
  changing size is not caught by this: that is C3 in `docs/WHEEL_READER_PLAN.md`.

**Wheel reader** (default for mechanical counters, `counter_reader: "wheels"`, registry
`wheel_readers` in `readers.json`, model `wheels-v1`, bundled, 2.2 MB, ~2 ms for 8 wheels on one
CPU thread): the crop is split into `digits` equal cells (whole cells, dividers included); each
cell, grey by its darkest colour channel, 32×48, full contrast, goes through a small CNN that
gives a distribution over 100 positions around the wheel (0.0–9.9). `readers.wheel_value` then
finds the most likely *consistent* value by dynamic programming from right to left: the last
wheel turns freely, every other wheel stands on its digit plus how far its right neighbour is
past 9. Wheels at rest may sit one bin off (`READING["wheel_slack_bins"]`), and all wheels may
appear shifted together by up to `wheel_max_shift_bins` (a region drawn a little above or below
the digits) at a cost of `wheel_shift_penalty` per bin. The last wheel is rounded to the nearest
digit; the confidence is the mean probability per wheel. It always returns `digits` digits, so
the digit count check does not apply. Counters made before it (no stored `counter_reader`) keep
the text reader: schema v12 stores `"ocr"` for them, and so does importing an older export
(`db.keep_text_reader`). The model, its training data and its
results: [`tools/wheelreader/README.md`](../tools/wheelreader/README.md); how it is to get better:
[`WHEEL_READER_PLAN.md`](WHEEL_READER_PLAN.md).

- Why a wheel reader (2026-10-09): on every meter tested it read more readings right than
  PP-OCRv6 small and gave no value too high, which the text reader did (numbers in its README).
  The remaining misses are ±1 on a half-turned last wheel.
- Evaluated (spike on Commons photos): PP-OCR read LCD, LED, dot-matrix and flip-segment
  displays correctly (7/7 with a tight region); it fails on small blurry LCDs, and on rolling
  counter wheels when the whole counter is read as one line. DINOv2 per digit (4/23) and a CNN
  trained on synthetic digits (12/23) were worse. The public meter-digit datasets/models found
  carry no licence, so they are not used.
- Evaluated for mechanical counters (2026-10, 742 frames of two water meters with a fixed
  camera, 53 distinct counter states, kept outside the repository): read as one line the
  counter was never right (half digits above/below and dividers read as extra digits). With
  one cell per wheel and the row-band search the first six wheels were right in 32/35 and 17/18
  states, and in every state where no wheel was turning (29/29, 17/17). Learning each digit
  from the meter's own labelled frames (DINOv2 + head, or matching against labelled wheels) was
  much worse and was dropped. Only one meter type was tested, and the settings were chosen on
  the same frames.
- Evaluated for red decimal wheels (2026-10, issue #40: 120 checked readings of a user's water
  meter with red-on-white decimals, from three exports): in a plain grey image a red "9" was
  read as "5". The darkest colour channel raised exact reads with PP-OCRv6 small from 61 to 70
  (17 images of the newest export: 13 → 15) and left the black-digit meters above as they were
  (small 97 → 99, tiny 92 → 91 of 106; every wheel at rest still right). Tiny reads that meter
  much worse than small (8/17) and its misreads go up, which passes the "went down" check, so
  the docs recommend small for mechanical counters. Using the last value to pick among the
  digits the reader hesitated between added only 4 more of 120 and was not built.
- **Model choice is global per kind** (Settings), so at most three models are loaded. The
  detector and the reader are loaded on first use and released when the last sensor of their kind is deleted. The
  chosen backbone and detector are stored in the database at first start, so a later release
  that recommends other defaults does not change an installation. Registry entries are never
  changed or removed once released.

## 6. Image sources

| Source | Details |
|---|---|
| HA camera entity (default) | `camera_proxy` via the Supervisor/HA API. Works with any `camera.*` entity. |
| HTTP snapshot URL | HTTP GET of a JPEG/PNG. |
| RTSP stream | First decoded frame via PyAV (bundled FFmpeg). |
| Upload | Images (JPG/PNG/WebP/BMP), ZIP archives, video (MP4/MKV/MOV/AVI/WebM). |

Video upload: one frame every *N* seconds (default 5), near-duplicates skipped by perceptual
hash, then the current model suggests a label for each frame. Upload and ZIP sizes are capped
(`UPLOAD_LIMITS`). A single camera can feed multiple sensors, each with its own ROI.

Before a frame is fetched the address is checked (`sources.check_source`): a snapshot URL must be
`http(s)://`, a stream a network address (FFmpeg would also open local files and its own
pseudo-protocols), a camera an entity ID. A snapshot is read up to `RUNTIME["max_frame_mb"]`, and
every image is refused above `RUNTIME["max_image_megapixels"]` — checked from its header, before
the pixels are decoded.

## 7. When and how a sensor decides

- **Triggers** (per sensor, `sensor.triggers`, defaults in `settings.TRIGGER_DEFAULTS`):
  - Regular interval (default 10 s) — the safety net, counted from the last check whatever
    caused it. `regular: false` switches it off: the sensor then checks only when triggered and
    once after start-up.
  - State changes of chosen HA entities (WebSocket `subscribe_trigger`, attribute-only changes
    and `unavailable`/`unknown` ignored). `only_states` limits an entity to one new state
    (compared without regard to case).
  - Optional region change detection: a small greyscale copy of the ROI is compared every
    *N* seconds; the AI only runs when the difference exceeds a threshold.
  - Every trigger starts a *burst* (default every 2 s for 30 s) to catch the final state.
  - A sensor that recognises faces starts one by itself when a check finds a person nobody is
    named for (`engine.logic.steer_face_burst`, tunables `FACES["burst_*"]`): someone walking up
    shows a usable face for a few seconds only. It runs while they come closer or show a face, ends
    once everyone is named, when they are gone, without progress or after `burst_max_s`, and comes
    again only after they have gone. The light is held through it like any burst.
  - The MQTT `classify` button checks once.
- **Light** (`triggers.light_entity`, `light_delay_s`; picked with the camera in the UI): a light,
  switch or input_boolean, switched over the HA REST API (`homeassistant.turn_on/off`). `lights.py`
  shares each light between its *holders*: a full check holds it from before its frame until the
  end of the burst (so it does not flash for every check), and a view with live frames (wizard
  region/test, the region editor, the Label tab) holds it with a lease (`POST /lights/hold`,
  renewed every `RUNTIME["light_view_renew_s"]`, let go after `light_view_lease_s` without renewal,
  swept every `light_sweep_s`). It is switched on by the first holder and off by the last — only if
  VisionState switched it on; a light that is already on is not touched. Frames are taken once it
  had `light_delay_s` to get bright; until then (and without a holder) the UI shows the last
  analysed frame instead of grabbing dark ones. The view shows the state and a per-viewer switch
  (browser storage). Change-detection probes do not switch it: they compare frames without the
  light, are skipped while it is held (a lit frame would always look like a change), and a change
  is checked on a new frame taken in the light (a lit check never sets the probe baseline). A
  failure is logged and shown in the settings; the check runs anyway.
  While a check waits for the light (`_grab_in_light`), frames are fetched and thrown away every
  `light_warmup_interval_s`, and the frame after the wait is checked. Found with an ESP32 camera
  (issue #32): ESPHome keeps one picture ready, taken right after the previous one was fetched (up
  to 1/`idle_framerate` = 10 s earlier), and the sensor only adjusts its exposure between
  pictures — so the first picture after the light came on was dark. RTSP is live and only waits.
  A light VisionState switched off less than `light_off_settle_s` ago counts as its own even when
  Home Assistant still reports it `on`, so a quick next check switches it on and waits.
- **Unknown state:** top probability below the threshold (default 70 %) → `unknown`.
- **Debounce:** the state changes only after *N* consecutive agreeing results (default 2).
- Camera unavailable → entities become `unavailable` (not a false state).
- **Review queue** (global rules in the DB, per-sensor overrides in `sensor.review`, defaults in
  `settings.REVIEW_DEFAULTS`): frames below 85 % (never below the sensor threshold), frames
  where the state flip-flops (3 changes in 10 min) and optional random spot checks (default 0 %);
  at most one per sensor per 5 minutes. Rejected readings always go there (no cooldown), but only
  once while the same one waits: a reading rejected for the same reason with the same value is
  counted on the waiting item (`repeats`, `last_seen`; `engine.reading._repeat_rejected`) and not stored
  again. A meter standing still on a rejected value all night filled the queue with one frame per
  check, all with the same answer. The waiting item keeps its first frame and time, so a value
  confirmed there counts from when it was first read; after *Skip* the next one is queued again.
  **Dismiss all** (per sensor, on the Review page and a reading sensor's Quality tab) marks every
  waiting item of that sensor as skipped — for clearing out what piled up while setting a sensor
  up; given answers and the reading counts stay.
  **Review reminder** (global, DB setting `reminder`, defaults in `settings.REMINDER_DEFAULTS`,
  `engine/reminders.py`, decision in `logic.reminder_action`): when at least `min_items` (1)
  frames wait and the oldest has waited `after_days` (7), a `persistent_notification` with a fixed
  `notification_id` (`visionstate_review`, so it is replaced, never piled up) links to the app
  (`/app/<slug>` from Home Assistant 2026.2, `/hassio/ingress/<slug>` before; the slug from the
  Supervisor). Optionally also a `notify.<service>` push (off by default; `data.url`/`clickAction`
  open the app). One reminder per round (`reminder_sent_at` in the DB, survives restarts); again
  every `repeat_days` only with `repeat` (off by default). The round ends — and the notification is
  dismissed — when it is no longer due. Looked at hourly and right after the queue changes. A
  failed push is logged and not retried (no hourly notifications); a failed notification is tried
  again. Not when a frame arrives: no nagging.
  An answer for a state sensor adds the frame as a `review` sample; `prediction.sample_id` links the
  two, so answering again (an answered frame clicked in the Review page's list, or the History tab)
  relabels that sample, or deletes it on *Skip*, instead of adding the frame twice.

## 8. Home Assistant integration (MQTT Discovery)

One HA **device** per sensor (the entities a user sees are listed in DOCS.md). A state sensor:
the result as `sensor` with `device_class: enum` (options = state keys + `unknown`), its
confidence, an `image` of the region that was classified, a `button` to check now and a `switch`
to pause it.

**Object sensors** replace the first two with two entities per selected class:
`binary_sensor.<name>_<class>` (`device_class: occupancy`, attributes: confidence,
boxes, last seen, last trigger) and `sensor.…_<class>_count`. The image shows the region with the
boxes; the button is named "Detect now". Deselecting a class removes its entities. Each class
has its own Material Design icon (`icon` in `detectors.json`, checked against `@mdi/svg` 7.4.47,
the version Home Assistant ships) instead of the occupancy class's house icon.

**Reading sensors** publish the value on `sensor.<name>` with `unit_of_measurement`,
`device_class` and `state_class` from the mode (counter → `total_increasing`, value →
`measurement` except `monetary`, time left → `duration` in `min`), plus the confidence sensor.
Home Assistant's MQTT discovery refuses an entity whose unit its device class does not take (exact
match: `m3` with `water` made the value entity disappear without a word), so `mqtt.ha_unit` writes a
unit as Home Assistant does when that is clear (`m3` → `m³`, `kwh` → `kWh`) and otherwise leaves the
device class out (a warning in the log). The lists are `settings.HA_DEVICE_CLASS_UNITS`. Units
without a device class are never touched: those entities exist, with history in their unit.
Attributes: `read_text`, `rejected`, `last_update`, `last_trigger`. The button is "Read now".
Four more entities with `enabled_by_default: false` (nothing changes for users who do not turn
them on): *Raw reading* (`raw`, diagnostic), *Problem* (`problem`, enum of
`settings.READING_PROBLEMS`, diagnostic), *Accepted (24 h)* (`accepted`, %, diagnostic, kept in
memory and starting over after a restart) and, for counters only, *Rate* (`rate`: the change over
about `rate_window_min` (15) minutes — the last accepted reading before the window anchors it, so
readings far apart give the average since the previous one; unit and device class from the
counter's unit via `settings.READING_RATE_UNITS`: kWh → kW power, m³ → m³/h and L → L/min volume
flow rate, else `<unit>/h`; `suggested_display_precision` from `mqtt.rate_precision`: enough
decimals for the counter's smallest rise over the window, as Home Assistant otherwise showed
0.03 m³/h as 0). A sensor that stops being a counter loses its rate entity. A fifth,
*Reader image* (`image`, `reader_image`, diagnostic, also off by default), is the image the reader
saw at the last reading (the region after display processing), as on the Live tab.

Plus one app-wide **VisionState** device with `sensor.visionstate_review_queue` (frames waiting
for review; attributes: per-sensor breakdown, `oldest_waiting_since`). The review reminder (§7)
uses Home Assistant's REST API (`persistent_notification`, `notify`), not MQTT.

Attributes on the state entity: `probabilities`, `top_state`, `last_update`, `trained`,
`last_trigger`. Availability: app-wide LWT plus per-sensor camera availability. Removing a sensor
removes its entities. **Send to Home Assistant** (`sensor.publish`, default
`settings.SENSOR_PUBLISH_DEFAULT`, DB migration 9 keeps existing sensors on): when off, discovery
stays (the entities and the IDs the user gave them are kept), but no value is published (the
engine routes every sensor value through `_send`) and the sensor's availability topic is
`offline`, so its entities are unavailable and record no statistics. The `enabled` switch's state
is still published. Turning it on publishes `online` and the last value at once. Kept by
export/import.

**Entity IDs** follow Home Assistant's convention: VisionState sends no `default_entity_id`, so
Home Assistant names each entity after the device (the sensor's name) and the entity name
(`has_entity_name`), e.g. `sensor.garage_door`, `image.garage_door_last_frame`. The `unique_id`s
(`visionstate_<slug>_<entity>`) never change. Sensors made before 0.6.3b6 have
`sensor.entity_prefix` set (DB migration 8) and keep sending the `default_entity_id`s they always
had (`sensor.visionstate_<slug>`, … — exactly the same discovery payload as before), also through
export/import (bundles without the field count as older). The UI shows the IDs from Home
Assistant's entity registry (`config/entity_registry/list` over the WebSocket API, read every
5 minutes and shortly after discovery), so IDs the user changed or that got `_2` are right;
outside Home Assistant it shows the expected IDs.

## 9. User interface (Ingress)

Principle: **easy by default, details on demand.** Dark theme, responsive.

1. **Dashboard** — cards per sensor: live thumbnail, state, confidence, 24 h timeline, health.
2. **New sensor wizard** — camera → region → *detect*: states (names), objects (popular
   first, all 80 behind "Show all") or a reading (display or counter), each tested on a fresh
   frame → optional triggers.
3. **Label** — live view, one button per state (keys 1–9), current prediction, day/night
   coverage, last trigger, undo.
4. **Upload** — drag & drop images/ZIP/video; label in a grid or accept all suggestions.
5. **Dataset** — filter by state/unlabelled, relabel, unlabel, delete.
6. **Quality** — accuracy, confusion matrix, samples per state (day/night), suggestions,
   *possibly mislabelled* images with keep / change / delete. A red cell of the confusion matrix
   lists the images behind it (the cross-validation's wrong guesses, also those the user kept).
7. **History** — published state changes and flagged frames; "add as" to the dataset. The same
   list as the History page (11), fixed to this sensor.
8. **Sensor settings** — name, source, region, states, when to check, output, review overrides,
   export, delete.
9. **Review** — the review queue across sensors, keyboard driven.
10. **Settings** — status, AI models (state backbone, object detector, number reader),
    global review rules, storage (disk use and history limits), import.
11. **History** — every sensor's history rows in one list, filtered by sensors, *what* (state,
    object class or own label — the row's `state_key`; never a reading), event, time window and
    *waiting for review*, newest or oldest first. Each filter takes several values (any of them
    matches); filters combine with *and*. Events are a registry per kind
    (`settings.HISTORY_EVENTS`: `change`, `flagged`; `appeared`, `cleared`, `filtered`, `asked`,
    `face_seen`; `value`, `rejected`, `verified`), sent to the UI in `/config`; `api/history.py`
    says which rows each matches. One function (`history_query`) turns a filter into SQL for the list
    (`GET /history`, paged by a cursor on `(created_at, id)` so rows arriving at the top never
    repeat a row), its count and the facets (`GET /history/facets`: rows per sensor, key and event,
    each counted without its own filter so other choices stay offered). The filter lives in the
    page URL (`#/history?sensor=3,5&key=our_car`); a sensor's History tab is the same view with the
    sensor fixed, and links to the page. New rows are counted every `HISTORY["poll_s"]`
    (`newer_than` = the newest row when loaded) and shown on request, so the list never moves
    under a finger. Index `(sensor_id, created_at, id)`; indexes added later are created at start-up.

Reading sensors have four tabs: **Live** (value, last read, the analysed frame and the
image the reader saw), **Quality** (accepted share today / 7 / 30 days, by reason, per day, and
the rejected and checked readings with *read correctly* / *misread*), **History** (new values
and rejected readings) and **Settings** (mode,
decimals, unit, device class, display, limits). The reading settings start with the type —
*digital display* or *mechanical counter* (with its number of digits; the cells are drawn over
the region in the wizard and on the Settings tab).

Object sensors have three tabs instead: **Live** (the exact analysed frame with its boxes and
per-class status), **History** (appeared / cleared / filtered away, expandable to the frame with
boxes) and **Settings** (objects, region, triggers, output, and *What you taught* once something
was taught). Every box on Live and on a history frame can be tapped to teach it (§5); a
**Quality** tab (taught boxes per answer, own labels, frames filtered away lately) appears once
something was taught, so a sensor that was never taught looks exactly as before. The boxes on Live
change together with the frame they belong to, so the page does not move while the next frame
loads.

## 10. Data model & extensibility

- SQLite; schema version in `PRAGMA user_version` (`db.SCHEMA_VERSION`) with additive migrations
  (`db.MIGRATIONS`). An older version started on a newer database ignores the columns it does not
  know (rollback works; checked by the upgrade test).
- `sensor.kind`: `single_state`, `objects` (`sensor.objects` holds the fields of
  `settings.OBJECT_DEFAULTS`, the classes and the own labels `custom`) or `reading`
  (`sensor.reading` holds the fields of `settings.READING_DEFAULTS`); reserved for `multi_label`. Every reading is
  counted per sensor and local day in `reading_stat` (reads, accepted, rejected per reason);
  every rejected reading is a `prediction` row with its frame and `review_reason` "rejected"
  (one while the same reading waits for review, see §7)
  (spot checks: "spot_check"), and `read_ok` / `correct_value` hold the user's verdict. Verified
  readings are excluded from the history clean-up — they are the data a later reader
  improvement would learn from (schema v7). Object events are `prediction` rows (class or own
  label, `on`/`off`, or `filtered` when a class starts being filtered away, with `detections`);
  boxes taught to an object sensor are `sample` rows with `object_label`, `detected`, `box`,
  `score` (schema v10; their embeddings are cached under `roi_key` "taught"); readings are `prediction` rows with `state_key` "reading" and the
  value (or none when rejected), `probs` = text, value, reason.
- Labels live in a separate `sample_label` table (many-to-many) → multi-label needs no schema change.
- Extension points: backbone registry (`backbones.json`), detector registry (`detectors.json`),
  reader registry (`readers.json`, also the wheel readers),
  `sources.SOURCE_TYPES`, trigger settings.
- Versioned REST API (`/api/v1`) used by the frontend; `GET /api/v1/config` exposes every
  default and limit so the UI never hard-codes them.

## 11. Storage

| What | Where | In HA backup |
|---|---|---|
| Settings, DB, embeddings, trained heads | `/data` (per app) | Yes |
| Downloaded backbone models | `/data/models` | Excluded (re-downloadable) |
| Training images (and boxes taught to object sensors, as crops) | `/data/samples/<sensor>/` | Yes |
| Thumbnails of training images | `/data/thumbs/sample/` | Excluded (made again when asked for) |
| The people's taught faces (crops, shared by every sensor) | `/data/faces/` | Yes |
| History frames | `/media/visionstate/history/` (beta: `/media/visionstate_beta`) | With the media folder. Limits: 7 days and 2 GB by default, whichever comes first (see below) |

What the user taught lives in `/data`, next to the database that lists it: Home Assistant backs up
an app's `/data` (also by itself before every update) but not the media folder, so training
images in the media folder came back without their rows, or rows without their images, after a
restore (issue #45). The media folder is also shown to every Home Assistant user by the media
browser. Only the history stays there: large, removed by its limits, fine to lose. Training images are
what the user labelled or taught (a taught box is a small crop, a state sensor's image a whole
frame), usually tens of MB in all; Settings → Storage shows how much.

Versions up to 0.9.1b2 kept training images in the media folder. They are read there until they
are copied (`engine.HistoryMixin.move_samples`, in the clean-up loop, so start-up is not held up);
the old copies stay for `SAMPLES_MOVE["keep_old_days"]` after the first copy, so restoring the
backup made before the update still gives the older version its images, then they are removed.
Files no sensor lists are left in the media folder.

History frames are full camera frames (JPEG 90). They are removed by age (`history_days`;
frames waiting for review get twice as long) and by total size (`history_max_gb`, 0 = no limit):
oldest first, frames waiting for review only when that is not enough. Both limits are a DB
setting edited in Settings → Storage (`/api/v1/storage`, which also reports disk use);
the former app option `history_retention_days` was removed in 0.6.1 without carrying its value
over (early days; the release notes say so). The clean-up runs every
10 minutes and right after the limits change. Training images are never removed
automatically.

## 12. Import / export

- **Sensor bundle** (`.zip`): `manifest.json` (schema, app version), sensor settings, states,
  ROI, triggers, review overrides, all samples with labels (object sensors: taught boxes with
  their label, detected class, box and score, and the own labels).
- Camera credentials are removed from exported URLs; the importer re-enters them.
- **Readings to share** (`GET /sensors/{id}/reading-export`, Quality tab of a reading sensor),
  so reading can be improved: the readings checked by hand (newest first), then the ones nobody
  checked, accepted or rejected (newest first, `"answer": "unchecked"`; never used as labels without a check,
  see `docs/WHEEL_READER_PLAN.md`), whose frame is kept — each only the region it was read in
  plus `export_margin` (the region is kept with every stored reading since 0.7.1b2,
  `probs["roi"]`; older ones are cut with today's region, `"region": "current"`). One ZIP of at
  most `READING["export_max_mb"]` (GitHub takes 25 MB per file; about 1,000 readings); the
  readings that did not fit are counted (`left_out`). `readings.json` (`"format": 3`; 2 left out
  the rejected readings nobody answered, so a sensor's checks could not be replayed: read text, value, rejection reason, confidence, answer, right value, day only and
  seconds since the oldest reading; per reading the reader id and app version (kept with every
  stored reading since 0.7.1b5, null before), whether the picture was greyscale (IR), whether
  the sensor's light was on and the camera picture's size; for the wheel reader each wheel's position, probability and most
  likely positions, the common shift and the value with the last wheel's fraction), the reading
  settings without anything that tells where the sensor is (no source, name or region), the
  user's optional "What meter is this?" text, a README and a CC0 LICENSE — shared images may then
  be used in tests and evaluations. Images are crops of the stored frames at their resolution.
  The same frame read several times is exported each time (duplicates are removed by the
  training tools). The reader itself does not learn from the answers.
- Import always creates a new sensor and retrains it; bundles are validated like API input.
  `manifest.json` is capped (`UPLOAD_LIMITS["max_manifest_mb"]`); an unreadable image in a bundle
  is skipped and counted (the response's `skipped`), so an import never stops halfway.
- Not implemented: a sensor bundle with its trained head (retraining is faster than shipping it).

### Backup of everything (`backup.py`, `api/backup.py`, Settings → Backup)

For moving between installations and between the stable, beta and dev apps (which have their own
data and share entities): one ZIP with everything, and a restore that **replaces** everything.
Adding to what is there is left to the sensor bundle above: merging people, IDs and history of two
installations is much code for a rare need.

- **A copy of the database, not an export of its rows**: SQLite's backup of the live database,
  with the rows of the parts left out deleted from the copy. A new table or column is in every
  backup without code; `settings.BACKUP_PARTS` names each table's and each folder's part, and
  `tests/test_backup.py` fails for one that is in none, so nothing added later goes missing.
  Files go along by folder (`files/<Settings folder>/…`), thumbnails never (made again).
- **Parts** (all ticked at export): sensors and settings (always: rows, trained heads), training
  images, people and faces, history (with `reading_stat`), downloaded models. A restore empties
  the folders of every part but the models, also when the backup does not have the part (its rows
  are gone); models are only added (a missing one is downloaded again).
- **Camera passwords** are masked like in a bundle unless the user ticks them (unticked by
  default, `BACKUP["credentials"]`): a full backup is for moving one's own installation, where
  typing every password again is the usual pain, but the file then holds them. A restore lists
  the sensors whose password was masked.
- **Versions**: `backup.json` has the archive's `format` and the database's `schema`. An older
  backup is migrated by the usual migrations; a newer one (format or schema) is refused with what
  to do — an older version cannot know what a newer one stored. A backup made by every database
  version and format is kept in `tests/assets/backups/` (`tests/backup_fixture.py`, a test fails
  when the current one is missing), and every one is restored by every later version's tests.
  `Database.init` never lowers `user_version`, so a rollback or such a refused case never makes
  data migrations run twice.
- **Safety**: the upload is checked and extracted next to the live data and its database migrated
  there first; only then is the runtime stopped, its database closed (its worker threads must not
  write into the restored one: `Database.close`, waited for), the live data moved aside and the
  restored moved in, and a new runtime started. Any failure puts the old data back; a restore cut
  off by a stop is undone (or finished) at the next start (`backup.clean_leftovers`). Requests made
  meanwhile wait for the new runtime (`BACKUP["hold_requests_s"]`) instead of failing. Archive paths
  outside the known folders are ignored. The download is streamed (no temporary file of its size);
  the restore needs the upload and its contents on disk for a moment. Uploads reach the app
  streamed (`ingress_stream: true` in `config.yaml`): without it the Supervisor reads a request
  into memory and refuses one over 16 MB, which had also kept videos and sensor bundles under that.
- **Home Assistant**: sensors and people that are only in the app before the restore have their
  entities removed; those in the backup keep their unique IDs and so their entity IDs.

## 13. Configuration

App options are only for logging and a broker Home Assistant does not provide (list in DOCS.md).
Everything else (AI models, review rules, sensor settings) lives in the UI and the database, so it
is part of backups and needs no restart.

## 14. Roadmap

| Milestone | Content | Version |
|---|---|---|
| **MVP** ✅ | HA camera source, sensor + ROI, labelling, training, MQTT sensor, amd64+aarch64 | 0.1.0 |
| **Training UX** ✅ | Upload (images/ZIP/video), review queue, dataset, quality, history | 0.1.0 |
| **Triggers** ✅ | HA entity triggers, region change detection, bursts | 0.2.0 |
| **Hardening** ✅ | Credential redaction, import validation, upload limits | 0.2.1 |
| **Review rules** ✅ | Global rules with per-sensor overrides | 0.3.0 |
| **Prebuilt images** ✅ | GHCR images, releases | 0.3.1 |
| **Data quality** ✅ | Possibly mislabelled samples, review queue entity, wizard triggers step | 0.4.0 |
| **Maintenance** ✅ | Dependency updates, version-safe head reload, image smoke test, beta channel, Python 3.14, clean exit on stop | 0.6.0 (betas 0.4.1b1–b3) |
| **Region shapes & mobile** ✅ | Polygon regions, mobile layout fixes, Playwright UI tests in CI | 0.6.0 (betas 0.4.1b5–b6) |
| **Object sensors** ✅ | Pretrained detector (D-FINE), per-class binary + count entities, Live tab | 0.6.0 (beta 0.5.0b1) |
| **Reading sensors** ✅ | OCR of displays (PP-OCRv6), counter / value / time left, plausibility checks | 0.6.0 (betas 0.6.0b2–b4) |
| **Mechanical counters** ✅ | Rolling digit wheels (water, gas): one cell per wheel, digit count check | 0.6.1 (beta 0.6.1b6) |
| **Teaching object sensors** ✅ | Correct a box (not it / something else), own labels ("Our car"), missed boxes, Quality tab | 0.6.3 (beta 0.6.3b10) |
| **Readings & light** ✅ | Reading Quality tab and review of rejected readings, extra reading entities (rate, problem, reader image), a light for each check, regular check off / trigger states, entity IDs without prefix | 0.6.3 (betas 0.6.3b1–b16) |
| **Hardening** ✅ | Loops that survive unexpected errors (MQTT bridge, sensor loops), redacted log lines and tracebacks, source address checks, frame and image size limits, sturdier import, engine split into a package, coverage in CI | 0.6.4 |
| **History** ✅ | History page with filters shared by every sensor's History tab, the images behind the Quality tab's mix-ups | 0.7.0 |
| **Wheel reader** ✅ | Mechanical counters read wheel by wheel with a model made for VisionState; wheels mid-turn read right; export v2 of readings | 0.8.0 (betas 0.7.1b3–b6) |
| **Review reminder** ✅ | A notification in Home Assistant (optionally a push) when frames have waited for review a long time | 0.8.0 (beta 0.7.1b1) |
| **Larger models, tunable teaching** ✅ | D-FINE M and L, DINOv2 base, a model comparison in Settings, teaching limits globally and per sensor | 0.9.0 (beta 0.8.1b1) |
| **Recognising faces** ✅ | Faces in person boxes (YuNet), people taught by face and shared by every camera (SFace, or AuraFace for a fast PC), a People page, an entity per person saying where they are, unknown person, review questions, export opt-in — phases 1–3 of [`FACE_RECOGNITION_PLAN.md`](FACE_RECOGNITION_PLAN.md), and teaching a face from photos | 0.9.0 (betas 0.8.1b3–b5) |
| **Counter limits** ✅ | A counter's limit grows with the time it was away (learned, a fixed rate or per reading), Review moves a counter on, readings held when the camera's picture changes size, training images in the app's storage (backups hold them) | 0.9.1 (betas 0.9.1b1–b6) |
| **Reviewing readings** ✅ | A counter's value sent at its first reading, decimals for its rate, a rejected reading that repeats waits in Review once, Review shows a reading's region large and one sensor at a time | 0.9.2 (betas 0.9.2b1–b4) |
| **Backup of everything** | One file with everything (parts to leave out, camera passwords only when ticked), a restore that replaces it and keeps the entities, backups of every database version kept in the tests; uploads over 16 MB through Ingress | beta 0.10.0b1 |
| **Better counter readings** | Benchmark, the last value as an expectation, calibrated confidence, extra decimal, self-calibrating regions, wheels-v2, learning per sensor — status per phase in [`WHEEL_READER_PLAN.md`](WHEEL_READER_PLAN.md) | planned |

**Open ideas** (not scheduled): more on the History page (export of a filtered selection, a
summary per hour or day, "seen in the frame" as a filter, saved filters, bulk actions); full
export/import of everything; merge/replace import;
less MQTT/camera traffic (throttle frame publishing, reuse the engine's latest frame in the UI);
video de-duplication on the ROI instead of the full frame; light theme following Home
Assistant; mechanical counters beyond [`WHEEL_READER_PLAN.md`](WHEEL_READER_PLAN.md): pointer
dials and gauges; several readings per sensor (a sign with four prices, a
counter plus its dials); per-sensor model
choice with unloading of idle models (DINOv2 stays loaded: object sensors that were taught use
it); zones and line crossing for object sensors; classes outside COCO (an open-vocabulary
detector) — until then, a state sensor covers many of them ("parcel on the doorstep").
