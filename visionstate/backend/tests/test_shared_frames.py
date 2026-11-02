"""Shared snapshot freshness, failures, lifetime and independent state checks."""

import asyncio
import io
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from visionstate import imaging
from visionstate.db import Database
from visionstate.engine import checks
from visionstate.engine.base import RuntimeBase
from visionstate.engine.checks import ChecksMixin
from visionstate.engine.state import SensorConfig
from visionstate.settings import Settings
from visionstate.shared_frames import SharedFrames
from visionstate.sources import SourceError


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "green").save(out, "JPEG")
    return out.getvalue()


class Camera:
    def __init__(self):
        self.calls = []
        self.decodes = 0
        self.error = None
        self.data = jpeg()

    async def grab(self, kind, source):
        self.calls.append((kind, source))
        if self.error:
            raise self.error
        return self.data

    def decode(self, data):
        self.decodes += 1
        return imaging.decode(data)


def config(sensor_id=1, **changes):
    cfg = SensorConfig(
        sensor_id, str(sensor_id), str(sensor_id), "ha_camera", "camera.garage", None, 30, 0.7, 2, True, []
    )
    return replace(cfg, **changes)


def engine(camera):
    obj = object.__new__(ChecksMixin)
    obj.live = {}
    obj.grabber = camera
    obj.shared_frames = SharedFrames(camera.grab, camera.decode, 0.001)
    obj._send = AsyncMock()
    obj._run_states = AsyncMock()
    return obj


def test_runtime_uses_the_current_camera_provider(tmp_path):
    async def run():
        settings = Settings(
            data_dir=tmp_path / "data",
            media_dir=tmp_path / "media",
            frontend_dir=tmp_path / "frontend",
            bundled_models_dir=tmp_path / "models",
        )
        obj = RuntimeBase(settings, Database(tmp_path / "test.db"))
        original = obj.grabber
        camera = Camera()
        obj.grabber = camera
        try:
            await obj.shared_frames.get("http", "camera")
            assert len(camera.calls) == 1
        finally:
            await obj.shared_frames.close()
            await original.close()
            await obj.ha.close()

    asyncio.run(run())


def test_sensor_steps_share_regular_checks_but_not_a_forced_check(monkeypatch):
    async def run():
        camera = Camera()
        obj = engine(camera)
        obj.load_sensor = lambda sensor_id: config(sensor_id)
        monkeypatch.setattr(checks.backbones, "trim_memory", lambda: None)
        await asyncio.gather(*(obj._sensor_step(i) for i in range(4)))
        assert len(camera.calls) == 1
        assert obj._run_states.await_count == 4
        obj.live_state(1).force = True
        await obj._sensor_step(1)
        assert len(camera.calls) == 2
        assert camera.decodes == 1  # forced check used the original direct decode path
        obj.load_sensor = lambda sensor_id: config(sensor_id, enabled=False)
        await obj._sensor_step(5)
        assert len(camera.calls) == 2
        await obj.shared_frames.close()

    asyncio.run(run())


def test_four_checks_share_one_capture_and_decode_but_next_batch_is_fresh():
    async def run():
        camera = Camera()
        pool = SharedFrames(camera.grab, camera.decode, 0.001)
        frames = await asyncio.gather(*(pool.get("ha_camera", "camera.garage") for _ in range(4)))
        assert len(camera.calls) == camera.decodes == 1
        assert all(frame[1] is frames[0][1] for frame in frames)
        assert not pool._pending
        await pool.get("ha_camera", "camera.garage")
        assert len(camera.calls) == camera.decodes == 2
        await pool.close()
        assert not pool._tasks

    asyncio.run(run())


def test_sound_sensor_keeps_its_continuous_listener():
    async def run():
        camera = Camera()
        obj = engine(camera)
        cfg = config(kind="sound")
        obj.load_sensor = lambda sensor_id: cfg
        obj._listen = AsyncMock(return_value=7)
        assert await obj._sensor_step(cfg.id) == 7
        obj._listen.assert_awaited_once_with(cfg)
        assert not camera.calls and not camera.decodes
        obj._run_states.assert_not_awaited()
        await obj.shared_frames.close()

    asyncio.run(run())


def test_supplied_frame_keeps_its_identity_and_never_fetches_again():
    async def run():
        camera = Camera()
        obj = engine(camera)
        cfg = config()
        frame_id = obj.live_state(cfg.id).remember(camera.data)
        image = imaging.decode(camera.data)
        await obj.run_once(cfg, data=camera.data, image=image, frame_id=frame_id)
        assert not camera.calls and not camera.decodes
        assert obj.live_state(cfg.id).frame_id == frame_id
        assert obj._run_states.await_args.args[1] is image
        await obj.shared_frames.close()

    asyncio.run(run())


def test_exact_source_and_type_are_isolated():
    async def run():
        camera = Camera()
        pool = SharedFrames(camera.grab, camera.decode, 0.001)
        await asyncio.gather(pool.get("http", "a"), pool.get("http", "b"), pool.get("ha_camera", "a"))
        assert len(camera.calls) == camera.decodes == 3
        await pool.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [SourceError("offline"), OSError("invalid JPEG")])
def test_shared_failure_is_not_cached_and_recovers(failure):
    async def run():
        camera = Camera()
        pool = SharedFrames(camera.grab, camera.decode, 0.001)
        await pool.get("http", "a")  # a prior success must never mask the next failure
        camera.error = failure
        errors = await asyncio.gather(pool.get("http", "a"), pool.get("http", "a"), return_exceptions=True)
        assert all(err is failure for err in errors)
        assert len(camera.calls) == 2
        camera.error = None
        await pool.get("http", "a")
        assert len(camera.calls) == 3
        await pool.close()

    asyncio.run(run())


