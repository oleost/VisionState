# Scope & Design Decisions

> Describes VisionState **as built** (beta 0.4.1b4 / stable 0.4.0, 2026-09-30) and the open
> ideas. Update it whenever a decision changes.
> Project: **VisionState** · Licence: Apache-2.0 · Repository: `github.com/oleost/VisionState`

## 1. Vision

A Home Assistant app (formerly "add-on") that lets anyone teach a local AI to recognise
**states** in camera images — e.g. a garage door being `open`, `closed` or `partial` —
and exposes the result as a regular Home Assistant sensor. Training happens by clicking
the matching state on a live image, or by bulk-uploading images/video. Everything runs
locally, on any CPU, with a polished UI inside Home Assistant.

## 2. Goals / Non-goals

**Goals**
- Generic: works with any camera Home Assistant knows about, plus direct URLs.
- Runs on Intel/AMD (amd64) and Raspberry Pi 4/5 and other aarch64 boards, CPU only.
- Very low effort to train: good results with ~10–30 images per state.
- Simple, polished UI via HA Ingress.
- Public, well-documented, configurable, multi-arch builds.
- Import/export of sensors with their training images.

**Non-goals (for now)**
- Object detection / tracking of moving objects with bounding boxes.
- Multi-label sensors (the data model allows this later, see §10).
- Reading numbers (e.g. meters) — discussed, see §16 open ideas.
- Cloud training or cloud inference; telemetry of any kind.
- armv7 / i386 (deprecated by Home Assistant).

## 3. Terminology

| Term | Meaning |
|---|---|
| **Sensor** | One thing to recognise, e.g. "Garage door". Becomes one HA device with entities. |
| **State** | One possible value of a sensor. Has a display name ("Open") and a key (`open`, derived from the name, used in HA). |
| **Source** | Where images come from: HA camera entity, HTTP snapshot URL, RTSP stream, or upload. |
| **ROI** | Region of interest — a rectangle, or a polygon (up to `ROI_MAX_POINTS` corners), cropped from the frame before classification; outside a polygon is filled with a neutral colour. |
| **Sample** | A stored full frame with a state label; the ROI is applied when embedding, so moving the ROI never loses data. |
| **Backbone** | Pre-trained vision model that turns an image into an embedding vector. |
| **Head** | Small per-sensor classifier trained on embeddings. |
| **Trigger** | Something that makes a sensor check its camera besides the regular interval. |

## 4. Platform & deployment

- Home Assistant app with **Ingress** (sidebar panel, HA handles authentication); requests
  from anything other than the Ingress proxy are refused.
- Architectures: `amd64`, `aarch64`. Prebuilt images on GHCR, pulled by Home Assistant.
- Two release channels (see §18): stable (`main`) and beta (`beta` branch, `#beta` repo URL).
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
| `dinov2-small-q8` | DINOv2 ViT-S/14, 8-bit quantised (bundled) | Default everywhere, ~25 ms per frame on a desktop CPU |
| `dinov2-small` | DINOv2 ViT-S/14, fp32 (downloaded on demand, SHA-256 checked) | Optional, slightly more accurate |

- MobileNet was dropped: the available ONNX exports only expose classification logits.
- Switching backbone retrains every sensor from its stored samples.
- Execution provider: any ONNX Runtime provider present in the image (CPU today). GPU/Coral
  were assessed and deliberately not pursued (little gain for this workload; Coral cannot run
  the transformer model and is often in use by other software).
- Heads trained by another scikit-learn version, or for another backbone, are retrained
  automatically at startup.
- **Quality check:** after each training, cross-validated predictions give the accuracy, the
  confusion matrix and a list of *possibly mislabelled* samples (label ≠ out-of-fold prediction).
- Not implemented: training-time augmentation (not needed so far), fine-tuning.

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

## 7. When and how a sensor decides

- **Triggers** (per sensor, `sensor.triggers`, defaults in `settings.TRIGGER_DEFAULTS`):
  - Regular interval (default 10 s) — the safety net.
  - State changes of chosen HA entities (WebSocket `subscribe_trigger`, attribute-only changes
    and `unavailable`/`unknown` ignored).
  - Optional region change detection: a small greyscale copy of the ROI is compared every
    *N* seconds; the AI only runs when the difference exceeds a threshold.
  - Every trigger starts a *burst* (default every 2 s for 30 s) to catch the final state.
  - The MQTT `classify` button checks once.
