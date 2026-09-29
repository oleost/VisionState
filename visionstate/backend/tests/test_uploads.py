"""Video, ZIP and PyAV-based frame grabbing (the paths a PyAV upgrade can break)."""

import io
import zipfile
from pathlib import Path

import av
import pytest
from PIL import Image

from visionstate import sources, uploads

from .test_integration import garage

FPS = 5


def make_video(path: Path, segments: list[tuple[str, int]]) -> None:
    """H.264 (fallback MPEG-4) video with a static garage scene per (state, seconds) segment."""
    codec = "libx264" if "libx264" in av.codecs_available else "mpeg4"
    with av.open(str(path), "w") as out:
        stream = out.add_stream(codec, rate=FPS)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
        for state, seconds in segments:
            frame_image = Image.open(io.BytesIO(garage(state, 1)))
            for _ in range(seconds * FPS):
                for packet in stream.encode(av.VideoFrame.from_image(frame_image)):
                    out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


def test_video_frames_are_extracted_and_deduplicated(tmp_path):
    video = tmp_path / "garage.mp4"
    make_video(video, [("closed", 4), ("open", 4), ("partial", 4)])
    frames = list(uploads.frames_from_file(video, "garage.mp4", frame_interval_s=1))
    # One frame per second = 12 candidates; identical frames within a segment are skipped.
    assert len(frames) == 3
    assert all(origin == "video" for _, origin in frames)
    assert all(img.size == (320, 180) for img, _ in frames)


def test_zip_upload_reads_images_and_skips_other_files(tmp_path):
    archive = tmp_path / "set.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.jpg", garage("open", 1))
        z.writestr("folder/b.jpg", garage("closed", 2))
        z.writestr("notes.txt", "ignore me")
        z.writestr("__MACOSX/._a.jpg", b"junk")
    frames = list(uploads.frames_from_file(archive, "set.zip", frame_interval_s=1))
    assert len(frames) == 2


def test_unsupported_file_is_rejected(tmp_path):
    path = tmp_path / "x.exe"
    path.write_bytes(b"MZ")
    with pytest.raises(uploads.UploadError):
        list(uploads.frames_from_file(path, "x.exe", 1))


def test_stream_grab_decodes_a_frame(tmp_path):
    """grab_rtsp uses PyAV like for RTSP; a local file exercises the same decode path offline."""
    video = tmp_path / "clip.mp4"
    make_video(video, [("open", 1)])
    data = sources.grab_rtsp(str(video))
    assert Image.open(io.BytesIO(data)).size == (320, 180)


def test_stream_grab_error_is_redacted():
    with pytest.raises(sources.SourceError) as err:
        sources.grab_rtsp("rtsp://admin:secret@127.0.0.1:9/stream")
    assert "secret" not in str(err.value)
