# Scope & Design Decisions

> Status: **v0.3 — 0.1.0 implemented** — agreed decisions from the scoping discussion (2026-09-28).
> Project: **VisionState** · Licence: Apache-2.0 · Repository: `github.com/oleost/VisionState`

## 1. Vision

A Home Assistant app (formerly "add-on") that lets anyone teach a local AI to recognise
**states** in camera images — e.g. a garage door being `open`, `closed` or `partial` —
and exposes the result as a regular Home Assistant sensor. Training happens by clicking
a snapshot and choosing the correct state, or by bulk-uploading images/video. Everything
runs locally, on any CPU, with a polished UI inside Home Assistant.

## 2. Goals / Non-goals

**Goals**
- Generic: works with any camera Home Assistant knows about, plus direct URLs.
- Runs on Intel/AMD (amd64) and Raspberry Pi 4/5 and other aarch64 boards, CPU only.
- Very low effort to train: good results with ~10–30 images per state.
- Beautiful, simple UI via HA Ingress.
- Public, well-documented, configurable, multi-arch builds.
- Full import/export of sensors, datasets and trained models.

**Non-goals (v1)**
- Object detection / bounding boxes (Frigate already does this).
- Multi-label sensors (the data model is designed to allow this later, see §10).
- Cloud training or cloud inference.
- armv7 / i386 (deprecated by Home Assistant).

## 3. Terminology

| Term | Meaning |
|---|---|
| **Sensor** | One thing to recognise, e.g. "Garage door". Becomes one HA entity. |
| **State** | One possible value of a sensor, e.g. `open`. |
| **Source** | Where images come from: HA camera entity, RTSP/HTTP URL, or upload. |
| **ROI** | Region of interest — rectangle cropped from the frame before classification. |
| **Sample** | A stored image (cropped + original reference) with a state label. |
| **Backbone** | Pre-trained vision model that turns an image into an embedding vector. |
| **Head** | Small per-sensor classifier trained on embeddings. |

## 4. Platform & deployment

- Home Assistant app with **Ingress** (sidebar panel, HA handles authentication).
- Architectures: `amd64`, `aarch64`. Built with GitHub Actions, published to GHCR.
- Also runnable as a plain Docker container (for development and non-HA-OS users),
  configured via environment variables and a long-lived HA token.
- MQTT broker discovered automatically through the Supervisor services API
  (`services: mqtt:want`), manual override possible.

## 5. ML approach

**Embedding + lightweight head** (transfer learning without fine-tuning):

1. Grab frame → crop ROI → resize (letterbox) → normalise.
2. Backbone (ONNX Runtime) → embedding vector.
3. Per-sensor head (scikit-learn logistic regression; kNN as fallback for very few samples)
   → probability per state.
4. Post-processing → published state.

Why: training takes seconds on a Raspberry Pi, works with few samples, and every new
label can retrain immediately. Embeddings are cached per sample, so retraining never
re-runs the backbone.

**Backbones (selectable per installation, pluggable):**

| ID | Model | Use |
|---|---|---|
| `dinov2-small-q8` | DINOv2 ViT-S/14, 8-bit quantised (bundled) | Default everywhere |
| `dinov2-small` | DINOv2 ViT-S/14, fp32 (downloaded on demand) | Optional, slightly more accurate |

> Implementation note (0.1.0): MobileNet was dropped because the available ONNX exports only
> expose classification logits; the 8-bit DINOv2 runs ~25 ms per frame on a desktop CPU and is
> fast enough for Raspberry Pi 4/5. The head is logistic regression with `C=30` on
> L2-normalised features (clear frames ≈85–95 %, ambiguous ≈50 %).

- The default backbone is bundled in the image (works offline); others are downloaded on
  demand to `/data/models` with SHA-256 verification.
- Switching backbone re-computes embeddings in the background from stored samples.
- **Execution providers** behind one `InferenceBackend` interface: CPU (default),
  OpenVINO (Intel iGPU/CPU, bonus), Coral Edge TPU and CUDA (bonus, later).
- Training-time augmentation: not in 0.1.0 (DINOv2 features are robust enough so far);
  planned as light brightness/contrast/noise jitter only.
- Optional "fine-tune" mode is explicitly out of scope for v1 but the pipeline must allow it.

## 6. Image sources

| Source | Details |
|---|---|
| HA camera entity (default) | `camera_proxy` via Supervisor API. Covers Frigate, ESP32-CAM, Reolink, generic, etc. |
| RTSP / HTTP snapshot URL | Direct, via FFmpeg (RTSP) or HTTP GET (JPEG). |
| Upload | Images (JPG/PNG/WebP), ZIP archives, video files (MP4/MKV/MOV). |
| Frigate events (v3) | Trigger classification on Frigate MQTT events for a camera. |

Video upload: extract one frame every *N* seconds (configurable), drop near-duplicates
(perceptual hash), then present the frames for bulk labelling.

A single camera can feed multiple sensors, each with its own ROI.

## 7. Inference & post-processing

- **Triggers (0.2.0):** per-sensor interval as a safety net; state changes of chosen HA
  entities (WebSocket API) and optional region change detection start a *burst* of
  faster checks; plus the MQTT `classify_now` button. Stored in `sensor.triggers` (JSON),
  merged with `settings.TRIGGER_DEFAULTS`.
- **Unknown state:** if top probability < threshold (default 0.70) → `unknown`.
- **Debounce:** state changes only after *N* consecutive agreeing predictions (default 2).
- Both configurable per sensor.
- Camera unavailable → entity becomes `unavailable` (not a false state).

## 8. Home Assistant integration (MQTT Discovery)

One HA **device** per sensor, with entities:

