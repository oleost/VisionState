"""A backup of everything (export) and restoring one, which replaces everything.

What goes in is declared in ``settings.BACKUP_PARTS``: the database goes along whole (a part left
out has its rows deleted from the copy), each part's folders file by file. The archive:
``backup.json`` (``Manifest``), ``visionstate.db`` and ``files/<Settings folder>/<path>``.

A restore is staged first (``stage``: checked, extracted and the database migrated, all next to
the live data), then swapped in (``swap``) while the runtime is stopped (api/backup.py); the old
files are kept aside until the new runtime has started, and put back if anything fails.
"""

from __future__ import annotations

import json
import logging
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .bundle import BACKUP_MANIFEST as MANIFEST
from .bundle import MANIFEST as BUNDLE_MANIFEST
from .db import SCHEMA_VERSION, Database
from .redact import MASK, has_credentials, redact
from .settings import BACKUP, BACKUP_LEFT_OUT, BACKUP_PARTS, VERSION, Settings

log = logging.getLogger(__name__)

DATABASE = "visionstate.db"
FILES = "files/"
STAGE = ".restore"  # in the data folder: the uploaded archive and what it holds
ASIDE = ".restore-old"  # suffix of a folder kept aside during a swap
RENAME_TRIES = 25  # of 0.2 s each
DONE = "done"  # in STAGE: the restored data started, what was kept aside can go
# Training images older versions kept in the media folder: copies already moved are left out.
SHADOWED = {"old_samples_dir": "samples_dir"}


def required_parts() -> set[str]:
    return {name for name, part in BACKUP_PARTS.items() if not part["optional"]}


def folders(settings: Settings) -> dict[str, Path]:
    """Every folder a backup knows, by its Settings name: those of the parts, then the left-out ones."""
    names = [name for part in BACKUP_PARTS.values() for name in part["folders"]]
    return {name: getattr(settings, name) for name in (*names, *BACKUP_LEFT_OUT)}


def _files(settings: Settings, name: str) -> Iterator[tuple[Path, str]]:
    """(file, path inside the folder) of one folder of a part, sorted."""
    root = getattr(settings, name)
    if not root.is_dir():
        return
    shadow = getattr(settings, SHADOWED[name]) if name in SHADOWED else None
    if shadow is not None and root.resolve() == shadow.resolve():
        return  # the media folder set to the app's own storage: the same files
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if shadow is not None and (shadow / rel).exists():
            continue
        yield path, rel


def sizes(settings: Settings) -> dict[str, int]:
    """Bytes on disk per part (the sensors part: with the database)."""
    result = {}
    for name, part in BACKUP_PARTS.items():
        total = sum(path.stat().st_size for folder in part["folders"] for path, _ in _files(settings, folder))
        if not part["optional"]:
            total += sum(p.stat().st_size for p in _db_files(settings.db_path) if p.exists())
        result[name] = total
    return result


def _db_files(db_path: Path) -> list[Path]:
    return [db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")]


# --- Export ---------------------------------------------------------------------------------


def snapshot(db_path: Path, target: Path, parts: set[str], credentials: bool) -> dict:
    """A consistent copy of the live database (SQLite's backup, safe while it is written to)
    without the rows of the parts left out; camera credentials masked unless ``credentials``.
    Returns what the manifest says about it."""
    source = sqlite3.connect(db_path)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        source.close()
    try:
        copy.execute("PRAGMA foreign_keys=OFF")
        for name, part in BACKUP_PARTS.items():
            if name not in parts:
                for table in part["tables"]:
                    copy.execute(f'DELETE FROM "{table}"')
        masked = []
        for sensor_id, name, source_url in copy.execute("SELECT id, name, source FROM sensor").fetchall():
            if not credentials and has_credentials(source_url):
                copy.execute("UPDATE sensor SET source = ? WHERE id = ?", (redact(source_url), sensor_id))
                masked.append(name)
        sensors = [{"name": n, "kind": k} for n, k in copy.execute("SELECT name, kind FROM sensor ORDER BY id")]
        people = copy.execute("SELECT count(*) FROM person").fetchone()[0]
        schema = copy.execute("PRAGMA user_version").fetchone()[0]
        copy.commit()
        copy.execute("PRAGMA journal_mode=DELETE")  # one file, no -wal next to it
        copy.execute("VACUUM")
    finally:
        copy.close()
    return {"schema": schema, "sensors": sensors, "people": people, "masked": masked}


def write(settings: Settings, out: BinaryIO, parts: set[str], credentials: bool) -> None:
    """Writes a backup with ``parts`` (the required ones always) to ``out``, which need not be
    seekable (a download is streamed)."""
    parts = (parts | required_parts()) & set(BACKUP_PARTS)
    with tempfile.TemporaryDirectory(prefix="visionstate-backup-") as tmp:
        db_copy = Path(tmp) / DATABASE
        about = snapshot(settings.db_path, db_copy, parts, credentials)
        manifest = {
            "format": BACKUP["format"],
            "app_version": VERSION,
            "schema": about["schema"],
            "exported_at": datetime.now(UTC).isoformat(),
            "parts": sorted(parts),
            "credentials": credentials,
            "sensors": about["sensors"],
            "people": about["people"],
            "masked": about["masked"],
        }
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False), zipfile.ZIP_DEFLATED)
            archive.write(db_copy, DATABASE, zipfile.ZIP_DEFLATED)
            for name, part in BACKUP_PARTS.items():
                if name not in parts:
                    continue
                for folder in part["folders"]:
                    for path, rel in _files(settings, folder):
                        # Images and models hardly shrink: stored as they are, so a Raspberry Pi is not held up.
                        archive.write(path, f"{FILES}{folder}/{rel}")


