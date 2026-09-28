"""Turning uploaded files (images, ZIP archives, videos) into frames."""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

from PIL import Image

from . import imaging
from .settings import IMAGE_EXTENSIONS, VIDEO, VIDEO_EXTENSIONS

log = logging.getLogger(__name__)


class UploadError(Exception):
    pass


def kind_of(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix == ".zip":
        return "zip"
    return "unknown"


def frames_from_file(path: Path, filename: str, frame_interval_s: float) -> Iterator[tuple[Image.Image, str]]:
    """Yields (image, origin) for every usable frame in an uploaded file."""
    kind = kind_of(filename)
    if kind == "image":
        yield imaging.decode(path.read_bytes()), "upload"
    elif kind == "zip":
        yield from _frames_from_zip(path)
    elif kind == "video":
        yield from ((img, "video") for img in frames_from_video(path, frame_interval_s))
    else:
        raise UploadError(f"Unsupported file type: {filename}")


def _frames_from_zip(path: Path) -> Iterator[tuple[Image.Image, str]]:
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or kind_of(info.filename) != "image" or "__MACOSX" in info.filename:
                continue
            try:
                yield imaging.decode(archive.read(info)), "upload"
            except OSError:
                log.warning("Skipping unreadable image %s in ZIP", info.filename)


def frames_from_video(path: Path, interval_s: float) -> Iterator[Image.Image]:
    """One frame every ``interval_s`` seconds, skipping near-duplicates."""
    import av

    interval_s = max(0.2, interval_s or VIDEO["frame_interval_s"])
    last_hash: int | None = None
    next_ts = 0.0
    emitted = 0
    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            for frame in container.decode(stream):
                if frame.time is None or frame.time + 1e-6 < next_ts:
                    continue
                next_ts = frame.time + interval_s
                image = frame.to_image().convert("RGB")
                current = imaging.dhash(image)
                if last_hash is not None and imaging.hamming(current, last_hash) <= VIDEO["dedupe_distance"]:
                    continue
                last_hash = current
                yield image
                emitted += 1
                if emitted >= VIDEO["max_frames"]:
                    break
    except av.FFmpegError as err:
        raise UploadError(f"Could not read video: {err}") from err


def image_bytes_to_image(data: bytes) -> Image.Image:
    return imaging.decode(data)


def zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buf.getvalue()
