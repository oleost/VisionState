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
  - `backend/visionstate/backbones.json` (state sensors), `detectors.json` (object sensors) and
    `readers.json` (reading sensors) are the model registries. Entries are never changed or
    removed once released (a new model gets a new id); `python -m visionstate.backbones <dir>`
    downloads every bundled model of all three.
  - Sensor kinds (`settings.SENSOR_KINDS`): `single_state`, `objects` and `reading`; the UI's tabs per kind
    are in `ui.ts` (`TABS_BY_KIND`). New features must say which kind(s) they apply to.
  - `frontend/src/lib/tokens.css` holds all colours/type/spacing; `ui.ts` holds UI constants;
    `api.ts` is the only place that builds API URLs; `router.svelte.ts` defines app paths.
  - The UI reads sensor defaults, limits and palette from `GET /api/v1/config`.
- Backend checks: `cd visionstate/backend && .venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check visionstate tests && .venv/Scripts/ruff format visionstate tests`
  (tests need the model: `python -m visionstate.backbones models`).
- Frontend checks: `cd visionstate/frontend && npm run check && npm run build`.
- **UI testing routine** (every UI change, before a beta release) — many users run Home Assistant
  on phones/tablets:
  - Automated: `cd visionstate/frontend && npm run build && VS_PYTHON=../backend/.venv/Scripts/python npm run e2e`
    (Playwright, `e2e/`; also runs in CI). It starts the backend + `scripts/fake_camera.py`,
    seeds one sensor of each kind (a trained state sensor, an object sensor on a real photo and a
    reading sensor on a drawn display), and runs every page on **desktop** (1440×900, mouse) and
    **mobile** (Pixel 7, real touch via CDP): no console errors, no sideways scrolling, no text
    running out of buttons or cards, plus the wizard (all three kinds), region editor gestures,
    labelling, review, boxes and readings. Add a test
    for every new page or gesture.
  - Look at the full-page screenshots in `test-results/pages/{desktop,mobile}/` after UI changes,
    for every page at both sizes.
  - Then try the change by hand in a real browser (Claude: the Chrome tools) — passing tests are not
    enough: start `scripts/fake_camera.py 8198` and the backend (`VISIONSTATE_PORT`, `_DATA`, `_MEDIA`,
    `_FRONTEND=frontend/dist`, `_BUNDLED_MODELS` as in `playwright.config.ts`; a copy of the last e2e
    run's `data` and `media` folders gives seeded sensors), use the changed screens at desktop width
    and at ~390 px, and check the console. Reload after a rebuild (a hash-only navigation keeps the old page).
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
  `/display.jpg?text=12:05&style=lcd|led` (a drawn seven-segment display, for reading sensors).
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
- **Models:** registries pin a Hugging Face revision and SHA-256. Every model file is checked
  against real inputs in tests (`test_objects.py`, `test_reading.py`) — one public ONNX conversion
  turned out to be broken and only a test with real photos showed it. Test images must be CC0 /
  public domain (list sources in `tests/assets/README.md`) or drawn by our own code
  (`tests/displays.py`).
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
- **Run CI before tagging.** A tag whose CI fails has no images and its version number is lost
  (the next try needs a new one). Push the commit to a temporary branch and open a draft PR
  against `beta`; tag only when it is green, then close the PR and delete the branch. Tags that
  never got images or a release can be deleted.
- Documentation-only changes (README, DOCS.md, CLAUDE.md, images) may go to `main` through a PR
  without a beta; merge `main` back into `beta` afterwards.
- **Beta release** (on `beta`):
  1. `python scripts/channel.py beta X.Y.ZbN`, add a `## X.Y.ZbN` entry to `visionstate/CHANGELOG.md`, commit.
  2. `git tag vX.Y.ZbN && git push origin vX.Y.ZbN` (**tag only**); wait until CI (tests + smoke test)
     published `ghcr.io/oleost/visionstate-{amd64,aarch64}:X.Y.ZbN` (the registry answers 200 for
     `https://ghcr.io/v2/oleost/visionstate-<arch>/manifests/X.Y.ZbN` with an anonymous pull token).
  3. `git push origin beta`; `gh release create vX.Y.ZbN --prerelease`.
- **Promote to stable** (only when the user says the beta is tested):
  1. `git switch -c promote/X.Y.Z beta`; `python scripts/channel.py stable X.Y.Z`; in the changelog,
     merge the `X.Y.ZbN` entries into one `## X.Y.Z` entry; commit.
  2. `git merge origin/main`; if `visionstate/config.yaml` conflicts, re-run
     `python scripts/channel.py stable X.Y.Z` to resolve it; commit.
  3. `git tag vX.Y.Z && git push origin vX.Y.Z` (tag only); wait for the images.
  4. Push the branch, open a PR to `main`, wait for CI, merge it; `gh release create vX.Y.Z --latest`.
  5. Merge `main` back into `beta`, keeping beta's config: `git switch beta && git merge main`,
     then `python scripts/channel.py beta <next beta version>` before the next beta release.
- Python version is **3.14** (Dockerfile image, CI `setup-python`, ruff `target-version`, local
  `.venv` created with `py -3.14`). Upgrade all of them together; Dependabot ignores Python image
  upgrades for that reason.
- **Docs checklist** — when behaviour, defaults, versions or the workflow change, update in the same
  change: `visionstate/DOCS.md` (user guide in HA), `README.md` (front page), `docs/SCOPE.md`
  (design as built, roadmap), `visionstate/CHANGELOG.md`, and this file.
- Dependency updates: Dependabot opens one grouped PR per ecosystem monthly. CI runs the tests and a
  smoke test that starts the built image (both architectures) against a real MQTT broker.
