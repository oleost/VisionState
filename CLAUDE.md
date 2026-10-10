# CLAUDE.md

VisionState — a Home Assistant app that turns camera images into sensors, all locally:
**states** the user teaches it (garage door open/closed; embedding model + small per-sensor
classifier), **objects** found by a pretrained detector (people, cars, animals) and **readings**
(a number on a display or the wheels of a meter). This file is the contributor guide for people
and AI assistants.

- **Language:** everything in the repository (code, comments, UI text, docs, commit messages) is
  written in English.
- Public repository `github.com/oleost/VisionState`, Apache-2.0, owned by its maintainer (oleost).
  The maintainer (and Claude working for them) commits straight to `beta`; everyone else opens a
  pull request against `beta`. Branches and releases: [`docs/RELEASING.md`](docs/RELEASING.md).

## Where things are written down

Every fact has **one** home; other files link to it instead of repeating it.

| What | Home |
|---|---|
| What the app does for a user, how to use it, the defaults a user sees | [`visionstate/DOCS.md`](visionstate/DOCS.md) (the app's *Documentation* tab) |
| The pitch, install, screenshots | [`README.md`](README.md) (and the short store text `visionstate/README.md`) |
| Design decisions and why, non-goals, roadmap, open ideas | [`docs/SCOPE.md`](docs/SCOPE.md) |
| Where the code is, how a check flows, data on disk, recipes for common changes | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Values of defaults, limits and tunables | `settings.py` and the model registries — docs name the setting, not its value (DOCS.md may state what the user sees) |
| Branches, channels, CI, release steps | [`docs/RELEASING.md`](docs/RELEASING.md) |
| How to work: principles, checks, testing, pitfalls | this file |
| The wheel reader model: data, licences, results, how to train it | [`tools/wheelreader/README.md`](tools/wheelreader/README.md) |
| The plan for better counter readings, and its status | [`docs/WHEEL_READER_PLAN.md`](docs/WHEEL_READER_PLAN.md) |
| Face recognition: models, licences, privacy and the plan (not built) | [`docs/FACE_RECOGNITION_PLAN.md`](docs/FACE_RECOGNITION_PLAN.md) |
| What changed in a version | `visionstate/CHANGELOG.md` (written at release, never edited afterwards) |
| Test image sources | `visionstate/backend/tests/assets/README.md` |

- Update the home of a fact **in the same change** as the code; a change that makes a doc wrong is
  not finished.
- No "as of version X" or "currently vN" status lines outside the CHANGELOG and the roadmap: they go
  stale. Say what is, and let git say when.
- `tests/test_docs.py` (backend, runs in CI) checks every Markdown link, every repository path and
  every `module.name` / `settings.X["key"]` in backticks against the code — a rename that leaves a
  doc behind fails the tests.

## Principles (do not break these)

- **No telemetry, nothing "phones home".** Download counts on GHCR are the only usage signal.
- **Only reachable through Home Assistant Ingress** (requests from other addresses are refused);
  outside Home Assistant the app has no login — documented, keep it that way.
- **Camera credentials never leak:** `redact.py` masks them in logs, errors and exports.
- **No hard-coded defaults** outside `settings.py` / the registries; the UI reads them from
  `GET /api/v1/config`.
- Home Assistant state values stay lower-case keys (`open`, `car_parked`); unique IDs and topics
  never change once released.
- Marketing text (README, release notes) promotes VisionState on its own merits — no comparisons
  with other products.

## Conventions

- `visionstate/` is the Home Assistant app (build context of the Dockerfile); the map of the code
  is [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — keep it current when the structure changes.
- Sensor kinds (`settings.SENSOR_KINDS`): `single_state`, `objects`, `reading`. A new feature says
  which kind(s) it applies to.
- New engine code goes into the part (mixin) it belongs to. A long-running loop must survive any
  exception (log it once, try again) — only `CancelledError` may end it.
- Model registries (`backbones.json`, `detectors.json`, `readers.json`): entries are never changed
  or removed once released; a new model gets a new id, pinned by Hugging Face revision and SHA-256,
  and is checked against real inputs in the tests (one public ONNX conversion turned out to be
  broken and only a test with real photos showed it).
- Frontend: colours/type/spacing only in `tokens.css`, UI constants in `ui.ts`, API URLs only in
  `api.ts`, app paths in `router.svelte.ts`. There is **no formatter** for the frontend (single
  quotes, ~120-character lines, kept by hand) — never run Prettier on it. Backend: ruff format.
- Never rely on Ctrl/Shift/hover-only interactions; hide keyboard hints with `.kbd-only`.
- Test images must be CC0 / public domain (sources in `tests/assets/README.md`) or drawn by our own
  code (`tests/displays.py`). Local experiment data lives in `/VisionStateLocal/` (ignored by git,
  outside the Docker build context): images with other licences may be used there for trying things
  out, never in the repository, the tests or a shipped model; its `README.md` lists each source.

## Checks

- Backend: `cd visionstate/backend && .venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check visionstate tests && .venv/Scripts/ruff format visionstate tests`
  and the types `.venv/Scripts/pyright --pythonpath .venv/Scripts/python.exe` (0 errors; on Linux and
  macOS the venv's programs are in `.venv/bin/`). Tests need
  the bundled models once: `python -m visionstate.backbones models`. CI fails below 88 % coverage;
  `--cov=visionstate --cov-report=term-missing:skip-covered` shows what is not tested.
- Frontend: `cd visionstate/frontend && npm run check && npm run build`.
- UI: see the routine below — every UI change, and before every beta release.

## Run it locally

```bash
cd visionstate/backend
py -3.14 -m venv .venv                     # inside the project: long paths break pip elsewhere on Windows
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m visionstate.backbones models
VISIONSTATE_DATA=./dev/data VISIONSTATE_MEDIA=./dev/media VISIONSTATE_BUNDLED_MODELS=./models \
  HA_URL=http://homeassistant.local:8123 HA_TOKEN=<long-lived token> \
  VISIONSTATE_MQTT_HOST=<broker> .venv/Scripts/python -m visionstate
```

The UI is then on port 8099 (no login: only on your own machine). `npm run dev` in
`visionstate/frontend` serves the UI with hot reload and proxies `/api` to it. On Windows set
`PYTHONUTF8=1` for scripts that read or write source files; the app handles the Windows event loop
itself (`__main__.py`).

- **Fake camera** (no Home Assistant needed): `python scripts/fake_camera.py 8765`, and use
  `http://127.0.0.1:8765/…` as an *HTTP snapshot URL*: `/snapshot.jpg` (a drawn garage door;
  `/set?state=open|closed|partial`, `/set?night=1`), `/photo/<name>.jpg` (the CC0 photos from
  `backend/tests/assets`, for object sensors), `/display.jpg?text=12:05&style=lcd|led` (a drawn
  seven-segment display) and `/counter.jpg?value=89939.5&digits=7&decimals=3` (a drawn mechanical
  counter; `.5` = the last wheel half way to the next digit).
- **The Home Assistant side:** any local MQTT broker (e.g. Mosquitto on 1883) and
  `VISIONSTATE_MQTT_HOST=127.0.0.1`: discovery configs appear under `homeassistant/…`, values under
  `visionstate/<slug>/…`.

## UI testing routine

Many users run Home Assistant on phones and tablets.

- **Automated:** `cd visionstate/frontend && npm run build && VS_PYTHON=../backend/.venv/Scripts/python npm run e2e`
  (Playwright, `e2e/`; once: `npx playwright install chromium webkit`). It starts the backend and
  `scripts/fake_camera.py`, seeds one sensor of each kind (a trained state sensor, an object sensor
  on a real photo, reading sensors on a drawn display and a drawn mechanical counter), and runs every
  page on **desktop** (1440×900, mouse), **mobile** (Pixel 7, real touch via CDP) and **iphone**
  (iPhone 14, WebKit like the Home Assistant app on iOS): no console errors, no sideways scrolling,
  no text running out of buttons or cards, plus the wizard, region editor gestures, labelling,
  review, boxes, teaching and readings. Add a test for every new page or gesture.
- Look at the full-page screenshots in `test-results/pages/{desktop,mobile,iphone}/` for every page.
- **Then by hand in a real browser** (Claude: the Chrome tools) — passing tests are not enough:
  start `scripts/fake_camera.py 8198` and the backend (`VISIONSTATE_PORT`, `_DATA`, `_MEDIA`,
  `_FRONTEND=frontend/dist`, `_BUNDLED_MODELS` as in `playwright.config.ts`; a copy of the last e2e
  run's `data` and `media` folders gives seeded sensors), use the changed screens at desktop width
  and at ~390 px, and check the console. Reload after a rebuild (a hash-only navigation keeps the
  old page). A Chrome window does not get narrower than 500 px: for a real phone width, open any
  same-origin URL (e.g. `/api/v1/status`) and replace the page with
  `<iframe src="/#/…" style="width:390px;height:844px">`.
- Handy: only the phone `npx playwright test --project=mobile`; one test `-g "reading sensor"`;
  watch it `--headed` or step through it `--ui`; after a failure `npx playwright show-trace
  test-results/<test>/trace.zip`.
- **Docs screenshots** (`docs/promo/`): Playwright at 1440 px wide with device scale 1.2, against the
  fake camera and CC0 photos, with a local MQTT broker so the header shows *MQTT connected*. Keep
  images in one row the same height, and show the maintainer before publishing.

## Test pitfalls found the hard way

- The UI polls frames and status, so the network never goes idle — wait for elements or for
  `img.complete`, never for `networkidle`.
- History rows and retrains happen *after* a value or state is published. Never read them right
  after; poll with `wait_for` / `expect.poll`. A sensor reports `ok` with its first model — wait for
  `model.n_samples` when a test needs the model trained on all labels.
- Touch in Playwright goes through CDP (`e2e/helpers.ts`): a fast release starts a fling that
  swallows the next tap, so drags rest briefly before lifting. Full-page screenshots drop touch
  emulation — assert touch-only CSS in the test, not from a screenshot.
- A change that makes text longer while something reloads can shift the page on phones and make a
  tap land elsewhere; keep the last result on screen while re-testing.
- A Svelte `$effect` tracks every state read until the first `await` of any function it calls. If
  that function later sets what it read, the effect restarts it on every answer — the live frames
  polled the camera dozens of times a second from 0.1.0 until 0.6.5. Call such functions with
  `untrack`, name the real dependencies with `void x`, and give a polling loop a counter so a
  restart ends the old loop. The e2e test "no page asks the server more often than it polls"
  counts requests per page.
- The Chrome tab Claude drives counts as hidden (`document.visibilityState`); views that only poll
  while visible look fine there. Use `setVisibility` (e2e) or override it by hand.
