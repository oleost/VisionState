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

Built in `VisionStateLocal/wheel/v2/` (data and scripts never go into the repository; a set's
images may only become test assets when they are CC0). To be moved into
[`tools/wheelreader`](../tools/wheelreader/README.md) with wheels-v2.

| Set | Source | Use |
|---|---|---|
| `ole2_a`, `ole2_b` | the maintainer's export of 2026-10-22 (CC0), split by date: 14–18 Oct / 19–22 Oct | test; `a` may train a candidate that is then tested on `b` only |
| `ped18_a`, `ped18_b` | pedromfa's export of 2026-10-18 (#40, CC0), up to / after 15 Oct | the same |
| `pedro40`, `pedro32`, `pedro_esp` | pedromfa's earlier exports (#32, #40) | wheels-v1 was trained on them |
| `bol_cold`, `bol_warm` | bolausson (GPL-3.0) | test only, never training |
| `dryad5`, `dryad6` | Dryad test set (CC0); a third of the photos is upside down, the right way up is kept | test |
| new shared exports | §6 | **split by meter** (or by date for one meter) |

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

**Gate for a model or decoder:** over three training runs, no set gets more too-high readings and
none drops by more than the run-to-run spread. The geometry cases must not get worse. AURC must
not rise.

## 4. wheels-v2: a cell with context

**Model.** Each wheel's cell plus half a cell on each side (from the picture around the region,
not only the region; beyond the picture the edge is repeated), grey from the darkest channel as
v1, scaled to 64×48 with autocontrast. The CNN is v1's with a wider head, and the output is the
same 100 position bins, so `readers.wheel_decode` and the counter checks stay. The neighbours in
view let it find the wheel nearest the middle when the cell is off, and see a wheel that the
region cuts. A second output gives each cell's embedding (the layer before the bins), so
*learning per sensor* (§8) can later be built in the app without a new model.

**Data:**

- drawn rows of five wheels, cut as the app cuts a region that is off by up to 38 % of a cell,
  with dividers, neighbours in other colours and printed text above or below the windows; the
  photometric steps of `synth.py`;
- Dryad training crops (each photo's orientation as for v1);
- pedromfa #32/#40 and every shared export with labels as in §3, split by meter;
- still to add: red wheels half way (the maintainer's frames of 14–18 Oct are the cases), IR and
  night frames, and fogged glass.

**Confidence:** the weakest wheel (§1, point 3), part of the registry entry
(`"confidence": "weakest"`), with the threshold picked from the risk–coverage curve so that the
UI's "at least 70 % sure" means about that. v1 keeps its mean.

**App:**

- The cells are cut from the frame, not from the cropped region. `engine.models` passes the frame
  and the region today's code crops, and the new cutting takes the context from the frame.
- New registry fields for the input and context (`cell_width` 64, a context share), and the
  confidence kind. `readers.wheel_cells` stays for v1.
- "What the reader saw" shows the cells with their context dimmed.
- The wheel model becomes a per-sensor setting. Today every counter uses
  `readers.DEFAULT_WHEEL_READER`. A counter stored without the setting is read as wheels-v1 (a
  database step, as for `counter_reader`), a new counter gets v2, and the reading editor offers
  the switch (as for the text reader → wheel reader in 0.8.0). The setting goes into sensor
  exports too.
- Both models are bundled: v2 as the default (3.7 MB), and v1 because released entries never
  change and counters still use it (2.2 MB). Bundled only means the file is in the image. A model
  is loaded into memory when the first counter that uses it reads, and released when the last
  one is deleted or switched.

**Before release:**

- three training runs pass the gate;
- the model is checked against real photos in the tests (registry rule: the drawn e2e counter
  and the CC0 test images);
- timed on a real Raspberry Pi 4 (v1 2.3 ms and v2 4.7 ms for 8 wheels on one desktop thread;
  the Pi is slower);
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

## 7. Tried and dropped

- **Tuning the region by the model's own likelihood** (the region's edges moved until the last K
  frames decode most likely). On the maintainer's meter it raised the too-high readings from 9 to
  30. A model is often most sure on a crop it reads wrong, so its likelihood cannot steer the
  geometry. The context model makes the geometry matter less instead.
- **Averaging shifted crops** at read time: no gain on most sets, 3–8× the work.
- **A wider model, and drawn off-centre cells without context**: no gain.
- **Weakest-wheel confidence for wheels-v1 on its own:** it works (§1), but it comes with
  wheels-v2 instead of as a separate setting for v1 (the maintainer's call, 2026-10-23).

## 8. Later, when a case asks for it

- **The last value as a soft expectation** (decoder prior): the window [last value, last + rate ×
  time] is scored directly; a reading far outside it that the image explains clearly better is
  not published but sent to Review. It would have stopped 734.156 (+36 steps in four minutes).
  Two risks: a wrong last value that locks the prior in, and a meter that was replaced. Both need
  tests that replay sequences.
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
| Benchmark, labels, replay, geometry cases (§3) | — | small | a fair measure | **done** locally; into `tools/wheelreader` with v2 |
| wheels-v2 data and training (§4) | benchmark | medium | robust to regions that do not fit | three training runs done, gate open |
| wheels-v2 in the app (§4) | training | medium | the model and the weakest-wheel confidence in use | planned |
| Ask for shared meters (§6) | export | ongoing | the data everything else is limited by | next |
| The rest of §8 | real cases | — | — | when a case asks for it |

Update the status column in the same change that builds a step.

## 10. Risks

- **Overfitting to the few meters we have:** split by meter, keep unseen meters in the gate,
  prefer drawn variety over more images of the same meter, and three training runs per decision.
- **Labels from sequences:** an accepted misread on the chain widens intervals around it. Wrong
  labels are found by the label check (§5).
- **A slower model on small hardware:** v2 does about twice v1's work; measure on a real Pi 4 before
  release.
- **Context from outside the region** can bring in something that moves (a hand, a shadow): drawn
  rows and real exports cover it; watch for it in the label check.

## 11. References

- Guo, Pleiss, Sun, Weinberger: *On Calibration of Modern Neural Networks*, ICML 2017 —
  [arXiv:1706.04599](https://arxiv.org/abs/1706.04599). Temperature scaling and ECE.
- Geifman, El-Yaniv: *SelectiveNet: A Deep Neural Network with an Integrated Reject Option*, ICML 2019 —
  [arXiv:1901.09192](https://arxiv.org/abs/1901.09192). Risk–coverage and reject options.
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