| Entity | Type | Purpose |
|---|---|---|
| State | `sensor` (`device_class: enum`, `options` = states + `unknown`) | The main result |
| Confidence | `sensor` (%) | Top probability |
| Last frame | `image` | The frame (ROI) that was classified |
| Classify now | `button` | Trigger from automations |
| Enabled | `switch` | Pause/resume a sensor |

Attributes on the state entity: per-state probabilities, last update, model version.
Availability topic per app instance (LWT). Removing a sensor removes its entities.

## 9. User interface (Ingress)

Principle: **easy by default, details on demand.**

1. **Dashboard** — cards per sensor: live thumbnail, current state, confidence, sparkline
   of recent states, health (untrained / needs data / good).
2. **Create sensor wizard** — name → pick source → draw ROI on a live frame → define
   states → start capturing.
3. **Label (capture)** — live view with one big button per state; keyboard shortcuts
   (1–9); shows current prediction so you only click when it's wrong.
4. **Bulk upload** — drag & drop images/ZIP/video → pick a state for all, or label in a
   grid with multi-select.
5. **Review queue (active learning)** — images where the model was unsure or states
   flipped, plus a small random sample; one click to confirm or correct.
6. **Dataset gallery** — per-state grid, move/delete/relabel, filter by date/confidence.
7. **Quality** — cross-validated accuracy, confusion matrix, samples per state,
   warnings (imbalance, too few samples, only daytime images, etc.).
8. **History** — timeline of published states with frames; "this was wrong" → relabel.
9. **Settings** — backbone, execution provider, MQTT, retention, import/export.

Follows HA light/dark theme; responsive (works in the HA mobile app).

## 10. Data model & extensibility

- SQLite; schema version tracked with `PRAGMA user_version` (migrations added when the schema first changes).
- `sensor.kind` field: `single_state` in v1; reserved for `multi_label`, `binary`, `count`.
- Labels stored in a separate `sample_label` table (many-to-many), even though v1
  enforces one label per sample → multi-label needs no schema change.
- Plugin interfaces: `Source`, `Backbone`, `InferenceBackend`, `Trigger`.
- Versioned REST API (`/api/v1`) used by the frontend — also usable by others.

## 11. Storage

| What | Where | In HA backup |
|---|---|---|
| Config, DB, embeddings, trained heads | `/data` | Yes |
| Downloaded backbone models | `/data/models` | Excluded (re-downloadable) |
| Sample images | `/media/<slug>/samples/<sensor>/` | Per user's media backup choice |
| Prediction frames (history) | `/media/<slug>/history/` | Retention policy (default 7 days, uncertain frames kept longer) |

## 12. Import / export

- **Sensor bundle** (`.zip`): `manifest.json` (schema version, app version, backbone),
  sensor config, states, ROI, samples + labels, optionally the trained head.
- **Full export**: all sensors + settings.
- Import modes: *create new*, *merge into existing* (map states), *replace*.
- If the backbone differs, embeddings are recomputed on import.

## 13. Configuration (app options)

`log_level`, `default_backbone`, `execution_provider`, `media_path`, `mqtt` (auto/manual),
`history_retention_days`, `max_concurrent_inferences`. Everything sensor-specific lives in
the UI, not in app options.

## 14. Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy + Alembic, asyncio scheduler |
| Inference | ONNX Runtime (+ optional OpenVINO EP), NumPy, Pillow, FFmpeg |
| Training | scikit-learn |
| MQTT | aiomqtt |
| Frontend | Svelte 5 + Vite + TypeScript, plain CSS with design tokens (`tokens.css`), hash router (Ingress-safe) |
| Packaging | HA app repo layout, multi-arch Docker, GitHub Actions → GHCR |
| Quality | pytest, ruff, mypy; Vitest + Playwright; pre-commit |
| Docs | MkDocs Material (GitHub Pages) + app `DOCS.md` |

## 15. Proposed repo layout

```
/                       repository.yaml (HA app repository)
/visionstate            config.yaml, Dockerfile, DOCS.md, CHANGELOG.md, icon/logo
/visionstate/backend    Python package + tests (inside the app dir: HA builds from it)
/visionstate/frontend   Svelte app
/docs                   MkDocs site (incl. this file)
/.github/workflows      lint, test, multi-arch build, release
```

## 16. Roadmap

| Milestone | Content |
|---|---|
| **M0 – Design** | This scope, UI mockups |
| **M1 – MVP** ✅ | HA camera source, create sensor + ROI, capture & label, train, MQTT sensor, CPU backbone, amd64+aarch64 builds |
| **M2 – Training UX** ✅ | Bulk upload (images/ZIP/video), review queue, gallery, quality page, history |
| **M3 – Portability** ✅ (partly) | Import/export (create-new mode), backbone switching, RTSP/HTTP sources, retention. Merge/replace import still open. |
| **M3.5 – Triggers** ✅ (0.2.0) | HA entity triggers (WebSocket `subscribe_trigger`), region change detection, follow-up bursts; all per sensor, defaults in `settings.TRIGGER_DEFAULTS` |
| **Review rules** ✅ (0.3.0) | Global review rules (DB setting) with per-sensor overrides (`sensor.review`), defaults in `settings.REVIEW_DEFAULTS` |
| **M4 – Acceleration** | OpenVINO, Coral, Frigate snapshots of events |
| **M5 – Release** | Docs site, screenshots, v1.0 public release |

## 17. Identity

| Item | Value |
|---|---|
| Name | VisionState |
| App slug | `visionstate` |
| Repository | `github.com/oleost/VisionState` |
| Images | `ghcr.io/oleost/visionstate-{arch}` |
| MQTT discovery prefix / node id | `homeassistant` / `visionstate` |
| Licence | Apache-2.0 |
