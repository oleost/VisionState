"""Backup of everything and restoring one (backup.py; the parts are settings.BACKUP_PARTS)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import zipfile
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from .. import backup
from ..db import Database, Sensor
from ..engine import Runtime
from ..engine.state import SensorConfig
from ..mqtt import SensorDescriptor
from ..redact import redact
from ..settings import BACKUP, BACKUP_PARTS
from .common import API_PREFIX, runtime
from .imports import SENSOR_FIELDS, validated_sensor

log = logging.getLogger(__name__)
router = APIRouter(prefix=API_PREFIX, tags=["backup"])


@router.get("/backup")
async def backup_info(request: Request) -> dict:
    """Bytes on disk per part and the free space here (a restore needs about twice its size)."""
    settings = runtime(request).settings
    sizes = await asyncio.to_thread(backup.sizes, settings)
    return {"parts": sizes, "free": shutil.disk_usage(settings.data_dir).free}


@router.get("/backup/export")
async def backup_export(request: Request, leave_out: str = "", credentials: bool = False) -> StreamingResponse:
    """Everything as one ZIP, streamed while it is written; ``leave_out``: parts not wanted
    (comma-separated), ``credentials``: with the camera passwords."""
    left_out = {part for part in leave_out.split(",") if part}
    unknown = left_out - set(BACKUP_PARTS)
    if unknown:
        raise HTTPException(422, f"Unknown parts: {', '.join(sorted(unknown))}")
    if left_out & backup.required_parts():
        raise HTTPException(422, "The sensors are always in a backup")
    parts = set(BACKUP_PARTS) - left_out
    settings = runtime(request).settings
    filename = f"visionstate-backup-{datetime.now().strftime('%Y-%m-%d-%H%M')}.zip"
    return StreamingResponse(
        backup.stream(settings, parts, credentials),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/backup/restore")
async def backup_restore(request: Request) -> dict:
    """Replaces everything with a backup (the request body is the ZIP): staged and checked
    first, then swapped in while the runtime is stopped; on any error the data from before stays."""
    app = request.app
    if getattr(app.state, "restoring", False):
        raise HTTPException(409, "A restore is already running")
    app.state.restoring = True
    try:
        return await _restore(request)
    finally:
        app.state.restoring = False
        app.state.ready.set()


async def _restore(request: Request) -> dict:
    old: Runtime = runtime(request)
    settings = old.settings
    limit = BACKUP["max_restore_gb"] * 1_000_000_000
    length = int(request.headers.get("content-length") or 0)
    if length > limit:
        raise HTTPException(413, f"The backup is larger than {BACKUP['max_restore_gb']} GB")
    # The upload, then what it holds, then the old data kept aside until the new runs.
    need = 2 * length + BACKUP["free_margin_mb"] * 1_000_000
    if length and shutil.disk_usage(settings.data_dir).free < need:
        raise HTTPException(
            507, f"Not enough free disk space: a restore of this backup needs about {need // 1_000_000} MB"
        )
    folder = backup.stage_dir(settings)
    await asyncio.to_thread(backup.clear_stage, settings)
    upload = folder / "upload.zip"
    try:
        folder.mkdir(parents=True)
        size = 0
        with upload.open("wb") as file:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"The backup is larger than {BACKUP['max_restore_gb']} GB")
                await asyncio.to_thread(file.write, chunk)
        try:
            staged = await asyncio.to_thread(backup.stage, settings, upload)
            after = await asyncio.to_thread(_staged_sensors, staged)
        except (ValueError, KeyError, OSError, zipfile.BadZipFile) as err:
            raise HTTPException(400, f"Restore failed: {redact(str(err))}") from err
        upload.unlink(missing_ok=True)
        await _swap(request, old, staged, after)
    finally:
        await asyncio.to_thread(backup.clear_stage, settings)
    manifest = staged.manifest
    return {
        "sensors": len(staged.slugs),
        "people": len(staged.people),
        "parts": manifest.get("parts", []),
        "app_version": manifest.get("app_version"),
        "exported_at": manifest.get("exported_at"),
        "masked": staged.masked,
    }


async def _swap(request: Request, old: Runtime, staged: backup.Staged, after: dict[str, SensorDescriptor]) -> None:
    """Stops the runtime, swaps the data and starts a runtime on it; puts the old data back and
    starts on that when anything fails. ``after``: the entities of the backup's sensors."""
    app = request.app
    settings = old.settings
    # The entities of sensors and people the backup does not have go away in Home Assistant;
    # the others keep their unique IDs, and with them their entity IDs.
    before = await asyncio.to_thread(_descriptors, old.db)
    for slug, descriptor in before.items():
        if slug in after:
            await old.mqtt.remove_stale(descriptor, after[slug])
        else:
            await old.mqtt.remove_discovery(descriptor)
    for key, _ in await asyncio.to_thread(old._people):
        if key not in staged.people:
            await old.mqtt.remove_person(key)
    app.state.ready.clear()  # other requests wait for the new runtime
    await old.stop()
    await _closed(old.db)
    await asyncio.to_thread(old.release)  # the new runtime loads the models again
    try:
        moved = await asyncio.to_thread(backup.swap, settings, staged)
    except Exception as err:
        log.exception("Restore: swapping the data failed; the data from before is kept")
        await _start(app, settings)
        raise HTTPException(500, f"Restore failed: {redact(str(err))}") from err
    try:
        await _start(app, settings)
    except Exception as err:
        log.exception("Restore: the restored data did not start; the data from before is put back")
        await _stop(app)
        await asyncio.to_thread(backup.undo, settings, moved)
        await _start(app, settings)
        raise HTTPException(500, f"Restore failed: {redact(str(err))}") from err
    await asyncio.to_thread(backup.discard, settings, moved)
    log.info("Restored a backup made by VisionState %s", staged.manifest.get("app_version"))


