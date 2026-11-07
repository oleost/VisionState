"""A check's clock (timing.py): its parts, the wait for a model run kept apart, and when its frame
was asked for."""

import asyncio
import time

from visionstate import timing
from visionstate.shared_frames import SharedFrames


def test_parts_add_up_and_a_wait_for_a_model_run_counts_apart():
    async def run() -> timing.Check:
        busy = asyncio.Semaphore(1)
        with timing.checking() as check:
            with timing.step("grab"):
                await asyncio.sleep(0.02)
            await busy.acquire()  # another sensor's model run
            asyncio.get_running_loop().call_later(0.05, busy.release)
            with timing.step("detect"):
                async with timing.slot(busy):
                    await asyncio.sleep(0.02)
            with timing.step("detect"):  # a part that runs twice adds up
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.02)
            timing.lap("publish")  # the stretch since the last part
        return check

    check = asyncio.run(run())
    assert list(check.steps) == ["grab", "detect", "publish"]
    assert 0.015 <= check.steps["grab"] < 0.04
    assert 0.025 <= check.steps["detect"] < 0.05  # its own work, not the wait
    assert 0.04 <= check.waited < 0.08
    assert 0.015 <= check.steps["publish"] < 0.04
    summary = check.summary()
    assert summary.endswith(")") and ", waited 0.0" in summary and "(grab 0.0" in summary


def test_a_check_started_by_a_probe_keeps_its_frame_time():
    async def run() -> tuple[float | None, float | None]:
        with timing.checking() as probe:
            timing.captured()
            asked = probe.captured_at
            with timing.checking() as check:  # the check the probe starts: the same one
                assert check is probe
            return asked, timing.captured_at()

    before = time.time()
    asked, kept = asyncio.run(run())
    assert asked is not None and before <= asked <= time.time() and kept == asked
    assert timing.current() is None and timing.captured_at() is None  # over with the check


def test_without_a_check_nothing_is_timed():
    async def run() -> None:
        timing.captured()
        timing.lap("publish")
        with timing.step("detect"):
            async with timing.slot(asyncio.Semaphore(1)):
                pass

    asyncio.run(run())
    assert timing.current() is None


def test_a_shared_frame_knows_when_the_camera_was_asked():
    async def grab(kind: str, source: str) -> bytes:
        await asyncio.sleep(0.03)  # a slow camera: the time is when it was asked, not when it answered
        return b"jpeg"

    async def run() -> tuple[float, float | None]:
        frames = SharedFrames(grab, lambda data: None, 0.0)  # type: ignore[arg-type]
        start = time.time()
        frame = await frames.get("http", "x")
        await frames.close()
        return start, frame.taken_at

    start, taken = asyncio.run(run())
    assert taken is not None and start <= taken < start + 0.02