- **Unknown state:** top probability below the threshold (default 70 %) → `unknown`.
- **Debounce:** the state changes only after *N* consecutive agreeing results (default 2).
- Camera unavailable → entities become `unavailable` (not a false state).
- **Review queue** (global rules in the DB, per-sensor overrides in `sensor.review`, defaults in
  `settings.REVIEW_DEFAULTS`): frames below 85 % (never below the sensor threshold), frames
  where the state flip-flops (3 changes in 10 min) and optional random spot checks (default 0 %);
  at most one per sensor per 5 minutes.

## 8. Home Assistant integration (MQTT Discovery)

One HA **device** per sensor:

| Entity | Type | Purpose |
|---|---|---|
| `sensor.visionstate_<slug>` | `sensor` (`device_class: enum`, options = state keys + `unknown`) | The result |
| `…_confidence` | `sensor` (%) | Top probability |
| `image.…_frame` | `image` | The ROI that was classified |
| `button.…_classify` | `button` | Check now (automations) |
| `switch.…_enabled` | `switch` | Pause / resume |

Plus one app-wide **VisionState** device with `sensor.visionstate_review_queue` (frames waiting
for review, per-sensor breakdown as attribute).

Attributes on the state entity: `probabilities`, `top_state`, `last_update`, `trained`,
`last_trigger`. Availability: app-wide LWT plus per-sensor camera availability. Entity ids are
set with `default_entity_id` (requires Home Assistant 2025.10 or newer). Removing a sensor
removes its entities.

## 9. User interface (Ingress)

Principle: **easy by default, details on demand.** Dark theme, responsive.

1. **Dashboard** — cards per sensor: live thumbnail, state, confidence, 24 h timeline, health.
2. **New sensor wizard** — camera → region → states → optional triggers.
3. **Label** — live view, one button per state (keys 1–9), current prediction, day/night
   coverage, last trigger, undo.
4. **Upload** — drag & drop images/ZIP/video; label in a grid or accept all suggestions.
5. **Dataset** — filter by state/unlabelled, relabel, unlabel, delete.
6. **Quality** — accuracy, confusion matrix, samples per state (day/night), suggestions,
   *possibly mislabelled* images with keep / change / delete.
7. **History** — published state changes and flagged frames; "add as" to the dataset.
8. **Sensor settings** — name, source, region, states, when to check, output, review overrides,
   export, delete.
9. **Review** — the review queue across sensors, keyboard driven.
10. **Settings** — status, AI model and execution provider, global review rules, import.

## 10. Data model & extensibility

- SQLite; schema version in `PRAGMA user_version` with additive migrations (`db.MIGRATIONS`, currently v4).
- `sensor.kind`: `single_state` today; reserved for `multi_label`, `binary`, `count`.
- Labels live in a separate `sample_label` table (many-to-many) → multi-label needs no schema change.
- Extension points: backbone registry (`backbones.json`), `sources.SOURCE_TYPES`, trigger settings.
- Versioned REST API (`/api/v1`) used by the frontend; `GET /api/v1/config` exposes every
  default and limit so the UI never hard-codes them.

## 11. Storage

| What | Where | In HA backup |
|---|---|---|
| Settings, DB, embeddings, trained heads | `/data` (per app) | Yes |
| Downloaded backbone models | `/data/models` | Excluded (re-downloadable) |
| Training images | `/media/visionstate/samples/<sensor>/` (beta: `/media/visionstate_beta`) | With the media folder |
| History frames | `/media/…/history/` | Retention (default 7 days; unreviewed flagged frames twice as long) |

## 12. Import / export

- **Sensor bundle** (`.zip`): `manifest.json` (schema, app version), sensor settings, states,
  ROI, triggers, review overrides, all samples with labels.
- Camera credentials are removed from exported URLs; the importer re-enters them.
- Import always creates a new sensor and retrains it; bundles are validated like API input.
- Not implemented: full export of all sensors + global settings, merge/replace import modes,
  exporting trained heads (retraining is faster than shipping them).

## 13. Configuration (app options)

