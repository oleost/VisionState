# Wheel reader: plan for better counter readings

The design for improving how VisionState reads **mechanical counters** (rolling digit wheels on
water and gas meters); what is done and what is next is in the table in §9. It applies to reading sensors with the *counter* display and the wheel reader
(`counter_reader: "wheels"`). The general design is in [`SCOPE.md`](SCOPE.md), and the model and its
training are in [`tools/wheelreader`](../tools/wheelreader/README.md).

## 1. Where we are (wheels-v1)

- **How it reads.** The region is split into one cell per wheel. A small CNN (565 k parameters,
  2.2 MB, about 2 ms for 8 wheels on one CPU thread) gives each wheel's position as a distribution
  over 100 bins (0.0–9.9). `readers.wheel_value` then finds the most likely value whose wheels fit
  together, from right to left, with one common shift of up to ±2 bins. The last wheel is rounded,
  and the confidence is the mean probability per wheel.
- **Training data and results:** [`tools/wheelreader/README.md`](../tools/wheelreader/README.md).
- **Known weaknesses, with evidence:**
  - *±1 on the last digit* when the last wheel is about half way. The rounding moves with the
    per-frame shift (pedro32: 81 exact + 12 at +1).
  - *Only three real meter types tested.* Gas meters, IR or night images and angled cameras are untested.
  - *Confidence is only moderately informative.* A threshold that rejects 4 % of the reads catches
    about half of the wrong ones (Dryad test).
  - *Every frame is read on its own.* The last value is only used afterwards, by hard checks
    (`went down`, `max_step`, `readers.settling`).
  - *Geometry is assumed:* equal-width cells and a fronto-parallel view.
  - *A wrong label can hide in the training data* (pedro32/0120 is labelled 628596; the wheels show 7–8).

## 2. Principles

1. **The base model stays general and stable.** Per-sensor tuning is a fallback on top of it, never
   instead of it, and it can always be switched off.
2. **Every change is measured** on a fixed benchmark (§3) before it ships. Nothing that adds a
   reading that is *too high* ships: a too-high value passes the counter checks and blocks the
   sensor until the meter catches up.
3. **Only human answers are labels.** The model never trains on its own guesses (confirmation bias:
   self-training on its own pseudo-labels makes wrong predictions stronger, especially when the
   images differ from the training data).
4. **Licences:** training data must allow any use (CC0, or CC BY with attribution in the model card
   and a verified source). No NC/ND sets, no sets without a licence.
5. **No telemetry.** Data only arrives when a user exports it and shares it on purpose.
6. **The registry rules stay.** A changed model, or changed calibration of a model, gets a new
   registry id. Existing sensors keep their behaviour until the user picks the new one, as in
   0.8.0 (counters made before the wheel reader kept the text reader).

## 3. Phase 0 — benchmark and metrics (the base for everything else)

**Benchmark** (in `VisionStateLocal/`, never in the repository; any image that can go into the
repository is CC0 and becomes a test asset):

| Set | Source | Use |
|---|---|---|
| `pedro40` 0001–0017 | issue #40 (CC0) | held out (wheels-v1 was trained on it in the end: hold out the *next* export instead) |
| `bol_cold`, `bol_warm` | bolausson (GPL-3.0) | test only, never training |
| `dryad_test` | Dryad (CC0) | test, right way up |
| new shared exports | §5 | **split by meter**: one meter is either training or test, never both |

**Metrics**, reported per set by `tools/wheelreader/evaluate.py`:

- **exact** (last digit rounded), **±1**, **too high** (above +1), **too low**.
- **Risk–coverage:** sort the readings by confidence and plot the error rate of the accepted
  readings against the share accepted. The default threshold is then picked for a target such as
  "at most 1 % of accepted readings wrong", and its coverage (how many are accepted) is reported.
  The area under the curve (AURC) gives one number to compare models and decoders.
- **Calibration:** expected calibration error (ECE). Readings are grouped by confidence, and in
  each group the share that is right is compared with the mean confidence.
- **Sequence metrics** for exports that are a time series: published value right, number of
  rejections, longest stretch blocked by a too-high value.

