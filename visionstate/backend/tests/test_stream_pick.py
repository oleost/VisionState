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
