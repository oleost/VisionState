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
  - `backend/visionstate/backbones.json` is the registry of AI models.
  - `frontend/src/lib/tokens.css` holds all colours/type/spacing; `ui.ts` holds UI constants;
    `api.ts` is the only place that builds API URLs; `router.svelte.ts` defines app paths.
  - The UI reads sensor defaults, limits and palette from `GET /api/v1/config`.
- Backend checks: `cd visionstate/backend && .venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check visionstate tests && .venv/Scripts/ruff format visionstate tests`
  (tests need the model: `python -m visionstate.backbones models`).
- Frontend checks: `cd visionstate/frontend && npm run check && npm run build`.
- Releases: Home Assistant pulls prebuilt images (`image:` in `visionstate/config.yaml`), so a
  version must never reach `main` before its image exists. For every release:
  1. Bump `version` in `visionstate/config.yaml` and add a `visionstate/CHANGELOG.md` entry; commit.
  2. `git tag vX.Y.Z` and push **only the tag** (`git push origin vX.Y.Z`); wait for the CI `image`
     jobs to publish `ghcr.io/oleost/visionstate-{amd64,aarch64}:X.Y.Z`.
  3. Then push `main` and create the GitHub release (`gh release create vX.Y.Z`).
- Python version: the Dockerfile image and the CI test version (`setup-python`) must be upgraded
  together; Dependabot ignores Python image upgrades for that reason.
- Dependency updates: Dependabot opens one grouped PR per ecosystem monthly. CI runs the tests and a
  smoke test that starts the built image (both architectures) against a real MQTT broker.
