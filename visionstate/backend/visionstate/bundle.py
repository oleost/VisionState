"""Export and import of sensors as portable ZIP bundles."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from .db import Database, Sample, Sensor
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
                    }
                )
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
        return f"visionstate-{sensor.slug}.zip"


def read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        if MANIFEST not in archive.namelist():
            raise ValueError("Not a VisionState bundle (manifest.json missing)")
        manifest = json.loads(archive.read(MANIFEST))
    if manifest.get("schema", 0) > BUNDLE_SCHEMA:
        raise ValueError("Bundle was created by a newer VisionState version")
    return manifest


def read_sample_bytes(path: Path, filename: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(f"samples/{filename}")
        if info.file_size > UPLOAD_LIMITS["max_zip_member_mb"] * 1_000_000:
            raise ValueError(f"{filename} is too large")
        return archive.read(info)
