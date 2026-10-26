"""Backup of everything and restoring one (backup.py; the parts are settings.BACKUP_PARTS)."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from .. import backup
from ..db import Database, Person, Sensor
from ..engine import Runtime
from ..engine.state import SensorConfig
from ..mqtt import MqttBridge, SensorDescriptor
from ..redact import redact
from ..settings import BACKUP, BACKUP_PARTS, Settings
from .common import API_PREFIX, runtime
from .imports import SENSOR_FIELDS, OutOfLimits, validated_sensor

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
    if length:
        _check_space(settings, length, uploaded=False)
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
        _check_space(settings, size, uploaded=True)  # also for an upload that did not say its size
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


def _check_space(settings: Settings, size: int, uploaded: bool) -> None:
    """Room for a restore of ``size`` bytes: the upload (unless ``uploaded``) and what it holds,
    which is extracted next to the folders it replaces (backup._stage_root) — the history on the
    media folder's disk, which may be another one. The data from before is only renamed."""
    margin = BACKUP["free_margin_mb"] * 1_000_000
    need = {settings.data_dir: size + margin + (0 if uploaded else size)}
    if os.stat(settings.media_dir).st_dev != os.stat(settings.data_dir).st_dev:
        need[settings.media_dir] = size + margin
    for folder, amount in need.items():
        if shutil.disk_usage(folder).free < amount:
            raise HTTPException(
                507,
                f"Not enough free disk space in {folder}: a restore of this backup needs about "
                f"{amount // 1_000_000} MB there",
            )


@dataclass
class _Gone:
    """What the data before a restore had in Home Assistant and the backup does not. Forgotten once
    the restored data runs, not before: a restore that fails keeps the old data, entities and all."""

    sensors: list[SensorDescriptor]
    changed: list[tuple[SensorDescriptor, SensorDescriptor]]  # (before, after) of the same slug
    people: list[str]

    async def forget(self, mqtt: MqttBridge) -> None:
        # The sensors in the backup keep their unique IDs, and with them their entity IDs.
        for descriptor in self.sensors:
            await mqtt.remove_discovery(descriptor)
        for before, after in self.changed:
            await mqtt.remove_stale(before, after)
        for key in self.people:
            await mqtt.remove_person(key)


async def _swap(request: Request, old: Runtime, staged: backup.Staged, after: dict[str, SensorDescriptor]) -> None:
    """Stops the runtime, swaps the data and starts a runtime on it; puts the old data back and
    starts on that when anything fails. ``after``: the entities of the backup's sensors."""
    app = request.app
    settings = old.settings
    before = await asyncio.to_thread(_descriptors, old.db)
    gone = _Gone(
        sensors=[descriptor for slug, descriptor in before.items() if slug not in after],
        changed=[(descriptor, after[slug]) for slug, descriptor in before.items() if slug in after],
        people=[key for key in await asyncio.to_thread(_person_keys, old.db) if key not in staged.people],
    )
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
        await _start(app, settings, gone.forget)
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


def _person_keys(db: Database) -> list[str]:
    with db.session() as s:
        return list(s.scalars(select(Person.key)))


def _staged_sensors(staged: backup.Staged) -> dict[str, SensorDescriptor]:
    """The entities of the backup's sensors, by slug. Each sensor is checked like a sensor import
    (imports.validated_sensor): a setting this version does not know refuses the restore before
    anything is stopped (the database version alone does not show a new choice in a setting). A
    value outside today's limits is kept, as an update keeps it: a limit made narrower since must
    not keep anyone from restoring their own backup."""
    db = Database(staged.root / backup.DATABASE)
    try:
        with db.session() as s:
            rows = s.scalars(select(Sensor)).all()
            for row in rows:
                data = {key: getattr(row, key) for key in SENSOR_FIELDS if key != "states"}
                data["states"] = [{"key": st.key, "name": st.name, "color": st.color} for st in row.states]
                try:
                    validated_sensor(data)
                except OutOfLimits as err:
                    log.warning("Restore: the sensor %r has a setting outside the limits (%s), kept", row.name, err)
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


async def _start(app, settings: Settings, on_connect: Callable[[MqttBridge], Awaitable[None]] | None = None) -> None:  # noqa: ANN001
    """A runtime on the data there is now; ``on_connect``: done once its MQTT is connected."""
    db = Database(settings.db_path)
    try:
        await asyncio.to_thread(db.init)  # an older backup's database is upgraded here
        rt = Runtime(settings, db)
        if on_connect is not None:
            rt.on_first_connect.append(on_connect)
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
    """Closes a stopped runtime's database: waits for the work still running in its threads (a
    training, a clean-up) to let go of it, then closes its connections."""
    in_use = db.close()
    for _ in range(int(BACKUP["release_wait_s"] / 0.1)):
        if in_use() == 0:
            break
        await asyncio.sleep(0.1)
    else:
        log.warning("Restore: %d database connections still in use after %s s", in_use(), BACKUP["release_wait_s"])
    db.engine.dispose()
