# Wheel reader: plan for better counter readings

How VisionState is to read **mechanical counters** (rolling digit wheels on water and gas meters)
better: what the shared data shows, what was tried, and what comes next, with its status in §9.
It applies to reading sensors with the *counter* display and the wheel reader
(`counter_reader: "wheels"`). The general design is in [`SCOPE.md`](SCOPE.md); the model and how
to train it are in [`tools/wheelreader`](../tools/wheelreader/README.md).

## 1. What the data shows

**How wheels-v1 reads.** The region is split into one equal cell per wheel. A small CNN (565 k
parameters, 2.2 MB, about 2 ms for 8 wheels on one CPU thread) gives each wheel's position as a
distribution over 100 bins (0.0–9.9). `readers.wheel_value` finds the most likely value whose
wheels fit together, with one common shift of up to ±2 bins; the confidence is the geometric mean
of the decoded bins.

**The benchmark** (§3) holds about 6,000 readings of four real meters and the Dryad test photos:
the maintainer's water meter (8 wheels, 3 red, ESP32-CAM; 8 days), pedromfa's (7 wheels, ESP32;
8 days, shared in issue #40), two bolausson meters and Dryad's 5- and 6-digit meters. Results of
wheels-v1, region as drawn:

| Set | Right (±1 on the last wheel) | Too high | Note |
|---|---|---|---|
| pedromfa, 2 periods | 99.0–99.7 % | 4 + 1 | the replay (§3) of the first period: 3 too high published, 5.3 h blocked |
| maintainer, 19–22 Oct | 98.0 % | 1 | |
| maintainer, 14–18 Oct | 86.6 % | 9 | 6.7 h blocked over the 8 days (5 stretches) |
| bolausson cold / warm | 100 % / 100 % | 0 | |
| Dryad 5 digits / 6 digits | 99.4 % / 90.2 % | 6 / 26 | right way up |

What goes wrong, in order of harm:

1. **A region that does not fit the wheels.** v1 reads the middle of equal cells, so a region a
   little wider, narrower or off makes the outer cells show half a wheel. On pedromfa's frames
   (99–100 % as drawn), with the region's right edge half a cell short v1 read 43–69 % right;
   with half a cell more on each side, 0–94 % depending on the training run; 10 % taller added up
   to 9 too-high readings. The maintainer's region cut the last wheel, which caused most of the
   misreads of 14–18 Oct.
2. **The last (red) wheel half way, on a meter the model has not seen.** On the maintainer's meter
   a red wheel between 5 and 6 was read as 7.8–8.0 (two steps too high) or as 2.0; the
   second-to-last wheel half way between 1 and 2 was read as 5 (734.156 published for about
   734.120). When the water then stands still, the same hard frame comes back for hours, and a
   too-high value blocks the sensor until the meter catches up. Three of the five blocked stretches
   came from a too-high value. In the other two the value was right and the readings after it
   misread lower.
3. **A confidence that hides one bad wheel.** The mean over wheels stays high when one wheel is
   unclear. The weakest wheel's mass within ±0.4 of where the value puts it ("weakest wheel") is a
   much better signal. Replayed through the counter checks at 0.70, it published 2 wrong instead
   of 5 on the maintainer's meter (436 instead of 419 published), and 0 instead of 3 on
   pedromfa's. With 1 % of the accepted readings wrong, 82 % instead of 42 % of the maintainer's
   readings were accepted; risk–coverage (AURC) was better on Dryad and pedromfa #32 too.
4. **Dark, striped or noisy frames** (camera glitches) were read as noise; the checks stopped them
   all, two above the threshold.
5. **Dryad 6-digit meters** (dirt, blur, fog) are read 83 % exactly.

**Trained candidates** (same recipe, 25 epochs, three training runs each where it mattered; the
benchmark differs by 3–5 points on the maintainer's hard period and 2 on Dryad 6 between runs of
the *same* recipe, so smaller differences mean nothing):

| Candidate | Result |
|---|---|
| **Cell with context**: each cell plus half a cell on each side, 64×48 input, drawn rows of wheels with the region off by up to 38 % of a cell | **robust to a region that does not fit**: 99–100 % on pedromfa's frames with the region half a cell short or wide, or 10 % taller (v1: 0–94 %); as drawn the same as v1 (within run-to-run spread); 958 k parameters, 3.7 MB, 4.7 ms for 8 wheels on one thread |
| The same, model twice as wide | no better |
| One cell, drawn cells with the wheel off-centre and neighbours' digits | no better than v1 |
| **One-cell net + context net, leaving one meter out** | better reading and geometry, more wrong published values in some runs: §4.1 |
| Shared exports as training data (labels: §5) | on the meter's own other period: small gain at most (14–18 Oct 83–89 % → 91 % with 19–22 Oct in training; the other way round no gain) |

**Tried without training, and dropped:** reading 3–8 slightly shifted crops and averaging (a
small gain on one meter, none on the others); letting the app tune the region by what the model
finds most likely (§7: it made things worse).

## 2. Principles

1. **The base model stays general and stable.** Anything per sensor is a fallback on top of it,
   never instead of it, and it can always be switched off.
2. **Every change is measured** on the benchmark (§3) before it ships, with at least three training
   runs. Nothing that adds a reading that is *too high* ships: a too-high value passes the counter
   checks and blocks the sensor until the meter catches up.
3. **Labels come from people or from other frames, never from the reading itself.** A reading's
   label is a person's answer, or the interval between the readings next to it in time that the
   sequence confirms (§5). A model never trains on its own value for a frame (confirmation bias:
   self-training on its own pseudo-labels makes wrong predictions stronger).
4. **Licences:** training data must allow any use (CC0, or CC BY with attribution in the model card
   and a verified source). No NC/ND sets, no sets without a licence.
5. **No telemetry.** Data only arrives when a user exports it and shares it on purpose.
6. **The registry rules stay.** A new model, or a changed way to compute its confidence, gets a new
   registry id. Existing sensors keep their behaviour until the user picks the new one, as in
   0.8.0 (counters made before the wheel reader kept the text reader).

## 3. The benchmark

The scripts are in [`tools/wheelreader`](../tools/wheelreader/README.md) (`bench.py`,
`exports.py`); the sets live in `VisionStateLocal/wheel/` and never go into the repository (a
set's images may only become test assets when they are CC0). The sets of the new exports are
`ole_a`, `ole_b`, `ped_a` and `ped_b`, and their twins with labels read by eye (`_eye`, §4.1).

| Set | Source | Use |
|---|---|---|
| `ole2_a`, `ole2_b` | the maintainer's export of 2026-10-22 (CC0), split by date: 14–18 Oct / 19–22 Oct | test; `a` may train a candidate that is then tested on `b` only |
| `ped18_a`, `ped18_b` | pedromfa's export of 2026-10-18 (#40, CC0), up to / after 15 Oct | the same |
| `pedro40`, `pedro32`, `pedro_esp` | pedromfa's earlier exports (#32, #40) | wheels-v1 was trained on them |
| `bol_cold`, `bol_warm` | bolausson (GPL-3.0) | test only, never training |
| `dryad5`, `dryad6` | Dryad test set (CC0); a third of the photos is upside down, the right way up is kept | test |
| new shared exports | §6, from VisionState 0.8.0 on only (§4.2) | **split by meter** (or by date for one meter); a candidate is tested leaving its meter out |

**Labels:**

- A person's answer (checked readings) gives the value.
- Every other reading gets the interval between the trusted readings before and after it in time.
  Trusted readings are the checked ones, plus the accepted readings on the longest non-decreasing
  chain of checked and accepted values (checked ones weigh most).
- Where a stretch was blocked (a run of "went down"), the frames were looked at and labelled by
  eye.

The export now says where the region is in each image (`"box"`). Before, a margin cut off at the
frame's edge put the cells in the wrong place in 94 % of the maintainer's images.

**Metrics** per set:

- right (within ±1 on the last wheel), exact, too high;
- risk–coverage: AURC, and the share accepted at 1 % wrong — for the mean and for the
  weakest-wheel confidence;
- for the time series: a **replay** through *went down* and the change limit at threshold 0.70.
  It counts published values, wrong ones and the hours a too-high value stood;
- **geometry**: the same frames read with the region's edges moved (half a cell short or wide, a
  quarter cell off, 10 % up, down, taller, shorter).

**Gate for a model or decoder:** over five training runs (§4.2), no set gets more too-high readings and
none drops by more than the run-to-run spread. The geometry cases must not get worse. AURC must
not rise.

## 4. The next wheel model

### 4.1 Where wheels-v2 stands

The candidate: a one-cell net and a context net (each cell plus half a cell on each side, 64×48),
their log probabilities averaged, the weakest-wheel confidence. Measured **leaving one meter out**
(trained without the meter it is tested on, three training runs), against wheels-v1:

| | wheels-v1 | v2, three runs |
|---|---|---|
| maintainer 14–18 Oct, right / too high | 86.6 % / 9 | 92.3–93.5 % / 6–10 |
| maintainer, replay: published / wrong / hours too high | 192 / 2 / 3.2 h | 198–218 / 3–5 / 2.4 h |
| maintainer, region as drawn, too high | 2 | 3–5 |
| Dryad 6 digits, right / too high | 90.2 % / 26 | 91.5–94.0 % / 9–16 |
| region's right edge half a cell in: maintainer / pedromfa | 43 % / 69 % | 64–66 % / 93–99 % |
| pedromfa #32: share accepted at 1 % wrong | 0.95 (trained on it) | 0.11–0.23 |
| bolausson cold | 35 of 35 | 34 of 35 in two runs |

Clearly better at reading and at regions that do not fit, but worse on wrong published values and,
in some runs, on too-high readings: **not released** (the maintainer's rule: nothing ships that is
only a little better and worse on any point). What was learnt on the way:

- **A model memorises a meter's framing.** Trained on pedromfa's 716 frames, all cut the same way,
  a region moved a quarter cell read 0 % right (239 too high). Training on the same frames also
  cut with the region moved at random (8 cuts per frame, up to 38 % of a cell) fixed it (99.6 %).
- **The context net alone is unstable** between training runs (maintainer 14–18 Oct: 70–91 %);
  averaging weights (EMA) did not help; the pair with a one-cell net is stable.
- **The hard case on the maintainer's meter** is a wheel showing 7 with the next digit in view
  below it, read as 2 or 4. Crossed sevens are now in the drawn rows.
- **Run-to-run spread is as large as most differences** (3–5 points on the hard period), and the
  counts that decide (wrong published values) are small (2 against 3–5): three runs cannot tell
  them apart well (§4.2, step 1).

**On the new exports** (the maintainer's and pedromfa's of 2026-10-23, from 0.8.0 on, each split
in two periods `a` and `b`; leaving one meter out):

- **Labels read by eye.** The chain labels of §5 were too wide to judge a model by (median 8
  steps, 3–18 % exact). All 1,305 test frames were read by eye instead: one value, or two
  neighbours when the last wheel stands between digits; 4 frames nobody can read are left out.
  Five of these readings were wrong at first, all caught where the readers were sure and the
  frame, zoomed in, agreed with them.
- **One network pair is still not steady.** Five training runs trained on chain labels published
  16, 3, 2, 0 and 0 wrong values on the maintainer's first period (wheels-v1: 3); trained on the
  labels read by eye, three runs published 1, 3 and 0, and one run was worse than wheels-v1 on two
  of the four sets.
- **An ensemble is.** The average of five runs (or three) published **no wrong value on any of the
  four sets** (wheels-v1: 3, 2, 1 and 0), read more frames right (maintainer `a`: 97.8 % of the
  runs of frames against 93.0 %; the paired interval +1.5 to +8.5 points) and published more.
  It costs one pair per run, hence distillation (§4.2, step 4).
- **Distilled into one pair, nearly.** One pair trained half on the labels, half towards the
  ensemble's probabilities (`train.py --teacher`, not restricted to the interval): in three runs
  the paired gain over wheels-v1 is significant on three of the four sets (maintainer `a` +5.2
  points, `b` +1.5, pedromfa `a` +0.6) and none on the fourth; no wrong value published on the
  maintainer's meter (wheels-v1: 3 and 2), and the region moved or the wrong size hurts far less
  (right edge half a cell in: 96 % against 69 % on pedromfa's meter). Two things keep it from
  release: one run of three publishes one wrong value on pedromfa's `b` (a wheel half way from
  1 to 2 read as 7.6, sure enough to stand for 6 hours), the same run with a teacher of eight
  pairs as of three; and it is less sure (it publishes 130–150 frames of maintainer `a` at the
  threshold, wheels-v1 168, the ensemble 202). A temperature fitted on the Dryad test photos does
  not help: there it is already right (99 % of the wheels).

### 4.2 How the next model is trained

New data first: the maintainer and pedromfa export again with 0.10.1b1 or later, and training and
tests use **readings from VisionState 0.8.0 on only** (`"region": "as read"`, with `"box"`), so
every cell is cut where it was read. Then, in this order, each step measured on its own (§3):

1. **Measurement that can decide.** Five training runs instead of three; a per-frame paired
   comparison (the same frames read by both models) with a bootstrap interval, instead of
   comparing totals; a still meter's repeats counted once (a frame shown for hours otherwise
   counts hundreds of times). Without this, a gain and the noise look alike.
