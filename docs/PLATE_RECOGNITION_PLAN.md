# Licence plate recognition: research and plan

Recognising **which car** it is by its licence plate: the household's own cars first, and the text
of plates it does not know. It applies to the `objects` sensor kind. Phases 1 and 2 are built (§10 says
what was decided, what the benchmark measured and where the build differs from this plan); how it
works now is in [`SCOPE.md`](SCOPE.md) §5. Research done 2026-11-08, licence statements read from
the model pages and their issue trackers that day (§4). This plan keeps apart what the maintainer
asked for (§1), what was proposed (§2–§9) and what is not yet checked (marked *unverified*).

## 1. What the maintainer asked for (2026-11-08)

- Plates of **as many countries as possible**.
- **First priority:** recognise the household's own plates.
- **Also:** read plates it does not know and send them to Home Assistant.

## 2. Proposal: a part of object sensors, like faces

The same reasons as for faces ([`FACE_RECOGNITION_PLAN.md`](FACE_RECOGNITION_PLAN.md) §2): the
detector already finds `car`, `truck`, `bus` and `motorcycle` boxes, so a plate is looked for only
inside a vehicle box, and nothing runs while no vehicle is there. Own labels (`{key, name,
parent}`), keeping an answer while a box stays (`keep_iou`), the review queue, the Quality tab and
export exist already (SCOPE §5, *Teaching object sensors*).

An own car can then be known two ways, which help each other: by how it looks (an own label under
`car`, as "Our car" is today) and by its plate. A plate decides when it is read; the look decides
when the plate is not seen (from the side, too far, at night).

## 3. How one check would flow

1. The detector finds vehicle boxes (as now).
2. **Plate detector** on each vehicle box, cut from the full frame (not the detector's smaller
   input), at most a few vehicles per check (a setting, like `FACES["max_faces"]`).
3. Each plate found is straightened (it is a flat rectangle: the four corners, like a reading
   region) and **read** to text.
4. **Matching:** the text is compared with the household's plates, allowing for the characters a
   reader confuses (`0/O`, `1/I`, `8/B`, `5/S`, `2/Z`) and a missing character at the edge. A plate
   counts after it was read the same way on more than one frame while its box stayed (`keep_iou`).
5. A match makes the vehicle box that car (an own label). No match: an **unknown plate** with its
   text, if the user turned that on (§6).

## 4. Models found (licences as stated on 2026-11-08)

The repository rule applies: weights in a registry are pinned and checked on real inputs. As with
faces, the hard question is the licence of the **weights and their training data**.

### Plate detectors

