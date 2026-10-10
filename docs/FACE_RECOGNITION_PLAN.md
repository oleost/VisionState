# Face recognition: research and plan

A plan, not yet built: recognising **who** a person is (`Ole`, `Kari`, *someone unknown*) when an
object sensor sees a person. It applies to the `objects` sensor kind only. The general design is in
[`SCOPE.md`](SCOPE.md), the code map in [`ARCHITECTURE.md`](ARCHITECTURE.md). Research done
2026-10-10; licence statements were read from the model pages that day and must be checked again
before a model is chosen (§4).

## 1. The idea in one paragraph

An object sensor that counts `person` can switch on **Recognise faces**. For every counted person
box, VisionState looks for a face in the upper part of the box, aligns it, turns it into a face
embedding and compares it with the faces the user taught for each person. A match makes the box
that person. People are **own labels** under `person` (as "Our car" is under `car` today), so each
one gets the binary sensor and count that own labels already get, and the teaching, review
("Is this Kari?"), Quality tab and export work the way they already do for own labels. Only the
embedding differs: a face model instead of DINOv2 on the whole box.

## 2. Why a part of object sensors, not a new sensor kind

- **The person detector already runs.** Face recognition is a second step on boxes the object
  sensor already found, so it costs nothing while nobody is there. A separate kind would detect
  people a second time.
- **The model for it already exists.** Own labels (`sensor.objects["custom"]`, `{key, name,
  parent}`), taught boxes (`sample` rows with `object_label`), matching with a threshold, a margin
  and an *ask* band, keeping an answer while a box stays (`keep_iou`), the review queue and export
  are all built (SCOPE §5, *Teaching object sensors*). Face recognition is another way to compute
  the similarity for one parent class.
- **Why DINOv2 is not enough for people.** DINOv2 on a whole person box mostly measures clothing,
  posture and background: the same person in another jacket looks like someone else, and two
  people in similar clothes look the same. A face model is trained to ignore exactly that. This is
  measured in phase 0 (§6), not assumed: DINOv2 on the box is the baseline every face model must beat.

## 3. How one check would flow

1. The object check finds and counts person boxes as today.
2. **Face search** (when the sensor has Recognise faces on, also before anyone is taught, so the
   Live tab can show whether faces are usable, §9.2):
   for at most N counted person boxes (most certain first, like `TEACH["max_checked"]`), crop the
   upper part of the box with a margin from the full frame (not the scaled 640×640 detector input),
   scale it up to the face detector's input and run the face detector. Keep the best face per
   person box, with its 5 landmarks (eyes, nose, mouth corners). A face outside its person box is
   ignored. A box that keeps a confident name (step 7) is not searched every check, only every few
   checks, to save CPU while someone stands in view.
3. **Quality gate:** skip faces that are too small (eye distance in pixels, a setting), too blurry
   (variance of the Laplacian), turned too far (landmark symmetry) or with a low detector score.
   Starting point for the minimum: the literature puts reliable identification at roughly 70–100
   pixels between the eyes and calls it unreliable below about 40; phase 0 sets the default.
   A skipped face is *no face*, never *unknown*: a back of a head says nothing about who it is.
4. **Align:** a similarity transform from the 5 landmarks onto the recogniser's template
   (112×112 for the ArcFace family) — a small NumPy least-squares fit (Umeyama) and Pillow's
   affine transform, so no OpenCV dependency is added.
5. **Embed** with the face recogniser (L2-normalised), cached per sample like other embeddings.
6. **Compare** with each taught person: cosine similarity to the mean of the person's best 3
   taught faces (not the single best: one mislabelled face would then match anything like it;
   Frigate uses a trimmed mean for the same reason). The same rule as taught boxes: a match needs
   the threshold *and* a margin over the next person; between the ask threshold and the match
   threshold the box is *ask* and goes to the review queue; below it the face is *unknown*.
   **The thresholds belong to the face model**, stored with its registry entry, not in `TEACH`:
   DINOv2's 0.88 means nothing for a face model (OpenCV's SFace demo uses a cosine of 0.363 for
   "same person").