**Gate for a new model or decoder:** no set gets more too-high readings, the exact rate on unseen
meters does not drop, and AURC on the benchmark is lower.

**Label hygiene:** a reading whose label the model strongly disagrees with (very likely value ≠
label, high confidence) is listed for a person to look at again. That is how pedro32/0120 was
found. The label is corrected or the image is dropped, never silently.

## 4. Phase A — safer decoding (no new training)

### A1. The last value as a soft expectation

**Why in the decoder and not in the network:** the network sees one wheel. Teaching it time would
need sequences we do not have, and it could learn to repeat the last value instead of looking.
In the decoder the expectation can be tested, explained, set per sensor and used with every model.

**Design:**

- `wheel_value` gets an optional *prior* over the value V (in last-wheel units, with the fraction):
  `score(V) = Σ wheels log p(position_i(V)) + log prior(V)`.
- The prior comes from the last **published** (debounced) value L at time t₀, with elapsed time Δt:
  - V < L − settling tolerance: −∞ in practice (a very large penalty, so it can still be inspected);
  - L ≤ V ≤ L + R·Δt + slack: flat, with a mild preference for small steps (an exponential
    in V − L);
  - above that, a steep penalty.
- **R (largest plausible rate):** the sensor's `max_step` per interval when it is set, otherwise
  learned from its own history (for example 3× the 99th percentile of the accepted rate over 30
  days, at least a default in `settings.READING`). It is never learned from rejected readings.
- **Search:** the window [L, L + R·Δt + slack] is usually small (a few hundred last-wheel steps).
  Every V in it is scored directly (wheels × candidates, microseconds), and also the free
  optimum without the prior (today's DP). Result:
  - the free optimum is inside the window: same as today, with a better confidence;
  - outside the window, but the window's best explains the image almost as well (log-likelihood
    ratio below a threshold): take the window's best (an unclear wheel settled by the expectation);
  - outside and clearly better: **do not publish**. Mark the reading as "disagrees with the last
    value", send it to review and count it.
- **Protection against lock-in** (a wrong value used as the expectation):
  - only debounced, published values become L;
  - N readings in a row that clearly disagree with L, *and agree with each other*, release the
    prior. They are treated as a new start, which also covers a replaced or reset meter. This is
    shown in the History as an event and on the Live tab;
  - after a restart, L is the last value from the history (as today), but its age counts: an old L
    gives a wide window.
- **UI and history:** a new reason, `disagrees_with_last` (lower-case key for the "problem" entity),
  and the Live tab shows "read as … (expected … – …)".
- **Tests:** a pure unit test of the prior and the window decision (drawn positions); integration
  tests that replay sequences (one blurry wheel settled by the prior; a meter that is replaced
  releases the prior after N readings; a too-high value is never published).
- **Measure:** the sequence metrics on the exports, before and after.

### A2. A confidence that means what it says

- **Problem:** the mean per-wheel probability is not the chance that the whole reading is right.
  Networks are also often over-confident (Guo et al. 2017).
- **Temperature scaling:** divide the logits by one temperature T, fitted on the benchmark's
  held-out real images. This changes the confidence, not the reading.
- **Reading-level confidence:** use the decoder's alternatives: P(best V) / Σ P(top-k V) (a
  normalised margin between the best value and its nearest rivals, typically ±1 on a wheel), not the
  mean per wheel. The k-best come from the DP (keep the top few per state) or from the A1 window.
- **Pick the default threshold** from the risk–coverage curve (§3). The UI text stays "at least
  70 % sure", but 70 % then really means 70 %.
- **Registry:** T belongs to the model, so a calibrated wheels-v1 is a new entry
  (`wheels-v1c`, same file and SHA-256, plus `"temperature"`). Sensors keep their old behaviour until chosen
  (registry rule; `confidence: "wheel-mean"` versus `"value-margin"`).

### A3. The last wheel's fraction

- The decoder already knows the last wheel is at, say, 9.8. A sensor option "**one more decimal from the last
  wheel**" (wheel reader only) publishes V with one decimal more, for a smoother rate entity and
  earlier leak detection.