| Model | Licence | Training data | Notes |
|---|---|---|---|
| **YOLOv9 plate detectors** ([open-image-models](https://github.com/ankandrew/open-image-models), `yolo-v9-t-256` … `yolo-v9-s-608-license-plate-end2end`) | Code MIT. Weights: no licence stated; the author trained with a modified fork of the GPL-3.0 YOLOv9 and says he does not know whether GPL applies to the weights ([issue #22](https://github.com/ankandrew/open-image-models/issues/22)) | About 11k images "scrapped from the web (mostly from free/open datasets)", not named ([issue #14](https://github.com/ankandrew/open-image-models/issues/14)) | ONNX, end-to-end (NMS inside), several input sizes. Hosted as GitHub release assets, not on Hugging Face. Used by the widely used fast-alpr. |
| **Own: D-FINE fine-tuned on plates** | D-FINE is Apache-2.0 (already in `detectors.json`) | Open Images V7 class *Vehicle registration plate*: boxes CC BY 4.0, images listed as CC BY 2.0 (*unverified* per image) | Clean licence; worldwide photos from Flickr. Means training a detector ourselves (like the wheel reader: `tools/`), which the non-goals list as "custom-trained detectors" — a decision for the maintainer. |
| Global License Plate Dataset (74 countries, 5M images) | CC BY-NC-ND 4.0 | — | Not usable (no commercial use, no derivatives). |
| Ultralytics YOLO plate models | AGPL-3.0 | — | Not usable in an Apache-2.0 app. |

### Plate readers

| Model | Licence | Training data | Notes |
|---|---|---|---|
| **fast-plate-ocr global** ([fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr), `cct-xs-v2-global-model`, `cct-s-v2-global-model` recommended) | Repository MIT; weights: no licence stated (questions closed as duplicates, [fast-alpr #50](https://github.com/ankandrew/fast-alpr/issues/50)) | "A custom dataset plus synthetic data" ([issue #89](https://github.com/ankandrew/fast-plate-ocr/issues/89)); the older global model: 85k+ plates from 65+ countries; Indian plates left out | Made for plates: fixed-length output, and the v2 models also say the plate's region (country). Very small and fast (under 1 ms on a GPU; CPU *unverified*). GitHub release assets. |
| **PP-OCRv6 tiny / small** | Apache-2.0 | PaddleOCR's | Already in `readers.json` (tiny is bundled). General text, so any country's characters, but it knows nothing of plate layouts (two-line plates, the country strip, stickers). Costs no new model: the baseline to beat. |
| Own plate reader | Apache-2.0 (ours) | Plates drawn by our own code (like `tests/displays.py`) plus CC-licensed photos | A project like the wheel reader. Only if the two above are not good enough. |

**Leaning (a proposal):** phase 0 measures **PP-OCRv6 on plates** found by the YOLOv9 detector,
against fast-plate-ocr, on real frames. The licence choice is the same as for faces (§9 question 1).

## 5. Own plates (first priority)

- **Typed, not taught:** the user writes the plate (`AB 12345`) on a car; spaces and dashes are
  ignored. Proposal: a car is an own label under `car` (or `truck`, `motorcycle`), so it can have
  both a plate and taught boxes.
- One binary sensor per own car, as an own label has today (`on` while it is seen), and its count.
- **Shared by every camera**, like people for faces: a car is the household's, not one sensor's.
  (*Proposal*; per sensor is simpler.)
- A plate read close to an own plate but not the same (one character off) is asked in Review:
  "Is this AB 12345?" — an answer teaches nothing to a model, only confirms the match rule works.

## 6. Unknown plates (second priority)

- An entity with the **last unknown plate's text**, sent only when it changes (SCOPE §8: no change
  at every check), plus the plate as a picture like the snapshot entities (*proposal*).
- **Off by default**, turned on per sensor after a confirmation saying what it means (§7).
- Proposal: no list of every plate ever seen inside VisionState; the history keeps the read as it
  keeps other results, under its usual retention. Home Assistant's own recorder keeps the entity's
  states; the confirmation says so.

## 7. Privacy and the law (not legal advice)

- A plate is personal data in the EU and EEA: it points to a person through the register.
- A camera that sees only the user's own drive is a household matter; one that sees a public road
  or a neighbour's property is not, and reading the plates of passers-by is then processing other
  people's data (the EU Court in *Ryneš*, C-212/13, for cameras that cover a public space). Rules
  differ per country.
- So: own plates only need the user's own plates (no one else's data is kept: a plate that does
  not match is dropped); unknown plates are off by default, and the confirmation names the
  public-road question. Nothing leaves the machine except to Home Assistant, as everywhere else.

## 8. Plan

- **Phase 0 — measure** (no code in the app): the user's own cameras (dev app, *Camera use*),
  frames with the household's cars at the usual distances, by day and at night (IR glare on plates
  is *unverified*). How many pixels a plate needs, whether HA's snapshot is sharp enough or the
  stream is needed, and which reader does best. Done in a script in `/VisionStateLocal/`, nothing in
  the repository.
- **Phase 1 — own plates:** detector and reader in a new registry `plates.json`, downloaded on
  first use, the typed plates, matching, the binary sensor per car, Review for a near match, export
  and backup of the plates (`settings.BACKUP_PARTS`), tests with drawn plates and a CC0 photo.
- **Phase 2 — unknown plates:** the entity, the confirmation, history.

## 9. Open questions for the maintainer

1. **Licence bar:** the plate models found say nothing about their weights' licence, and the
   detector's training code is a GPL fork. Options: (a) offer them as a download the user accepts,
   naming that (as SFace for faces); (b) only Apache/MIT-clean models: PP-OCRv6 as reader and our
   own D-FINE plate detector trained on Open Images; (c) (a) now and (b) later.
2. **Own cars shared by every camera**, or per sensor?
3. **Unknown plates:** only the last one as an entity, or also an event per plate (an automation
   "plate seen")? Kept in history like other results, or not at all?
4. **Target machine:** must it run on a Raspberry Pi 4, like faces?

## 10. Decisions and the build

The maintainer's answers to §9 (2026-11-08):

1. **Licence bar:** like faces — preferably one model usable without accepting anything, and
   another one besides. Built: the default **PP-OCR small (open)** (PP-OCRv6 small text detection
   and recognition, Apache-2.0, PaddleOCR's own data), and **Plate model, fast** (YOLOv9-t-384
   plate finder + fast-plate-ocr `cct-xs-v2-global`) and **Plate model, accurate** (YOLOv9-t-640
   + `cct-s-v2-global`), chosen only after accepting that no licence is stated for their weights
   (`plate_model_accepted`, a tick box under Settings → AI model). Their files are GitHub release
   assets, not on Hugging Face: pinned by SHA-256 (a replaced file fails the download).
2. **Cars shared by every camera**: yes (the DB setting `cars`).
3. **Unknown plates as an event**, so Home Assistant keeps their history: an `event` entity per
   sensor, *known_car* and *unknown_plate*; each event is also a history row with the frame.
4. **Raspberry Pi 4** ideally; extra models that do not run there are fine. *Plate model, fast*
   is the one for a Pi (about 25 ms per vehicle on a desktop CPU).

**The benchmark** (`/VisionStateLocal/plates/`: 64 photos of cars from Wikimedia Commons, CC0 to
CC BY-SA, eight countries — Norway, Sweden, Denmark, Finland, Germany, the Netherlands, the United
Kingdom, France —, 53 with a legible plate; vehicles found by D-FINE S, every finder × reader).
*Any*: one of a vehicle's reads is the plate; *near*: within one confusable step
(`plates.distance` ≤ 1); time: median per vehicle on 4 threads of a desktop CPU.

| Finder + reader | Any | Near | ms |
|---|---|---|---|
| YOLOv9-t-640 + cct-s-v2 (*accurate*) | 51 | 51 | 63 |
| YOLOv9-t-384 + cct-xs-v2 (*fast*) | 50 | 51 | 26 |
| YOLOv9-s-608 + cct-s-v2 | 51 | 52 | 101 |
| PP-OCR det small + cct-s-v2 | 48 | 51 | 60 |
| **PP-OCR det small + PP-OCR rec small** (*open*) | 39 | 47 | 45 |
| PP-OCR det tiny + PP-OCR rec small | 34 | 46 | 35 |
| PP-OCR det tiny + PP-OCR rec tiny | 21 | 36 | 32 |

The plate reader made for plates is what matters: PP-OCR adds or confuses characters (an EU band's
country letter, I for L). Two changes raised the open model from 37 to 39 (*near* 45 to 47):
stretching the contrast of a plate before it is read, and reading the two lines of a two-line
plate one by one. The text finder also finds stickers and badges (*WAISER123*), which is why a
read must look like a plate and an unknown plate needs two equal reads.

**Where the build differs from §3–§9:**

- No review question for a near match yet (§5): a read one step off names the car when a second
  check agrees, as a pending face does. Questions can come when real cameras show they are needed.
- Phase 0 was a benchmark on Commons photos, not the maintainer's cameras: the thresholds
  (`PLATES`) are first tried on the dev app and adjusted there, as for faces.
- No *last unknown plate* text entity (§6): the event carries the plate, and Home Assistant
  keeps events.
- A sticker read better than the plate does not hide it: the read closest to a car's plate wins.

**A car's look and its plate** (asked for 2026-11-09: a car the look says is ours, read one
character off, should count; configurable). A car can be taught by its look like an own label;
when the look says a car, a read within the sensor's look distance (up to two characters) of its
plate names it at once. The plate still overrules the look: an exact read of another car, or a far
plate read the same way `unknown_after` times. No new model or measurement: the look is the
sensor's taught boxes, as before.