class _QueueWriter:
    """A file-like ``write`` that hands pieces of ``BACKUP["chunk_kb"]`` to a queue; it gives up
    (OSError) once the reader is gone."""

    def __init__(self, pieces: queue.Queue, stopped: threading.Event):
        self.pieces, self.stopped = pieces, stopped
        self.buffer = bytearray()

    def write(self, data: bytes) -> int:
        self.buffer += data
        if len(self.buffer) >= BACKUP["chunk_kb"] * 1024:
            self._put(bytes(self.buffer))
            self.buffer.clear()
        return len(data)

    def flush(self) -> None:
        if self.buffer:
            self._put(bytes(self.buffer))
            self.buffer.clear()

    def _put(self, item: object) -> None:
        while not self.stopped.is_set():
            try:
                self.pieces.put(item, timeout=1)
                return
            except queue.Full:
                continue
        raise OSError("The download was stopped")


def stream(settings: Settings, parts: set[str], credentials: bool) -> Iterator[bytes]:
    """The backup as it is written, piece by piece (no temporary file of its size): a thread
    writes it, this yields the pieces. Stopping early (the download was cancelled) ends the thread."""
    pieces: queue.Queue = queue.Queue(maxsize=8)
    stopped = threading.Event()
    done = object()

    def produce() -> None:
        writer = _QueueWriter(pieces, stopped)
        try:
            write(settings, writer, parts, credentials)  # type: ignore[arg-type]  # write() is all ZipFile needs
            writer.flush()
            writer._put(done)
        except Exception as err:  # noqa: BLE001 - handed to the reader, which fails the download
            if not stopped.is_set():
                log.warning("Backup failed: %s", redact(str(err)))
                try:
                    writer._put(err)
                except OSError:
                    pass

    thread = threading.Thread(target=produce, name="backup", daemon=True)
    thread.start()
    try:
        while True:
            item = pieces.get()
            if item is done:
                return
            if isinstance(item, BaseException):
                raise item
            yield item  # type: ignore[misc]
    finally:
        stopped.set()


# --- Restore --------------------------------------------------------------------------------


@dataclass
class Staged:
    """A checked backup: its database in ``root``, each folder next to where it goes (``_stage_root``)."""

    root: Path
    manifest: dict
    slugs: set[str]
    people: set[str]
    masked: list[dict]  # sensors whose camera password was left out: {id, name}


def stage_dir(settings: Settings) -> Path:
    return settings.data_dir / STAGE


def _stage_root(settings: Settings, folder: str) -> Path:
    """Where a folder of the backup is extracted: on the same disk as the folder it replaces, so
    the swap only renames — the media folder may be network storage, and copying the history
    there would hold the stopped app for minutes (long enough for Home Assistant's watchdog)."""
    target = getattr(settings, folder)
    base = settings.media_dir if settings.media_dir in target.parents else settings.data_dir
    return base / STAGE / "new"


def clear_stage(settings: Settings) -> None:
    """Removes the staging folders (the data folder's and the media folder's)."""
    for base in (settings.data_dir, settings.media_dir):
        shutil.rmtree(base / STAGE, ignore_errors=True)


def _member_target(name: str, known: dict[str, Path], root: Path, settings: Settings) -> Path | None:
    """Where a member of the archive goes in a staging folder; None for one a backup never has."""
    if name in (MANIFEST, DATABASE):
        return root / name if name == DATABASE else None
    if not name.startswith(FILES) or "\\" in name:
        return None
    path = PurePosixPath(name[len(FILES) :])
    if path.is_absolute() or len(path.parts) < 2 or any(p in ("", ".", "..") for p in path.parts):
        return None
    folder = path.parts[0]
    if folder not in known or folder in BACKUP_LEFT_OUT:
        return None
    return _stage_root(settings, folder).joinpath(*path.parts)


