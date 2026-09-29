"""Switch visionstate/config.yaml between the stable and the beta channel.

    python scripts/channel.py beta 0.4.1b1     # on the beta branch, for every beta release
    python scripts/channel.py stable 0.4.1     # when promoting a tested beta to main

Everything that differs between the channels is defined in CHANNELS below; the rest of
config.yaml is shared. The file is edited line by line so its formatting is preserved.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "visionstate" / "config.yaml"

CHANNELS = {
    "stable": {
        "version": re.compile(r"^\d+\.\d+\.\d+$"),
        "name": "VisionState",
        "panel_title": "VisionState",
        "environment": {},
    },
    "beta": {
        "version": re.compile(r"^\d+\.\d+\.\d+b\d+$"),
        "name": "VisionState (beta)",
        "panel_title": "VisionState β",
        # Own media folder so the beta can never touch the stable app's training images.
        "environment": {"VISIONSTATE_MEDIA": "/media/visionstate_beta"},
    },
}


def render(text: str, channel: str, version: str) -> str:
    spec = CHANNELS[channel]
    if not spec["version"].match(version):
        raise SystemExit(f"{version!r} is not a valid {channel} version (e.g. 0.4.1 or 0.4.1b1)")
    text = re.sub(r"^name: .*$", f"name: {spec['name']}", text, count=1, flags=re.M)
    text = re.sub(r'^version: .*$', f'version: "{version}"', text, count=1, flags=re.M)
    text = re.sub(r"^panel_title: .*$", f"panel_title: {spec['panel_title']}", text, count=1, flags=re.M)
    # Replace (or remove) the environment block, which is always kept at the end of the file.
    text = re.sub(r"\nenvironment:\n(?:  .*\n?)*", "\n", text).rstrip("\n") + "\n"
    if spec["environment"]:
        lines = "".join(f"  {key}: {value}\n" for key, value in spec["environment"].items())
        text += f"environment:\n{lines}"
    return text


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in CHANNELS:
        raise SystemExit(__doc__)
    channel, version = sys.argv[1], sys.argv[2]
    CONFIG.write_text(render(CONFIG.read_text(encoding="utf-8"), channel, version), encoding="utf-8")
    print(f"config.yaml set to {channel} {version}")


if __name__ == "__main__":
    main()
