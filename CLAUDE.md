# CLAUDE.md

VisionState — a Home Assistant app that classifies states (e.g. garage door open/closed)
from camera images using a local embedding model + lightweight per-sensor classifier.

- **Source of truth for scope and design decisions:** [`docs/SCOPE.md`](docs/SCOPE.md).
  Update it when a decision changes.
- **Language:** everything in the repository (code, comments, UI text, docs, commit
  messages, this file) is written in English.
- Public repository `github.com/oleost/VisionState`, Apache-2.0.

## Layout and conventions

- `visionstate/` is the Home Assistant app (build context of the Dockerfile).
  - `backend/visionstate/settings.py` holds every backend default/tunable; import from there.
  - `backend/visionstate/backbones.json` (state sensors) and `detectors.json` (object sensors) are
    the model registries. Entries are never changed or removed once released (a new model gets a
    new id); `python -m visionstate.backbones <dir>` downloads every bundled model of both.
  - Sensor kinds (`settings.SENSOR_KINDS`): `single_state` and `objects`; the UI's tabs per kind
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
    seeds a trained sensor, and runs every page on **desktop** (1440×900, mouse) and **mobile**
    (Pixel 7, real touch via CDP): no console errors, no sideways scrolling, no text running
    out of buttons, plus the wizard, region editor gestures, labelling and review. Add a test
    for every new page or gesture.
  - Look at the full-page screenshots in `test-results/pages/{desktop,mobile}/` after UI changes.
  - Never rely on Ctrl/Shift/hover-only interactions; hide keyboard hints with `.kbd-only`.
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
- **Beta release** (on `beta`):
  1. `python scripts/channel.py beta X.Y.ZbN`, add a `## X.Y.ZbN` entry to `visionstate/CHANGELOG.md`, commit.
  2. `git tag vX.Y.ZbN && git push origin vX.Y.ZbN` (**tag only**); wait until CI (tests + smoke test)
     published `ghcr.io/oleost/visionstate-{amd64,aarch64}:X.Y.ZbN`.
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