def read_manifest(archive: zipfile.ZipFile) -> dict:
    if MANIFEST not in archive.namelist():
        if BUNDLE_MANIFEST in archive.namelist():
            raise ValueError("This is the export of one sensor: add it with Import a sensor")
        raise ValueError("This is not a VisionState backup (backup.json is missing)")
    info = archive.getinfo(MANIFEST)
    if info.file_size > 5_000_000:
        raise ValueError("backup.json is too large")
    manifest = json.loads(archive.read(info))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("format"), int):
        raise ValueError("This is not a VisionState backup (bad backup.json)")
    made_by = manifest.get("app_version", "?")
    if manifest["format"] > BACKUP["format"]:
        raise ValueError(f"This backup was made by a newer VisionState ({made_by}): update this app first")
    schema = manifest.get("schema")
    if not isinstance(schema, int) or schema < 1:
        raise ValueError("This is not a VisionState backup (no database version in backup.json)")
    if schema > SCHEMA_VERSION:
        raise ValueError(
            f"This backup was made by a newer VisionState ({made_by}, database version {schema}; "
            f"this app has {SCHEMA_VERSION}): update this app first"
        )
    return manifest


def stage(settings: Settings, upload: Path) -> Staged:
    """Checks the uploaded backup and extracts it next to the live data; migrates its database
    on that copy, so nothing live is touched until ``swap``. Raises ValueError when it is not a
    backup this version can restore."""
    root = stage_dir(settings) / "new"
    for base in (settings.data_dir, settings.media_dir):
        shutil.rmtree(base / STAGE / "new", ignore_errors=True)
    root.mkdir(parents=True)
    known = folders(settings)
    limit = BACKUP["max_restore_gb"] * 1_000_000_000
    with zipfile.ZipFile(upload) as archive:
        manifest = read_manifest(archive)
        members = [info for info in archive.infolist() if not info.is_dir()]
        if sum(info.file_size for info in members) > limit:
            raise ValueError(f"The backup is larger than {BACKUP['max_restore_gb']} GB unpacked")
        if DATABASE not in archive.namelist():
            raise ValueError("The backup has no database")
        for info in members:
            target = _member_target(info.filename, known, root, settings)
            if target is None:
                if info.filename != MANIFEST:
                    log.warning("Restore: left out %s (not something a backup holds)", info.filename[:200])
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
    _keep_known_models(_stage_root(settings, "models_dir") / "models_dir")
    db_path = root / DATABASE
    try:
        con = sqlite3.connect(db_path)
        try:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("The backup's database is damaged")
            # VisionState makes only tables and indexes: a trigger or view would run with the app's writes.
            if con.execute("SELECT count(*) FROM sqlite_master WHERE type NOT IN ('table', 'index')").fetchone()[0]:
                raise ValueError("The backup's database holds triggers or views VisionState never makes")
            con.execute("SELECT count(*) FROM sensor").fetchone()
        finally:
            con.close()
        db = Database(db_path)
        try:
            db.init()  # migrate an older backup here, before anything live is replaced
        finally:
            db.engine.dispose()
    except sqlite3.DatabaseError as err:
        raise ValueError(f"The backup's database cannot be read: {err}") from err
    single_file(db_path)
    con = sqlite3.connect(db_path)
    try:
        slugs = {row[0] for row in con.execute("SELECT slug FROM sensor")}
        people = {row[0] for row in con.execute("SELECT key FROM person")}
        rows = con.execute("SELECT id, name, source FROM sensor ORDER BY name").fetchall()
        masked = [{"id": i, "name": name} for i, name, source in rows if MASK in source]
    finally:
        con.close()
    return Staged(root, manifest, slugs, people, masked)


def _keep_known_models(folder: Path) -> None:
    """Only the models of the registries, as downloaded (SHA-256 checked like a download): the app
    loads a model file it finds without checking it again, so a backup must not bring another."""
    from .backbones import all_specs, sha256_file

    known = {spec.filename: spec.sha256 for spec in all_specs()}
    for path in sorted(folder.rglob("*")) if folder.is_dir() else []:
        if not path.is_file():
            continue
        name = path.relative_to(folder).as_posix()
        if name not in known or sha256_file(path) != known[name]:
            log.warning("Restore: left out the model file %s (not a known model, or changed)", name[:200])
            path.unlink()