2. **Decoding the time series** (app side, no new model). A counter never goes down and rises at
   a bounded rate, so the value is decoded over the last frames together (a Viterbi pass over
   candidate values with monotone steps), not frame by frame. It targets exactly what held v2
   back: a hard frame that comes back for hours. It helps v1 too, comes as a new registry entry
   or setting so existing counters keep their behaviour, and is measured with the replay.
3. **Labels and loss.** The interval loss only asks that the mass lies inside the label's
   interval. Add a soft circular target inside the interval (most mass near the middle for a wheel
   at rest), and fit a temperature on held-out meters (Guo et al.), so that "70 % sure" means about
   that. Weight the frames that matter: a wheel half way, the last wheel, red wheels.
4. **Teachers into one student (distillation).** Train five networks, then one network of the
   app's size on their averaged output, restricted to each frame's label interval and
   renormalised. The labels still come from other frames (principle 3); what the ensemble knows
   about *where inside* the interval comes along, and the run-to-run spread of a single network
   shrinks. Frames with a wide label interval then teach too.
5. **Better drawn wheels.** Today's rows are flat strips. Draw the wheels as cylinders (digits
   squeezed at top and bottom, curvature, the next digit half in view), with the flash hot spot
   and glass reflections of an ESP32-CAM, red wheels, IR and fogged glass, and the fonts of real
   meters. This aims at what no amount of one meter's frames fixes: a meter the model has not seen.
