# CLAUDE.md

VisionState — a Home Assistant app that turns camera images into sensors, all locally:
**states** the user teaches it (garage door open/closed; embedding model + small per-sensor
classifier), **objects** found by a pretrained detector (people, cars, animals) and **readings**
(a number on a display, OCR). This file is the contributor guide for people and AI assistants.

- **Source of truth for scope and design decisions:** [`docs/SCOPE.md`](docs/SCOPE.md).
  Update it when a decision changes.
- **Language:** everything in the repository (code, comments, UI text, docs, commit
  messages, this file) is written in English.
- Public repository `github.com/oleost/VisionState`, Apache-2.0. Pull requests go to `beta`.

## Principles (do not break these)

- **No telemetry, nothing "phones home".** Download counts on GHCR are the only usage signal.
- **Only reachable through Home Assistant Ingress** (requests from other addresses are refused);
  outside Home Assistant the app has no login — documented, keep it that way.
- **Camera credentials never leak:** `redact.py` masks them in logs, errors and exports.
- **No hard-coded defaults** outside `settings.py` / the registries; the UI reads them from
  `GET /api/v1/config`.
- Home Assistant state values stay lower-case keys (`open`, `car_parked`).
- Marketing text (README, release notes) promotes VisionState on its own merits — no comparisons
  with other products.

## Layout and conventions

- `visionstate/` is the Home Assistant app (build context of the Dockerfile).
  - `backend/visionstate/settings.py` holds every backend default/tunable; import from there.
  - **Map of the code: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — what runs where, how a
    check flows, which files a common change touches. Keep it current when the structure changes.
  - `backend/visionstate/engine/` is the runtime: `Runtime` (`runtime.py`) is put together from one
    mixin per part (`models`, `checks`, `objects`, `reading`, `teaching`, `training`, `publishing`,
    `history`, `reminders`) on `RuntimeBase` (`base.py`, the shared state); a part inherits the parts it uses.
    `state.py` holds `SensorConfig`/`LiveState`, `logic.py` the pure decisions. New engine code goes
    into the part it belongs to; a long-running loop must survive any exception (log it once, try
    again) — only `CancelledError` may end it.
  - `backend/visionstate/backbones.json` (state sensors), `detectors.json` (object sensors) and
    `readers.json` (reading sensors; also the wheel readers for mechanical counters, trained with
    `tools/wheelreader` — see its README) are the model registries. Entries are never changed or
    removed once released (a new model gets a new id); `python -m visionstate.backbones <dir>`
    downloads every bundled model of all three.
  - Sensor kinds (`settings.SENSOR_KINDS`): `single_state`, `objects` and `reading`; the UI's tabs per kind
    are in `ui.ts` (`TABS_BY_KIND`). New features must say which kind(s) they apply to.
  - The History page and every sensor's History tab are one view (`lib/components/history/`,
    a row component per kind); its filter is in the page URL (`lib/history.ts`). On the server
    `api/history.py` turns every filter into SQL in one place (`history_query`); a new filter
    goes there, a new event into `settings.HISTORY_EVENTS` + `EVENT_ROWS` + `ui.ts HISTORY_EVENTS`.
  - Object sensors can be taught (`teach.py`, `api/teach.py`, `settings.TEACH`): taught boxes are
    `sample` rows with `object_label`; own labels live in `objects["custom"]`. The detector is never
    retrained; DINOv2 (always loaded) compares boxes with the taught ones.
  - `frontend/src/lib/tokens.css` holds all colours/type/spacing; `ui.ts` holds UI constants;
    `api.ts` is the only place that builds API URLs; `router.svelte.ts` defines app paths.
  - The UI reads sensor defaults, limits and palette from `GET /api/v1/config`.
- Backend checks: `cd visionstate/backend && .venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check visionstate tests && .venv/Scripts/ruff format visionstate tests`
  (tests need the model: `python -m visionstate.backbones models`), and the types:
  `.venv/Scripts/pyright --pythonpath .venv/Scripts/python.exe` (0 errors; CI runs it). CI measures
  coverage and fails below 88 %; add `--cov=visionstate --cov-report=term-missing:skip-covered` to see
  what is not tested.
