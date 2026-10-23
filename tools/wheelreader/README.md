# Wheel reader

The model VisionState reads mechanical counters (water and gas meters) with: a small CNN that
looks at one wheel and tells how far it has turned — a distribution over 100 positions around
the wheel (0.0, 0.1, … 9.9). The app (`visionstate/backend/visionstate/readers.py`,
`WheelReader`) splits the region into one cell per wheel and finds the most likely value whose
wheel positions fit together (a wheel only turns while the wheel to its right goes 9 → 0).

This folder holds everything needed to make the model again. Nothing here is part of the app
image; the data never goes into the repository (it lives in `VisionStateLocal/`, ignored by git).

## The released model

| Id | File | Size | Input | Output |
|---|---|---|---|---|
| `wheels-v1` | `wheels-v1.onnx` | 2.2 MB, 565 k parameters | `cells`: N × 1 × 48 × 32, grey 0…1 | `logits`: N × 100 |

Cell preprocessing (must match `readers.wheel_cells`): grey from each pixel's darkest colour
channel, resized to 32 × 48 (bilinear), autocontrast with 1 % cut-off, divided by 255.

Licence: Apache-2.0. Trained on data that may be used for anything:

| Data | Licence | Use |
|---|---|---|
| Drawn wheels (`synth.py`, 600 000 cells): fonts from Google Fonts (`fonts.py`, pinned) | SIL OFL 1.1 (the fonts; drawn images are not covered) | training |
| [Word-Wheel Water Meter Dataset](https://doi.org/10.5061/dryad.7d7wm3860) (Sci Data 2026), recognition crops | CC0 | training (labels: the nearest digit per wheel) |
| Checked readings exported from VisionState and shared in issues [#32](https://github.com/oleost/VisionState/issues/32) and [#40](https://github.com/oleost/VisionState/issues/40) | CC0 (the export's LICENSE.txt) | training; `wheels-v1` was evaluated with #40 0001–0017 held out, then trained on all of it |

Results of `wheels-v1` (2026-10-09; whole reading exact, last digit rounded):

| Set | Wheel reader | PP-OCRv6 small | PP-OCRv6 tiny |
|---|---|---|---|
| #40 water meter with red wheels (ESP32 camera), 0001–0017 held out | 17/17 | 15/17 | 8/17 (5 too high) |
| Two water meters never seen in training (test only, GPL images kept locally) | 30–33/35 and 15–18/18, none too high | 30/35 (2 too high), 15/18 (1 too high) | — |
| Dryad test set (2,400 photos), right way up | 97.7 % | — | — |

Trained without the #40 images it read them just as well. The remaining misses are ±1 on a
half-turned last wheel. Known weaknesses and what comes next are in the plan below.

## Making it again

Python 3.12 with CUDA PyTorch (any NVIDIA GPU; training takes about 10 minutes on an RTX 4080):

```
py -3.12 -m venv VisionStateLocal/wheel/.venv
VisionStateLocal/wheel/.venv/Scripts/python -m pip install -r tools/wheelreader/requirements.txt --extra-index-url https://download.pytorch.org/whl/cu128
cd tools/wheelreader
PY=../../VisionStateLocal/wheel/.venv/Scripts/python
$PY fonts.py                       # fonts into VisionStateLocal/wheel/fonts
$PY data.py synth 600000 synth 1000
$PY data.py dryad                  # needs the Dryad download, see below
$PY data.py pedromfa               # needs the shared exports, see below
$PY train.py s1_synth synth:1 --epochs 20                        # picks the Dryad orientation
$PY train.py wheels synth:1 dryad_train:1 pedro_train:0.15 pedro_test:0.03 --epochs 25 --width 16 --dryad-orient $(realpath ../../VisionStateLocal/wheel/runs/s1_synth.pt)
$PY export.py ../../VisionStateLocal/wheel/runs/wheels.pt 16     # ONNX next to it, with a timing
$PY evaluate.py ../../VisionStateLocal/wheel/runs/wheels.pt --width 16 --shift 2
```

- **Dryad:** download the dataset from <https://datadryad.org/dataset/doi:10.5061/dryad.7d7wm3860>
  (2.6 GB, needs a free log-in), put the zip in `VisionStateLocal/`, take
  `Word-Wheel_Water_Meter_Dataset.zip` out of it into `VisionStateLocal/dryad-wordwheel/` and
  extract its `recognition/` folder into `VisionStateLocal/dryad-wordwheel/rec/` (Python's
  `zipfile`; `unzip` with a wildcard misses the sub-folders). About a third of the crops are upside down
  without a flag: `data.py` keeps both orientations and `train.py --dryad-orient` keeps the one a
  model trained on drawn wheels finds more likely.
- **Shared exports:** the zips attached to issues #32 and #40, extracted into
  `VisionStateLocal/pedromfa-watermeter/`, `pedromfa-watermeter-esp/` and
  `pedromfa-watermeter-issue40/`.
- `baseline.py` (backend venv) reads the same sets with the app's text reader for comparison;
  `items.py` also knows local test-only sets that are not redistributable (GPL), which are
  skipped when they are missing.

## Candidates and the benchmark

The next model (a one-cell net and a context net read together, `context.py`) is built and
measured with these; each script's docstring says how to run it:

| Script | Does |
|---|---|
| `rows.py` | drawn rows of wheels, so a cell can sit off-centre with its neighbour in view |
| `exports.py` | labels and cells from a shared export (only readings with `"box"`); `eye.json` and `frames.json` in the export folder hold labels read by eye |
| `bench.py` | the benchmark: `eval` (with a replay through *went down* and the change limit), `geometry` (the region's edges moved) and `compare` (paired, with a bootstrap interval); a model spec of several `.pt` files joined with `+` is an ensemble |
| `distill.py` | an ensemble's probabilities on the training sets, for `train.py --teacher` |
| `temporal.py` | decoding over the time series instead of frame by frame |
| `fit_boxes.py` | regions fitted to the labels (dropped: the export says where the region was) |
| `export_v2.py` | the pair as one ONNX file with a second output, the embedding |

A candidate is measured **leaving one meter out**: trained without the meter it is tested on,
several training runs (`--seed`), each compared with wheels-v1 on the same frames. Results and
what was tried are in the plan.

What is planned next (benchmark, wheels-v2 and more): [`docs/WHEEL_READER_PLAN.md`](../../docs/WHEEL_READER_PLAN.md).

A new model is a new registry entry (`readers.json`, `wheel_readers`) with a new id, hosted on
Hugging Face with its revision and SHA-256 pinned; released entries are never changed.