6. **A pretrained start, last.** Self-supervised pretraining on all unlabelled cells (every export
   frame and Dryad), then training as now. It helps most when labels are scarce, but fine-tuning
   can drift towards the few labelled meters when the domains differ (Wang et al.); only worth it
   if steps 1–5 leave a gap. A large backbone (DINOv2) is too slow per wheel on small hardware.

Not planned: self-training on the model's own readings (principle 3), and a new network design
before the steps above are measured.

### 4.3 Model and app

The context design stays the base: neighbours in view let the model find the wheel nearest the
middle when a cell is off, and see a wheel the region cuts. A second output gives each cell's
embedding (the layer before the bins), so *learning per sensor* (§8) can be built in the app
without a new model. The app is ready for a second wheel reader: each counter names its own
(`readers.wheel_reader_of`; counters set up before keep wheels-v1), a model with a second input
is fed the cells with their neighbours from the frame (`readers.wheel_context_cells`), the
confidence kind is a registry field, and a wheel reader is loaded on first use and let go when no
counter reads with it. Both models are bundled once there are two.

**Before release:**

- five training runs pass the gate (§3) on the new exports, with no case of §4.1 worse than
  wheels-v1;
- the model is checked against real photos in the tests (registry rule: the drawn e2e counter and
  the CC0 test images);