def test_decode_failure_releases_batch():
    async def run():
        camera = Camera()
        camera.data = b"not an image"
        pool = SharedFrames(camera.grab, camera.decode, 0.001)
        errors = await asyncio.gather(pool.get("http", "a"), pool.get("http", "a"), return_exceptions=True)
        assert all(isinstance(err, OSError) for err in errors)
        assert camera.decodes == 1
        camera.data = jpeg()
        await pool.get("http", "a")
        assert camera.decodes == 2
        await pool.close()

    asyncio.run(run())


def test_cancelling_one_waiter_does_not_cancel_other():
    async def run():
        camera = Camera()
        pool = SharedFrames(camera.grab, camera.decode, 0.01)
        a = asyncio.create_task(pool.get("http", "a"))
        b = asyncio.create_task(pool.get("http", "a"))
        await asyncio.sleep(0)
        a.cancel()
        assert isinstance((await asyncio.gather(a, return_exceptions=True))[0], asyncio.CancelledError)
        await b
        assert len(camera.calls) == 1
        await pool.close()

    asyncio.run(run())


def test_cancelling_all_waiters_avoids_capture_and_allows_new_batch():
    async def run():
        camera = Camera()
        pool = SharedFrames(camera.grab, camera.decode, 0.01)
        a = asyncio.create_task(pool.get("http", "a"))
        await asyncio.sleep(0)
        a.cancel()
        await asyncio.gather(a, return_exceptions=True)
        assert not camera.calls
        await pool.get("http", "a")
        assert len(camera.calls) == 1
        await pool.close()

    asyncio.run(run())


def test_late_waiter_never_receives_a_frame_from_inflight_capture():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        camera = Camera()

        async def slow_grab(kind, source):
            started.set()
            await release.wait()
            return await camera.grab(kind, source)

        pool = SharedFrames(slow_grab, camera.decode, 0.001)
        first = asyncio.create_task(pool.get("http", "a"))
        await started.wait()
        second = asyncio.create_task(pool.get("http", "a"))
        release.set()
        await asyncio.gather(first, second)
        assert len(camera.calls) == camera.decodes == 2
        await pool.close()

    asyncio.run(run())


def test_close_cancels_pending_work_and_rejects_new_requests():
    async def run():
        camera = Camera()
        pool = SharedFrames(camera.grab, camera.decode, 10)
        pending = asyncio.create_task(pool.get("http", "a"))
        await asyncio.sleep(0)
        await pool.close()
        assert isinstance((await asyncio.gather(pending, return_exceptions=True))[0], asyncio.CancelledError)
        assert not camera.calls and not pool._tasks and not pool._pending
        with pytest.raises(RuntimeError, match="closed"):
            await pool.get("http", "a")

    asyncio.run(run())


def test_close_cancels_an_inflight_capture():
    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()
        camera = Camera()

        async def grab(kind, source):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        pool = SharedFrames(grab, camera.decode, 0)
        pending = asyncio.create_task(pool.get("http", "a"))
        await started.wait()
        await pool.close()
        assert cancelled.is_set()
        assert isinstance((await asyncio.gather(pending, return_exceptions=True))[0], asyncio.CancelledError)
        assert not pool._tasks and not pool._pending

    asyncio.run(run())


def test_shared_run_keeps_independent_sensor_frames_rois_and_state_checks():
    async def run():
        camera = Camera()
        obj = engine(camera)
        configs = [config(i, roi={"x": i / 4, "y": 0, "w": 0.25, "h": 1}) for i in range(4)]
        await asyncio.gather(*(obj.run_once(cfg, shared=True) for cfg in configs))
        assert len(camera.calls) == camera.decodes == 1
        assert obj._run_states.await_count == 4
        assert {call.args[0].id for call in obj._run_states.await_args_list} == set(range(4))
        assert len({id(call.args[1]) for call in obj._run_states.await_args_list}) == 1
        for cfg in configs:
            live = obj.live_state(cfg.id)
            assert live.available is True and live.last_run is not None
            assert live.frames[-1][1] == camera.data
            assert live.frame_id == live.frames[-1][0]
        await obj.shared_frames.close()

    asyncio.run(run())


def test_capture_error_marks_each_sensor_offline_without_inference():
    async def run():
        camera = Camera()
        camera.error = SourceError("offline")
        obj = engine(camera)
        await asyncio.gather(obj.run_once(config(1), shared=True), obj.run_once(config(2), shared=True))
        assert len(camera.calls) == 1
        assert obj.live_state(1).available is obj.live_state(2).available is False
        assert obj._run_states.await_count == 0
        assert obj._send.await_count == 2
        await obj.shared_frames.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "forced",
        "paused",
        "light",
        "probe",
        "trigger_only",
        "burst",
        "face_burst",
        "recheck",
        "rtsp",
        "objects",
        "reading",
        "sound",
    ],
)
def test_only_compatible_regular_state_checks_can_share(case):
    obj = engine(Camera())
    cfg = config()
    assert obj._share_regular_frame(cfg, False)
    if case == "paused":
        cfg.enabled = False
    elif case == "light":
        cfg.triggers["light_entity"] = "light.a"
    elif case == "probe":
        cfg.triggers["change_detection"] = True
    elif case == "trigger_only":
        cfg.triggers["regular"] = False
    elif case == "burst":
        obj.live_state(cfg.id).burst_until = time.time() + 60
    elif case == "face_burst":
        obj.live_state(cfg.id).face_burst.until = time.time() + 60
    elif case == "recheck":
        obj.live_state(cfg.id).recheck_at = time.time() + 1
    elif case == "rtsp":
        cfg.source_type = "rtsp"
    elif case in {"objects", "reading", "sound"}:
        cfg.kind = case
    assert not obj._share_regular_frame(cfg, case == "forced")
