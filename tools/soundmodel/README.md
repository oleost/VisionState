# Sound model

The model VisionState's sound sensors listen with: **CED-Tiny** ([`mispeech/ced-tiny`](https://huggingface.co/mispeech/ced-tiny),
Apache-2.0) with one output added. The app (`visionstate/backend/visionstate/sounds.py`,
`SoundModel`) declares it in `sounds.json`; why this model, how it was measured and what it is
used for are in [`docs/SOUND_PLAN.md`](../../docs/SOUND_PLAN.md) §5.

Nothing here is part of the app image.

## The file

| | |
|---|---|
| Made by | `make_model.py`, from the ONNX file of `mispeech/ced-tiny` at revision `ace276d29dd0bb3f3517b0fa8cf300738c409019` (its SHA-256 is checked) |
| File | `ced-tiny-embedding.onnx`, 6,682,315 bytes |
| SHA-256 | `3917c2fafedac1c9903817ddf025c0f289a4422f3aaa5594790d570ab7067235` (the same byte for byte every run with `requirements.txt`) |
| Input | `waveform`: N × samples, 16 kHz mono, float −1…1, any length (the app uses 2 s) |
| Outputs | `logits`: N × 527, the AudioSet classes' scores — **already probabilities** (the graph ends in a sigmoid; unchanged from the original); `embedding`: N × 576, the encoder's tokens (the input of its last layer) pooled three ways: mean, standard deviation and maximum over the tokens |

Only the `embedding` output and the nodes that make it are new: the weights and the class scores
are the original's (`make_model.py` checks that the scores match). The registry entry pins this
file; it is never changed once released — a new conversion is a new id.

Licence: Apache-2.0, like the original (its copyright and licence go with the file, see the model
card below). Trained by its authors on AudioSet: Google's labels are CC BY 4.0, the sound is
YouTube videos that are not licensed for reuse — accepted for VisionState (SOUND_PLAN.md §5).

## Making it

```bash
cd tools/soundmodel
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python make_model.py out
```

It prints the path, the SHA-256 and the size; they must be the ones above. For tests and local
runs, copy it into the backend's models folder under the registry's file name:

```bash
cp out/ced-tiny-embedding.onnx ../../visionstate/backend/models/ced-tiny.onnx
```

## Publishing it (once, by the maintainer)

The app downloads the model from Hugging Face, pinned by revision, like the wheel reader
(`oleost/visionstate-wheel-reader`). Until it is published, `sounds.json` has `"url": ""`: the app
says so on the sensor ("not published yet"), `python -m visionstate.backbones --all` skips it, and
the tests that need it are skipped in CI.

1. Make the file (above) and check the SHA-256.
2. Create the model repository `oleost/visionstate-sound` on Hugging Face (public, licence
   Apache-2.0) and upload `ced-tiny-embedding.onnx` with a `README.md` made from the model card below.
3. Take the commit hash of that upload (the repository's *Files* tab, or
   `huggingface_hub.HfApi().model_info("oleost/visionstate-sound").sha`).
4. In `visionstate/backend/visionstate/sounds.json`, set `url` to
   `https://huggingface.co/oleost/visionstate-sound/resolve/<commit>/ced-tiny-embedding.onnx`
   (`sha256` and `size` stay as they are).
5. Check it downloads: `python -m visionstate.backbones /tmp/m --all` must print the file, and
   `sha256sum /tmp/m/ced-tiny.onnx` the SHA-256 above. Then the backend tests, which now run the
   sound tests in CI too (add `sounds.json` to the models cache key in `.github/workflows/ci.yml`
   if it is not there).

## Model card (README.md of the Hugging Face repository)

```markdown
---
license: apache-2.0
base_model: mispeech/ced-tiny
tags: [audio-classification, onnx, audioset]
---

# CED-Tiny with an embedding output (VisionState)

The ONNX file of [mispeech/ced-tiny](https://huggingface.co/mispeech/ced-tiny) (revision
ace276d29dd0bb3f3517b0fa8cf300738c409019) with one output added, `embedding`: the encoder's
tokens pooled (mean, standard deviation, maximum; N × 576). The weights and the class scores
(`logits`, N × 527 AudioSet classes, already probabilities) are the original's. Input:
`waveform`, N × samples of 16 kHz mono audio.

Made by `tools/soundmodel/make_model.py` in https://github.com/oleost/VisionState for its sound
sensors, which recognise sounds such as sirens and smoke alarms and learn a household's own sounds
from the embedding.

CED: Xian Li, Nian Shao, Xiaofei Li, "CED: Consistent ensemble distillation for audio tagging",
ICASSP 2024 (https://arxiv.org/abs/2308.11957). Licence: Apache-2.0, as the original. Trained by
its authors on AudioSet.
```