- timed on a real Raspberry Pi 4 (v1 2.3 ms, the v2 pair about 6.5 ms for 8 wheels on one desktop
  thread);
- a model card with the data and its licences.

## 5. Labels from shared exports

- An export holds the checked readings and all unchecked ones, accepted or rejected, with
  `seconds` (`SCOPE.md` §12). From those, every reading gets an interval of values from its
  neighbours (§3). Every wheel then gets the interval of positions it passes through, widened a
  little for a wheel at rest. A wheel that may have turned more than half way gets no label (zero
  loss).
- The intervals come from other frames, so a reading's own misread never teaches the model
  (principle 3). The chain can still hold an accepted misread of a step or two. Such a misread
  only widens the intervals around it.
- Label check after every training run: a reading whose label the model strongly disagrees with
  (very likely value ≠ label, high confidence) is listed for a person to look at. That is how
  pedro32/0120 was found (labelled 628596; the wheels show 7–8). The label is corrected or the
  image dropped, never silently.
- Deduplicate a still meter's repeats when they dominate a set, and split by meter (or by date for
  one meter).

## 6. Data: the biggest limit

Four real meters are not enough to measure "a meter the model has not seen" well. On the
maintainer's meter every candidate reads the hard period at 81–89 %, whatever its design. Wanted
most:

- more meters, each exported after a week or two: gas meters (often white on black), electricity
  meters with a rolling counter, red decimal wheels, IR or night frames, angled cameras, fogged
  glass;
- sharing already works (*Quality* → *Export readings*, then a GitHub Discussion; `visionstate/DOCS.md`
  says how). What is missing is asking for it: a pinned Discussion, "Share your meter", saying
  which meters are wanted most and how exports are used (split by meter, credited in the model
  card);
