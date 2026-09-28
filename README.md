# VisionState

> 🚧 **Work in progress** — first test release (0.1.0). Expect rough edges; feedback welcome.

Teach a local AI to recognise **states** in your camera images — a garage door that is
`open`, `closed` or `partial`, a gate, a light, a parking spot — and use the result as a
regular Home Assistant sensor.

- 📸 **Train by clicking** — look at the live image, press the matching state. Done.
- 📦 **Bulk upload** — images, ZIP archives or video, with suggested labels.
- 🧠 **Runs locally on any CPU** — Intel, AMD and Raspberry Pi (amd64 / aarch64).
- 🏠 **Native Home Assistant** — app with Ingress UI, sensors via MQTT Discovery.
- 🎥 **Any camera** — every `camera.*` entity (Frigate, ESP32-CAM, Reolink, …) or RTSP/HTTP.
- 🔁 **Import / export** — move sensors, datasets and models between installations.

## Install (Home Assistant OS / Supervised)

1. **Settings → Apps** (called **Add-ons** in older versions) → **App store** → ⋮ (top right) → **Repositories**.
2. Add `https://github.com/oleost/VisionState` and close the dialog.
3. Find **VisionState** in the store, click **Install**. The first install builds the app on
   your machine and can take 5–15 minutes (longer on a Raspberry Pi).
4. Make sure the **Mosquitto broker** app and **MQTT** integration are installed.
5. Start VisionState and open it from the sidebar.

Usage is described in [`visionstate/DOCS.md`](visionstate/DOCS.md) (also shown on the app's
Documentation tab).

## How it works

A pre-trained vision model ([DINOv2](https://github.com/facebookresearch/dinov2), ONNX) turns
the region you draw into a feature vector. A tiny classifier per sensor is trained on your
labelled images in about a second, so every click improves the sensor immediately. Results
are debounced and published to Home Assistant through MQTT discovery.

## Development

```
visionstate/            Home Assistant app (config.yaml, Dockerfile, docs)
  backend/              Python 3.12 · FastAPI · ONNX Runtime · scikit-learn
  frontend/             Svelte 5 · Vite · TypeScript
docs/SCOPE.md           Scope, design decisions and roadmap
```

Backend:

```bash
cd visionstate/backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m visionstate.backbones models            # download the bundled model once
pytest
VISIONSTATE_DATA=./dev/data VISIONSTATE_MEDIA=./dev/media VISIONSTATE_BUNDLED_MODELS=./models \
  HA_URL=http://homeassistant.local:8123 HA_TOKEN=<long-lived token> \
  VISIONSTATE_MQTT_HOST=<broker> python -m visionstate
```

Frontend (proxies `/api` to the backend on port 8099):

```bash
cd visionstate/frontend
npm install
npm run dev
```

## Licence

[Apache-2.0](LICENSE). The DINOv2 model weights are released by Meta under Apache-2.0.
