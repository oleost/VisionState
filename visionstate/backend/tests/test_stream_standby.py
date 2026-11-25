"""A sensor with faces or plates keeps its stream open between the checks (sources.RtspStream on
standby, FrameGrabber), paced as filmed (sources.Pacer), and reads plates on it between the checks
(engine: stream_focus, stream_scan, PlatesMixin._plates_between, logic.steer_plate_burst)."""

from __future__ import annotations

import asyncio
import io
import threading
import time
from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest
from PIL import Image

from visionstate import faces, plates, sources
from visionstate.engine.checks import ChecksMixin
from visionstate.engine.logic import burst_pace, in_burst, steer_plate_burst
from visionstate.engine.plates import PlatesMixin
from visionstate.engine.state import LiveState
from visionstate.settings import FACES, PLATES, RUNTIME
from visionstate.sources import FrameGrabber, Look, Pacer, RtspStream

GOP = 10


def video(path, frames: int = 40) -> None:
    """A small H.264 video, a key frame every GOP frames, 25 frames a second, frame n at pts n."""
    with av.open(str(path), "w") as out:
        stream = out.add_stream(
            "libx264", rate=25, options={"g": str(GOP), "keyint_min": str(GOP), "sc_threshold": "0", "bf": "0"}
        )
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        stream.codec_context.time_base = Fraction(1, 25)
        for n in range(frames):
            frame = av.VideoFrame.from_ndarray(np.full((64, 64, 3), (n * 6) % 256, np.uint8), format="rgb24")
            frame.pts = n
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


# --- pacing ---------------------------------------------------------------------------------------


def test_a_stream_that_comes_in_chunks_is_let_through_as_it_was_filmed(monkeypatch):
    """Home Assistant's HLS comes a few seconds at once: paced, the frames come as they were
    filmed, a little late; a chunk that came late makes the frames after it that much later, and
    a jump in the stream's clock (it started again) starts the pacing again."""
    clock = [100.0]
    monkeypatch.setattr(sources.time, "monotonic", lambda: clock[0])
    pacer = Pacer()
    released: list[tuple[float, float]] = []

    def arrive(at: float) -> None:
        clock[0] += pacer.due(at)  # the stream thread sleeps that long
        released.append((at, clock[0]))

    for k in range(12):  # the first chunk: 6 s of frames, all at once
        arrive(k * 0.5)
    assert [round(t - 100, 1) for _, t in released] == pytest.approx([k * 0.5 for k in range(12)], abs=0.1)
    clock[0] = 107.0  # the next chunk comes a second after its first frame was due
    for k in range(12, 24):
        arrive(k * 0.5)
    late = released[12:]
    assert late[0][1] == pytest.approx(107.0)
    gaps = [b[1] - a[1] for a, b in zip(late, late[1:], strict=False)]
    assert all(0.45 < g < 0.51 for g in gaps)  # one by one again, a second later
    clock[0] = 200.0
    assert pacer.due(5.0) == 0.0 and pacer.lag == 0.0  # back to the start: paced from here


# --- standby: key frames only, every frame when asked ----------------------------------------------


def test_a_stream_on_standby_decodes_only_key_frames_until_every_frame_is_asked_for(tmp_path, monkeypatch):
    """On standby only the key frames are decoded; asked for every frame, the frames since the last
    key frame are decoded at once and every one after them (the moment that started a burst)."""
    path = tmp_path / "door.mkv"
    video(path)
    decoded: list[int] = []
    asked = threading.Event()
    every = [False]
    original = RtspStream._add

    def add(self, frame, grey):
        decoded.append(round(float(frame.pts * frame.time_base) * 25))  # its number
        return original(self, frame, grey)

    calls = [0]

    def every_frame(self) -> bool:  # the 16th packet asks for every frame (a burst starts)
        calls[0] += 1
        if calls[0] == 16:
            every[0] = True
            asked.set()
        return every[0]

    monkeypatch.setattr(RtspStream, "_add", add)
    monkeypatch.setattr(RtspStream, "_every", every_frame)
    stream = RtspStream("door", keep_open=30, opener=lambda: (str(path), {}, 5.0), standby=True)
    try:
        for _ in range(200):
            if not stream.alive:
                break
            time.sleep(0.01)
        assert asked.is_set()
        assert decoded == [0, GOP, *range(GOP + 1, 40)]
    finally:
        stream.close()


