"""The speed test: models measured on this machine, on the camera or microphone of a sensor.

One test at a time. While it runs every sensor is paused and every model let go, so the numbers are
of one model alone: each is downloaded when needed, loaded, run (the same work as in a check) and let
go before the next. Afterwards what was downloaded only for the test is deleted, unless asked to keep
it, and the sensors go on. The results are kept in memory until the next test or a restart.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import platform
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import select

from .. import backbones, detectors, faces, imaging, readers
from ..db import Sensor
from ..dials import read_needles
from ..redact import redact
from ..settings import (
    BENCHMARK,
    DETECTION,
    KIND_OBJECTS,
    KIND_SOUND,
    KIND_STATES,
    SOUND,
    VERSION,
)
from ..sounds import MODELS as SOUND_MODELS
from ..sources import AudioStream
from .checks import ChecksMixin
from .state import SensorConfig

log = logging.getLogger(__name__)

TEMP_FILE = ".speed-test-downloads.json"  # in the models folder: deleted after the test, also after a crash


class DownloadStopped(Exception):
    """The speed test was stopped during a download."""


def process_memory_mb() -> float | None:
    """The memory this process holds now; None where the system does not say (only Linux does here)."""
    if sys.platform == "win32":
        return None
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
    except OSError, IndexError, ValueError:
        return None
    return pages * os.sysconf("SC_PAGE_SIZE") / 1e6


def available_memory_mb() -> float | None:
    """The memory the system can still give; None where it does not say."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1000
    except OSError, IndexError, ValueError:
        pass
    return None


def total_memory_mb() -> float | None:
    if sys.platform == "win32":
        return None
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e6
    except AttributeError, OSError, ValueError:
        return None


class MemoryPeak:
    """The most memory the process holds while this runs, looked at from a thread of its own."""

    def __init__(self):
        self.peak = process_memory_mb()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="speed-test-memory", daemon=True)

    def _watch(self) -> None:
        while not self._stop.wait(BENCHMARK["memory_sample_s"]):
            now = process_memory_mb()
            if now is not None and (self.peak is None or now > self.peak):
                self.peak = now

    def __enter__(self) -> MemoryPeak:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


def timings(values: list[float]) -> dict | None:
    """Median and 95th percentile of durations in seconds, in milliseconds."""
    if not values:
        return None
    return {
        "median": round(float(np.median(values)) * 1000, 1),
        "p95": round(float(np.percentile(values, 95)) * 1000, 1),
    }


@dataclass
class BenchmarkRun:
    sensor: dict
    picks: list[dict]
    keep: bool
    state: str = "running"  # running, finished, cancelled, failed
    step: str = "Pausing the sensors"
    index: int = 0
    round: int = 0
    rounds: int = 0
    downloaded_mb: float = 0.0
    error: str = ""
    results: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    deleted: list[str] = field(default_factory=list)
    machine: dict = field(default_factory=dict)
    cancelled: bool = False

    def view(self) -> dict:
        return {
            "state": self.state,
            "step": self.step,
            "index": self.index,
            "total": len(self.picks),
            "round": self.round,
            "rounds": self.rounds,
            "downloaded_mb": round(self.downloaded_mb),
            "error": self.error,
            "sensor": self.sensor,
            "results": self.results,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "deleted": self.deleted,
            "kept": self.keep,
            "machine": self.machine,
        }


