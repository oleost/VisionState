"""Exercise every native library the app uses, to find one that needs a newer CPU.

Run inside the app image under an emulated old CPU (see the "Old CPU check" step in
.github/workflows/ci.yml):

    qemu-x86_64-static -cpu kvm64 /usr/local/bin/python /probe/cpu_probe.py /assets

A library built for a newer CPU either refuses to import (NumPy) or dies with "Illegal
instruction"; each step is announced before it runs, so the log shows where.
"""

import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.getcwd())  # run from the backend folder (/app/backend in the image)
ASSETS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "visionstate/backend/tests/assets"
failed: list[str] = []


def step(name: str):
    def run(fn):
        print(f"run  {name}", flush=True)
        try:
            detail = fn()
            print(f"ok   {name}{f' — {detail}' if detail else ''}", flush=True)
        except Exception as err:  # noqa: BLE001 - report every failure, then exit non-zero
            failed.append(name)
            print(f"FAIL {name}: {type(err).__name__}: {str(err)[:400]}", flush=True)
        return fn

    return run


@step("numpy")
def _numpy():
    import numpy as np

    rng = np.random.default_rng(0)
    a = rng.normal(size=(200, 200)).astype(np.float32)
    np.linalg.svd(a @ a.T)
    np.fft.fft2(a)
    np.sort(a, axis=1)
    np.einsum("ij,jk->ik", a, a)
    (a > 0).sum()
    np.histogram(a, bins=256)
    return np.__version__


@step("pillow")
def _pillow():
    import PIL
    from PIL import Image, ImageFilter, ImageOps

    photo = Image.open(ASSETS / "beach.jpg").convert("RGB")
    small = photo.resize((224, 224), Image.Resampling.LANCZOS)
    ImageOps.autocontrast(small.convert("L"), cutoff=1).filter(ImageFilter.GaussianBlur(2))
    for fmt in ("JPEG", "PNG", "WEBP"):
        buf = io.BytesIO()
        small.save(buf, format=fmt)
        Image.open(io.BytesIO(buf.getvalue())).load()
    return PIL.__version__


@step("scipy")
def _scipy():
    import numpy as np
    import scipy
    from scipy import linalg, optimize, sparse, special

    a = np.random.default_rng(1).normal(size=(120, 120))
    linalg.svd(a)
    linalg.solve(a @ a.T + np.eye(120), np.ones(120))
    optimize.minimize(lambda x: ((x - 3) ** 2).sum(), np.zeros(8), method="L-BFGS-B")
    sparse.random(200, 200, density=0.05, random_state=1).tocsr() @ np.ones(200)
    special.expit(a)
    return scipy.__version__


@step("scikit-learn (train and cross-validate a head)")
def _sklearn():
    import numpy as np
    import sklearn

    from visionstate import classifier

    rng = np.random.default_rng(2)
    vectors = np.concatenate([rng.normal(0, 1, (30, 768)), rng.normal(0.5, 1, (30, 768))]).astype(np.float32)
    labels = ["open"] * 30 + ["closed"] * 30
    result = classifier.train(vectors, labels, "probe", 1, ["open", "closed"])
    result.head.predict(vectors[:4], ["open", "closed"])
    return sklearn.__version__


@step("onnxruntime: state backbone")
def _backbone():
    import onnxruntime
    from PIL import Image

    from visionstate import backbones
    from visionstate.settings import load_settings

    spec = backbones.BACKBONES[backbones.DEFAULT_BACKBONE]
    path = backbones.locate(spec, [load_settings().bundled_models_dir])
    vector = backbones.Embedder(spec, path).embed([Image.open(ASSETS / "beach.jpg").convert("RGB")])
    return f"{onnxruntime.__version__}, {vector.shape}"


@step("onnxruntime: object detector")
def _detector():
    from PIL import Image

    from visionstate import backbones, detectors
    from visionstate.settings import load_settings

    spec = detectors.DETECTORS[detectors.DEFAULT_DETECTOR]
    path = backbones.locate(spec, [load_settings().bundled_models_dir])
    found = detectors.Detector(spec, path).detect(Image.open(ASSETS / "beach.jpg").convert("RGB"), 0.5)
    keys = sorted({d.as_dict()["key"] for d in found})
    assert "dog" in keys and "person" in keys, keys
    return ", ".join(keys)


@step("onnxruntime: number reader")
def _reader():
    from PIL import Image

    from visionstate import backbones, readers
    from visionstate.settings import load_settings

    spec = readers.READERS[readers.DEFAULT_READER]
    path = backbones.locate(spec, [load_settings().bundled_models_dir])
    text, _ = readers.Reader(spec, path).read_display(Image.open(ASSETS / "lcd.png").convert("RGB"), "auto")
    assert text.text.replace(".", "") == "01234567", text
    return text.text


@step("av (encode and decode a video)")
def _av():
    import av
    import numpy as np

    from visionstate import uploads

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clip.mp4"
        with av.open(str(path), "w") as container:
            stream = container.add_stream("mpeg4", rate=5)
            stream.width, stream.height, stream.pix_fmt = 160, 120, "yuv420p"
            for i in range(15):
                frame = av.VideoFrame.from_ndarray(np.full((120, 160, 3), i * 15, dtype=np.uint8), format="rgb24")
                container.mux(stream.encode(frame))
            container.mux(stream.encode())
        frames = list(uploads.frames_from_video(path, 1.0))
    assert frames, "no frames decoded"
    return f"{av.__version__}, {len(frames)} frames"


@step("the app itself (FastAPI, pydantic, SQLAlchemy) with a state sensor, a detection and a reading")
def _app():
    import dataclasses

    from fastapi.testclient import TestClient

    from visionstate.main import create_app
    from visionstate.settings import load_settings

    class Camera:
        async def grab(self, source_type, source):
            return (ASSETS / source).read_bytes()

        async def close(self):
            pass

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings = dataclasses.replace(
            load_settings(), data_dir=Path(tmp) / "data", media_dir=Path(tmp) / "media", frontend_dir=Path(tmp) / "none"
        )
        with TestClient(create_app(settings)) as client:
            client.app.state.runtime.grabber = Camera()
            assert client.get("/api/v1/config").status_code == 200
            status = client.get("/api/v1/status").json()
            assert status["backbone"] and not status["backbone_error"], status
            read = client.post(
                "/api/v1/preview/read",
                json={"source_type": "http", "source": "lcd.png", "reading": {"mode": "counter", "decimals": 1}},
            )
            assert read.status_code == 200 and read.json()["value"] == "123456.7", read.text
            detect = client.post("/api/v1/preview/detect", json={"source_type": "http", "source": "beach.jpg"})
            assert detect.status_code == 200 and detect.json()["detections"], detect.text
            sensor = client.post(
                "/api/v1/sensors",
                json={"name": "Probe", "source_type": "http", "source": "cat.jpg", "states": [{"name": "A"}, {"name": "B"}]},
            )
            assert sensor.status_code == 201, sensor.text
    return f"version {status.get('version')}"


@step("other native modules (import)")
def _others():
    import importlib

    names = ["httptools", "websockets", "yaml", "watchfiles", "google.protobuf", "pydantic_core", "sqlalchemy", "joblib", "aiomqtt"]
    missing = []
    for name in names:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            missing.append(name)
    return f"not installed: {', '.join(missing)}" if missing else None


if failed:
    print("FAILED:", ", ".join(failed))
    sys.exit(1)
print("all steps passed")