def test_a_stream_on_standby_gives_its_newest_key_frame_at_once(monkeypatch):
    """A check on standby (key frames only, one every second or two) takes the newest key frame it
    was not given yet, without waiting for the next; when it was given, it waits."""
    stop = threading.Event()
    monkeypatch.setattr(RtspStream, "_run", lambda self: stop.wait())
    stream = RtspStream("rtsp://camera/stream", keep_open=30, standby=True)
    grey = sources._grey

    def level(jpeg: bytes) -> float:
        return float(np.asarray(Image.open(io.BytesIO(jpeg)).convert("L")).mean())

    def plain(value: int) -> av.VideoFrame:
        return av.VideoFrame.from_ndarray(np.full((16, 16, 3), value, np.uint8), format="rgb24")

    try:
        stream._add(plain(200), grey)
        assert abs(level(stream.grab()) - 200) < 4  # at once
        later = threading.Timer(0.05, lambda: stream._add(plain(40), grey))
        later.start()
        assert abs(level(stream.grab()) - 40) < 4  # given already: the next one
        stream.every_frame(30)
        later = threading.Timer(0.05, lambda: stream._add(plain(120), grey))
        later.start()
        assert abs(level(stream.grab()) - 120) < 4  # every frame: one decoded after the grab
    finally:
        stop.set()
        stream.close()


def test_a_sensor_with_faces_or_plates_reads_a_home_assistant_camera_through_its_stream(monkeypatch):
    """On standby a Home Assistant camera is read through its own stream at every check once it is
    open (snapshots while it opens), paced; a burst makes it decode every frame."""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 10, 10)).save(buf, format="JPEG")
    snap = buf.getvalue()
    made: list[RtspStream] = []

    class FakeHA:
        enabled = True

        async def snapshot(self, entity_id):
            return snap

        async def stream_address(self, entity_id):
            return "http://supervisor/core/api/hls/abc/master_playlist.m3u8", {}

    def run(self):
        made.append(self)
        bright = av.VideoFrame.from_ndarray(np.full((64, 64, 3), 220, np.uint8), format="rgb24")
        while self._add(bright, sources._grey):
            time.sleep(0.01)

    monkeypatch.setattr(RtspStream, "_run", run)

    async def scenario() -> None:
        grabber = FrameGrabber(FakeHA())  # type: ignore[arg-type]
        try:
            assert await grabber.grab("ha_camera", "camera.door", standby=True) == snap  # opening
            for _ in range(200):
                if grabber._streams["camera.door"].ready:
                    break
                await asyncio.sleep(0.01)
            assert await grabber.grab("ha_camera", "camera.door", standby=True) != snap
            stream = made[0]
            assert stream.standby and stream.paced and stream.keep_open == RUNTIME["stream_standby_s"]
            assert not stream._every()
            await grabber.grab("ha_camera", "camera.door", standby=True, burst=True)
            assert stream._every()
            # A grab without standby (the live view) does not take the stream off standby.
            await grabber.grab("ha_camera", "camera.door", focus=[[0.0, 0.0, 1.0, 1.0]])
            assert stream.standby and stream.keep_open == RUNTIME["stream_standby_s"]
        finally:
            await grabber.close()

    asyncio.run(scenario())


# --- where a stream looks, and for what ------------------------------------------------------------