- external sets checked so far (2026-10):

| Source | Licence | Verdict |
|---|---|---|
| Dryad Word-Wheel Water Meter Dataset (Sci Data 2026) | CC0 | in use |
| Roboflow Universe water-meter digit sets (several) | CC BY 4.0 (stated) | possible: check where the images come from first; attribution in the model card; mostly digit boxes, so their labels need converting |
| Yandex.Toloka water meters (1,244 images) and copies on Kaggle / Hugging Face / Dataset Ninja | CC BY-NC-ND 4.0 | not usable (copies that claim CC BY are not to be trusted) |
| UniData / ud-smart-city water meter sets | CC BY-NC-ND 4.0 / commercial | not usable |
| UFPR-AMR, Copel-AMR (Laroca et al.) | no open licence found (given out on request for research) | not usable |
| AI-on-the-edge data and models | no licence (data); non-commercial (project) | ideas only, no data or code |
| MR-AMR (Mendeley, 140,000 digits of Moroccan water and electricity meters, whole and half-turned) | CC BY-NC 3.0 | not usable, though the closest match |
| Dig21000 (IEEE DataPort, 21,000 rotary meter digits, partly from Roboflow) | no licence stated; behind a subscription | not usable |
| NRC-GAMMA (28,883 gas meter images, Ottawa) | CC BY 4.0 | not usable: pointer dials of one meter model, not wheels |
| Photos on Openverse (Flickr, Wikimedia Commons) of water, gas and electricity meters | CC0, public domain or CC BY per photo | **possible as a test set of meters the model has not seen**: hundreds of photos per search term, of which only some show a legible counter; each picked and labelled by eye, CC BY credited in the model card, only CC0 into the repository |

## 7. Tried and dropped

- **Tuning the region by the model's own likelihood** (the region's edges moved until the last K
  frames decode most likely). On the maintainer's meter it raised the too-high readings from 9 to
  30. A model is often most sure on a crop it reads wrong, so its likelihood cannot steer the
  geometry. The context model makes the geometry matter less instead.
- **Fitting each export stretch's region to its labels** (by the model's likelihood of them): it
  made the maintainer's 14 Oct worse. Exports now say where the region was instead.
- **Training on a meter's frames only as they were cut**: the model learns the framing (§4.1);
  frames are always also cut with the region moved.
- **Weight averaging (EMA)** for the context net: no steadier.
- **Averaging shifted crops** at read time: no gain on most sets, 3–8× the work.
- **A wider model, and drawn off-centre cells without context**: no gain.
- **Decoding over the time series** (§4.2, step 2; `temporal.py`): no gain for the pair; the gain
  it showed for wheels-v1 came from tuning its cost on the same frames it was measured on.
