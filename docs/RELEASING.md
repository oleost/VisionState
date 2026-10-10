# Releasing

Branches, channels, versions and the release steps. How to work on the code is in
[`CLAUDE.md`](../CLAUDE.md).

## Channels and branches

| | Stable | Beta |
|---|---|---|
| Branch | `main` (branch-protected; changes only by promoting a tested beta) | `beta` (every change lands here first, directly or by a PR to `beta`) |
| Home Assistant repository | `https://github.com/oleost/VisionState` | `https://github.com/oleost/VisionState#beta` |
| App name | VisionState | VisionState (beta) |
| Version | `X.Y.Z` | `X.Y.ZbN` |
| Images | `ghcr.io/oleost/visionstate-{amd64,aarch64}:X.Y.Z` (+`latest`) | `…:X.Y.ZbN` (+`beta`) |
| Media folder | `/media/visionstate` | `/media/visionstate_beta` |
| GitHub release | latest | pre-release |

- **Never commit to `main` directly.** Documentation-only changes (Markdown, images, issue
  templates) may go to `main` through a PR without a beta; merge `main` back into `beta` afterwards.
- `scripts/channel.py` is the only way to change the name, version and channel fields in
  `visionstate/config.yaml`.
- Home Assistant pulls prebuilt images (`image:` in config.yaml), so a version must never reach a
  branch before its images exist. CI refuses tags that do not match config.yaml and branches that
  carry the wrong channel.
- The slug (`visionstate`), the MQTT discovery prefix and node id (`homeassistant` / `visionstate`)
  and every `unique_id` never change: entity IDs in Home Assistant hang on them.

## What CI does

On every push to `main`/`beta`, every PR and every tag (`.github/workflows/ci.yml`): backend
(ruff, pyright, pytest with coverage ≥ 88 % — the docs check included —, the app options check
`scripts/check_options.py`, `pip-audit`), frontend (`svelte-check`, build, `npm audit`), e2e on
desktop, Android and iPhone (WebKit), then both images built on native runners and tested: a smoke
test against a real MQTT broker (start, discovery, the memory peak with all three models under
`MEMORY_LIMIT`, a clean `docker stop`), `scripts/cpu_probe.py` under an emulated old CPU (amd64
`kvm64`, aarch64 `cortex-a53`) and `scripts/upgrade_test.sh` (from the last stable release and
back). The audits only warn on betas and block stable releases. Only a version tag publishes the
images; a beta tag then also creates the pre-release from the changelog entry and fast-forwards
`beta`.

If a tag's CI fails, nothing was published: delete the tag (`git push origin :refs/tags/vX.Y.ZbN`,
`git tag -d …`), fix, and tag the same version again. When unsure, run CI first on a temporary
branch with a draft PR against `beta` and close it afterwards.

## Beta release

On `beta`, about 10 minutes, unattended after the tag.

0. **The maintainer says "go" first.** Run the local checks (backend, frontend, e2e, hands-on in a
   browser; see `CLAUDE.md`), start a local instance for the maintainer to try (fake camera +
   backend with seeded sensors), give the URL of the changed screens, sum up what was done and show
   the release notes (the new `CHANGELOG.md` entry). Commit, version and tag only after an explicit
   "go".
1. `python scripts/channel.py beta X.Y.ZbN`, add a `## X.Y.ZbN` entry to `visionstate/CHANGELOG.md`
   (it becomes the release notes), commit. Do **not** push `beta`.
2. `git tag vX.Y.ZbN && git push origin vX.Y.ZbN` (**tag only**); `gh run watch` follows it.
3. `git fetch origin && git status` — local `beta` should equal `origin/beta`. If the release
   changed a file under `.github/workflows/`, CI may not push the branch: run `git push origin beta`
   yourself once the images exist (the registry answers 200 for
   `https://ghcr.io/v2/oleost/visionstate-<arch>/manifests/X.Y.ZbN` with an anonymous pull token).
   A tagline for the release title is optional: `gh release edit vX.Y.ZbN --title "X.Y.ZbN — …"`.

## Before promoting a beta to stable

There is no separate release candidate: the beta being promoted is checked as it is. Go through
all of it and tell the maintainer what was checked and what was found.

1. **CI on that beta is green**, including what only blocks stable releases (the audits).
2. **On the test Home Assistant** (a Home Assistant OS VM with the beta app installed):
   - update the beta app to the beta being promoted; the standing test sensors (one of each kind)
     keep working, their entities keep their IDs, the log has no warnings or errors;
   - restart Home Assistant, reboot the host and restart the MQTT broker: the app and its entities
     come back by themselves;
   - make a backup of the app and restore it: the sensors are back;
   - **soak for 1 hour** with the sensors checking: the app's memory (Supervisor app stats) and its
     data on disk level off instead of growing, the log stays quiet;
   - open the app in Home Assistant at desktop width and on a phone (Ingress);
   - prepare the **update from the last stable release**: the stable app is installed there too. It
     shares MQTT topics and unique IDs with the beta app, so never run both: stop the beta app,
     start the stable one, and give it one sensor of each kind (exports of the standing test sensors).
3. **What changed since the last stable release**: new or upgraded dependencies have a licence that
   fits Apache-2.0 (`git diff vX.Y.Z -- visionstate/backend/requirements.txt
   visionstate/frontend/package.json`); `homeassistant:` in config.yaml still names the oldest Home
   Assistant that works; the docs describe what is being released (the roadmap in `SCOPE.md` and
   the status in `WHEEL_READER_PLAN.md` included).

## Promote to stable

Only when the maintainer says the beta is tested, after the checks above.

1. `git switch -c promote/X.Y.Z beta`; `python scripts/channel.py stable X.Y.Z`; in the changelog,
   merge the `X.Y.ZbN` entries into one `## X.Y.Z` entry; commit.
2. `git merge origin/main`; if `visionstate/config.yaml` conflicts, re-run
   `python scripts/channel.py stable X.Y.Z`; commit.
3. `git tag vX.Y.Z && git push origin vX.Y.Z` (tag only); wait for the images.
4. Push the branch, open a PR to `main`, wait for CI, merge it. Right away, update the stable app on
   the test Home Assistant (reload the store): its sensors keep working, their entities keep their
   IDs, the log has no warnings or errors. Only then `gh release create vX.Y.Z --latest`; stop the
   stable app and start the beta app again.
5. Merge `main` back into `beta`, keeping beta's config: `git switch beta && git merge main`, then
   `python scripts/channel.py beta <last beta version>` and commit (a beta version whose images
   exist); the next beta release sets the next version.

## Dependencies and platforms

- Dependabot opens one grouped PR per ecosystem monthly, against `beta`.
- Python is **3.14** everywhere (Dockerfile image, CI `setup-python`, ruff `target-version`, the
  local `.venv`). Upgrade all of them together; Dependabot ignores Python image upgrades for that
  reason.
- **Old CPUs and virtual machines must keep working** (x86-64-v1, e.g. Proxmox `kvm64`; ARMv8.0,
  e.g. Raspberry Pi 3/4 and Home Assistant Yellow with CM4). NumPy is pinned below 2.4 because newer
  wheels need x86-64-v2 (found through issue #29; Dependabot ignores them). When `cpu_probe.py`
  fails after a dependency update, that update needs a newer CPU — keep the old version.
