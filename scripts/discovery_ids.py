"""Unique IDs and suggested entity IDs in Home Assistant MQTT discovery (for scripts/upgrade_test.sh).

    mosquitto_sub -t 'homeassistant/#' -v -W 5 | python3 scripts/discovery_ids.py collect > ids.json
    python3 scripts/discovery_ids.py compare before.json after.json

Home Assistant keys an entity on its unique ID: that must never change. A suggested entity ID
(default_entity_id) is only used when Home Assistant creates an entity, so it may be dropped, but
not changed into another ID (an entity that comes back, e.g. a deselected object class, would get
a new ID).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def collect() -> None:
    """``mosquitto_sub -v`` lines ("<topic> <payload>") -> {topic: {unique_id, default_entity_id}}."""
    out = {}
    for line in sys.stdin:
        topic, _, payload = line.rstrip("\n").partition(" ")
        if payload:
            config = json.loads(payload)
            out[topic] = {"unique_id": config.get("unique_id"), "default_entity_id": config.get("default_entity_id")}
    print(json.dumps(out, sort_keys=True))


def compare(before_file: str, after_file: str) -> None:
    before = json.loads(Path(before_file).read_text())
    after = json.loads(Path(after_file).read_text())
    bad = []
    for topic, old in before.items():
        new = after.get(topic)
        if new is None:
            bad.append(f"{topic}: missing after the upgrade")
            continue
        if new["unique_id"] != old["unique_id"]:
            bad.append(f"{topic}: unique_id {old['unique_id']} -> {new['unique_id']}")
        if new["default_entity_id"] and new["default_entity_id"] != old["default_entity_id"]:
            bad.append(f"{topic}: default_entity_id {old['default_entity_id']} -> {new['default_entity_id']}")
    for line in bad:
        print(f"::error::{line}")
    if bad:
        raise SystemExit(1)
    print(f"{len(before)} discovery configs: unique IDs and entity IDs unchanged")


if __name__ == "__main__":
    if sys.argv[1:2] == ["collect"]:
        collect()
    elif sys.argv[1:2] == ["compare"] and len(sys.argv) == 4:
        compare(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__)
