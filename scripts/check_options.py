"""Check that the app options stay compatible with the last stable release.

    python scripts/check_options.py            # against the newest stable tag (vX.Y.Z)
    python scripts/check_options.py v0.6.2     # against a given tag or commit

Home Assistant validates the options users saved against the new ``schema`` when they update.
An option that is removed, or whose type changes, makes their saved options invalid, so the app
does not start. New options must be optional (``str?``) or come with a default in ``options``.
Exits non-zero and lists every problem.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "visionstate/config.yaml"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def newest_stable_tag(current: str) -> str:
    """The newest stable tag other than the version being checked (a stable release's own tag)."""
    tags = [t for t in git("tag", "--list", "v*").split() if re.fullmatch(r"v\d+\.\d+\.\d+", t) and t != f"v{current}"]
    if not tags:
        raise SystemExit("no stable tag (vX.Y.Z) found; fetch the tags first (git fetch --tags)")
    return max(tags, key=lambda t: tuple(int(n) for n in t[1:].split(".")))


def problems(old: dict, new: dict) -> list[str]:
    found = []
    old_schema, new_schema = old.get("schema") or {}, new.get("schema") or {}
    new_options = new.get("options") or {}
    for key, kind in old_schema.items():
        if key not in new_schema:
            found.append(f"option {key!r} was removed (saved options would no longer be valid)")
        elif str(new_schema[key]).rstrip("?") != str(kind).rstrip("?"):
            found.append(f"option {key!r} changed type: {kind!r} -> {new_schema[key]!r}")
        elif str(kind).endswith("?") and not str(new_schema[key]).endswith("?") and key not in new_options:
            found.append(f"option {key!r} became required without a default")
    for key, kind in new_schema.items():
        if key not in old_schema and not str(kind).endswith("?") and key not in new_options:
            found.append(f"new option {key!r} is required but has no default in 'options'")
    return found


def main() -> None:
    new = yaml.safe_load((ROOT / CONFIG).read_text(encoding="utf-8"))
    ref = sys.argv[1] if len(sys.argv) > 1 else newest_stable_tag(str(new["version"]))
    old = yaml.safe_load(git("show", f"{ref}:{CONFIG}"))
    found = problems(old, new)
    for line in found:
        print(f"::error::{line}")
    if found:
        raise SystemExit(1)
    print(f"app options are compatible with {ref}")


if __name__ == "__main__":
    main()
