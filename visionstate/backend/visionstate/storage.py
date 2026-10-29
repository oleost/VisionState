"""File storage for sample images, history frames and thumbnails."""

from __future__ import annotations

import uuid
from pathlib import Path

from PIL import Image

from . import imaging
from .settings import THUMB_SIZE, Settings

CLIP_SUFFIX = ".m4a"  # a sound clip (AAC), see sounds.encode_clip
VIDEO_CLIP_SUFFIX = ".mp4"  # a sound sensor's clip with the camera's video (sources.AudioStream.video_clip)


def is_clip(filename: str | None) -> bool:
    """A sound sensor's clip (sound only, or with video), not a camera frame."""
    return bool(filename) and str(filename).endswith((CLIP_SUFFIX, VIDEO_CLIP_SUFFIX))


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings

    def sample_path(self, sensor_id: int, filename: str) -> Path:
        """A training image; one not moved yet from the media folder is read where it is."""
        path = self.new_sample_path(sensor_id, filename)
        if not path.exists():
            old = self.old_sample_path(sensor_id, filename)
            if old.exists():
                return old
        return path

    def new_sample_path(self, sensor_id: int, filename: str) -> Path:
        return self.settings.samples_dir / str(sensor_id) / filename

    def old_sample_path(self, sensor_id: int, filename: str) -> Path:
        """Where older versions kept a training image (see Settings.old_samples_dir)."""
        return self.settings.old_samples_dir / str(sensor_id) / filename

    def face_path(self, filename: str) -> Path:
        """A taught face (db.Face): shared by every sensor, so not in a sensor's folder."""
        return self.settings.faces_dir / filename

    def history_path(self, sensor_id: int, filename: str) -> Path:
        """A history frame, or a sound sensor's clip (in the app's own storage, see Settings.clips_dir)."""
        if is_clip(filename):
            return self.settings.clips_dir / str(sensor_id) / filename
        return self.settings.history_dir / str(sensor_id) / filename

    def thumb_path(self, kind: str, item_id: int) -> Path:
        folder = self.settings.sample_thumbs_dir if kind == "sample" else self.settings.thumbs_dir / kind
        return folder / f"{item_id}.jpg"

    @staticmethod
    def new_filename() -> str:
        return f"{uuid.uuid4().hex}.jpg"

    def _write(self, path: Path, image: Image.Image | None, data: bytes | None = None) -> None:
        """Whole or not at all: written next to it, then put in its place (a power cut or a full
        disk never leaves half a picture under the name a row lists). ``data``: the JPEG to write
        as it is (otherwise ``image`` is encoded)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f"{path.name}.part")
        try:
            if data is None:
                assert image is not None
                data = imaging.encode_jpeg(image)
            part.write_bytes(data)
            part.replace(path)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    def save_sample(self, sensor_id: int, image: Image.Image) -> str:
        filename = self.new_filename()
        self._write(self.new_sample_path(sensor_id, filename), image)
        return filename

    def save_face(self, image: Image.Image) -> str:
        filename = self.new_filename()
        self._write(self.face_path(filename), image)
        return filename

    def delete_face(self, filename: str) -> None:
        self.face_path(filename).unlink(missing_ok=True)

    def save_history(self, sensor_id: int, image: Image.Image) -> str:
        """A camera frame for the history: the camera's own JPEG when ``image`` is exactly that
        (imaging.original_jpeg) — not encoded again, so no time spent and no quality lost, and
        usually smaller. Only the history: a training image is always encoded again, which leaves
        out what an uploaded photo may carry besides its pixels (where it was taken)."""
        filename = self.new_filename()
        self._write(self.history_path(sensor_id, filename), image, imaging.original_jpeg(image))
        return filename

    def save_clip(self, sensor_id: int, data: bytes, video: bool = False) -> str:
        """A sound sensor's clip for the history (sounds.encode_clip, or with video)."""
        filename = f"{uuid.uuid4().hex}{VIDEO_CLIP_SUFFIX if video else CLIP_SUFFIX}"
        self._write(self.history_path(sensor_id, filename), None, data)
        return filename

    def save_sample_clip(self, sensor_id: int, data: bytes) -> str:
        """A clip taught to a sound sensor (an own sound, or "not this")."""
        filename = f"{uuid.uuid4().hex}{CLIP_SUFFIX}"
        self._write(self.new_sample_path(sensor_id, filename), None, data)
        return filename

    def thumbnail(self, kind: str, item_id: int, source: Path) -> Path:
        """Cached thumbnail for a sample or history frame."""
        target = self.thumb_path(kind, item_id)
        if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
            # Decoded just large enough (a JPEG decodes at 1/2, 1/4 or 1/8 of its size): three times
            # faster for a 4K frame, and a page of history asks for dozens at once.
            size = (THUMB_SIZE, THUMB_SIZE)
            self._write(target, imaging.thumbnail(imaging.load(source, at_least=size)))
        return target

    def delete_sample(self, sensor_id: int, sample_id: int, filename: str) -> None:
        self.new_sample_path(sensor_id, filename).unlink(missing_ok=True)
        self.old_sample_path(sensor_id, filename).unlink(missing_ok=True)
        self.thumb_path("sample", sample_id).unlink(missing_ok=True)

    def delete_history(self, sensor_id: int, prediction_id: int, filename: str | None) -> None:
        if filename:
            self.history_path(sensor_id, filename).unlink(missing_ok=True)
        self.thumb_path("history", prediction_id).unlink(missing_ok=True)

    @staticmethod
    def _folder_bytes(folder: Path) -> int:
        return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.exists() else 0

    def history_bytes(self) -> int:
        """History frames, sound clips and the frames' thumbnails on disk."""
        return (
            self._folder_bytes(self.settings.history_dir)
            + self._folder_bytes(self.settings.clips_dir)
            + self._folder_bytes(self.settings.thumbs_dir / "history")
        )

    def samples_bytes(self) -> int:
        """Training images (and taught faces) and their thumbnails on disk."""
        return (
            self._folder_bytes(self.settings.samples_dir)
            + self._folder_bytes(self.settings.faces_dir)
            + self._folder_bytes(self.settings.sample_thumbs_dir)
        )

    def delete_sensor(self, sensor_id: int) -> None:
        import shutil

        for directory in (
            self.settings.samples_dir / str(sensor_id),
            self.settings.old_samples_dir / str(sensor_id),
            self.settings.history_dir / str(sensor_id),
            self.settings.clips_dir / str(sensor_id),
        ):
            shutil.rmtree(directory, ignore_errors=True)
