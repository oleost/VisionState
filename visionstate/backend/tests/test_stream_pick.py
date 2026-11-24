"""Looking for a face on a stream: the frame sharpest where the face is looked for (sources.RtspStream)."""

from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import av
import numpy as np
from PIL import Image

from visionstate import sources
from visionstate.sources import RtspStream, focus_sharpness

HEAD = [[0.25, 0.25, 0.75, 0.75]]  # where the face is looked for (normalised)


def frame(sharp: bool) -> av.VideoFrame:
    """A frame with a checkerboard in the middle: sharp, or smeared (a step blurs it)."""
    rgb = np.full((64, 64, 3), 128, np.uint8)
    board = (np.indices((32, 32)).sum(axis=0) % 2 * 255).astype(np.uint8)
    if not sharp:
        board = np.full_like(board, 128)
    rgb[16:48, 16:48] = board[..., None]
    return av.VideoFrame.from_ndarray(rgb, format="rgb24")


def sharpness_of(jpeg: bytes) -> float:
    grey = np.asarray(Image.open(io.BytesIO(jpeg)).convert("L"))
    return focus_sharpness(grey, HEAD)


def test_focus_sharpness_looks_only_where_the_face_is():
    sharp, smeared = frame(True).to_ndarray(format="gray"), frame(False).to_ndarray(format="gray")
    assert focus_sharpness(sharp, HEAD) > 100 * max(focus_sharpness(smeared, HEAD), 1.0)
    assert focus_sharpness(sharp, [[0.0, 0.0, 0.2, 0.2]]) == focus_sharpness(smeared, [[0.0, 0.0, 0.2, 0.2]])
    assert focus_sharpness(sharp, [[0.5, 0.5, 0.5, 0.5]]) == 0.0  # nothing to look at


def test_a_grab_with_focus_takes_the_sharpest_frame_since_the_last(monkeypatch):
    """The frames between two checks of someone walking up are blurred by their steps in turn: a
    grab with a focus takes the sharpest of them, not the newest."""
    stop = threading.Event()
    monkeypatch.setattr(RtspStream, "_run", lambda self: stop.wait())  # frames come from the test
    stream = RtspStream("rtsp://camera/stream", keep_open=30)
    pool = ThreadPoolExecutor(1)
    grey = sources._grey

    def grab_then(frames: list[bool], focus=HEAD) -> bytes:
        future = pool.submit(stream.grab, focus)
        time.sleep(0.05)  # the grab waits for a frame decoded after it
        for sharp in frames:
            stream._add(frame(sharp), grey)
        return future.result(timeout=5)

    try:
        grab_then([False])  # the first grab gives the focus: nothing scored before it
        stream._add(frame(False), grey)
        stream._add(frame(True), grey)
        stream._add(frame(False), grey)
        picked = grab_then([False])
        assert sharpness_of(picked) > 100 and stream.picked is not None and stream.picked["frames"] == 4
        # Without a focus (no face looked for): the newest frame, as before.
        stream._add(frame(True), grey)
        assert sharpness_of(grab_then([False], focus=None)) < 10 and stream.picked is None
    finally:
        stop.set()
        stream.close()
        pool.shutdown()


def test_a_part_of_a_frame_is_converted_as_ffmpeg_does():
    """crop_rgb converts only the head region of a decoded frame (4:2:0 YUV), as FFmpeg would."""
    rgb = np.random.default_rng(1).integers(0, 256, (64, 96, 3), dtype=np.uint8)
    rgb[:, :48] = (200, 60, 30)  # flat colour on the left, noise on the right
    for name in ("yuv420p", "yuvj420p", "rgb24"):
        decoded = av.VideoFrame.from_ndarray(rgb, format="rgb24").reformat(format=name)
        ours = np.asarray(sources.crop_rgb(decoded, (8, 10, 40, 50)), np.int16)
        ffmpeg = np.asarray(decoded.to_image().crop((8, 10, 40, 50)), np.int16)
        assert ours.shape == ffmpeg.shape == (40, 32, 3)
        assert np.abs(ours - ffmpeg).max() <= 3, name


def test_a_grab_with_a_face_scan_takes_the_best_look(monkeypatch):
    """With a scan, the stream looks for a face in a frame every FACES["scan_interval_s"] and keeps
    the best look of each FACES["scan_slot_s"]: the next grab gives the frame of the best look
    (not the sharpest), and ``looks`` all of them, best first."""
    monkeypatch.setitem(sources.FACES, "scan_interval_s", 0.0)
    monkeypatch.setitem(sources.FACES, "scan_slot_s", 1e-6)  # every frame a slot of its own
    stop = threading.Event()
    monkeypatch.setattr(RtspStream, "_run", lambda self: stop.wait())
    stream = RtspStream("rtsp://camera/stream", keep_open=30)
    pool = ThreadPoolExecutor(1)
    grey = sources._grey

    def bright(level: int) -> av.VideoFrame:  # how good a look the scan below makes of it
        return av.VideoFrame.from_ndarray(np.full((64, 64, 3), level, np.uint8), format="rgb24").reformat(
            format="yuv420p"
        )

    def scan(crops, offsets, size):
        assert size == (64, 64) and all(x % 2 == 0 and y % 2 == 0 for x, y in offsets)
        return [
            ((1, float(np.asarray(c).mean())), round(float(np.asarray(c).mean())))
            if np.asarray(c).mean() > 20
            else None
            for c in crops
        ]

    def grab_then(frames: list, focus=HEAD, scan=scan) -> bytes:
        future = pool.submit(stream.grab, focus, scan)
        time.sleep(0.05)
        for f in frames:
            stream._add(f, grey)
        return future.result(timeout=5)

    def level(jpeg: bytes) -> float:
        return float(np.asarray(Image.open(io.BytesIO(jpeg)).convert("L")).mean())

    try:
        grab_then([bright(10)])  # the first grab gives the focus and the scan
        for value in (60, 200, 10, 120):
            stream._add(bright(value), grey)
        picked = grab_then([bright(10)])
        assert abs(level(picked) - 200) < 4  # the best look's frame, not the newest
        found = [look.found for look in stream.looks]  # the levels, give or take YUV's rounding
        assert len(found) == 3 and all(abs(a - b) <= 2 for a, b in zip(found, (200, 120, 60), strict=True))
        assert stream.looks[0].picked
        assert all(look.crop.size[0] > 32 for look in stream.looks)  # the head region, grown
        # Without a scan: no looks, the sharpest frame as before.
        stream._add(bright(200), grey)
        grab_then([bright(10)], scan=None)
        assert stream.looks == []
        # A scan that fails is logged and finds nothing; the stream goes on.
        grab_then([bright(10)])

        def broken(crops, offsets, size):
            raise RuntimeError("model gone")

        grab_then([bright(10)], scan=broken)
        stream._add(bright(200), grey)
        assert level(grab_then([bright(10)], scan=broken)) > 0 and stream.looks == []
    finally:
        stop.set()
        stream.close()
        pool.shutdown()
