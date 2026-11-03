"""Threads per model run: one per core unless the user chooses (Settings → AI model), applied
without a restart and kept across one."""

import pytest
from fastapi.testclient import TestClient

from visionstate import backbones
from visionstate.main import create_app
from visionstate.settings import RUNTIME, Settings

from .conftest import MODEL_DIR, requires_model

# Two chips with two cores each, every core with two hyper-threads (8 CPUs), like an x86 server.
CPUINFO = "".join(
    f"processor\t: {n}\nphysical id\t: {n // 4}\ncore id\t\t: {(n // 2) % 2}\nflags\t\t: fpu sse4_2 avx avx2 fma\n\n"
    for n in range(8)
)
# An ARM board: no core ids, no hyper-threads.
CPUINFO_ARM = "".join(f"processor\t: {n}\nFeatures\t: fp asimd asimddp crc32\n\n" for n in range(4))


@pytest.fixture(autouse=True)
def automatic():
    """The thread count is process-wide: every test starts and ends with the default."""
    backbones.set_threads(RUNTIME["inference_threads"])
    yield
    backbones.set_threads(RUNTIME["inference_threads"])


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "d", media_dir=tmp_path / "m", frontend_dir=tmp_path, bundled_models_dir=MODEL_DIR
    )


def _usable(monkeypatch, count: int) -> None:
    monkeypatch.setattr(backbones.os, "sched_getaffinity", lambda pid: set(range(count)), raising=False)


def test_one_thread_per_physical_core(monkeypatch):
    monkeypatch.setattr(backbones, "_cpuinfo", lambda: CPUINFO)
    _usable(monkeypatch, 8)
    assert backbones.cpu_cores() == 4  # hyper-threads not counted
    _usable(monkeypatch, 2)
    assert backbones.cpu_cores() == 2  # a container allowed on fewer CPUs
    assert backbones.cpu_flags() == ["avx", "avx2", "fma"]


def test_every_cpu_where_cores_are_not_listed(monkeypatch):
    monkeypatch.setattr(backbones, "_cpuinfo", lambda: CPUINFO_ARM)
    _usable(monkeypatch, 4)
    assert backbones.cpu_cores() == 4
    assert backbones.cpu_flags() == ["asimd", "asimddp"]
    monkeypatch.setattr(backbones, "_cpuinfo", lambda: "")  # not Linux: ONNX Runtime counts them
    assert backbones.cpu_flags() == [] and backbones.cpu_cores() is None
    assert backbones.inference_threads() == 0


@requires_model
def test_chosen_threads_reach_onnx_runtime(monkeypatch):
    monkeypatch.setattr(backbones, "cpu_cores", lambda: 3)
    spec = backbones.BACKBONES[backbones.DEFAULT_BACKBONE]
    session = backbones.cpu_session(MODEL_DIR / spec.filename)
    assert session.get_session_options().intra_op_num_threads == 3  # automatic: one per core
    backbones.set_threads(2)
    session = backbones.cpu_session(MODEL_DIR / spec.filename)
    assert session.get_session_options().intra_op_num_threads == 2


@requires_model
def test_threads_are_chosen_in_the_settings_and_kept(settings, monkeypatch):
    monkeypatch.setattr(backbones.os, "cpu_count", lambda: 4)
    with TestClient(create_app(settings)) as client:
        info = client.get("/api/v1/settings").json()
        assert info["inference_threads"] == 0 and info["threads_max"] == 4
        assert info["threads_auto"] == backbones.cpu_cores()  # None on a Windows PC
        rt = client.app.state.runtime
        before = rt.embedder
        rt.detector = object()  # stands for a loaded detector: loaded again on its next use
        body = {"backbone": info["backbone"], "inference_threads": 2}
        assert client.put("/api/v1/settings", json=body).json()["inference_threads"] == 2
        assert rt.embedder is not before  # loaded again, without a restart
        assert rt.embedder.session.get_session_options().intra_op_num_threads == 2
        assert rt.detector is None
        assert client.put("/api/v1/settings", json={**body, "inference_threads": 5}).status_code == 400
        assert client.put("/api/v1/settings", json={**body, "inference_threads": -1}).status_code == 400
    backbones.set_threads(0)
    with TestClient(create_app(settings)) as client:  # a restart keeps the choice
        assert client.get("/api/v1/settings").json()["inference_threads"] == 2
        assert backbones.inference_threads() == 2
        rt = client.app.state.runtime
        rt.benchmark_running = lambda: True
        busy = client.put("/api/v1/settings", json={"backbone": rt.embedder.spec.id, "inference_threads": 1})
        assert busy.status_code == 409  # not in the middle of a speed test


def test_the_report_names_the_threads_and_cpu_features():
    from visionstate.engine.benchmark import BenchmarkRun

    sensor = {"kind": "objects", "source_type": "http"}
    run = BenchmarkRun(sensor=sensor, picks=[], keep=False)
    run.machine = {"version": "x", "cpus": 4, "threads": 1, "threads_auto": False, "cpu_flags": ["avx2", "fma"]}
    assert run.report().splitlines()[0] == "VisionState x speed test, 4 CPUs, 1 thread per model, avx2 fma"
    run.machine = {**run.machine, "threads": 4, "threads_auto": True}
    assert ", 4 threads per model (automatic), avx2 fma" in run.report()
    run.machine = {**run.machine, "threads": 0, "cpu_flags": []}  # off Linux: ONNX Runtime counts them
    assert run.report().splitlines()[0].endswith("4 CPUs, threads per model (automatic)")