- The settling rule (`readers.settling`) and the "went down" check work on V with a tolerance of a
  few tenths, not one whole step.
- Off by default (entity precision and the Energy dashboard change), with a hint in the reading editor.

## 5. Phase B — better data in

### B1. The export ("Export readings") — done

Built in 0.8.0; what it contains is in [`SCOPE.md`](SCOPE.md) §12. Decisions that differ from the
first plan (2026-10-10):
- the light is recorded per reading automatically (greyscale picture, and whether VisionState had
  the sensor's light on) instead of a flag in the dialog;
- frames are **not** deduplicated in the app — a reading of the same frame is still a reading; the
  training tools remove duplicate images (§B4);
- the unchecked accepted readings are **always** included (no option, no count limit): the images
  are what is scarce. Their value is the reader's guess, so the tools use them as labels only after
  a check (§B4);
- no spot-check button; `spot_rate` is the way to get accepted readings checked;
- one ZIP of at most 24 MB (GitHub's limit is 25 MB per file), checked readings first, then the
  newest unchecked ones; what does not fit is counted in `left_out`. Splitting into parts was built
  and dropped as more than we need now. Images are not scaled down.

Privacy: only the region with a small margin, no camera, name or position, and the day only. The
user looks at the images before sharing (README.txt in the ZIP).

### B2. Collecting

- A pinned GitHub Discussion, "Share your meter", that explains why exports help, how they are
  used (split by meter; credited in the model card) and that CC0 applies.
- A Discussions form (or an issue template) with the meter type, camera and light.
- Most wanted: gas meters (often white on black), electricity meters with a rolling counter, IR or
  night frames, angled cameras and fogged glass.

### B3. External data (checked 2026-10)

| Source | Licence | Verdict |
|---|---|---|
| Dryad Word-Wheel Water Meter Dataset (Sci Data 2026) | CC0 | in use |
| Roboflow Universe water-meter digit sets (several) | CC BY 4.0 (stated) | possible: check where the images come from first; attribution in the model card; mostly digit boxes, so their labels need converting |
| Yandex.Toloka water meters (1,244 images) and copies on Kaggle / Hugging Face / Dataset Ninja | CC BY-NC-ND 4.0 | not usable (copies that claim CC BY are not to be trusted) |
| UniData / ud-smart-city water meter sets | CC BY-NC-ND 4.0 / commercial | not usable |
| UFPR-AMR, Copel-AMR (Laroca et al.) | no open licence found (given out on request for research) | not usable |
| AI-on-the-edge data and models | no licence (data); non-commercial (project) | ideas only, no data or code |

### B4. Data hygiene

- Deduplicate by frame. Split by meter (or by date for one meter, as for #40).
- **Unchecked readings** (`"answer": "unchecked"`) are the reader's own value, so they are never a
  label as they are (principle 3). They become one after a check: a person looks at them, or the
  sequence confirms them (a counter only goes up: a value between two confirmed neighbours that
  fits the rate). Until the tools do that check, they skip them.
- A label check after every training run (§3).
- `VisionStateLocal/README.md` lists the source and licence of every set, as it does now.

## 6. Phase C — geometry: let the app correct itself

The goal: a region that is drawn a little off, or a camera that moves a little, does not hurt.

### C1. A more robust model (training, in wheels-v2)

Drawn cells with perspective, rotation of ±5°, cells of unequal width, a neighbouring wheel
showing at the side and wheels that sit a little off each other at rest.

### C2. Calibration per sensor (no training)

- In the background, every so often (for example after 50 readings, and after the region is
  changed), the app takes the last K frames of the sensor and tries small changes to how the region
  is read:
  - vertical offset ±10 %;
  - height scale ±10 %;
  - rotation ±3°;
  - cell borders moved ±10 % one by one, or as a linear stretch (cells of unequal width).
- It keeps the change with the best total decode score over the K frames. Cost: K × changes ×
  about 2 ms; a coarse-to-fine search keeps it to a few seconds on a Pi.
- **The result** is stored with the sensor (`reading.calibration`, reset when the region or the
  number of digits changes).
  - A small change is used straight away and shown on the Live tab.
  - A large change is offered: "The digits sit a little lower than the region. Adjust?", with a
    before/after image.
- It **replaces the per-frame shift** in time: a fixed offset per sensor gives a stable last digit
  and removes most ±1 readings (§1).
- Tests: drawn counters with a known offset, rotation and cell stretch, where the calibration
  must find them again; e2e for the suggestion on all three devices.

### C3. Camera drift

For a camera that moves a little over days (a bracket that sags, a door being closed): estimate
the shift of the region against a reference frame by phase correlation (numpy FFT, sub-pixel, no
OpenCV) and move the region along. AI-on-the-edge does this with two reference marks the user
picks. Doing it automatically, on the area around the region, needs no user step. It must check
the correlation peak and skip frames where it is weak (light changes, IR switching).

### C4. Perspective correction

The manual 4-point correction stays planned for strongly angled cameras (see the earlier
decision: measure the read rate at real angles first, for example with the user's ESP32 camera, or
with `fake_camera` given a perspective parameter). C2 can later suggest the four points. Open
questions: reuse the 4-corner polygon region or a new corner tool (existing sensors must keep
working), the corner order and orientation, and dragging corners on a phone.

## 7. Phase D — wheels-v2

- **Drawn cells:** whole counter rows drawn in perspective and then cut like the app does;
  monochrome IR; flash reflections on glass; fog and drops; DIN-like narrow fonts (OFL); the failure
  types from phase B.
- **Context:** the cell plus a strip of each neighbour (or a model that reads the whole row and
  gives one position per wheel), so carries are seen together. Measure against the per-wheel model.
- **Test-time augmentation:** read 2–3 slightly shifted crops and average them (a few ms). A small
  ensemble only if the benchmark shows a clear gain.
- **Dryad position labels:** fit positions for the Dryad crops with v1 (inside each crop's
  nearest-digit interval, offline) and train v2 on the sharper labels. This is only for that public
  training set, anchored by the exactly known drawn wheels, never for user data.
- **An embedding output** (the layer before the last) as a second ONNX output, for phase E.
- **Limits:** under 5 MB and under 10 ms per reading on a Raspberry Pi 4. Measure on a real Pi
  (CI's QEMU check only shows that it runs).
- **Release:** new id `wheels-v2`, the benchmark gate (§3), and a model card with the data and its licences.

## 8. Phase E — learning per sensor

- **What already exists:** the review queue asks "read correctly / misread, what was it?", and the
  answer is stored with the frame and the region it was read in (`prediction.read_ok`,
  `correct_value`, `probs.roi`). The reader does not learn from it today.
- **Design (like object sensors' taught boxes):**
  - From every reading checked by hand, the wheels at rest give exact labels (digit d at d.0), and
    the last wheel gives a ±0.5 interval.
  - Their embeddings (v2's second output) are kept per sensor.
  - A new wheel's position probabilities are mixed with a nearest-neighbour vote among that
    sensor's own wheels. The weight grows with the number of examples and with how well it did on
    the sensor's own checked readings.
  - The base model is never changed. The adaptation can be dropped and rebuilt at any time, and
    it is part of the sensor's data (backup and export).
- **Safety net:** a leave-one-out check on the sensor's checked readings. The adaptation is used
  only when it beats the base model there. Otherwise it is off, and the Quality tab says so
  ("adapted from 23 checked readings · 21 → 23 right").
- **Asking at the right moment:** a reading where the base model and the adaptation disagree, or
  the confidence is low (A2), goes to review as "Read as 632.59, not sure — right?". That is the
  same queue and the same answers, used only for that sensor.
- **No self-training** on the sensor's own unchecked readings (principle 3).

## 9. Order, size and dependencies

| Phase | Builds on | Size | Gives | Status |
|---|---|---|---|---|
| 0 Benchmark | — | small | a fair measure for everything after | planned |
| A1 Expectation | 0 | medium | far fewer too-high and unclear readings | planned |
| A2 Calibration | 0 | small | a threshold that means what it says | planned |
| A3 Extra decimal | — | small | a smoother rate, earlier leak detection | planned |
| B1 Export v2 | — | small–medium | the data we need, labelled properly | **done, 0.8.0** |
| B2 Collecting | B1 | ongoing | more meter types | next (issue forms point to Discussions) |
| C2 Calibration per sensor | 0 | medium | a stable last digit, forgiving regions | planned |
| C3 Camera drift | C2 | small–medium | sensors survive small camera moves | when a real case asks for it |
| C4 4-point perspective | real angle tests | medium | angled cameras | waits for a test with a real angled meter |
| D wheels-v2 | 0, B (data), C1 | large | a better base model | planned |
| E Learning per sensor | D (embedding), A2 | medium | the last step for an unusual meter | planned |

Update the status column in the same change that builds a phase.

Suggested order: **0 → A1 + A2 (B2 alongside) → A3 → C2 → D → E**,
with C3 and C4 when real cases ask for them. A and C2 need no new training and help every existing
counter sensor right away.

## 10. Risks

- **Prior lock-in (A1):** mitigated by publishing only debounced values, releasing the prior after
  N agreeing disagreements, and showing it. It must be tested on sequences with a deliberately wrong start.
- **Calibration on little data (A2):** T fitted on a few hundred readings may not carry over to
  other meters. Refit for each new benchmark and report the ECE per set.
- **Overfitting to the few meters we have (D):** split by meter, keep unseen meters in the gate,
  and prefer drawn variety over more images of the same meter.
- **Self-calibration fooled by a frame without digits (C2/C3)** (night, a hand in front): use only
  frames with a confident reading, and require an improvement over many frames.
- **Per-sensor adaptation that learns a mistake (E):** human labels only, the leave-one-out gate
  and an off switch.

## 11. References

- Guo, Pleiss, Sun, Weinberger: *On Calibration of Modern Neural Networks*, ICML 2017 —
  [arXiv:1706.04599](https://arxiv.org/abs/1706.04599). Temperature scaling and ECE.
- Minderer et al.: *Revisiting the Calibration of Modern Neural Networks*, 2021 —
  [arXiv:2106.07998](https://arxiv.org/pdf/2106.07998). Calibration under distribution shift.
- Geifman, El-Yaniv: *SelectiveNet: A Deep Neural Network with an Integrated Reject Option*, ICML 2019 —
  [arXiv:1901.09192](https://arxiv.org/abs/1901.09192). Risk–coverage and reject options.
- Self-training under distribution shift and confirmation bias:
  [arXiv:2411.00586](https://arxiv.org/html/2411.00586v1),
  [arXiv:2303.10856](https://arxiv.org/pdf/2303.10856).
- Zhao et al.: *A Comprehensive Dataset for Word-Wheel Water Meter Reading Under Challenging
  Conditions*, Scientific Data 2026 — [PMC13031774](https://pmc.ncbi.nlm.nih.gov/articles/PMC13031774/),
  data [doi:10.5061/dryad.7d7wm3860](https://doi.org/10.5061/dryad.7d7wm3860) (CC0). It labels
  half-turned wheels but reports no separate results for them (DenseNet 98.15 % overall).
- AI-on-the-edge-device documentation (ideas only):
  [parameters](https://jomjol.github.io/AI-on-the-edge-device-docs/Parameters/) (PreValue,
  MaxRateValue, ChangeRateThreshold, ExtendedResolution),
  [correction algorithm](https://jomjol.github.io/AI-on-the-edge-device-docs/Correction%20Algorithm/),
  [alignment with two reference marks](https://jomjol.github.io/AI-on-the-edge-device-docs/Alignment/).
- Phase correlation for image shifts:
  [scikit-image registration example](https://scikit-image.org/docs/0.25.x/auto_examples/registration/plot_register_translation.html)
  (the method; we would write it with numpy FFT).
- Dataset licences: [Dataset Ninja: Water Meters](https://datasetninja.com/water-meters) (CC BY-NC-ND 4.0),
  [UniDataPro/water-meters](https://huggingface.co/datasets/UniDataPro/water-meters),
  [Roboflow: water meter digits reading](https://universe.roboflow.com/ai-8rgrs/water-meter-digits-reading) (CC BY 4.0).