def runtime(live: LiveState, face_model=None, plate_model=None) -> SimpleNamespace:
    return SimpleNamespace(
        live_state=lambda sid: live,
        wanted_faces=lambda: face_model,
        wanted_plates=lambda: plate_model,
        face_limits=lambda cfg: {"min_face_px": 30.0},
    )


def config(faces_on: bool = True, plates_on: bool = True, source_type: str = "ha_camera") -> SimpleNamespace:
    return SimpleNamespace(id=1, source_type=source_type, faces_on=faces_on, plates_on=plates_on)


def test_a_stream_looks_at_heads_and_at_vehicles_not_yet_known_by_their_plate():
    now = time.time()
    live = LiveState()
    live.detections = [
        {"key": "person", "box": [0.1, 0.1, 0.3, 0.9]},
        {"key": "car", "box": [0.5, 0.5, 0.9, 0.9]},
        {"key": "car", "box": [0.0, 0.6, 0.2, 0.8], "plate": {"state": "match", "sure": True}},  # known
        {"key": "truck", "box": [0.6, 0.1, 0.7, 0.2], "filtered": True},
    ]
    rt = runtime(live)
    assert ChecksMixin.stream_focus(rt, config()) == ([], [])  # no burst: nothing looked at closely
    live.plate_burst_until = now + 5
    boxes, kinds = ChecksMixin.stream_focus(rt, config())
    assert kinds == ["plate"] and boxes == [[0.5, 0.5, 0.9, 0.9]]
    live.face_burst.until = now + 5
    boxes, kinds = ChecksMixin.stream_focus(rt, config())
    assert kinds == ["face", "plate"]
    assert ChecksMixin.stream_focus(rt, config(source_type="http")) == ([], [])
    assert ChecksMixin.keeps_stream(config(faces_on=False)) and not ChecksMixin.keeps_stream(config(False, False))
    assert not ChecksMixin.keeps_stream(config(source_type="http"))


def test_a_stream_scan_runs_the_face_detector_on_heads_and_the_plate_finder_on_vehicles():
    class FaceDetector:
        def detect(self, crops):
            points = ((10.0, 20.0), (30.0, 20.0), (20.0, 30.0), (12.0, 40.0), (28.0, 40.0))
            return [faces.Face((2.0, 2.0, 42.0, 52.0), points, 0.9) for _ in crops]

    class Finder:
        def find(self, image):
            return [plates.Found((5.0, 5.0, 25.0, 12.0), 0.6), plates.Found((10.0, 30.0, 70.0, 45.0), 0.8)]

    rt = runtime(LiveState(), SimpleNamespace(detector=FaceDetector()), SimpleNamespace(finder=Finder()))
    scan = ChecksMixin.stream_scan(rt, config(), ["face", "plate", "plate"])
    assert scan is not None
    crop = Image.new("RGB", (80, 80))
    # The second vehicle region was too small to cut: the crops are of regions 0 and 2.
    result = scan([crop, crop], [(100, 200), (300, 400)], (1000, 1000), [0, 2])
    face_rank, face = result[0]
    assert isinstance(face, faces.Face) and face.box[0] == 102.0 and face_rank[0] in (0, 1)
    plate_rank, plate = result[1]
    assert isinstance(plate, plates.Found) and plate.box == (310.0, 430.0, 370.0, 445.0)
    assert plate_rank == plates.look_rank(plates.Found((10.0, 30.0, 70.0, 45.0), 0.8))
    assert ChecksMixin.stream_scan(runtime(LiveState()), config(), ["face", "plate"]) is None


# --- plates read between the checks -----------------------------------------------------------------

CARS = [{"key": "our_car", "name": "Our car", "plate": "VS 12345"}]


def look(box: tuple[float, float, float, float], picked: bool = False) -> Look:
    crop_at = (int(box[0]) - 20, int(box[1]) - 20)
    return Look(
        rank=(1, 50.0),
        at=0.0,
        crop=Image.new("RGB", (200, 100)),
        offset=crop_at,
        size=(1000, 1000),
        found=plates.Found(box, 0.9),
        region=0,
        picked=picked,
    )


