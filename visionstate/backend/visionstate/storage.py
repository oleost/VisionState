"""File storage for sample images, history frames and thumbnails."""

from __future__ import annotations

import uuid
from pathlib import Path

from PIL import Image

from . import imaging
from .settings import Settings


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings

    def sample_path(self, sensor_id: int, filename: str) -> Path:
        return self.settings.samples_dir / str(sensor_id) / filename

    def history_path(self, sensor_id: int, filename: str) -> Path:
        return self.settings.history_dir / str(sensor_id) / filename

    def thumb_path(self, kind: str, item_id: int) -> Path:
        return self.settings.thumbs_dir / kind / f"{item_id}.jpg"

    @staticmethod
    def new_filename() -> str:
        return f"{uuid.uuid4().hex}.jpg"

    def _write(self, path: Path, image: Image.Image) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(imaging.encode_jpeg(image))

    def save_sample(self, sensor_id: int, image: Image.Image) -> str:
        filename = self.new_filename()
        self._write(self.sample_path(sensor_id, filename), image)
        return filename

    def save_history(self, sensor_id: int, image: Image.Image) -> str:
        filename = self.new_filename()
        self._write(self.history_path(sensor_id, filename), image)
        return filename

    def thumbnail(self, kind: str, item_id: int, source: Path) -> Path:
        """Cached thumbnail for a sample or history frame."""
        target = self.thumb_path(kind, item_id)
        if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
            self._write(target, imaging.thumbnail(Image.open(source).convert("RGB")))
        return target

    def delete_sample(self, sensor_id: int, sample_id: int, filename: str) -> None:
        self.sample_path(sensor_id, filename).unlink(missing_ok=True)
        self.thumb_path("sample", sample_id).unlink(missing_ok=True)

    def delete_history(self, sensor_id: int, prediction_id: int, filename: str | None) -> None:
        if filename:
            self.history_path(sensor_id, filename).unlink(missing_ok=True)
        self.thumb_path("history", prediction_id).unlink(missing_ok=True)

    @staticmethod
    def _folder_bytes(folder: Path) -> int:
        return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.exists() else 0

    def history_bytes(self) -> int:
        """History frames and their thumbnails on disk."""
        return self._folder_bytes(self.settings.history_dir) + self._folder_bytes(self.settings.thumbs_dir / "history")

    def samples_bytes(self) -> int:
        """Training images and their thumbnails on disk."""
        return self._folder_bytes(self.settings.samples_dir) + self._folder_bytes(self.settings.thumbs_dir / "sample")

    def delete_sensor(self, sensor_id: int) -> None:
        import shutil

        for directory in (self.settings.samples_dir / str(sensor_id), self.settings.history_dir / str(sensor_id)):
            shutil.rmtree(directory, ignore_errors=True)