**One list of cars** (asked for 2026-11-09: own labels such as *Tesla* and the plate cars did not
hang together). Every named vehicle is a car of the household, shared by every sensor, with no
plate, one or several; its look is taught per camera. Own labels of a vehicle class move into
the list by themselves (same key: same taught boxes and entities). A car named by its look alone
is an entity only of the sensors its look was taught on, or that read plates.

**Learning a car's look from its plate** (asked for 2026-11-09: when it is sure of the plate but
not of the car, learn; switchable, sensible defaults). On by default: plates are off by default,
and an exact read at high confidence is the surest label the app gets without the user. Limits
(`PLATES` *learn_*): a high read score, at most once an hour per car and camera (a parked car
gives the same picture for hours), a cap of boxes per car and camera, never past the sensor's
taught-box limit. The boxes are ordinary taught boxes marked *by its plate*.

**Plates seen at an angle** (asked for 2026-11-09: straightened automatically). `plates.straighten`
turns the cut plate level and its characters upright, from an edge map of the plate (the turn
that makes its row sums most uneven, then the shear that does the same for its columns); the
plate is read both ways and the reader's surer read is kept, so a plate seen straight on loses
nothing. Measured on the benchmark's vehicles turned and sheared by hand (a script in the local benchmark folder; exact reads
of 53, straightening off → on; turn in degrees, shear as x moved per y):

| Turn, shear | open | fast | accurate |
|---|---|---|---|
| 0, 0 | 39 → 40 | 50 → 50 | 51 → 51 |
| 8, 0 | 31 → 32 | 49 → 49 | 49 → 49 |
| −15, 0 | 15 → 18 | 48 → 49 | 47 → 47 |
| 22, 0 | 14 → 15 | 45 → 46 | 42 → 42 |
| 0, 0.3 | 32 → 33 | 48 → 49 | 48 → 48 |
| 10, −0.3 | 18 → 19 | 43 → 46 | 43 → 44 |
| −12, 0.25 | 14 → 16 | 45 → 46 | 46 → 46 |

It costs 5–15 ms per plate read. The models made for plates hardly need it (their finder and
reader were trained on plates at angles). The open model loses most of its reads at an angle
before reading: its text finder finds the turned text badly (the plate cut too short, or not
found). Measuring the text's own direction for the cut (as PP-OCR's own pipeline does) was tried
and read fewer plates, also straight on; left out.

**Left:** real cameras at night (infrared on a reflecting plate), the open model's text finder on
plates at an angle, a plate reader that is open *and* made for plates (trained on plates drawn by
our own code, like the wheel reader), and letters outside A–Z (Ü, Ø).