def test_plates_seen_between_the_checks_are_read_and_go_to_the_nearest_vehicle(monkeypatch):
    texts = iter(["VS12345", "VS1234S", "XX"])

    def read_look(model, image, found):
        assert found.box[0] == 20 and found.box[1] == 20  # in the crop's pixels
        return plates.PlateRead(found.box, next(texts), 0.9, found.score, between=True)

    monkeypatch.setattr(plates, "read_look", read_look)
    rt = SimpleNamespace(_sem=asyncio.Semaphore(1))
    looks = [
        look((520.0, 620.0, 580.0, 640.0)),  # in the first vehicle
        look((100.0, 100.0, 160.0, 120.0), picked=True),  # this check's own frame: read by the check
        look((880.0, 620.0, 940.0, 640.0)),  # just beside the first vehicle (it moved)
        look((10.0, 900.0, 70.0, 920.0)),  # near no vehicle
    ]
    boxes = {3: (400, 500, 800, 700)}
    seen = asyncio.run(PlatesMixin._plates_between(rt, None, looks, boxes, (1000, 1000)))  # type: ignore[arg-type]
    assert [r.text for r in seen[3]] == ["VS12345", "VS1234S"]
    assert seen[3][0].box == (120.0, 120.0, 180.0, 140.0) and seen[3][0].between  # in the vehicle box


def name_car(texts: list[tuple[str, bool]], before: dict | None = None) -> dict:
    det = {"key": "car", "box": [0, 0, 1, 1]}
    reads = [plates.PlateRead((10, 10, 60, 25), t, 0.9, 0.9, between=b) for t, b in texts]
    object.__new__(PlatesMixin)._name_car(det, reads, before or {}, CARS, (0, 0, 100, 100), (100, 100))
    return det


def test_reads_between_the_checks_count_like_checks_of_their_own():
    # A close read alone waits for one more; a close read between the checks that agrees confirms it.
    assert name_car([("VS1234S", False)])["plate"]["state"] == "pending"
    named = name_car([("VS1234S", False), ("VS12345X", True)])
    assert named["plate"]["state"] == "match" and named["label"] == "our_car"
    # An unknown plate read again between the checks is unknown in the same check.
    assert name_car([("ZX98765", False)])["plate"]["state"] == "reading"
    assert name_car([("ZX98765", False), ("ZX98765", True)])["plate"]["state"] == "unknown"
    # The best read was one between the checks: said so.
    assert name_car([("VS12345", True)])["plate"].get("between") is True


# --- a burst while a car comes -----------------------------------------------------------------------


def test_a_vehicle_not_known_by_its_plate_that_moves_makes_the_sensor_check_often():
    now = 1000.0
    coming = {"key": "car", "box": [0.1, 0.4, 0.3, 0.6]}
    turned = {"key": "car", "box": [0.3, 0.45, 0.6, 0.75]}
    until = steer_plate_burst(0.0, [coming], [], now)
    assert until == now + PLATES["burst_hold_s"]
    until = steer_plate_burst(until, [turned], [coming], now + 1)
    assert until == now + 1 + PLATES["burst_hold_s"]
    # Parked (the same box as the last check): the burst is not held longer, and ends in time.
    assert steer_plate_burst(until, [turned], [turned], now + 2) == until
    # Named surely by its plate: no reason to look at it.
    known = {**turned, "box": [0.0, 0.0, 0.2, 0.2], "plate": {"state": "match", "sure": True}}
    assert steer_plate_burst(until, [known], [], now + 3) == 0.0
    live = LiveState()
    live.plate_burst_until = now + 5
    assert burst_pace(2.0, live, now) == PLATES["burst_interval_s"] and in_burst(live, now)
    assert burst_pace(2.0, live, now + 6) is None and not in_burst(live, now + 6)
    assert FACES["burst_interval_s"] > 0
