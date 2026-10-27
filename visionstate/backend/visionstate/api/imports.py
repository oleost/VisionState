"""Importing a sensor from an exported bundle (export is in sensors.py)."""

from __future__ import annotations

import asyncio
import logging
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from sqlalchemy import select

from .. import bundle, detectors, faces, imaging, readers
from ..db import Person, keep_change_step, keep_text_reader, keep_wheel_reader
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
    slugify,
    unique_name,
    unique_slug,
)
from .faces import add_face_row, new_person, taken_keys
from .samples import spool_upload

if TYPE_CHECKING:
    from .sensors import SensorIn

log = logging.getLogger(__name__)
router = APIRouter(prefix=API_PREFIX, tags=["import"])


@router.post("/import", status_code=201)
async def import_bundle(request: Request, file: UploadFile = File(...)) -> dict:
    """A new sensor from an exported bundle (validated like the API); a state sensor is trained."""
    rt = runtime(request)
    path, too_large = await asyncio.to_thread(spool_upload, file, "bundle.zip")
    if too_large:
        path.unlink(missing_ok=True)
        raise HTTPException(413, f"Bundle is larger than {UPLOAD_LIMITS['max_file_mb']} MB")
    try:
        sensor_id, skipped, faces_off = await asyncio.to_thread(_import_sync, rt, path)
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as err:
        raise HTTPException(400, f"Import failed: {redact(str(err))}") from err
    finally:
        path.unlink(missing_ok=True)
    await rt.sensor_created(sensor_id)
    await rt.people_changed()  # people that came with their faces
    cfg = await asyncio.to_thread(rt.load_sensor, sensor_id)
    if cfg and cfg.kind == KIND_STATES:
        rt.schedule_retrain(sensor_id, delay=0)
    # faces_off: it recognised faces where it was exported; the UI says to turn that on again here.
    return {"id": sensor_id, "skipped": skipped, "faces_off": faces_off}


def _import_sync(rt: Runtime, path: Path) -> tuple[int, int, bool]:
    """Creates the sensor of a bundle; returns its ID, how many of its images were unreadable and
    whether face recognition, on where it was exported, was turned off."""
    manifest = bundle.read_manifest(path)
    data = dict(manifest["sensor"])
    data["reading"] = keep_text_reader(data.get("reading"))  # exported before the wheel reader
    # exported before a counter had a change limit of its own
    data["reading"] = keep_change_step(data["reading"])
    data["reading"] = keep_wheel_reader(data["reading"])  # exported before a counter named its wheel reader
    if isinstance(data["reading"], dict) and data["reading"].get("wheel_reader") not in (None, *readers.WHEEL_READERS):
        # exported by a newer version, with a wheel reader this one does not have: the default here
        data["reading"] = {**data["reading"], "wheel_reader": ""}
    try:
        spec = validated_sensor(data)
    except ValueError as err:
        raise ValueError(f"invalid sensor in bundle: {err}") from err
    faces_off = False
    if spec.objects is not None and spec.objects.faces is not None:
        # Taught faces come along, face recognition does not: it is turned on there, with its
        # own confirmation (and download).
        faces_off = bool(spec.objects.faces.enabled)
        spec.objects.faces.enabled = False
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
    stored = manifest.get("faces", []) if spec.kind == KIND_OBJECTS else []
    for item in stored if isinstance(stored, list) else []:
        try:
            _import_face(rt, path, sensor_id, item)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, HTTPException) as err:
            skipped += 1
            log.warning("Import: skipping a face: %s", getattr(err, "detail", err))
    return sensor_id, skipped, faces_off


SENSOR_FIELDS = (
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


# Pydantic's errors for a value outside a limit (a number, a length), not one this version does not know.
LIMIT_ERRORS = frozenset(
    {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "too_short",
        "too_long",
        "string_too_short",
        "string_too_long",
    }
)


class OutOfLimits(ValueError):
    """Every setting is one this version knows, but a value is outside today's limits."""


def validated_sensor(data: dict) -> SensorIn:
    """A sensor's settings validated exactly like a sensor made in the UI (kind, limits, source
    type, states, objects, readings, triggers): a sensor bundle and every sensor of a restored
    backup (api/backup.py). ValueError says what is wrong — often a setting of a newer version;
    OutOfLimits (a ValueError too) when it is only a value outside the limits."""
    from pydantic import ValidationError

    from .sensors import SensorIn

    try:
        return SensorIn.model_validate({k: data[k] for k in SENSOR_FIELDS if data.get(k) is not None}).checked()
    except ValidationError as err:
        errors = err.errors()
        error = errors[0]
        where = ".".join(str(part) for part in error.get("loc", ()))
        message = f"{where}: {error.get('msg')}" if where else str(error.get("msg"))
        if all(e.get("type") in LIMIT_ERRORS for e in errors):
            raise OutOfLimits(message) from err
        raise ValueError(message) from err
    except HTTPException as err:
        raise ValueError(str(err.detail)) from err


def _import_face(rt: Runtime, path: Path, sensor_id: int, item: dict) -> None:
    """A taught face from a bundle, for its person: the one with that key (or name), else a new one."""
    face = faces.face_to_dict(faces.face_from_dict(item["face"]))
    image = imaging.decode(bundle.read_sample_bytes(path, str(item["file"]), "faces"))
    key, name = str(item["person"]), str(item.get("name") or item["person"]).strip()[:64]
    with rt.db.session() as s:
        person = s.scalar(select(Person).where(Person.key == key))
        if person is None:
            person = next((p for p in s.scalars(select(Person)) if p.name.lower() == name.lower()), None)
        if person is None:
            person = new_person(s, name)
            if key not in taken_keys(s) and slugify(key, "") == key:
                person.key = key  # the same entity IDs as where it was exported
        filename = rt.storage.save_face(image)
        add_face_row(s, person.id, sensor_id, filename, image, face, "import")


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