- Frontend checks: `cd visionstate/frontend && npm run check && npm run build`.
- **UI testing routine** (every UI change, before a beta release) — many users run Home Assistant
  on phones/tablets:
  - Automated: `cd visionstate/frontend && npm run build && VS_PYTHON=../backend/.venv/Scripts/python npm run e2e`
    (Playwright, `e2e/`; also runs in CI; once: `npx playwright install chromium webkit`). It starts the backend + `scripts/fake_camera.py`,
    seeds one sensor of each kind (a trained state sensor, an object sensor on a real photo and
    reading sensors on a drawn display and a drawn mechanical counter), and runs every page on **desktop** (1440×900, mouse),
    **mobile** (Pixel 7, real touch via CDP) and **iphone** (iPhone 14, WebKit like the Home Assistant
    app on iOS; gestures with the mouse): no console errors, no sideways scrolling, no text
    running out of buttons or cards, plus the wizard (all three kinds), region editor gestures,
    labelling, review, boxes, teaching an object sensor and readings. Add a test
    for every new page or gesture.
  - Look at the full-page screenshots in `test-results/pages/{desktop,mobile,iphone}/` after UI changes,
    for every page in all three projects.
  - Then try the change by hand in a real browser (Claude: the Chrome tools) — passing tests are not
    enough: start `scripts/fake_camera.py 8198` and the backend (`VISIONSTATE_PORT`, `_DATA`, `_MEDIA`,
    `_FRONTEND=frontend/dist`, `_BUNDLED_MODELS` as in `playwright.config.ts`; a copy of the last e2e
    run's `data` and `media` folders gives seeded sensors), use the changed screens at desktop width
    and at ~390 px, and check the console. Reload after a rebuild (a hash-only navigation keeps the old page).
    A Chrome window does not get narrower than 500 px (and a maximised one not at all): for a real
    phone width, open any same-origin URL (e.g. `/api/v1/status`) and replace the page with
    `<iframe src="/#/…" style="width:390px;height:844px">` — media queries then see 390 px.
  - Handy while working (add `VS_PYTHON=…` as above): only the phone `npx playwright test
    --project=mobile`; one test `npx playwright test -g "reading sensor"`; watch it in a browser
    `--headed` or step through it with `--ui`; after a failure `npx playwright show-trace
    test-results/<test>/trace.zip` shows every step with DOM snapshots.
  - Never rely on Ctrl/Shift/hover-only interactions; hide keyboard hints with `.kbd-only`.

## Testing and verifying locally

- **Run the app against a fake camera** (no Home Assistant needed):
  `python scripts/fake_camera.py 8765`, then start the backend (see README → Run it locally) and
  use `http://127.0.0.1:8765/…` as an *HTTP snapshot URL*. The camera serves `/snapshot.jpg`
  (synthetic garage door; `/set?state=open|closed|partial`, `/set?night=1`), `/photo/<name>.jpg`
  (real CC0 photos from `backend/tests/assets`, for object sensors) and
  `/display.jpg?text=12:05&style=lcd|led` (a drawn seven-segment display, for reading sensors)
  and `/counter.jpg?value=89939.5&digits=7&decimals=3` (a drawn mechanical counter with rolling
  digit wheels; `.5` = the last wheel half way to the next digit).
- **Check the Home Assistant side** with any local MQTT broker (e.g. Mosquitto on 1883) and
  `VISIONSTATE_MQTT_HOST=127.0.0.1`: the discovery configs appear under `homeassistant/…` and
  values under `visionstate/<slug>/…`. The CI smoke test does the same against the built image.
- **Test pitfalls found the hard way** (keep tests stable):
  - The UI polls frames and status, so the network never goes idle — wait for elements or for
    `img.complete`, never for `networkidle`.
  - History rows and retrains happen *after* a value or state is published.
    Never read them right after; poll with `wait_for` / `expect.poll`. A sensor reports `ok` with
    its first model — wait for `model.n_samples` when a test needs the model trained on all labels.
  - Touch in Playwright goes through CDP (`e2e/helpers.ts`): a fast release starts a fling that
    swallows the next tap, so drags rest briefly before lifting. Full-page screenshots drop touch
    emulation — assert touch-only CSS in the test, not from a screenshot.
  - A change that makes text longer while something reloads can shift the page on phones and make
    a tap land elsewhere; keep the last result on screen while re-testing.
  - A Svelte `$effect` tracks every state read until the first `await` of any function it calls.
    If that function later sets what it read, the effect restarts it on every answer — the live
    frames polled the camera dozens of times a second from 0.1.0 until 0.6.5. Call such functions
    with `untrack`, name the real dependencies with `void x`, and give a polling loop a counter so
    a restart ends the old loop (a request still on its way would otherwise start a second one).
    The e2e test "no page asks the server more often than it polls" counts requests per page.
  - The Chrome tab Claude drives counts as hidden (`document.visibilityState`); views that only
    poll while visible look fine there. Use `setVisibility` (e2e) or override it by hand.
- **Models:** registries pin a Hugging Face revision and SHA-256. Every model file is checked
  against real inputs in tests (`test_objects.py`, `test_reading.py`) — one public ONNX conversion
  turned out to be broken and only a test with real photos showed it. Test images must be CC0 /
  public domain (list sources in `tests/assets/README.md`) or drawn by our own code
  (`tests/displays.py`).
- **Local experiment data** lives in `/VisionStateLocal/` (ignored by git, outside the Docker build
  context): images with other licences may be used there for trying things out, never in the
  repository, the tests or a shipped model. Its `README.md` lists each source and licence.
- **Windows development:** create the venv inside the project (`visionstate/backend/.venv`; long
  paths break pip elsewhere), and set `PYTHONUTF8=1` for scripts that read or write source files.
  The app itself handles the Windows event loop (`__main__.py`).
- **Docs screenshots** (`docs/promo/`): Playwright at 1440 px wide with device scale 1.2 (1728 px
  images), against the fake camera and CC0 photos, with a local MQTT broker so the header shows
  *MQTT connected*. Keep images in one row the same height, and show the user before publishing.

## Branches, channels and releases

- **Branches and channels — never commit to `main` directly.**
  - `beta` is the working branch: every change lands here first (directly or via PR to `beta`).
    Its `visionstate/config.yaml` is the beta channel ("VisionState (beta)", versions `X.Y.ZbN`,
    own media folder). Home Assistant users get it via `https://github.com/oleost/VisionState#beta`.
  - `main` is the stable channel and only changes by promoting a tested beta (PR from a
    `promote/X.Y.Z` branch). `main` is branch-protected.
  - `scripts/channel.py` is the only way to change name/version/channel fields in config.yaml.
  - Home Assistant pulls prebuilt images (`image:` in config.yaml), so a version must never reach a
    branch before its images exist. CI refuses tags that do not match config.yaml and branches that
    carry the wrong channel.
- **A beta tag is the CI run.** Run the local checks first (backend, frontend, e2e, hands-on in
  a browser), then push the tag: its CI tests, builds both images on native runners, and — for
  beta tags — creates the pre-release and fast-forwards `beta`. If it fails, nothing was
  published: delete the tag (`git push origin :refs/tags/vX.Y.ZbN`, `git tag -d …`), fix, and tag
  the same version again. For a stable release, or when unsure, run CI first on a temporary
  branch with a draft PR against `beta` and close it afterwards.
- Documentation-only changes (README, DOCS.md, CLAUDE.md, images) may go to `main` through a PR
  without a beta; merge `main` back into `beta` afterwards.
- **Beta release** (on `beta`, about 10 minutes, unattended after the tag):
  1. `python scripts/channel.py beta X.Y.ZbN`, add a `## X.Y.ZbN` entry to `visionstate/CHANGELOG.md`
     (it becomes the release notes), commit. Do **not** push `beta`.
  2. `git tag vX.Y.ZbN && git push origin vX.Y.ZbN` (**tag only**). CI then publishes
     `ghcr.io/oleost/visionstate-{amd64,aarch64}:X.Y.ZbN`, creates the GitHub pre-release and moves
     `beta` to the tagged commit; `gh run watch` follows it.
  3. `git fetch origin && git status` — local `beta` should equal `origin/beta`. If the release
     changed a file under `.github/workflows/`, CI may not push the branch: run `git push origin beta`
     yourself once the images exist (the registry answers 200 for
     `https://ghcr.io/v2/oleost/visionstate-<arch>/manifests/X.Y.ZbN` with an anonymous pull token).
     A tagline for the release title is optional: `gh release edit vX.Y.ZbN --title "X.Y.ZbN — …"`.
- **Before promoting a beta to stable** (there is no separate release candidate: the beta being
  promoted is checked as it is) — go through all of it, and tell the user what
  was checked and what was found:
  1. **CI on the beta being promoted is green**, including the checks that only block stable
     releases: the upgrade test from the last stable release and back (`scripts/upgrade_test.sh`,
     both architectures), the old CPU check (amd64 `kvm64`, aarch64 `cortex-a53`), the memory peak
     with all three models (see the notice in the smoke test; limit in `MEMORY_LIMIT`), the app
     options check (`scripts/check_options.py`), `pip-audit` / `npm audit` (a warning on betas,
     an error on stable), and e2e on desktop, Android and iPhone (WebKit).
  2. **On the test Home Assistant** (a Home Assistant OS VM with the beta app installed):
     - update the beta app to the beta being promoted; the standing test sensors (one of each kind) keep
       working, their entities in Home Assistant keep their IDs, the log has no warnings or errors;
     - restart Home Assistant, reboot the host and restart the MQTT broker: the app and its
       entities come back by themselves;
     - make a backup of the app and restore it: the sensors are back;
     - **soak for 1 hour** with the sensors checking: memory of the app (Supervisor app stats)
       and its data on disk level off instead of growing, the log stays quiet;
     - open the app in Home Assistant at desktop width and on a phone (Ingress);
     - prepare the **update from the last stable release in a real Home Assistant**: the stable
       app (repository without `#beta`) is installed there too. It shares MQTT topics and unique
       IDs with the beta app, so never run both: stop the beta app, start the stable one and give
       it one sensor of each kind (import exports of the standing test sensors).
  3. **What changed since the last stable release**: new or upgraded dependencies have a licence
     that fits Apache-2.0 (`git diff vX.Y.Z -- visionstate/backend/requirements.txt
     visionstate/frontend/package.json`); `homeassistant:` in config.yaml still names the oldest
     Home Assistant that works; DOCS.md, README.md and SCOPE.md describe what is being released.
- **Promote to stable** (only when the user says the beta is tested, after the checks above):
  1. `git switch -c promote/X.Y.Z beta`; `python scripts/channel.py stable X.Y.Z`; in the changelog,
     merge the `X.Y.ZbN` entries into one `## X.Y.Z` entry; commit.
  2. `git merge origin/main`; if `visionstate/config.yaml` conflicts, re-run
     `python scripts/channel.py stable X.Y.Z` to resolve it; commit.
  3. `git tag vX.Y.Z && git push origin vX.Y.Z` (tag only); wait for the images.
  4. Push the branch, open a PR to `main`, wait for CI, merge it. Right away, update the stable app
     on the test Home Assistant (reload the store): its sensors keep working, their entities keep
     their IDs, the log has no warnings or errors. Only then `gh release create vX.Y.Z --latest`;
     stop the stable app and start the beta app again.
  5. Merge `main` back into `beta`, keeping beta's config: `git switch beta && git merge main`,
     then `python scripts/channel.py beta <last beta version>` and commit (a beta version whose
     images exist); the next beta release sets the next version.
- Python version is **3.14** (Dockerfile image, CI `setup-python`, ruff `target-version`, local
  `.venv` created with `py -3.14`). Upgrade all of them together; Dependabot ignores Python image
  upgrades for that reason.
- **Old CPUs and virtual machines must keep working** (x86-64-v1, e.g. Proxmox `kvm64`; ARMv8.0,
  e.g. Raspberry Pi 3/4 and Home Assistant Yellow with CM4 — Green is ARMv8.2). NumPy is pinned
  below 2.4 because newer wheels need x86-64-v2 (Dependabot ignores them). CI runs
  `scripts/cpu_probe.py` in each image under an emulated old CPU (amd64 `kvm64`, aarch64
  `cortex-a53`); when it fails after a dependency update, that update needs a newer CPU — keep the
  old version.
- **Docs checklist** — when behaviour, defaults, versions or the workflow change, update in the same
  change: `visionstate/DOCS.md` (user guide in HA), `README.md` (front page), `docs/SCOPE.md`
  (design as built, roadmap), `visionstate/CHANGELOG.md`, and this file.
- Dependency updates: Dependabot opens one grouped PR per ecosystem monthly. CI runs the tests, a
  smoke test that starts the built image (both architectures) against a real MQTT broker, and the
  upgrade test from the last stable release.
