"""The history: clean-up by age and size, and the disk use shown in the settings."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

from sqlalchemy import delete, func, select

from ..db import Prediction, Sample, utcnow
from ..settings import HISTORY_CLEANUP, RUNTIME, SAMPLES_MOVE, STORAGE_DEFAULTS
from .base import RuntimeBase
from .publishing import PublishingMixin

log = logging.getLogger(__name__)

HistoryRow = tuple[int, int, str | None]  # a history row's id, sensor_id and frame


class HistoryMixin(PublishingMixin, RuntimeBase):
    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.move_samples)
            except Exception:  # noqa: BLE001
                log.exception("Moving training images failed")
            try:
                await asyncio.to_thread(self.cleanup_history)
                await self.publish_review_count()
            except Exception:  # noqa: BLE001
                log.exception("Cleanup failed")
            await asyncio.sleep(RUNTIME["cleanup_interval_s"])

    def move_samples(self) -> None:
        """Copy the training images older versions kept in the media folder to the app's own
        storage (Settings.samples_dir), where a backup holds them together with the database.

        Until then they are read where they are (Storage.sample_path). The old copies stay for
        SAMPLES_MOVE["keep_old_days"] after the first move, so the older version still finds its
        images when the backup made before the update is restored; then they are removed.
        """
        old_root = self.settings.old_samples_dir
        if not old_root.exists() or old_root.resolve() == self.settings.samples_dir.resolve():
            return  # nothing to move (or the media folder is set to the app's storage itself)
        with self.db.session() as s:
            rows = s.execute(select(Sample.sensor_id, Sample.filename)).all()
        copied = 0
        for sensor_id, filename in rows:
            old = self.storage.old_sample_path(sensor_id, filename)
            new = self.storage.new_sample_path(sensor_id, filename)
            if new.exists() or not old.exists():
                continue
            part = new.with_name(f"{new.name}.part")
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, part)
                part.replace(new)  # whole or not at all
            except FileNotFoundError:  # deleted meanwhile
                part.unlink(missing_ok=True)
                continue
            except OSError as err:  # disk full: the rest is read from the media folder and tried later
                part.unlink(missing_ok=True)
                log.warning("Could not move training images to the app's storage: %s", err)
                return
            if not old.exists():  # deleted while it was copied (Storage.delete_sample removes both)
                new.unlink(missing_ok=True)
                continue
            copied += 1
        if copied:
            log.info("Copied %d training images from %s to the app's storage", copied, old_root)
        moved_at = self.db.get_setting("samples_moved_at")
        if not isinstance(moved_at, str):
            self.db.set_setting("samples_moved_at", utcnow().isoformat())
            return
        if utcnow() - datetime.fromisoformat(moved_at) < timedelta(days=SAMPLES_MOVE["keep_old_days"]):
            return
        removed = 0
        for sensor_id, filename in rows:
            old = self.storage.old_sample_path(sensor_id, filename)
            if old.exists() and self.storage.new_sample_path(sensor_id, filename).exists():
                old.unlink()
                removed += 1
        shutil.rmtree(self.settings.thumbs_dir / "sample", ignore_errors=True)  # older versions' thumbnails
        for folder in [*old_root.iterdir(), old_root]:
            if folder.is_dir():
                try:
                    folder.rmdir()  # only when empty: what no sensor of this app knows is left alone
                except OSError:
                    pass
        if removed:
            log.info("Removed %d moved training images from %s", removed, old_root)

    def storage_limits(self) -> dict:
        """Effective history limits (Settings → Storage), with the defaults for what is not set."""
        return {
            "history_days": int(self.storage_rules.get("history_days") or STORAGE_DEFAULTS["history_days"]),
            "history_max_gb": float(self.storage_rules.get("history_max_gb", STORAGE_DEFAULTS["history_max_gb"])),
        }

    def set_storage_limits(self, rules: dict) -> None:
        self.storage_rules = dict(rules)
        self.history_trimmed = False  # the next clean-up tells whether the new size limit still bites
        self.db.set_setting("storage", self.storage_rules)

    def _history_file_size(self, prediction_id: int, sensor_id: int, frame: str | None) -> int:
        size = 0
        for path in (
            self.storage.history_path(sensor_id, frame) if frame else None,
            self.storage.thumb_path("history", prediction_id),
        ):
            if path is not None and path.exists():
                size += path.stat().st_size
        return size

    def _remove_history(self, rows: Sequence[HistoryRow]) -> None:
        """Remove history rows (id, sensor_id, frame); their files go once that is committed
        (db.Database.after_commit), so a failed commit never leaves rows without their frames."""
        for start in range(0, len(rows), HISTORY_CLEANUP["batch"]):
            batch = rows[start : start + HISTORY_CLEANUP["batch"]]
            with self.db.session() as s:
                s.execute(delete(Prediction).where(Prediction.id.in_([row[0] for row in batch])))
                self.db.after_commit(s, partial(self._delete_history_files, batch))

    def _delete_history_files(self, rows: Sequence[HistoryRow]) -> None:
        for prediction_id, sensor_id, frame in rows:
            self.storage.delete_history(sensor_id, prediction_id, frame)

    def cleanup_history(self) -> None:
        """Remove history older than the limit (frames waiting for review get twice as long),
        then the oldest frames while the history is larger than its size limit, then frames no
        row lists any more. Readings verified by hand are kept."""
        limits = self.storage_limits()
        days = limits["history_days"]
        cutoff = utcnow() - timedelta(days=days)
        review_cutoff = utcnow() - timedelta(days=days * 2)
        with self.db.session() as s:
            old: list[HistoryRow] = [
                (row.id, row.sensor_id, row.frame)
                for row in s.execute(
                    select(Prediction.id, Prediction.sensor_id, Prediction.frame).where(
                        Prediction.created_at < cutoff,
                        (Prediction.reviewed.is_(True)) | (Prediction.created_at < review_cutoff),
                        Prediction.read_ok.is_(None),  # verified readings are kept, like training images
                    )
                )
            ]
        self._remove_history(old)
        if old:
            log.info("Removed %d old history frames", len(old))
        trimmed = self._trim_history(limits["history_max_gb"])
        if trimmed:
            self.history_trimmed = True
            log.info("Removed %d history frames to stay under %.1f GB", trimmed, limits["history_max_gb"])
        orphans = self.remove_orphan_frames()
        if orphans:
            log.info("Removed %d history frames that no history row listed", orphans)

    def _trim_history(self, max_gb: float) -> int:
        """Remove the oldest history frames until all of them fit in ``max_gb`` (0 = no limit).

        Reviewed frames go first, frames still waiting for review only when that is not enough;
        verified readings are never removed. Returns how many rows were removed.
        """
        if max_gb <= 0:
            return 0
        budget = int(max_gb * 1024**3)
        used = self.storage.history_bytes()
        if used <= budget:
            return 0
        removed = 0
        for waiting in (False, True):
            with self.db.session() as s:
                rows: list[HistoryRow] = [
                    (row.id, row.sensor_id, row.frame)
                    for row in s.execute(
                        select(Prediction.id, Prediction.sensor_id, Prediction.frame)
                        .where(Prediction.reviewed.is_(not waiting), Prediction.read_ok.is_(None))
                        .order_by(Prediction.created_at)
                    )
                ]
            chosen: list[HistoryRow] = []
            for row in rows:
                if used <= budget:
                    break
                used -= self._history_file_size(*row)
                chosen.append(row)
            self._remove_history(chosen)
            removed += len(chosen)
            if used <= budget:
                break
        return removed

    def remove_orphan_frames(self) -> int:
        """History frames and thumbnails no row lists (the app stopped between writing a frame and
        its row, or a row went while its file could not be removed), once they are older than
        HISTORY_CLEANUP["orphan_age_s"]: a frame is written just before its row. Only the history
        folders are looked at. Returns how many files were removed."""
        with self.db.session() as s:
            listed = {
                (sensor_id, frame) for sensor_id, frame in s.execute(select(Prediction.sensor_id, Prediction.frame))
            }
            ids = set(s.scalars(select(Prediction.id)))
        before = time.time() - HISTORY_CLEANUP["orphan_age_s"]
        removed = 0
        candidates: list[tuple[Path, bool]] = []
        for history in (self.settings.history_dir, self.settings.clips_dir):
            if not history.is_dir():
                continue
            for folder in history.iterdir():
                if folder.is_dir() and folder.name.isdigit():
                    sensor_id = int(folder.name)
                    candidates += [(f, (sensor_id, f.name) in listed) for f in folder.iterdir() if f.is_file()]
        thumbs = self.settings.thumbs_dir / "history"
        if thumbs.is_dir():
            candidates += [(f, f.stem.isdigit() and int(f.stem) in ids) for f in thumbs.iterdir() if f.is_file()]
        for path, known in candidates:
            if known:
                continue
            try:
                if path.stat().st_mtime < before:
                    path.unlink()
                    removed += 1
            except OSError:  # gone meanwhile, or cannot be removed: tried again next time
                pass
        return removed

    def storage_usage(self) -> dict:
        """Disk use for the Settings page: history, training images and free space."""
        with self.db.session() as s:
            frames = s.scalar(select(func.count()).select_from(Prediction).where(Prediction.frame.is_not(None))) or 0
            oldest = s.scalar(select(func.min(Prediction.created_at)).where(Prediction.frame.is_not(None)))
            images = s.scalar(select(func.count()).select_from(Sample)) or 0
        media = self.settings.media_dir
        media.mkdir(parents=True, exist_ok=True)
        # History goes to the media folder, training images to the app's storage: the fuller one counts.
        free = min(shutil.disk_usage(media).free, shutil.disk_usage(self.settings.data_dir).free)
        return {
            "history_bytes": self.storage.history_bytes(),
            "history_frames": frames,
            "oldest_history": oldest,
            "training_bytes": self.storage.samples_bytes(),
            "training_images": images,
            "free_bytes": free,
            "limited_by_size": self.history_trimmed,
            **self.storage_limits(),
        }
