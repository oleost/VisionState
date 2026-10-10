# Architecture

A map of the code: what runs where, how one check flows through it, and which files to touch
for common changes. What VisionState does and why is in [`SCOPE.md`](SCOPE.md); how to work on
it is in [`CLAUDE.md`](../CLAUDE.md).

## The process

One Python process (`python -m visionstate`) serves the API and the built UI through Home
Assistant Ingress, runs every sensor and talks to MQTT and Home Assistant. It is all asyncio on
one event loop:

- **I/O** (camera, MQTT, Home Assistant) is async.
- **Database and file work** runs in worker threads (`asyncio.to_thread`): SQLite with WAL, one
  short session per unit of work (`Database.session()`).
- **AI inference and training** runs in worker threads too, at most
  `RUNTIME["max_concurrent_inferences"]` at a time (the semaphore `Runtime._sem`).
- **Long-running loops** (one per sensor, MQTT, the Home Assistant event stream, clean-up) catch
  every exception except `CancelledError`, log it once and go on.

There is one `Runtime` (`engine/runtime.py`), made in `main.py` at start-up and reached from the
API as `request.app.state.runtime`.

## Backend map (`visionstate/backend/visionstate/`)

| File | What it does |
|---|---|
| `__main__.py`, `main.py` | Entry point (logging, uvicorn), FastAPI app, Ingress-only guard, static UI |
| `settings.py` | **Every** default, limit and tunable, the app options (`Settings`), the `merge_*` helpers that fill a stored setting with defaults |
| `db.py` | SQLAlchemy tables and the schema migrations (`MIGRATIONS`, `SCHEMA_VERSION`) |
| `storage.py` | Image files on disk: training samples, history frames, thumbnails |
| `engine/` | The runtime — see below |
| `api/` | The REST API (`/api/v1/…`), one file per topic: `sensors.py` (CRUD, quality, export), `history.py` (the history: one filter for the list, its count and facets; history frames), `samples.py` (training images), `teach.py` (taught boxes), `cameras.py` (camera and entity lists, the light, previews), `review.py` (review queue and answers), `imports.py` (import), `system.py` (UI config, status, AI models, storage, review rules, review reminder), `common.py` (input models and views shared by them) |
| `sources.py` | Camera sources: Home Assistant camera, HTTP snapshot, RTSP; address checks and size limits; the Home Assistant REST client (states, services, notify services, the app's page) |
| `mqtt.py` | MQTT topics, Home Assistant discovery messages, the bridge (connect, publish, commands) |
| `ha_events.py` | Home Assistant WebSocket: state changes of trigger entities, entity registry |
| `lights.py` | A sensor's light: switched on for checks and while someone looks, shared by both |
| `backbones.py`, `detectors.py`, `readers.py` | The three AI models and their registries (`*.json`): embedding backbone (state sensors), object detector, number reader (and the wheel reader for mechanical counters, `readers.WheelReader`; trained with `tools/wheelreader`) |
| `classifier.py` | The small per-sensor classifier ("head") on top of the backbone |
| `teach.py` | Object sensors: comparing detected boxes with the boxes the user taught |
| `imaging.py` | Decoding, regions (ROI), crops, change signatures, drawing boxes |
| `uploads.py`, `bundle.py` | Uploaded files (images, ZIP, video) → frames; sensor export/import bundles |
| `redact.py` | Masks credentials in URLs, messages and log lines |

### `engine/`

`Runtime` is put together from one mixin per part. They all build on `RuntimeBase`
(`base.py`: the shared state and the few methods every part uses), and each part inherits the
parts it uses — its class line says what it depends on:

```
Runtime ── ChecksMixin ──┬─ ObjectChecksMixin ── TeachingMixin ── ModelsMixin ── TrainingMixin
        │                │                    └─ PublishingMixin
        │                └─ ReadingChecksMixin ── ModelsMixin, PublishingMixin
        ├─ HistoryMixin ── PublishingMixin                (all of them on RuntimeBase)
        └─ RemindersMixin ── PublishingMixin
```

| File | Part |
|---|---|
| `base.py` | `RuntimeBase`: the shared state (`__init__`), loading a sensor, its live state, waking it |
| `runtime.py` | Lifecycle (start/stop), sensors coming and going, waking a sensor, triggers |
| `state.py` | `SensorConfig` (a snapshot of a sensor row) and `LiveState` (everything a sensor knows between checks) |
| `logic.py` | Pure decisions, no I/O: debouncing object tracks, review, triggers, when to check next |
| `checks.py` | Each sensor's loop, change detection (probe), the light, `run_once` and the state sensors' check |
| `objects.py`, `reading.py` | The check of an object sensor and of a reading sensor |
| `models.py` | Loading and switching the three AI models; `detect_objects`, `read_number` |
| `teaching.py` | Taught boxes of object sensors (index, embeddings) |
| `training.py` | Training the state sensors' heads; embeddings of samples |
| `publishing.py` | Discovery and values to Home Assistant, the review queue entity, commands from it |
| `history.py` | History clean-up by age and size, disk use |
| `reminders.py` | The review reminder: a notification in Home Assistant (and a push) when frames waited long |

## How one check flows

```
sensor loop (checks._sensor_loop)          woken by: its interval, a trigger entity (runtime),
  │                                        change detection (checks.probe), the UI, an MQTT command
  ├─ load_sensor → SensorConfig            a fresh snapshot of the row, every pass
  ├─ light on? (checks._light_before)      lights.py, frames thrown away while it warms up
  ├─ grab a frame (sources.FrameGrabber)   → LiveState.frames (the UI labels these)
  ├─ decode (imaging.decode)
  └─ by kind:
      state sensor   → checks._run_states  backbone embedding → head → probabilities
      object sensor  → objects._run_objects detector → taught boxes (teaching) → tracks (logic)
      reading sensor → reading._run_reading reader → parse → plausibility → debounce
        │
        ├─ publish (publishing._send → mqtt.MqttBridge.publish)   state, attributes, image
        └─ history: a Prediction row + frame, when the value changed or the frame goes to review
```

A camera problem makes the sensor *unavailable* (its availability topic `offline`); any other
error is kept in `LiveState.error` and shown in the UI.

## Data

- **SQLite** (`<data>/visionstate.db`): `sensor` (+ `state`), `sample` (+ `sample_label`,
  `embedding` cache), `prediction` (the history and the review queue), `reading_stat`,
  `model_info` (per sensor), `setting` (global settings as JSON).
- **Files** (`<media>/`): `samples/<sensor>/`, `history/<sensor>/`, thumbnails; `<data>/heads/`
  (trained heads), `<data>/models/` (downloaded models).
- **Settings of a sensor** that have many fields (`triggers`, `objects`, `reading`, `review`) are
  stored as JSON and always read through `settings.merge_*`, so a field added later gets its
  default for existing sensors without a migration.

## Frontend map (`visionstate/frontend/src/`)

| Where | What |
|---|---|
| `lib/api.ts` | The only place that builds API URLs; one function per endpoint |
| `lib/types.ts` | Types of what the API returns |
| `lib/app.svelte.ts` | Shared state: config from `GET /api/v1/config`, status polling |
| `lib/router.svelte.ts` | Hash routes (`paths`), a page's query (`route.query`, `setQuery`) |
| `lib/history.ts` | The history filter: from and to the page URL, and as the API takes it |
| `lib/ui.ts` | UI constants (tabs per sensor kind, wizard texts); `tokens.css` colours, type, spacing |
| `pages/` | Dashboard, the new sensor wizard (`NewSensor.svelte`; its Detect step per kind in `pages/wizard/`), Review, History, Settings, and `SensorPage` with one file per tab in `pages/sensor/` (its History tab is `HistoryView`) |
| `lib/components/history/` | `HistoryView` (filters, list, paging, new rows) shared by the History page and the History tabs, and one row component per sensor kind |
| `lib/components/` | Building blocks (region editor, frames with boxes, editors for triggers, reading, states…) |

## Recipes

**A new setting of a sensor** (say, for reading sensors):
1. Default in `settings.READING_DEFAULTS` (limits in `READING_LIMITS`); `merge_reading` fills it in.
2. Accept it in the input model (`api/common.py`, `ReadingIn`).
3. Use it in the engine (`engine/reading.py`).
4. UI: the type in `lib/types.ts`, the field in its editor (`lib/components/ReadingParams.svelte`);
   defaults and limits come from `/config`, never hard-coded.
5. Tests: backend (`tests/test_reading.py`), and an e2e step when it changes the UI.

**A new Home Assistant entity**: its topic in `mqtt.topics` / `object_topics`, its discovery
config in the builder of its kind in `mqtt.py` (`_state_entities`, `_object_entities`,
`_reading_entities`; every sensor's: `discovery_messages`), the value from the engine through `self._send`, removal in
`MqttBridge.remove_discovery`. Unique IDs and topics are never changed once released (entity IDs
in Home Assistant hang on them).

**A new column**: the field in `db.py`, a new `SCHEMA_VERSION` with its entry in `MIGRATIONS`, and
a test that an old database is upgraded (see `tests/test_entity_ids.py`).

**A new API endpoint**: in the `api/` file of its topic, input as a pydantic model, its function
in `lib/api.ts`, its type in `lib/types.ts`.

**A new AI model**: a new entry in the registry JSON (never change a released one), checked
against real inputs in the tests.

**A new page or tab**: the route in `router.svelte.ts` (tab: `TABS_BY_KIND` in `ui.ts`), the page
in `pages/`, and an e2e test in `e2e/ui.spec.ts` on all three devices.