def _descriptors(db: Database) -> dict[str, SensorDescriptor]:
    """Each sensor's entities, by slug."""
    with db.session() as s:
        return {row.slug: SensorConfig.from_row(row).descriptor for row in s.scalars(select(Sensor))}


def _staged_sensors(staged: backup.Staged) -> dict[str, SensorDescriptor]:
    """The entities of the backup's sensors, by slug. Each sensor is checked like a sensor import
    (imports.validated_sensor): a setting this version does not know refuses the restore before
    anything is stopped (the database version alone does not show a new choice in a setting)."""
    db = Database(staged.root / backup.DATABASE)
    try:
        with db.session() as s:
            rows = s.scalars(select(Sensor)).all()
            for row in rows:
                data = {key: getattr(row, key) for key in SENSOR_FIELDS if key != "states"}
                data["states"] = [{"key": st.key, "name": st.name, "color": st.color} for st in row.states]
                try:
                    validated_sensor(data)
                except ValueError as err:
                    raise ValueError(
                        f"the sensor {row.name!r} has a setting this version does not know ({err}). "
                        f"The backup was made by VisionState {staged.manifest.get('app_version', '?')}: "
                        "update this app first"
                    ) from err
            return {row.slug: SensorConfig.from_row(row).descriptor for row in rows}
    finally:
        db.engine.dispose()
        backup.single_file(staged.root / backup.DATABASE)


async def _start(app, settings) -> None:  # noqa: ANN001
    db = Database(settings.db_path)
    try:
        db.init()
        rt = Runtime(settings, db)
        app.state.runtime = rt
        await rt.start()
    except Exception:
        db.engine.dispose()
        raise
    finally:
        app.state.ready.set()


async def _stop(app) -> None:  # noqa: ANN001
    rt: Runtime = app.state.runtime
    app.state.ready.clear()
    await rt.stop()
    await _closed(rt.db)
    await asyncio.to_thread(rt.release)


async def _closed(db: Database) -> None:
    """Closes a stopped runtime's database and waits for the work still running in its threads
    (a training, a clean-up) to let go of it."""
    in_use = db.close()
    for _ in range(int(BACKUP["release_wait_s"] / 0.1)):
        if in_use() == 0:
            return
        await asyncio.sleep(0.1)
    log.warning("Restore: %d database connections still in use after %s s", in_use(), BACKUP["release_wait_s"])
