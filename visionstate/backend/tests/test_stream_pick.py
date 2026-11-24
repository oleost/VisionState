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


def test_a_home_assistant_camera_is_read_through_its_own_stream_while_a_face_is_looked_for(monkeypatch):
    """A Home Assistant camera looked at for a face is read like an RTSP stream, through its own
    stream in Home Assistant (asked for when it opens: its address holds a token). Snapshots are
    taken while it opens (it is not closed meanwhile for being unused), and for a camera that
    cannot stream (not asked again for a while); a stream that stopped is opened again. The looks
    of a scan are given once: a snapshot after them must not get them too."""
    import asyncio

    from visionstate.sources import FrameGrabber, SourceError

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 10, 10)).save(buf, format="JPEG")
    snap = buf.getvalue()
    opened: list[tuple] = []

    class FakeHA:
        enabled = True
        can_stream = True

        async def snapshot(self, entity_id):
            return snap

        async def stream_address(self, entity_id):
            if not self.can_stream:
                raise SourceError(f"{entity_id}: does not support streaming")
            return "http://supervisor/core/api/hls/abc/master_playlist.m3u8", {"Authorization": "Bearer t"}

    may_open = threading.Event()

    def run(self):  # frames come from here once the address is known, until the stream is closed
        try:
            may_open.wait(5)
            opened.append(self._opener())
        except Exception as err:  # noqa: BLE001 - as RtspStream._run: the error ends the stream
            self._error = SourceError(str(err))
            return
        while self._add(frame(True), sources._grey):
            time.sleep(0.01)

    monkeypatch.setattr(RtspStream, "_run", run)
    monkeypatch.setitem(sources.FACES, "scan_interval_s", 0.0)
    ha = FakeHA()

    def scan(crops, offsets, size):
        return [((1, 1.0), "a face") for _ in crops]

    async def until_ready(entity_id: str) -> None:
        for _ in range(200):
            if grabber_ref[0]._streams[entity_id].ready:
                return
            await asyncio.sleep(0.02)

    grabber_ref: list = []

    async def scenario() -> None:
        grabber = FrameGrabber(ha)  # type: ignore[arg-type]
        grabber_ref.append(grabber)
        try:
            assert await grabber.grab("ha_camera", "camera.door", focus=HEAD) == snap  # opening
            stream = grabber._streams["camera.door"]
            stream._asked_at -= 60  # long unused: still kept while it opens
            assert await grabber.grab("ha_camera", "camera.door", focus=HEAD) == snap
            assert time.monotonic() - stream._asked_at < 5
            may_open.set()
            await until_ready("camera.door")
            assert sharpness_of(await grabber.grab("ha_camera", "camera.door", focus=HEAD, scan=scan)) > 100
            url, options, _ = opened[0]
            assert url.endswith(".m3u8") and "Bearer t" in options["headers"] and options["live_start_index"] == "-1"
            await asyncio.sleep(0.1)
            assert sharpness_of(await grabber.grab("ha_camera", "camera.door", focus=HEAD, scan=scan)) > 100
            assert [look.found for look in grabber.looks("camera.door")][:1] == ["a face"]
            assert grabber.looks("camera.door") == []  # given once
            assert await grabber.grab("ha_camera", "camera.door") == snap  # no face looked for: a snapshot

            # A stream that stopped after it ran is opened again (a snapshot meanwhile).
            stream.close()
            for _ in range(200):
                if not stream.alive:
                    break
                await asyncio.sleep(0.02)
            assert await grabber.grab("ha_camera", "camera.door", focus=HEAD) == snap
            assert grabber._streams["camera.door"] is not stream and "camera.door" not in grabber._no_stream
            await until_ready("camera.door")
            assert sharpness_of(await grabber.grab("ha_camera", "camera.door", focus=HEAD)) > 100

            # A camera that cannot stream: snapshots, and its stream is not asked for again soon.
            ha.can_stream = False
            assert await grabber.grab("ha_camera", "camera.gate", focus=HEAD) == snap
            await asyncio.sleep(0.2)
            assert await grabber.grab("ha_camera", "camera.gate", focus=HEAD) == snap
            assert "camera.gate" in grabber._no_stream and "camera.gate" not in grabber._streams
            assert await grabber.grab("ha_camera", "camera.gate", focus=HEAD) == snap
            assert "camera.gate" not in grabber._streams
        finally:
            await grabber.close()

    asyncio.run(scenario())