7. **Over time:** a person box that stays (`keep_iou`) keeps its name while its face, when seen,
   stays close enough; a name is published after M consistent checks or one very certain one.
   When a person appears, the existing burst triggers give extra checks, so a passing person is
   seen several times and the clearest face counts.
8. **Publish:** each person's own label turns on/off with the existing debounce and
   `clear_after_s`. *Unknown person* turns on only when several usable faces of the same person box
   matched nobody and that box never got a name — one bad frame of a family member must not raise
   it (§11).

What the face models cost is measured in phase 0; a person crop is small, so the face detector
runs on about 160–320 px, not on the whole frame.

## 4. Models found (licences as stated on 2026-10-10)

The repository rule applies: weights in a registry are pinned by Hugging Face revision and SHA-256
and tested on real inputs. The hard question for faces is the **licence of the weights and of the
data they were trained on**: almost all public face models are MIT/Apache *code* with weights
trained on research-only data sets. VisionState is Apache-2.0, so anyone may use it commercially;
weights it ships must allow that too.

### Face detectors

| Model | Licence | Training data | Notes |
|---|---|---|---|
| **YuNet** (OpenCV Zoo) | MIT (the model directory) | WIDER FACE, whose Hugging Face card says CC BY-NC-ND 4.0 | Tiny (under 1 MB), 5 landmarks, ONNX; the 2026may variant has a dynamic input (multiples of 32). Best technical fit; the data question must be settled. |
| **BlazeFace full-range** (MediaPipe) | Apache-2.0 (per a re-host; check Google's model card) | Google's own, per its model card (to read) | TFLite only; needs an ONNX conversion that we check against real photos. Made for faces up to about 5 m. |
| SCRFD, RetinaFace (InsightFace) | Code MIT, **models non-commercial research only** | WIDER FACE | Not usable. |
| YOLO face models (Ultralytics based) | AGPL-3.0 | WIDER FACE | Not usable in an Apache-2.0 app. |

### Face recognisers

| Model | Licence | Training data | Size / accuracy | Notes |
|---|---|---|---|---|
| **SFace** (OpenCV Zoo, MobileFaceNet with SFace loss) | Apache-2.0 (the model directory) | Not stated in the zoo; the paper trained on CASIA-WebFace, VGGFace2 and MS-Celeb-1M | 112×112, fp32 and int8 ONNX; the zoo reports 0.994 on its own evaluation | Made to pair with YuNet's 5 landmarks. Smallest step to a working pipeline; data provenance unclear. |
| **EdgeFace** XXS / XS / S / Base (Idiap) | BSD-3-Clause code; weights listed as BSD-3 by a third party | WebFace260M subsets (4M, 12M), terms not found | 1.2–18 M parameters; LFW 99.57–99.83 %, CPLFW 90.3–93.8 % | Won the compact track of the IJCB 2023 efficient face recognition competition; ONNX ports exist. Best accuracy per FLOP. |
| **AuraFace v1** (fal) | Apache-2.0 | "A commercial dataset" (not named) | The recogniser `glintr100.onnx` is 261 MB; LFW 0.9965, CPLFW 0.909 | The only one that says its data allows commercial use. Too big as a default on a Raspberry Pi; an optional download. Its repository also carries InsightFace's SCRFD detector, which is *not* covered by that. |
| ArcFace (ONNX Model Zoo), FaceNet ports | Apache/MIT on the files | MS1M / VGGFace2 / CASIA (research terms; MS-Celeb-1M was withdrawn) | — | Same data problem as SFace, and larger. |
| InsightFace packs (buffalo_l, antelopev2, …) | **Non-commercial research only** | — | — | Not usable. |

**Prior art, Frigate** (its source, `dev` branch, 2026-10-10): faces are only looked for inside a
`person` box. Detector: YuNet through OpenCV (`cv2.FaceDetectorYN`, a 100 kB file), plus OpenCV's
LBF landmark model (56 MB) to align onto the ArcFace 5-point template. Recognisers: *small* =
FaceNet (TFLite, 94 MB, CPU), *large* = an ArcFace ONNX of 261 MB (the size of the glintr100
network that AuraFace uses; which weights it is was not checked) for GPU/NPU. All four files are
downloaded on first use from one GitHub release, tagged Apache-2.0, that names no source or
training data. Matching: one mean embedding per person (outliers below cosine 0.3 dropped, then a
trimmed mean), cosine to it, mapped to a confidence by a sigmoid; a name needs several consistent
recognitions, larger faces weigh more, blurry ones less. Its docs advise 20–30 varied photos per
person and warn that bulk-adding similar ones overfits.

**Leaning:** prototype with **YuNet + SFace** (both from OpenCV Zoo, made to fit together, tiny)
and **EdgeFace-XS** as the second recogniser, with AuraFace as the accuracy reference. Before
anything ships, a decision on the training-data question (§10, question 1). Sizes in the OpenCV
Zoo: SFace 38.7 MB fp32 and 9.9 MB int8; YuNet well under 1 MB.

## 5. Privacy and the law

Not legal advice; the points to design for, and to say plainly in DOCS.md.

- **Biometric data.** A face template used to identify someone is special-category data under
  GDPR Art. 9. A private person's camera is only outside GDPR under the household exemption, and
  the CJEU (*Ryneš*, C-212/13, 2014) held that a home camera that also covers public space (a
  street, a neighbour's door) is not purely household use. Norway follows GDPR through the EEA.
- **EU AI Act — the biggest open question of this plan.**
  - A *user* is fine: the Act does not apply to the obligations of deployers who are natural
    persons in a purely personal, non-professional activity (Art. 2(10)).
  - The *software* may not be: a camera that names people without their active involvement, at a
    distance, by comparing with stored faces fits the definition of a **remote biometric
    identification system** (Art. 3(41), recital 17), and those are **high-risk** (Annex III,
    point 1(a); only one-to-one *verification* — "is this the person they claim to be" — is
    excluded). The exemption for free and open-source software (Art. 2(12)) does **not** cover
    systems placed on the market or put into service as high-risk. Whether a free, non-monetised
    GitHub project is "placed on the market" (a "commercial activity, whether for payment or free
    of charge") is not settled.
  - Timing: the Annex III obligations apply from 2 December 2027 after the "Digital Omnibus"
    (Regulation (EU) 2026/1744, per secondary sources — check the Official Journal).
  - The Art. 5 prohibition on *untargeted* scraping of facial images from the internet or CCTV to
    build or expand recognition databases applies to everyone: VisionState must never grow a gallery
    of strangers by itself.
  - **Consequence for the plan:** phases 0 and 1 (finding faces, no identities) identify nobody
    and are not affected. **Phase 2 waits for a written legal assessment** of whether VisionState
    would be a high-risk system and what that would mean (or a design that is clearly outside it).
- **Norway:** Datatilsynet's guidance for private cameras: your own house and garden only, not
  public areas where people pass, and not a neighbour's property. The AI Act comes to Norway through
  the EEA.
- Elsewhere other rules apply (e.g. Illinois BIPA); the docs tell the user it is their
  responsibility, as for any camera.

Design rules that follow:

1. **Off by default**, switched on per sensor with a short explanation of what is stored. The
   people taught are the user's household and guests; the docs say they should know, children
   included.
2. **Only taught faces are a gallery, and only a human adds to it.** A recognised face is never
   added to the person's faces by itself (that drifts: one wrong match teaches the next one).
   Unknown faces are not stored as templates; they only live in history frames, under the normal
   history limits (`history_days`, `history_max_gb`).
3. **Faces are kept where only the app can read them.** Training images live in `/media`, which
   Home Assistant's media browser shows to every user of the house; taught face crops go to the
   app's own `/data` instead. That puts them in Home Assistant backups — say so in DOCS.md, and that
   backups should be encrypted.
4. **Embeddings are biometric data too.** Published attacks rebuild a recognisable face from an
   ArcFace-style template, so the cached embeddings are deleted with the person and never exported
   (an import computes them again).
5. **Forgetting is complete:** deleting a person deletes their face samples, crops and cached
   embeddings; switching the feature off offers to delete everything.
6. **Export:** sensor bundles include taught faces only when the user ticks it; the readings export
   is unaffected. No face ever leaves the machine otherwise (no telemetry, as always).
7. **Not a security function.** No liveness check: a photo of a face is that face. DOCS.md says
   not to unlock doors or disarm alarms on it alone.
8. Infrared night images, masks, hats and sunglasses make recognition worse; the docs say so and
   the Quality tab shows it (§9.5).

## 6. Plan

| Phase | What | Done when |
|---|---|---|
| **0. Spike** (local, `/VisionStateLocal/`) | Frames from real doorbell/driveway cameras of the household (with their consent), at the camera resolution Home Assistant actually delivers. Measure: how often a usable face is found in a person box per pass; detectors (YuNet, BlazeFace) on person crops; recognisers (SFace, EdgeFace-XS/S, AuraFace) against **DINOv2 on the box** as the baseline: true matches at a fixed false-match rate, the gap between same-person and other-person similarities, day vs IR. Time per face on a desktop CPU and a Raspberry Pi 4. Read the weights' and data's terms; ask the authors where unclear. | A detector and recogniser chosen with numbers, the minimum face size and thresholds proposed, and a written verdict on licences. Or a "not good enough at typical camera distances" verdict, which is a valid outcome. |
| **1. Faces only** | A face registry (detectors and recognisers, pinned like the others), face search in person boxes, face boxes drawn in the Live tab, no identities. | Users can see whether their camera gives usable faces before teaching anything. |
| **2. People** (after the legal assessment, §5) | Recognise faces on an object sensor; teach a face from the Live tab or history ("Who is this?" → a person, new or existing); matching, ask band, entities per person and *unknown person*; Forget person. | A person is recognised on a test with CC0 / public-domain photos (below) and on the spike data. |
| **3. Quality and review** | Review questions ("Is this Kari?"), Quality tab per person (faces taught, similarity between people — look-alikes —, possibly mislabelled faces), history filter by person, import/export with the opt-in. | e2e tests on desktop, mobile and iPhone. |
| **4. Later** | Teaching from uploaded photos of a person (face found in the photo); a Home Assistant *event* entity "person recognised" for automations; the face from several checks combined per visit. | — |

**Test images.** Tests need several photos of the same real person that are CC0 or in the public
domain. Works of the US federal government are public domain (e.g. NASA astronaut portraits: several
photos per person); the sources go in `tests/assets/README.md` as usual. Personal photos of the
household stay in `/VisionStateLocal/` and never enter the repository, a test or a model.

## 7. Where it would go in the code

Following the existing structure (names are proposals):

- A face registry next to `detectors.json` and `readers.json` (a detector and a recogniser per
  entry), and a face module next to `teach.py`: crop, detect, quality gate, align, embed.
- Loading in `engine/models.py` (loaded on first use, released when no sensor uses it, as the
  detector and reader are); the per-check step in `engine/objects.py` after counting, the taught
  faces index next to the taught boxes in `engine/teaching.py`.
- Settings: a face block in `settings.py` (minimum eye distance, blur limit, thresholds, max faces
  per check), and `recognise_faces` in `settings.OBJECT_DEFAULTS`; all exposed through
  `GET /api/v1/config`.
- Data: taught faces are `sample` rows with `object_label` = the person's key, the face box and
  landmarks, and a crop of the face **with a margin, not aligned** (in `/data`, §5): another
  recogniser may need another template or size (FaceNet takes 160 px, the ArcFace family 112), so
  changing the model must be able to align again from the stored crop. Their embeddings are cached
  under their own `roi_key`. A schema migration only if the box and landmarks do not fit the
  existing columns.
- Entities: each person is an own label, so its binary sensor and count already exist; *unknown
  person* and the later event entity are new, with new unique IDs that never change.

## 8. The model is downloaded only when someone turns it on

Nothing about faces is in the app image, and nothing is downloaded or loaded until a user turns on
**Recognise faces** for a sensor and confirms it. This is the mechanism the optional models already
use, extended by one step:

- **Today:** a registry entry with `"bundled": false` is fetched by `backbones.download` (to
  `/data/models`, kept out of Home Assistant backups, checked against its SHA-256, refused when it
  does not match) when it is chosen under **Settings → AI model**, where the choice reads
  "… · download 21 MB". The detector and the reader are loaded on first use (`ensure_detector`,
  `ensure_reader`), and the status rows say "loaded when …" until then.
- **For faces:** the face registry's entries are never bundled. Turning faces on for the first
  sensor downloads the chosen face model (detector and recogniser) and loads it; turning off the
  last sensor that uses it releases it from memory. The file stays in `/data/models`, so turning it
  on again is instant and needs no network; **Settings → AI model** can delete it while no sensor
  uses it (a new button; the other models have no such need, they are small or bundled).
- **While it downloads** the sensor keeps working as an object sensor without faces. Today a
  download shows only "Switching (may download)…"; the face model is larger, so the status reports
  progress (bytes so far / size), and the sensor shows it (§9).
- **When it fails** (no internet, a checksum that does not match): the sensor's face card shows the
  error and **Try again**; the status row *Face model* shows it in red, as the other models do.
  Without internet the user can put the file in `/data/models` by hand; the error names the file
  and its source.
- **Import:** a sensor imported with taught faces gets the faces but not the feature: face
  recognition stays off until the user turns it on there (with the same confirmation and download).

## 9. User interface

Built from the pieces object sensors already have, so nothing is new to learn: the teach sheet
(tap a box), *Is this …?* in the review queue, the Quality tab that appears once something was
taught, the *What you taught* card on the Settings tab, own labels with their entities, and
**Settings → AI model**. Everything works by tap (phones and tablets first, no hover or keyboard
needed), and all texts below are proposals. Model names and sizes in them are examples until phase 0
has chosen the model.

### 9.1 Where it is switched on

- **Not in the new-sensor wizard.** The wizard stays short; when `person` is among the chosen
  objects, one line under it says *"Later you can teach it to recognise people by their face
  (Settings tab)."* Turning on a biometric feature deserves its own decision, not a step in a flow.
- **Sensor → Settings tab, card *Faces*** — only on object sensors that look for `person`
  (otherwise the card says *"Add Person to the objects to recognise faces."*). While off:

  > **Recognise people by their face**
  > Name the people you know; each gets an on/off sensor in Home Assistant.
  > [ Turn on… ]

- **Turn on…** opens a sheet (the teach sheet's layout: a bottom sheet on phones, a dialog on
  desktop) that says what happens before anything is downloaded:

  > **Recognise faces on *Front door*?**
  > - Faces are looked for only in people this sensor finds, and compared with faces **you** name.
  > - Stored on this Home Assistant only, in the app's own storage (not the media folder): a small
  >   picture of each face you teach. Faces of people you have not named are not kept, apart from
  >   the normal history frames. The people you teach should know about it.
  > - Needs the face model *YuNet + SFace* (Apache-2.0, 38 MB), downloaded once from Hugging Face.
  > - Cameras that see a street or a neighbour's door may need their consent where you live.
  >   *Read more* (DOCS.md).
  > - It is not a lock: a photo of a face looks like that face.
  >
  > [ Download and turn on ]  [ Cancel ]

  The model name, licence, size and source come from the registry through `GET /api/v1/config`,
  never from the UI. When the model is already downloaded the button is just **Turn on**.
- **After confirming** the card shows the download (*"Downloading the face model… 12 of 38 MB"*,
  a thin progress bar) and then the card's normal state (§9.6). Leaving the page does not stop it.

### 9.2 Seeing faces before teaching anyone

The first question a user has is *does my camera see faces well enough?* — answer it before
asking them to teach.

- **Live tab:** inside each person box a small face box is drawn (rounded, in the person's colour)
  when a face was found. A person box without a usable face gets a quiet tag in the *Right now*
  list: *"Person · face too small"*, *"… · face turned away"*, *"… · face blurry"*, *"… · no face
  seen"* — the quality gate's reasons, in words.
- **Faces card** (Settings tab) and the top of the Quality tab: *"A usable face in 9 of the last
  40 people seen."* When that share is low, a hint names the likely fix: a camera closer to where
  faces are, a higher resolution (an RTSP source instead of the snapshot), more light.

### 9.3 Teaching a person

- **Tap a person box** (Live tab or a history frame) → the teach sheet. With faces on, its
  close-up shows **the face**, not the whole body (on a phone the face is a few pixels in the
  frame), and a section comes first:

  > **Who is this?**
  > [ Kari ] [ Ole ] [ + New person… ]
  > Not someone you know? Close this.

  The existing choices (*Not a person*, *Something else…*) stay below it.
- **No usable face in the box:** the section says *"No clear face here — pick a moment where the
  face is visible"* and offers no names: a person is only ever taught from a face. That keeps a back
  or a jacket out of someone's faces.
- **New person…** asks for a name (as **New label…** does) and explains: *"Gets an on/off sensor
  and a count in Home Assistant."* People are own labels under `person`, marked as taught by face,
  so the existing own labels made from whole boxes keep working beside them.
- **After teaching** a toast says what it learned and what helps: *"Got it: this is Kari (1 face).
  A few more, in other light and angles, make it reliable."* Up to the number the docs recommend,
  each teach says how many faces the person has.
- **More than one person in a frame:** each box is taught on its own, as today.

### 9.4 When it is not sure: the review queue

- **"Is this Kari?"** — the same review card as own labels, but with the **face close-up beside the
  frame** (stacked on phones) and, beside it, one of Kari's taught faces to compare with. The card
  of own labels says *"It looks 82 % like …"*; a face model's cosine (OpenCV's SFace demo already
  calls 0.363 the same person) would read as nonsense there, so faces say *"probably"* / *"not
  sure"* instead of a number. Answers: *Yes, Kari* · *No, it is …* (chips of the other people and
  **New person…**) · *Skip*. A *yes* or *no* teaches that face.
- **"Who is this?"** for faces that match nobody is **off by default** (the Faces card: *Ask me
  about faces I do not know*): it keeps frames of strangers longer (review frames get twice the
  history time), so it is the user's choice. Answers: the people chips, **New person…**,
  **Don't know** (removes the item, teaches nothing).
- Limits as today: one waiting question per person, a cooldown, none with review off.

### 9.5 The Quality tab: People

A section **People** above *What you taught* (the tab appears once a person is taught, as now):

- One card per person: name, the number of faces and a strip of the taught face crops (tap one to
  see the frame it came from; **×** forgets that face), *last seen 5 min ago*, and a simple meter —
  *1–4 faces: "teach a few more"*, enough: none.
- **Look-alikes:** when two people's faces come close (the closest pair of taught faces above a
  setting), a notice on both cards: *"Kari and Eva look alike to the AI — teach both in more
  light and angles."*
- **Possibly mislabelled:** a taught face that is closer to another person than to its own gets a
  badge (as mislabelled samples do on state sensors), with *Move to Eva* and *Forget*.
- **Remove person** (tap again to confirm): their faces, their entities in Home Assistant, gone.

### 9.6 The Faces card when on (sensor Settings tab)

- **Recognise faces** (on/off). Off keeps what was taught for later, as **Use what you taught**
  does; the entities of people stay but report off.
- **Ask me about faces I do not know** (off by default, §9.4).
- **Smallest face** — a slider like *Smallest object*, in pixels between the eyes, defaulting from
  the face settings in `settings.py` (the value is never in the UI).
- **Forget all faces** (tap again to confirm): every taught face and person, their entities, and
  the cached face embeddings. **Forget all** of *What you taught* forgets faces too.
- The model line: *"YuNet + SFace · 38 MB · change under Settings → AI model."*

### 9.7 Settings → AI model and Status

- A field **Faces (object sensors)** with the face models (*"… · download 38 MB"* while not
  downloaded), the licence and source as for the detector, and *"Downloaded when a sensor turns on
  face recognition."* Changing it re-embeds every taught face (as a new state backbone retrains),
  so the confirmation says so; people are kept.
- A status row **Face model**: *loaded when a sensor recognises faces* / its name / the download
  progress / the error in red.
- **Delete downloaded file** next to it while no sensor uses faces.

### 9.8 Home Assistant, history and export

- **Entities:** each person is an on/off sensor and a count (an own label); **Unknown person** is
  one more on/off sensor per sensor, made when faces are turned on. The *In Home Assistant* card on
  the Live tab lists them, as it does for classes.
- **History:** people are in the *What* filter like own labels; a row of a person shows the face
  close-up.
- **Export:** for a sensor with taught faces, **Export** asks first: *Include the taught faces (N)*
  — unticked by default. Without them the bundle carries the people (names) but no faces.

### 9.9 Testing the UI

As every UI change (CLAUDE.md, *UI testing routine*): e2e tests on desktop, mobile and iPhone for
the turn-on sheet (with a face model served by the test, never a real download in CI), teaching a
face from the Live tab, the review card, the People section and forgetting; and by hand at desktop
width and ~390 px. The fake camera would need a frame with faces: public-domain photos (§6).

## 10. Open questions for the maintainer

0. **The AI Act (§5).** Get a written legal assessment before phase 2: is VisionState with face
   recognition a high-risk remote biometric identification system, is a free GitHub project
   "placed on the market", and what would follow? Phases 0–1 do not depend on it.

1. **Licence bar.** Ship only weights whose training data allows any use (today: AuraFace, and
   perhaps BlazeFace), or accept models like YuNet/SFace whose code licence is permissive but
   whose training data is research-only? A middle way: never bundle such a model, download it on
   demand after the user accepts its terms (as the optional models are downloaded today).
2. **Default target:** must face recognition run on a Raspberry Pi 4, or is it fine as
   "desktop/NUC recommended"?
3. **Camera resolution:** Home Assistant's camera proxy often gives a reduced snapshot; does a
   doorbell at 2–4 m give enough pixels per face? Phase 0 answers it; maybe an RTSP source per
   sensor becomes the recommendation for faces.
4. **Unknown person:** only an entity, or also a review item ("Who is this?") — which stores the
   frame longer under review retention?
5. **Where to switch it on:** only on the sensor's Settings tab after a confirmation (§9.1), or
   also in the new-sensor wizard? The plan keeps it out of the wizard.

## 11. Pitfalls to avoid

Collected from the research, from how others built it, and from VisionState's own history.

- **Every check is another chance of a false match.** A false-match rate per comparison is
  multiplied by the checks per visit (bursts!) and by the number of people taught. So a name needs
  agreement across checks (§3 step 7), the margin over the next person matters as much as the
  threshold, and thresholds are measured at a false-match rate per *visit*, not per image.
- **"Unknown" from a bad face.** Without the quality gate every family member seen from the side
  becomes *unknown person* — a false alarm in an automation. A face that fails the gate is no face.
- **Learning from its own answers.** Adding recognised faces to the gallery automatically drifts
  towards wrong people (the wheel reader plan's rule: only human answers are labels). Frigate's
  docs also warn that bulk-adding similar faces overfits.
- **One threshold for all models.** Similarities of different models are not comparable; each
  model's thresholds live with its registry entry and are measured on our data.
- **Faces that are not people at the door:** a TV, a poster or a framed photo in view. The region
  (ROI) and the person detector's size limits keep most out; the docs mention it.
- **Children, and time.** Recognition is weaker for children, and their faces change quickly. The
  Quality tab shows how old each person's taught faces are and suggests adding recent ones.
- **Night and infrared.** Greyscale IR frames match colour faces worse. Measure it in phase 0; if
  needed, keep separate day and night faces per person (the history already knows a greyscale
  frame).
- **Unequal accuracy across groups of people.** Public models are less accurate for some skin
  tones and ages; the docs say so instead of promising one accuracy.
- **A low-resolution snapshot.** The camera proxy may scale the frame down; then no face is ever
  usable. The Live tab's face reasons (§9.2) make that visible instead of failing silently.
- **Licence by repository label.** A repository tagged Apache-2.0 says nothing about the data
  behind the weights (Frigate's model release names none). Read the model card and the data's
  terms before a model enters the registry.
- **Polling while a face model loads.** A large model loaded in the event loop would freeze the
  UI; load it in a worker thread like the others, and never let a failed download stop the
  sensor's loop (CLAUDE.md: long-running loops survive any exception).