`log_level`, `history_retention_days`, `discovery_prefix`, and optional `mqtt_host`,
`mqtt_port`, `mqtt_username`, `mqtt_password`. Everything else (AI model, review rules,
sensor settings) lives in the UI.

## 14. Tech stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.14 (image and CI), FastAPI, uvicorn, SQLAlchemy, asyncio |
| Inference | ONNX Runtime, NumPy, Pillow, PyAV (bundled FFmpeg) |
| Training | scikit-learn |
| MQTT / HA | aiomqtt, websockets, httpx |
| Frontend | Svelte 5 + Vite + TypeScript, plain CSS with design tokens (`tokens.css`), hash router (Ingress-safe), bundled fonts |
| Packaging | HA app repository, Docker (python:3.14-slim), GitHub Actions → GHCR |
| Quality | pytest (unit + integration with a fake camera/HA/MQTT), ruff, svelte-check, Playwright UI tests (desktop + phone with touch, against the real backend and `scripts/fake_camera.py`), image smoke test in CI, Dependabot (monthly, to `beta`) |
| Docs | `README.md`, `visionstate/DOCS.md` (shown in HA), `CHANGELOG.md`, this file |

## 15. Repo layout

```
/                       repository.yaml, README.md, CLAUDE.md
/visionstate            HA app: config.yaml, Dockerfile, DOCS.md, CHANGELOG.md, icon/logo
/visionstate/backend    Python package + tests (inside the app dir: the Dockerfile builds from it)
/visionstate/frontend   Svelte app
/scripts                channel.py (stable/beta config switch), fake_camera.py (test camera)
/docs                   SCOPE.md, promo/ (README screenshots and logos)
/.github                CI workflow, Dependabot
```

## 16. Roadmap

| Milestone | Content | Version |
|---|---|---|
| **MVP** ✅ | HA camera source, sensor + ROI, labelling, training, MQTT sensor, amd64+aarch64 | 0.1.0 |
| **Training UX** ✅ | Upload (images/ZIP/video), review queue, dataset, quality, history | 0.1.0 |
| **Triggers** ✅ | HA entity triggers, region change detection, bursts | 0.2.0 |
| **Hardening** ✅ | Credential redaction, import validation, upload limits | 0.2.1 |
| **Review rules** ✅ | Global rules with per-sensor overrides | 0.3.0 |
| **Prebuilt images** ✅ | GHCR images, releases | 0.3.1 |
| **Data quality** ✅ | Possibly mislabelled samples, review queue entity, wizard triggers step | 0.4.0 |
| **Maintenance** ✅ (beta) | Dependency updates, version-safe head reload, image smoke test, beta channel, Python 3.14, clean exit on stop | 0.4.1b1–b3 |
| **Region shapes & mobile** ✅ (beta) | Polygon regions, mobile layout fixes, Playwright UI tests in CI | 0.4.1b4 |

**Open ideas** (not scheduled): full export/import of everything; merge/replace import;
less MQTT/camera traffic (throttle frame publishing, reuse the engine's latest frame in the UI);
video de-duplication on the ROI instead of the full frame; light theme following Home
Assistant; reading numbers (meters) as a new sensor kind; issue templates.

## 17. Identity

| Item | Value |
|---|---|
| Name | VisionState (beta: "VisionState (beta)") |
| App slug | `visionstate` |
| Repository | `github.com/oleost/VisionState` (`#beta` for the beta channel) |
| Images | `ghcr.io/oleost/visionstate-{amd64,aarch64}:X.Y.Z` (+`latest`) / `:X.Y.ZbN` (+`beta`) |
| MQTT discovery prefix / node id | `homeassistant` / `visionstate` |
| Licence | Apache-2.0 |

## 18. Release channels

- **beta** branch: all changes land here first; versions `X.Y.ZbN`, GitHub pre-releases, own
  media folder. The maintainer runs it permanently.
- **main** branch: stable; changes only by promoting a tested beta through a PR
  (branch-protected, CI required).
- CI builds, tests and smoke-tests the image on both architectures (start, MQTT, discovery, clean
  `docker stop` with exit code 0) before publishing, refuses
  tags that do not match `config.yaml`, and refuses the wrong channel on a branch.
- Step-by-step procedures are in `CLAUDE.md`.
