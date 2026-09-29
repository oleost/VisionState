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

# --- Triggers: when a sensor checks its camera ---------------------------------
#
# 1. The regular interval (SENSOR_DEFAULTS["interval_s"]) is the safety net.
# 2. A change of any listed Home Assistant entity (motion sensor, door contact, cover…)
#    starts a burst: frequent checks for a while, to catch both the movement and the end state.
# 3. Optional change detection compares a small greyscale copy of the region often and only
#    runs the AI (and starts a burst) when enough pixels changed.

TRIGGER_DEFAULTS = {
    "entities": [],  # Home Assistant entity ids that trigger a check when their state changes
    "burst_interval_s": 2.0,  # seconds between checks during a burst
    "burst_duration_s": 30.0,  # how long a burst lasts after the last trigger
    "change_detection": False,
    "change_interval_s": 2.0,  # how often the region is compared
    "change_threshold": 0.04,  # mean pixel difference (0-1) in the region that counts as a change
}
TRIGGER_LIMITS = {
    "burst_interval_s": (0.5, 60.0),
    "burst_duration_s": (0.0, 600.0),
    "change_interval_s": (0.5, 60.0),
    "change_threshold": (0.005, 0.5),
}
TRIGGER_MAX_ENTITIES = 20
# New entity states that are ignored (the entity going offline is not a real event).
TRIGGER_IGNORED_STATES = {"unavailable", "unknown"}
CHANGE_SIGNATURE_SIZE = 48  # edge length of the greyscale thumbnail used for change detection

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

# Built-in defaults. The global rules are editable in the UI (stored in the database) and
# every sensor can override single fields; an unset field falls back to the global value.
REVIEW_DEFAULTS = {
    "enabled": True,
    "below": 0.85,  # flag frames whose confidence is below this (never lower than the sensor threshold)
    "cooldown_s": 300,  # at most one flagged frame per sensor per cooldown
    "flip_limit": 3,  # this many published changes ...
    "flip_window_s": 600,  # ... within this window counts as flip-flopping
    "spot_rate": 0.0,  # share of confident frames sent for a random spot check
}
REVIEW_LIMITS = {
    "below": (0.0, 1.0),
    "cooldown_s": (0, 86_400),
    "flip_limit": (2, 50),
    "flip_window_s": (60, 86_400),
    "spot_rate": (0.0, 0.5),
}

# --- Uploads ----------------------------------------------------------------

UPLOAD_LIMITS = {
    "max_file_mb": 2048,  # per uploaded file (videos can be large)
    "max_zip_members": 5000,  # images read from one ZIP archive
    "max_zip_member_mb": 50,  # uncompressed size of one image inside a ZIP
}

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
    "ha_reconnect_delay_s": 10.0,  # wait before reconnecting to the Home Assistant event stream
}


def merge_review(global_rules: dict | None, sensor_overrides: dict | None) -> dict:
    """Effective review rules: built-in defaults < global rules < non-empty sensor overrides."""
    merged = {**REVIEW_DEFAULTS, **(global_rules or {})}
    merged.update({k: v for k, v in (sensor_overrides or {}).items() if v is not None and k in REVIEW_DEFAULTS})
    return merged


def merge_triggers(stored: dict | None) -> dict:
    """Trigger settings of a sensor: stored values on top of TRIGGER_DEFAULTS."""
    merged = {**TRIGGER_DEFAULTS, **(stored or {})}
    merged["entities"] = list(merged.get("entities") or [])
    return merged


# --- Privacy -----------------------------------------------------------------

# Query parameters whose values are hidden in logs, error messages and exports (e.g. Reolink ?user=&password=).
SECRET_QUERY_KEYS = ("user", "username", "password", "pass", "pwd", "token", "access_token", "key", "apikey", "auth")
# Remove camera credentials from exported sensor bundles (the importer re-enters them).
EXPORT_STRIP_CREDENTIALS = True

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
