"""Runtime configuration.

Every tunable default of the backend lives in this module. Other modules import
these values instead of repeating literals, so behaviour can be changed in one place.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

VERSION = os.environ.get("VISIONSTATE_VERSION", "dev")
APP_SLUG = "visionstate"

# --- Sensor behaviour -------------------------------------------------------

SENSOR_DEFAULTS = {
    "interval_s": 10.0,  # seconds between classifications
    "threshold": 0.70,  # below this confidence the sensor reports UNKNOWN_STATE
    "debounce": 2,  # consecutive matching results required before the state changes
}
SENSOR_LIMITS = {
    "interval_s": (1.0, 3600.0),
    "threshold": (0.0, 1.0),
    "debounce": (1, 20),
}
UNKNOWN_STATE = "unknown"

# Colours handed out to new states, in order. The UI reads colours from the API.
STATE_PALETTE = [
    "#ffa24c",
    "#5aa9ff",
    "#c39bff",
    "#7ee2b8",
    "#ff7b72",
    "#f5d76e",
    "#8fb8e0",
    "#e58fd0",
    "#a0d468",
]
MAX_STATES = 9  # keys 1-9 in the UI

# --- Review queue (active learning) ----------------------------------------

REVIEW = {
    "margin": 0.15,  # flag frames whose confidence is below threshold + margin
    "cooldown_s": 300,  # at most one flagged frame per sensor per cooldown
    "flip_limit": 3,  # this many published changes ...
    "flip_window_s": 600,  # ... within this window counts as flip-flopping
    "spot_rate": 0.01,  # share of confident frames sent for a random spot check
}

# --- Uploads ----------------------------------------------------------------

VIDEO = {
    "frame_interval_s": 5.0,
    "dedupe_distance": 4,  # max hamming distance between perceptual hashes to count as duplicate
    "max_frames": 500,
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
JPEG_QUALITY = 90
THUMB_SIZE = 320

# --- Classifier -------------------------------------------------------------

# Logistic regression on L2-normalised embeddings. Higher C = more confident probabilities;
# 30 keeps clear frames well above the default threshold while ambiguous frames stay near 50%.
CLASSIFIER = {"C": 30.0, "max_iter": 3000}

# --- Quality hints ----------------------------------------------------------

QUALITY = {
    "min_samples_per_state": 20,
    "min_night_samples": 5,
    "cv_folds": 5,
    "imbalance_ratio": 0.5,  # smallest state below this share of the largest -> warning
}

# --- Runtime ------------------------------------------------------------------

RUNTIME = {
    "max_concurrent_inferences": 2,
    "frame_cache_size": 5,  # recent frames kept per sensor so a label hits the frame the user saw
    "retrain_delay_s": 1.0,  # coalesce rapid label clicks into one retrain
    "cleanup_interval_s": 3600,
    "http_timeout_s": 15.0,
    "night_colorfulness": 4.0,  # mean channel difference below this = greyscale/IR image
}

SUPERVISOR_URL = "http://supervisor"
INGRESS_PROXY_IP = "172.30.32.2"


@dataclass
class Settings:
    """Settings resolved from the app options file and environment variables."""

    data_dir: Path
    media_dir: Path
    frontend_dir: Path
    bundled_models_dir: Path
    log_level: str = "info"
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    discovery_prefix: str = "homeassistant"
    history_retention_days: int = 7
    port: int = 8099
    supervisor_token: str = ""
    ha_url: str = ""
    ha_token: str = ""

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def heads_dir(self) -> Path:
        return self.data_dir / "heads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "visionstate.db"

    @property
    def samples_dir(self) -> Path:
        return self.media_dir / "samples"

    @property
    def history_dir(self) -> Path:
        return self.media_dir / "history"

    @property
    def thumbs_dir(self) -> Path:
        return self.media_dir / "thumbs"

    @property
    def is_supervised(self) -> bool:
        return bool(self.supervisor_token)

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.models_dir,
            self.heads_dir,
            self.samples_dir,
            self.history_dir,
            self.thumbs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    env = os.environ
    data_dir = Path(env.get("VISIONSTATE_DATA", "/data"))
    options: dict = {}
    options_file = data_dir / "options.json"
    if options_file.exists():
        options = json.loads(options_file.read_text(encoding="utf-8"))

    def opt(name: str, default):
        value = options.get(name)
        if value in (None, ""):
            value = env.get(f"VISIONSTATE_{name.upper()}", default)
        return type(default)(value) if default is not None and value is not None else value

    return Settings(
        data_dir=data_dir,
        media_dir=Path(env.get("VISIONSTATE_MEDIA", f"/media/{APP_SLUG}")),
        frontend_dir=Path(env.get("VISIONSTATE_FRONTEND", "/app/frontend/dist")),
        bundled_models_dir=Path(env.get("VISIONSTATE_BUNDLED_MODELS", "/opt/visionstate/models")),
        log_level=opt("log_level", "info"),
        mqtt_host=opt("mqtt_host", ""),
        mqtt_port=opt("mqtt_port", 1883),
        mqtt_username=opt("mqtt_username", ""),
        mqtt_password=opt("mqtt_password", ""),
        discovery_prefix=opt("discovery_prefix", "homeassistant"),
        history_retention_days=opt("history_retention_days", 7),
        port=int(env.get("VISIONSTATE_PORT", 8099)),
        supervisor_token=env.get("SUPERVISOR_TOKEN", ""),
        ha_url=env.get("HA_URL", ""),
        ha_token=env.get("HA_TOKEN", ""),
    )