- **Weakest-wheel confidence for wheels-v1 on its own:** it works (§1), but it comes with
  the next model instead of as a separate setting for v1 (the maintainer's call, 2026-10-23).

## 8. Later, when a case asks for it

- **The last value as an expectation** is now step 2 of §4.2. What stays here: a reading far
  outside it that the image explains clearly better is not published but sent to Review. It would
  have stopped 734.156 (+36 steps in four minutes). Risks for both: a wrong last value that locks
  the prior in, and a meter that was replaced; both need tests that replay sequences.
- **One more decimal from the last wheel's fraction**: a smoother rate, earlier leak detection;
  off by default.
- **Camera drift:** a picture that moved at the same size (phase correlation against a reference
  frame, numpy FFT). A picture of another size is already caught (readings held, "camera
  changed", `SCOPE.md` §5).
- **Four-point perspective correction** for strongly angled cameras: waits for a real angled meter.
- **Learning per sensor** from checked readings (nearest neighbours on an embedding output): the
  data so far shows a meter's own frames from another period help little. It waits until shared
  data says otherwise.
- **Refuse a frame that is too dark or flat** before reading it, instead of stretching noise into
  digits.

## 9. Order and status

| Step | Builds on | Size | Gives | Status |
|---|---|---|---|---|
| Export: where the region is in the image (`"box"`) | — | small | correct cells from every export | **done** |
| Benchmark, labels, replay, geometry cases (§3) | — | small | a fair measure | **done** (`tools/wheelreader`) |
| Counters name their wheel reader; context and confidence kinds in the app (§4.3) | — | medium | a new model without changing existing counters | **done** (0.10.1b1) |
| wheels-v2 pair, leaving one meter out (§4.1) | benchmark | medium | robust to regions that do not fit | **done**, not released (wrong published values) |
| New exports from 0.8.0 on, maintainer and pedromfa (§4.2) | 0.10.1b1 | small | correct cells, fresh frames to test on | **done**; test labels read by eye (§4.1) |
| Measurement: five runs, paired comparison, repeats once (§4.2, 1) | benchmark | small | decisions that are not noise | **done** |
| Decoder over the time series (§4.2, 2) | measurement | medium | fewer wrong published values, for v1 too | **dropped** (§7) |
| Ensemble of training runs (§4.1) | measurement | small | no wrong published value on any set | **done**, too slow to ship as it is |
| Distillation of the ensemble into one pair (§4.2, 4) | ensemble | medium | the ensemble's results at the app's cost | **in progress** (`distill.py`): better than wheels-v1 on three sets, not yet steady (§4.1) |
| Labels, loss, calibration; cylinder wheels (§4.2, 3, 5) | measurement | medium | the next model | planned |
| Ask for shared meters (§6) | export | ongoing | the data everything else is limited by | next |
| The rest of §8 | real cases | — | — | when a case asks for it |

Update the status column in the same change that builds a step.

## 10. Risks

- **Overfitting to the few meters we have:** split by meter, keep unseen meters in the gate,
  prefer drawn variety over more images of the same meter, and three training runs per decision.
- **Labels from sequences:** an accepted misread on the chain widens intervals around it. Wrong
  labels are found by the label check (§5).
- **A slower model on small hardware:** the v2 pair does about three times v1's work; measure on
  a real Pi 4 before release.
- **Context from outside the region** can bring in something that moves (a hand, a shadow): drawn
  rows and real exports cover it; watch for it in the label check.

## 11. References

- Guo, Pleiss, Sun, Weinberger: *On Calibration of Modern Neural Networks*, ICML 2017 —
  [arXiv:1706.04599](https://arxiv.org/abs/1706.04599). Temperature scaling and ECE.
- Geifman, El-Yaniv: *SelectiveNet: A Deep Neural Network with an Integrated Reject Option*, ICML 2019 —
  [arXiv:1901.09192](https://arxiv.org/abs/1901.09192). Risk–coverage and reject options.
- Hinton, Vinyals, Dean: *Distilling the Knowledge in a Neural Network*, 2015 —
  [arXiv:1503.02531](https://arxiv.org/abs/1503.02531). Teachers into one student.
- Kim, Chung, Jang: *Calibration of Ordinal Regression Networks*, 2024 —
  [arXiv:2410.15658](https://arxiv.org/abs/2410.15658). Soft ordinal targets and calibration.
- Wang et al.: *Self-Tuning for Data-Efficient Deep Learning*, ICML 2021 —
  [arXiv:2102.12903](https://arxiv.org/abs/2102.12903). Pretraining and fine-tuning drift with
  few labels across domains.
- Self-training under distribution shift and confirmation bias:
  [arXiv:2411.00586](https://arxiv.org/html/2411.00586v1),
  [arXiv:2303.10856](https://arxiv.org/pdf/2303.10856).
- Zhao et al.: *A Comprehensive Dataset for Word-Wheel Water Meter Reading Under Challenging
  Conditions*, Scientific Data 2026 — [PMC13031774](https://pmc.ncbi.nlm.nih.gov/articles/PMC13031774/),
  data [doi:10.5061/dryad.7d7wm3860](https://doi.org/10.5061/dryad.7d7wm3860) (CC0).
- AI-on-the-edge-device documentation (ideas only):
  [parameters](https://jomjol.github.io/AI-on-the-edge-device-docs/Parameters/),
  [correction algorithm](https://jomjol.github.io/AI-on-the-edge-device-docs/Correction%20Algorithm/),
  [alignment with two reference marks](https://jomjol.github.io/AI-on-the-edge-device-docs/Alignment/).
- Phase correlation for image shifts:
  [scikit-image registration example](https://scikit-image.org/docs/0.25.x/auto_examples/registration/plot_register_translation.html).
- Dataset licences: [Dataset Ninja: Water Meters](https://datasetninja.com/water-meters) (CC BY-NC-ND 4.0),
  [UniDataPro/water-meters](https://huggingface.co/datasets/UniDataPro/water-meters),
  [Roboflow: water meter digits reading](https://universe.roboflow.com/ai-8rgrs/water-meter-digits-reading) (CC BY 4.0).