class BenchmarkMixin(ChecksMixin):
    # --- What can be tested ----------------------------------------------------------------------

    def benchmark_roles(self, cfg: SensorConfig) -> list[str]:
        """The kinds of model a sensor can be tested with."""
        if cfg.kind == KIND_STATES:
            return ["backbones"]
        if cfg.kind == KIND_OBJECTS:
            return ["detectors", "face_models"]
        if cfg.kind == KIND_SOUND:
            return ["sound_models"]
        return ["wheel_readers", "readers"] if cfg.reading.get("display") == "counter" else ["readers"]

    @staticmethod
    def _benchmark_specs(role: str) -> dict:
        return {
            "backbones": backbones.BACKBONES,
            "detectors": detectors.DETECTORS,
            "face_models": faces.RECOGNISERS,
            "readers": readers.READERS,
            "wheel_readers": readers.WHEEL_READERS,
            "sound_models": SOUND_MODELS,
        }[role]

    def _benchmark_files(self, role: str, spec) -> list:  # noqa: ANN001 - a model of any registry
        return [faces.DETECTOR, spec] if role == "face_models" else [spec]

    def _benchmark_current(self, cfg: SensorConfig, role: str) -> str:
        """The model the sensor uses now in this role ("" when it uses none)."""
        if role == "backbones":
            return self.db.get_text("backbone", backbones.DEFAULT_BACKBONE)
        if role == "detectors":
            return self.db.get_text("detector", detectors.DEFAULT_DETECTOR)
        if role == "face_models":
            return self.face_choice if (cfg.objects.get("faces") or {}).get("enabled") else ""
        if role == "readers":
            return "" if readers.uses_wheels(cfg.reading) else self.db.get_text("reader", readers.DEFAULT_READER)
        if role == "wheel_readers":
            return readers.wheel_reader_of(cfg.reading) if readers.uses_wheels(cfg.reading) else ""
        return next(iter(SOUND_MODELS))

    def benchmark_options(self) -> dict:
        """What the page offers: the sensors, and for each the models it can be tested with."""
        with self.db.session() as s:
            ids = list(s.scalars(select(Sensor.id).order_by(Sensor.name)))
        sensors = []
        for sensor_id in ids:
            cfg = self.load_sensor(sensor_id)
            if cfg is None:
                continue
            models = []
            for role in self.benchmark_roles(cfg):
                current = self._benchmark_current(cfg, role)
                for spec in self._benchmark_specs(role).values():
                    files = self._benchmark_files(role, spec)
                    missing = [f for f in files if self.model_path(f) is None]
                    models.append(
                        {
                            "role": role,
                            "id": spec.id,
                            "name": spec.name,
                            "description": spec.description,
                            "installed": not missing,
                            "download_size": sum(f.size for f in missing),
                            "available": all(self.model_path(f) is not None or f.url for f in files),
                            "current": spec.id == current,
                            **spec.footprint.as_dict(),
                        }
                    )
            sensors.append(
                {
                    "id": cfg.id,
                    "name": cfg.name,
                    "kind": cfg.kind,
                    "source_type": cfg.source_type,
                    "interval_s": cfg.interval_s,
                    "models": models,
                }
            )
        return {"sensors": sensors, "rounds": BENCHMARK["rounds"]}

    # --- Start and stop --------------------------------------------------------------------------

    def benchmark_view(self) -> dict | None:
        return self.benchmark.view() if self.benchmark else None

    def benchmark_running(self) -> bool:
        return self.benchmark is not None and self.benchmark.state == "running"

    def benchmark_start(self, cfg: SensorConfig, picks: list[dict], keep: bool) -> BenchmarkRun:
        sensor = {
            "id": cfg.id,
            "name": cfg.name,
            "kind": cfg.kind,
            "source_type": cfg.source_type,
            "interval_s": cfg.interval_s,
        }
        run = BenchmarkRun(sensor, picks, keep, machine=self._benchmark_machine())
        self.benchmark = run
        self.benchmark_paused = True  # from here on no new check starts
        self._benchmark_task = self._spawn(self._benchmark_run(run, cfg))
        return run

    def benchmark_cancel(self) -> None:
        if self.benchmark_running() and self._benchmark_task is not None:
            assert self.benchmark is not None
            self.benchmark.cancelled = True
            self.benchmark.step = "Stopping"
            self._benchmark_task.cancel()

    @staticmethod
    def _benchmark_machine() -> dict:
        return {
            "version": VERSION,
            "cpus": os.cpu_count(),
            "memory_mb": round(total) if (total := total_memory_mb()) else None,
            "arch": platform.machine() or None,
        }

    async def _benchmark_run(self, run: BenchmarkRun, cfg: SensorConfig) -> None:
        light = "" if cfg.is_sound else cfg.triggers["light_entity"]
        stopping = False
        ended = "failed"
        try:
            await self._benchmark_wait_for_quiet()
            if light:
                run.step = "Switching on the light"
                held = await self.lights.hold(light, "benchmark")
                if not held.error:
                    await asyncio.sleep(self.lights.wait_s(light, cfg.triggers["light_delay_s"]) or 0.0)
            await self._benchmark_let_go()
            for index, pick in enumerate(run.picks, 1):
                run.index = index
                await self._benchmark_one(run, cfg, pick)
            ended = "finished"
        except asyncio.CancelledError:
            ended = "cancelled"
            stopping = not run.cancelled  # the app stops: nothing to come back to
            if stopping:
                raise
        except Exception as err:  # noqa: BLE001 - the sensors must go on whatever happens
            log.exception("The speed test failed")
            run.error = redact(str(err)) or type(err).__name__
        finally:
            # Running until all of this is done: the page shows the results with what was deleted, and
            # nothing here may keep the sensors from going on.
            run.step = "Finishing"
            try:
                if light:
                    await self.lights.release(light, "benchmark")
                run.deleted = await asyncio.to_thread(self.benchmark_delete_downloads, run.keep)
            except Exception:  # noqa: BLE001
                log.exception("Could not clean up after the speed test")
            if not stopping:
                await self._benchmark_resume()
            run.finished_at = time.time()
            run.state = ended

    async def _benchmark_wait_for_quiet(self) -> None:
        """Until the checks that were running and any training have ended."""
        deadline = time.monotonic() + BENCHMARK["wait_quiet_s"]
        while (self._steps_running or self.training) and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

    async def _benchmark_let_go(self) -> None:
        """Every model out of memory, so that what a model takes can be seen."""
        self.embedder = self.detector = self.reader = None
        self.wheel_readers.clear()
        self.face_model = None
        self.sound_model = None
        await asyncio.to_thread(backbones.release_memory)

    async def _benchmark_resume(self) -> None:
        """The models the sensors need, then the sensors."""
        try:
            backbone = await asyncio.to_thread(self.db.get_text, "backbone", backbones.DEFAULT_BACKBONE)
            await self.load_embedder(backbone)
        except Exception as err:  # noqa: BLE001
            log.exception("Could not load the backbone again")
            self.embedder_error = redact(str(err))
        self.benchmark_paused = False
        try:
            await self.faces_settled()
        except Exception:  # noqa: BLE001 - the sensors go on without faces, as when loading them fails
            log.exception("Could not load the face model again")
        for sensor_id in list(self._wake):
            self.wake(sensor_id)
        later, self._retrain_after_benchmark = self._retrain_after_benchmark, set()
        for sensor_id in later:
            self.schedule_retrain(sensor_id)

    # --- One model -------------------------------------------------------------------------------

    async def _benchmark_one(self, run: BenchmarkRun, cfg: SensorConfig, pick: dict) -> None:
        role, spec = pick["role"], self._benchmark_specs(pick["role"])[pick["id"]]
        result: dict = {"role": role, "id": spec.id, "name": spec.name, "status": "done", "error": ""}
        result["current"] = (await asyncio.to_thread(self._benchmark_current, cfg, role)) == spec.id
        run.results.append(result)
        run.round, run.rounds, run.downloaded_mb = 0, 0, 0.0
        try:
            result["download_s"] = await self._benchmark_download(run, role, spec)
            room = available_memory_mb()
            need = spec.footprint.ram_mb * BENCHMARK["memory_room"]
            if room is not None and need > room:
                result["status"] = "skipped"
                result["error"] = (
                    f"Not enough free memory: it takes about {spec.footprint.ram_mb} MB, {round(room)} MB is free"
                )
                return
            await self._benchmark_measure(run, cfg, role, spec, result)
        except asyncio.CancelledError:
            result["status"], result["error"] = "cancelled", ""
            raise
        except Exception as err:  # noqa: BLE001 - shown with this model, the next is tried
            log.warning("Speed test of %s failed: %s", spec.id, redact(str(err)) or type(err).__name__)
            result["status"], result["error"] = "failed", redact(str(err)) or type(err).__name__
        finally:
            gc.collect()
            await asyncio.to_thread(backbones.release_memory)

    async def _benchmark_download(self, run: BenchmarkRun, role: str, spec) -> float | None:  # noqa: ANN001
        files = [f for f in self._benchmark_files(role, spec) if self.model_path(f) is None]
        if not files:
            return None
        run.step = f"Downloading {spec.name}"
        started = time.monotonic()
        for file in files:
            if not file.url:
                raise RuntimeError("This model is not published yet")

            await asyncio.to_thread(self._benchmark_note_download, file.filename)
            await self._benchmark_fetch(run, file)
        return round(time.monotonic() - started, 1)

    async def _benchmark_fetch(self, run: BenchmarkRun, file) -> None:  # noqa: ANN001 - a model file
        """Download one file. Stopped, its thread must have ended before anything is deleted: it would
        put the finished file back afterwards, where nothing knows it came from the test."""
        stop = threading.Event()

        def progress(done: int) -> None:
            if stop.is_set():
                raise DownloadStopped
            run.downloaded_mb = done / 1e6

        fetching = asyncio.ensure_future(
            asyncio.to_thread(backbones.download, file, self.settings.models_dir, progress)
        )
        try:
            await asyncio.wait([fetching])  # being stopped does not cancel it
        except asyncio.CancelledError:
            stop.set()
            await asyncio.wait([fetching])
            fetching.exception()  # DownloadStopped, or what the download ended with
            raise
        fetching.result()

    async def _benchmark_measure(self, run: BenchmarkRun, cfg: SensorConfig, role: str, spec, result: dict) -> None:  # noqa: ANN001
        before = process_memory_mb()
        with MemoryPeak() as memory:
            run.step = f"Loading {spec.name}"
            started = time.monotonic()
            infer = await asyncio.to_thread(self._benchmark_load, cfg, role, spec)
            result["load_ms"] = round((time.monotonic() - started) * 1000)
            loaded = process_memory_mb()
            run.step = f"Testing {spec.name}"
            if cfg.is_sound:
                await self._benchmark_sound_rounds(run, cfg, infer, result)
            else:
                await self._benchmark_picture_rounds(run, cfg, infer, result)
            peak = memory.peak
        if before is not None and loaded is not None and peak is not None:
            result["memory_loaded_mb"] = round(loaded - before)
            result["memory_peak_mb"] = round(peak - before)
        del infer

    def _benchmark_load(self, cfg: SensorConfig, role: str, spec) -> Callable:  # noqa: ANN001
        """The model loaded; what comes back does the work of a check with it, on a decoded picture
        (for sound: on a window of samples)."""
        path = self.model_path(spec)
        assert path is not None
        if role == "backbones":
            embedder = backbones.Embedder(spec, path)
            return lambda image: embedder.embed([imaging.crop(image, cfg.roi)])
        if role == "detectors":
            detector = detectors.Detector(spec, path)
            box = imaging.region_box(cfg.roi, DETECTION["context_margin"])
            return lambda image: detector.detect(imaging.crop_box(image, box), cfg.threshold)
        if role == "face_models":
            detector_path = self.model_path(faces.DETECTOR)
            assert detector_path is not None
            face = faces.FaceModel(faces.FaceDetector(faces.DETECTOR, detector_path), faces.Recogniser(spec, path))

            def faces_in(image: Image.Image) -> None:  # one person: one region, one face
                found = face.detector.detect([image])[0]
                size = face.recogniser.spec.input_size
                aligned = faces.align(image, found, size) if found is not None else image.resize((size, size))
                face.recogniser.embed([aligned])

            return faces_in
        if role == "sound_models":
            model = self._sound_model_of(spec, path)
            return lambda window: model.run(window[None, :])
        reading = cfg.reading
        straightened = bool(reading.get("straighten")) and imaging.region_corners(cfg.roi) is not None
        if role == "wheel_readers":
            wheels = readers.WheelReader(spec, path)

            def wheels_in(image: Image.Image) -> None:
                needles = read_needles(image, cfg.roi, reading, straightened)
                seen = imaging.region_image(image, cfg.roi, True) if straightened else image
                box = None if straightened else imaging.pixel_box(seen, imaging.region_box(cfg.roi))
                wheels.read_counter(seen, int(reading["digits"]), box, needles)

            return wheels_in
        reader = readers.Reader(spec, path)
        return lambda image: reader.read_display(
            imaging.region_image(image, cfg.roi, straightened), reading["display"], int(reading["digits"])
        )

    @staticmethod
    def _sound_model_of(spec, path: Path):  # noqa: ANN001, ANN205 - imported here like the sound part does
        from ..sounds import SoundModel

        return SoundModel(spec, path)

    async def _benchmark_picture_rounds(
        self, run: BenchmarkRun, cfg: SensorConfig, infer: Callable, result: dict
    ) -> None:
        camera: list[float] = []
        decode: list[float] = []
        work: list[float] = []
        total: list[float] = []
        size, data = None, b""
        run.rounds = BENCHMARK["rounds"]
        started = time.monotonic()
        for number in range(BENCHMARK["warmup_rounds"] + BENCHMARK["rounds"]):
            run.round = max(0, number - BENCHMARK["warmup_rounds"] + 1)
            t0 = time.perf_counter()
            data = await self.grabber.grab(cfg.source_type, cfg.source)
            t1 = time.perf_counter()
            image = await asyncio.to_thread(imaging.decode, data)
            t2 = time.perf_counter()
            await asyncio.to_thread(infer, image)
            t3 = time.perf_counter()
            size = image.size
            del image
            await asyncio.to_thread(backbones.trim_memory)  # as after every check, not counted
            if number >= BENCHMARK["warmup_rounds"]:
                camera.append(t1 - t0)
                decode.append(t2 - t1)
                work.append(t3 - t2)
                total.append(t3 - t0)
                enough = len(total) >= BENCHMARK["min_rounds"]
                if enough and time.monotonic() - started > BENCHMARK["max_model_s"]:
                    break
        result.update(
            rounds=len(total),
            camera=timings(camera),
            decode=timings(decode),
            ai=timings(work),
            total=timings(total),
            picture=f"{size[0]}×{size[1]}" if size else None,
            picture_kb=round(len(data) / 1000),
        )

    async def _benchmark_sound_rounds(
        self, run: BenchmarkRun, cfg: SensorConfig, infer: Callable, result: dict
    ) -> None:
        stream = AudioStream(cfg.source, False)
        try:
            run.step = "Listening to the stream"
            t0 = time.monotonic()
            deadline = t0 + BENCHMARK["sound_wait_s"]
            need = int(SOUND["window_s"] * stream.rate)
            while stream.total < need:
                if time.monotonic() > deadline:
                    raise RuntimeError(stream.error or "No sound from the stream")
                await asyncio.sleep(0.05)
            ready = time.monotonic() - t0
            window = stream.tail(SOUND["window_s"])
        finally:
            stream.close()
        run.step = f"Testing {result['name']}"
        run.rounds = BENCHMARK["rounds"]
        work: list[float] = []
        started = time.monotonic()
        for number in range(BENCHMARK["warmup_rounds"] + BENCHMARK["rounds"]):
            run.round = max(0, number - BENCHMARK["warmup_rounds"] + 1)
            t1 = time.perf_counter()
            await asyncio.to_thread(infer, window)
            if number >= BENCHMARK["warmup_rounds"]:
                work.append(time.perf_counter() - t1)
                if len(work) >= BENCHMARK["min_rounds"] and time.monotonic() - started > BENCHMARK["max_model_s"]:
                    break
        result.update(
            rounds=len(work),
            sound_ready_ms=round(ready * 1000),
            ai=timings(work),
            total=timings(work),
            window_s=SOUND["window_s"],
            hop_s=SOUND["hop_s"],
        )

    # --- What was downloaded for the test --------------------------------------------------------

    def _benchmark_temp_path(self) -> Path:
        return self.settings.models_dir / TEMP_FILE

    def _benchmark_note_download(self, filename: str) -> None:
        """Written before the download: a crash must not leave the model behind for good."""
        path = self._benchmark_temp_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        names = json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps([*names, filename]))

    def benchmark_delete_downloads(self, keep: bool = False) -> list[str]:
        """What was downloaded only for the test is deleted (``keep``: stays); at start-up, what a
        test that never ended left behind. Returns the files deleted."""
        path = self._benchmark_temp_path()
        if not path.exists():
            return []
        try:
            names = json.loads(path.read_text())
        except ValueError:
            names = []
        deleted = []
        if not keep:
            for name in names:
                file = self.settings.models_dir / Path(name).name  # never out of the folder
                file.unlink(missing_ok=True)
                file.with_suffix(".part").unlink(missing_ok=True)
                deleted.append(file.name)
        path.unlink(missing_ok=True)
        return deleted
