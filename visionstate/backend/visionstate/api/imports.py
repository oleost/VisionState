"""Importing a sensor from an exported bundle (export is in sensors.py)."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from .. import bundle, detectors, imaging
from ..engine import Runtime
from ..redact import redact
from ..settings import (
    KIND_OBJECTS,
    KIND_STATES,
    UPLOAD_LIMITS,
)
from . import teach
from .common import (
    API_PREFIX,
    runtime,
    unique_name,
    unique_slug,
)
from .samples import copy_limited

log = logging.getLogger(__name__)
router = APIRouter(prefix=API_PREFIX, tags=["import"])


@router.post("/import", status_code=201)
async def import_bundle(request: Request, file: UploadFile = File(...)) -> dict:
    """A new sensor from an exported bundle (validated like the API); a state sensor is trained."""
    rt = runtime(request)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        path = Path(tmp.name)
        within_limit = copy_limited(file.file, tmp, UPLOAD_LIMITS["max_file_mb"] * 1_000_000)
    if not within_limit:
        path.unlink(missing_ok=True)
        raise HTTPException(413, f"Bundle is larger than {UPLOAD_LIMITS['max_file_mb']} MB")
    try:
        sensor_id, skipped = await asyncio.to_thread(_import_sync, rt, path)
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as err:
        raise HTTPException(400, f"Import failed: {redact(str(err))}") from err
    finally:
        path.unlink(missing_ok=True)
    await rt.sensor_created(sensor_id)
    cfg = await asyncio.to_thread(rt.load_sensor, sensor_id)
    if cfg and cfg.kind == KIND_STATES:
        rt.schedule_retrain(sensor_id, delay=0)
    return {"id": sensor_id, "skipped": skipped}


def _import_sync(rt: Runtime, path: Path) -> tuple[int, int]:
    """Creates the sensor of a bundle; returns its ID and how many of its images were unreadable."""
    from pydantic import ValidationError

    from .sensors import SensorIn

    manifest = bundle.read_manifest(path)
    data = manifest["sensor"]
    # Validate exactly like a sensor created in the UI (kind, limits, source type, states, objects, triggers).
    try:
        spec = SensorIn.model_validate(
            {
                key: data[key]
                for key in (
                    "name",
                    "kind",
                    "source_type",
                    "source",
                    "roi",
                    "states",
                    "objects",
                    "reading",
                    "interval_s",
                    "threshold",
                    "debounce",
                    "triggers",
                    "review",
                    "publish",
                )
                if data.get(key) is not None
            }
        )
        spec.checked()
    except ValidationError as err:
        raise ValueError(f"invalid sensor in bundle: {err.errors()[0].get('msg')}") from err
    except HTTPException as err:
        raise ValueError(f"invalid sensor in bundle: {err.detail}") from err
    samples = manifest.get("samples", []) if spec.kind in (KIND_STATES, KIND_OBJECTS) else []
    if not isinstance(samples, list) or len(samples) > UPLOAD_LIMITS["max_zip_members"]:
        raise ValueError("too many samples in bundle")
    # Object sensors: own labels and taught boxes come along (the rest of "objects" is validated above).
    custom = teach.import_custom((data.get("objects") or {}).get("custom")) if spec.kind == KIND_OBJECTS else []
    spec.enabled = True
    with rt.db.session() as s:
        spec.name = unique_name(s, spec.name)
        sensor = spec.new_sensor(unique_slug(s, spec.name))
        # Keep the entity ID style, so moving a sensor (e.g. between the stable and the beta app)
        # does not change its entity IDs. Bundles without the field are older: those had the prefix.
        sensor.entity_prefix = bool(data.get("entity_prefix", True))
        if custom:
            sensor.objects = {**(sensor.objects or {}), "custom": custom}
        s.add(sensor)
        s.flush()
        sensor_id = sensor.id
        state_ids = {st.key: st.id for st in sensor.states}
    own = {label["key"] for label in custom}
    skipped = 0
    # The sensor exists now: a broken image is skipped (like in an uploaded ZIP) instead of
    # failing the import halfway and leaving a sensor that was never started.
    for item in samples:
        try:
            _import_sample(rt, path, sensor_id, spec.kind, item, own, state_ids)
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as err:
            skipped += 1
            log.warning("Import: skipping a sample: %s", err)
    return sensor_id, skipped


def _import_sample(rt: Runtime, path: Path, sensor_id: int, kind: str, item: dict, own: set, state_ids: dict) -> None:
    if kind == KIND_OBJECTS:
        if not teach.importable_label(item.get("object_label"), own):
            return
        image = imaging.decode(bundle.read_sample_bytes(path, str(item["file"])))
        detected = item.get("detected")
        rt.add_object_example(
            sensor_id,
            image,
            item.get("box") or [0.0, 0.0, 1.0, 1.0],
            item["object_label"],
            detected if detected in detectors.LABELS.by_key else None,
            item.get("score"),
            "import",
            cropped=True,
        )
        return
    image = imaging.decode(bundle.read_sample_bytes(path, str(item["file"])))
    labels = [state_ids[k] for k in item.get("labels", []) if k in state_ids]
    rt.add_sample(sensor_id, image, "import", labels[0] if labels else None, bool(item.get("use_roi", True)))
