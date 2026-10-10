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


READINGS_FORMAT = 2  # readings.json; 2 added per-reading reader details, "meter", unchecked readings

READINGS_README = """Readings from a VisionState reading sensor
==========================================

Every image is only the region the sensor reads (a display or counter, with a little room
around it) — not the whole camera picture. readings.json lists for each image what the reader
read, the value it made of it, why it was rejected (if it was), and the answer given in
VisionState: "read correctly", "misread" (with the right value when it was entered), or
"unchecked" (an accepted reading nobody checked: the reader's value, not confirmed by a person).
The checked readings come first, newest first, then the unchecked ones, newest first.

Per reading it also says which reader read it ("reader"; null for readings from before
VisionState 0.7.1b5), the app version ("app"), whether the whole picture was greyscale (an
infrared or night camera) and whether VisionState had switched the sensor's light on ("lamp").
For the wheel reader, "wheels" says where it saw each wheel (positions 0.0-9.9, as seen in the
image), how sure it was, its most likely positions, the common shift of all wheels and the
value with the last wheel's fraction. "seconds" is the time since the oldest reading of the
export (no time of day).

The file stays under 24 MB (GitHub takes files up to 25 MB): the readings that did not fit
are counted in "left_out" — export again later for newer ones.

The reader does not learn from these answers by itself. Shared, they help make reading better
for everyone: what goes wrong on which kind of display or meter.

To share: attach this file to a post in GitHub Discussions or an issue at
https://github.com/oleost/VisionState — say what the display or meter is, if it is not in
readings.json ("meter"). Look through the images first; share only what you are happy to make
public. By sharing it you release the images and data as public domain (see LICENSE.txt).

Each image is cut with the region the reading was made in ("region": "as read"). Readings
from before VisionState 0.7.1b2 are cut with the region the sensor has now ("region":
"current"): if the region was moved since, those images may not show the display at all.
"""

READINGS_LICENSE = """CC0 1.0 Universal (public domain dedication)

By sharing this file, you dedicate the images and data in it to the public domain under
CC0 1.0: anyone may copy, change and use them, for any purpose, without asking.
Full text: https://creativecommons.org/publicdomain/zero/1.0/
"""

ANSWERS = {True: "read correctly", False: "misread", None: "unchecked"}


def export_readings(db: Database, storage: Storage, sensor_id: int, target: Path, reader: str, meter: str = "") -> str:
    """Writes a reading sensor's readings to share to ``target`` (one ZIP).

    The readings checked by hand come first, then the accepted ones nobody checked (marked
    "unchecked"), each newest first; every image is the region crop of the stored frame. The ZIP
    stays under ``READING["export_max_mb"]``; the readings that did not fit are counted.
    ``meter``: what the user says the meter is. Returns the suggested download filename; raises
    LookupError when there is nothing to export.
    """
    from . import imaging, readers
    from .settings import READING, merge_reading

    with db.session() as s:
        sensor = s.get(Sensor, sensor_id)
        if sensor is None:
            raise KeyError(sensor_id)
        settings = merge_reading(sensor.reading)
        wheels = settings["display"] == "counter" and settings["counter_reader"] == "wheels"
        mine = (Prediction.sensor_id == sensor_id, Prediction.frame.is_not(None))
        newest = Prediction.created_at.desc()
        rows = [
            *s.scalars(select(Prediction).where(*mine, Prediction.read_ok.is_not(None)).order_by(newest)),
            *s.scalars(
                select(Prediction)
                .where(*mine, Prediction.read_ok.is_(None), Prediction.published_key.is_not(None))
                .order_by(newest)
            ),
        ]
        current = sensor.roi
        slug = sensor.slug
    oldest = min((row.created_at for row in rows), default=None)
    budget = READING["export_max_mb"] * 1_000_000
    items: list[dict] = []
    listed = 0  # length of the readings so far in readings.json
    left_out = 0
    with target.open("wb") as file, zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            if not row.frame or not (path := storage.history_path(sensor_id, row.frame)).exists():
                continue
            if left_out:  # full
                left_out += 1
                continue
            details = row.probs or {}
            frame = imaging.decode(path.read_bytes())
            # Cut with the region it was read in; rows from before that was kept use today's region.
            region_then = "roi" in details
            box = imaging.region_box(details["roi"] if region_then else current, READING["export_margin"])
            image = imaging.encode_jpeg(imaging.crop_box(frame, box))
            # Room for readings.json still to come: it compresses to well under a fifth.
            if items and file.tell() + len(image) + listed // 4 + 100_000 > budget:
                left_out += 1
                continue
            name = f"images/{len(items) + 1:04d}.jpg"
            archive.writestr(name, image, compress_type=zipfile.ZIP_STORED)  # JPEG does not shrink
            items.append(
                {
                    "file": name,
                    "read": details.get("text"),
                    "value": details.get("value"),
                    "rejected": details.get("reason"),
                    "confidence": round(row.confidence, 4),
                    "answer": ANSWERS[row.read_ok],
                    "right_value": row.correct_value,
                    "date": row.created_at.date().isoformat(),  # the day only
                    "seconds": round((row.created_at - oldest).total_seconds()) if oldest else 0,
                    "region": "as read" if region_then else "current",
                    "reader": details.get("reader"),
                    "app": details.get("app"),
                    "greyscale": imaging.is_night(frame),
                    "lamp": details.get("lamp"),
                    **({"wheels": details["wheels"]} if "wheels" in details else {}),
                }
            )
            listed += len(json.dumps(items[-1]))
        if not items:
            raise LookupError("No readings to export yet")
        manifest = {
            "format": READINGS_FORMAT,
            "app_version": VERSION,
            "reader": readers.DEFAULT_WHEEL_READER if wheels else reader,
            "meter": meter.strip()[: READING["export_meter_max_chars"]] or None,
            # How the sensor reads; nothing about where it is (no camera, name or region position).
            "settings": {
                k: settings[k] for k in ("mode", "display", "digits", "counter_reader", "decimals", "unit", "max_step")
            },
            "left_out": left_out,
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