def single_file(db_path: Path) -> None:
    """Everything in the one database file (no -wal next to it): only that file is moved in."""
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
    finally:
        con.close()


def _aside(path: Path) -> Path:
    return path.with_name(path.name + ASIDE)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _rename(path: Path, target: Path) -> None:
    """A rename that waits a moment for a file still open (Windows locks it until a worker thread
    of the stopped runtime lets go)."""
    for attempt in range(RENAME_TRIES):
        try:
            path.rename(target)
            return
        except PermissionError:
            if attempt == RENAME_TRIES - 1:
                raise
            time.sleep(0.2)


def _replaced(settings: Settings) -> list[Path]:
    """What a restore moves aside, in this order: the database files, then every folder it empties
    (see BACKUP_PARTS). The database goes first, so a restore cut off at any point has either
    left it in place or moved it aside (``clean_leftovers`` relies on it)."""
    folders = [
        getattr(settings, name)
        for part in BACKUP_PARTS.values()
        if part["restore"] == "replace"
        for name in part["folders"]
    ]
    folders += [getattr(settings, name) for name in BACKUP_LEFT_OUT]
    # A folder inside another one is moved with the outer one.
    unique = list(dict.fromkeys(folders))
    return [*_db_files(settings.db_path), *(p for p in unique if not any(q in p.parents for q in unique))]


def swap(settings: Settings, staged: Staged) -> list[tuple[Path, Path]]:
    """Moves the live database and folders aside and the staged ones in. Returns what was moved
    aside (for ``discard`` or ``undo``); on an error it puts everything back itself."""
    moved: list[tuple[Path, Path]] = []
    added: list[Path] = []
    try:
        for path in _replaced(settings):
            if path.exists():
                aside = _aside(path)
                _remove(aside)
                _rename(path, aside)
                moved.append((path, aside))
        added.append(settings.db_path)
        shutil.move(staged.root / DATABASE, settings.db_path)
        for part in BACKUP_PARTS.values():
            for folder in part["folders"]:
                source = _stage_root(settings, folder) / folder
                if not source.is_dir():
                    continue
                target = getattr(settings, folder)
                if part["restore"] == "add":
                    for path in sorted(source.rglob("*")):
                        if path.is_file():
                            dest = target / path.relative_to(source)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(path, dest)
                            added.append(dest)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(source, target)
        settings.ensure_dirs()
    except Exception:
        undo(settings, moved, added)
        raise
    return moved


def undo(settings: Settings, moved: list[tuple[Path, Path]], added: list[Path] | None = None) -> None:
    """Puts back what ``swap`` moved aside; the restored files are removed. Every step is tried,
    so one file that cannot be removed does not keep the rest of the old data away."""
    if added is None:  # the swap finished: the restored database is in place
        added = [settings.db_path]
    restored_db = settings.db_path in added
    for path in [*added, *(_db_files(settings.db_path)[1:] if restored_db else [])]:
        try:
            path.unlink(missing_ok=True)
        except OSError as err:
            log.warning("Restore: could not remove %s: %s", path.name, err)
    for path, aside in reversed(moved):
        try:
            _remove(path)
            _rename(aside, path)
        except OSError as err:
            log.error("Restore: could not put %s back (it is in %s): %s", path, aside.name, err)
    settings.ensure_dirs()


def discard(settings: Settings, moved: list[tuple[Path, Path]]) -> None:
    """The restored data runs: removes the old files and the staging folder."""
    stage_dir(settings).mkdir(parents=True, exist_ok=True)
    (stage_dir(settings) / DONE).touch()  # from here on, a start-up finishes this instead of undoing
    for _, aside in moved:
        _remove(aside)
    clear_stage(settings)


def clean_leftovers(settings: Settings) -> None:
    """At start-up, after a restore that was cut off (the app stopped halfway): the data moved
    aside is put back, unless the restored data had already started (then it is removed). What
    was never moved aside is the data from before and is left alone — the live database above all."""
    finished = (stage_dir(settings) / DONE).exists()
    candidates = [*_db_files(settings.db_path), *folders(settings).values()]
    asides = [(path, _aside(path)) for path in candidates if _aside(path).exists()]
    if asides and not finished:
        log.warning("A restore was cut off: the data from before it is put back")
        if _aside(settings.db_path).exists():
            # The database was moved aside: what is in its place (if anything) is the restored one.
            for path in _db_files(settings.db_path):
                path.unlink(missing_ok=True)
    for path, aside in asides:
        if finished:
            _remove(aside)
        else:
            _remove(path)
            aside.rename(path)
    clear_stage(settings)
