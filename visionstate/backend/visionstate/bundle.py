"""Export and import of sensors as portable ZIP bundles."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from .db import Database, Prediction, Sample, Sensor
from .redact import has_credentials, redact
from .settings import EXPORT_STRIP_CREDENTIALS, UPLOAD_LIMITS, VERSION
from .storage import Storage

BUNDLE_SCHEMA = 1
MANIFEST = "manifest.json"


def export_sensor(db: Database, storage: Storage, sensor_id: int, target: Path) -> str:
    """Writes a bundle for one sensor to ``target``; returns the suggested download filename."""
    with db.session() as s:
        sensor = s.get(Sensor, sensor_id)
        if sensor is None:
            raise KeyError(sensor_id)
        key_by_state = {st.id: st.key for st in sensor.states}
        manifest = {
            "schema": BUNDLE_SCHEMA,
            "app_version": VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "sensor": {
                "slug": sensor.slug,
                "name": sensor.name,
                "kind": sensor.kind,
                "source_type": sensor.source_type,
                "source": redact(sensor.source) if EXPORT_STRIP_CREDENTIALS else sensor.source,
                "source_redacted": EXPORT_STRIP_CREDENTIALS and has_credentials(sensor.source),
                "roi": sensor.roi,
                "interval_s": sensor.interval_s,
                "threshold": sensor.threshold,
                "debounce": sensor.debounce,
                "triggers": sensor.triggers,
                "review": sensor.review,
                "objects": sensor.objects,
                "reading": sensor.reading,
                "entity_prefix": sensor.entity_prefix,
                "publish": sensor.publish,
                "states": [{"key": st.key, "name": st.name, "color": st.color} for st in sensor.states],
            },
            "samples": [],
        }
        samples = s.scalars(select(Sample).where(Sample.sensor_id == sensor_id)).all()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for sample in samples:
                path = storage.sample_path(sensor_id, sample.filename)
                if not path.exists():
                    continue
                archive.write(path, f"samples/{sample.filename}")
                manifest["samples"].append(
                    {
                        "file": sample.filename,
                        "labels": [key_by_state[lab.state_id] for lab in sample.labels if lab.state_id in key_by_state],
                        "origin": sample.origin,
                        "use_roi": sample.use_roi,
                        "is_night": sample.is_night,
                        "width": sample.width,
                        "height": sample.height,
                        "created_at": sample.created_at.isoformat(),
                        # object sensors: a taught box (see db.Sample)
                        **(
                            {
                                "object_label": sample.object_label,
                                "detected": sample.detected,
                                "box": sample.box,
                                "score": sample.score,
                            }
                            if sample.object_label is not None
                            else {}
                        ),
                    }
                )
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
        return f"visionstate-{sensor.slug}.zip"


READINGS_README = """Readings from a VisionState reading sensor, checked by hand
============================================================

Every image is only the region the sensor reads (a display or counter, with a little room
around it) — not the whole camera picture. readings.json lists for each image what the reader
read, the value it made of it, why it was rejected (if it was), and the answer given in
VisionState: read correctly, or misread (with the right value when it was entered).

The reader does not learn from these answers by itself. Shared, they help make reading better
for everyone: what goes wrong on which kind of display or meter.

To share: attach this file to a post in GitHub Discussions or an issue at
https://github.com/oleost/VisionState — please say what the display or meter is.
Look through the images first; share only what you are happy to make public.
By sharing it you release the images and data as public domain (see LICENSE.txt).

The region is the one the sensor has now; readings from before it was changed may be cut
differently.
"""

READINGS_LICENSE = """CC0 1.0 Universal (public domain dedication)

By sharing this file, you dedicate the images and data in it to the public domain under
CC0 1.0: anyone may copy, change and use them, for any purpose, without asking.
Full text: https://creativecommons.org/publicdomain/zero/1.0/
"""


def export_readings(db: Database, storage: Storage, sensor_id: int, target: Path, reader: str) -> str:
    """Writes the readings verified by hand (region crops + what was read) to ``target``.

    Returns the suggested download filename; raises LookupError when there is nothing to export.
    """
    from . import imaging
    from .settings import READING, merge_reading

    with db.session() as s:
        sensor = s.get(Sensor, sensor_id)
        if sensor is None:
            raise KeyError(sensor_id)
        settings = merge_reading(sensor.reading)
        rows = s.scalars(
            select(Prediction)
            .where(Prediction.sensor_id == sensor_id, Prediction.read_ok.is_not(None), Prediction.frame.is_not(None))
            .order_by(Prediction.created_at.desc())
            .limit(READING["export_limit"])
        ).all()
        box = imaging.region_box(sensor.roi, READING["export_margin"])
        slug = sensor.slug
    items = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            path = storage.history_path(sensor_id, row.frame)
            if not path.exists():
                continue
            crop = imaging.crop_box(imaging.decode(path.read_bytes()), box)
            name = f"images/{len(items) + 1:04d}.jpg"
            archive.writestr(name, imaging.encode_jpeg(crop))
            details = row.probs or {}
            items.append(
                {
                    "file": name,
                    "read": details.get("text"),
                    "value": details.get("value"),
                    "rejected": details.get("reason"),
                    "confidence": round(row.confidence, 4),
                    "answer": "read correctly" if row.read_ok else "misread",
                    "right_value": row.correct_value,
                    "date": row.created_at.date().isoformat(),  # the day only
                }
            )
        if not items:
            raise LookupError("No verified readings yet")
        manifest = {
            "app_version": VERSION,
            "reader": reader,
            # How the sensor reads; nothing about where it is (no camera, name or region position).
            "settings": {k: settings[k] for k in ("mode", "display", "digits", "decimals", "unit")},
            "readings": items,
        }
        archive.writestr("readings.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.writestr("README.txt", READINGS_README)
        archive.writestr("LICENSE.txt", READINGS_LICENSE)
    return f"visionstate-readings-{slug}.zip"


def read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        if MANIFEST not in archive.namelist():
            raise ValueError("Not a VisionState bundle (manifest.json missing)")
        info = archive.getinfo(MANIFEST)
        if info.file_size > UPLOAD_LIMITS["max_manifest_mb"] * 1_000_000:
            raise ValueError("manifest.json is too large")
        manifest = json.loads(archive.read(info))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sensor"), dict):
        raise ValueError("Not a VisionState bundle (no sensor in manifest.json)")
    schema = manifest.get("schema", 0)
    if not isinstance(schema, int):
        raise ValueError("Not a VisionState bundle (bad schema in manifest.json)")
    if schema > BUNDLE_SCHEMA:
        raise ValueError("Bundle was created by a newer VisionState version")
    return manifest


def read_sample_bytes(path: Path, filename: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(f"samples/{filename}")
        if info.file_size > UPLOAD_LIMITS["max_zip_member_mb"] * 1_000_000:
            raise ValueError(f"{filename} is too large")
        return archive.read(info)
