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

# --- Sensor kinds ---------------------------------------------------------------------
#
# "single_state": learns the user's own states from labelled examples (one state at a time).
# "objects": finds common objects (people, cars, animals …) with a pretrained detector, no training.
# The kind is chosen when the sensor is created and never changes.

KIND_STATES = "single_state"
KIND_OBJECTS = "objects"
KIND_READING = "reading"  # a number read from a display or counter (OCR), no training
SENSOR_KINDS = (KIND_STATES, KIND_OBJECTS, KIND_READING)

# Object sensors reuse threshold ("minimum confidence") and debounce ("detections in a row
# before on"), with their own defaults. Limits are the same as in SENSOR_LIMITS.
OBJECT_SENSOR_DEFAULTS = {
    "interval_s": 10.0,
    "threshold": 0.60,  # weak false detections cluster around 0.5-0.55
    "debounce": 1,
}
OBJECT_DEFAULTS = {
    "classes": ["person"],
    "min_size": 0.0,  # smallest box, as a share of the region's area (0 = any size)
    "clear_after_s": 30.0,  # an object stays "detected" this long after it was last seen
}
OBJECT_LIMITS = {
    "min_size": (0.0, 0.5),
    "clear_after_s": (0.0, 3600.0),
}
OBJECT_MAX_CLASSES = 20  # two Home Assistant entities per class

# Reading sensors reuse threshold (minimum OCR confidence) and debounce (equal readings in a row
# before a new value is published).
READING_SENSOR_DEFAULTS = {
    "interval_s": 30.0,
    "threshold": 0.70,  # clean LCDs read at ~0.99, LED displays at ~0.8
    "debounce": 2,
}
# counter: only goes up (energy/water/gas meters); value: any number (prices);
# time_left: "h:mm" or minutes on an appliance display, reported in minutes.
READING_MODES = ("counter", "value", "time_left")
# How the reader treats the region: "auto" reads it as it is and falls back to removing faint
# unlit segments when unsure; "led" (light digits on dark) and "lcd" (dark digits on light)
# always remove unlit segments, for displays where they show clearly; "counter" is a mechanical
# counter with rolling digit wheels: the region is split into ``digits`` equal cells and only the
# middle of each cell is read, so the dividers between the wheels are never read as digits.
READING_DISPLAYS = ("auto", "led", "lcd", "counter")
# Home Assistant device classes offered for readings ("" = none).
READING_DEVICE_CLASSES = ("", "energy", "water", "gas", "volume", "monetary", "duration", "power", "temperature")
READING_DEFAULTS = {
    "mode": "counter",
    "decimals": 0,  # digits after the decimal point; the reader's own dots and commas are ignored
    "unit": "",
    "device_class": "",
    "display": "auto",
    "digits": 6,  # "counter" display only: wheels inside the region; other digit counts are rejected
    "max_step": 0.0,
    # Share of accepted readings that is also sent to the review queue, to find misreads that
    # passed every check. Rejected readings always go there.
    "spot_rate": 0.0,  # largest plausible change between two readings (0 = no limit)
}
READING_LIMITS = {
    "decimals": (0, 4),
    "digits": (1, 12),
    "max_step": (0.0, 1e9),
    "spot_rate": (0.0, 1.0),
}
READING = {
    "chars": "0123456789.,:-",  # the reader may only output these characters
    # Quality tab: the periods it sums up (days, today included) and how many readings it lists.
    "quality_periods_days": (1, 7, 30),
    "quality_list_limit": 100,
    "segments_fallback_below": 0.6,  # "auto" display: below this confidence also try without unlit segments
    # "counter" display. Share of each cell's width that is read (the rest holds the dividers).
    "counter_cell_share": 0.7,
    # Half digits above and below the window read as extra or wrong digits, and wheels do not all
    # sit at the same height, so several row bands of the region (top, bottom as shares of its
    # height) are read and the most confident one with the right number of digits is kept.
    "counter_band_tops": (0.0, 0.08, 0.16, 0.24),
    "counter_band_bottoms": (1.0, 0.92, 0.84, 0.76, 0.68, 0.6),
    "counter_band_min_height": 0.5,
}

DETECTION = {
    "nms_iou": 0.7,  # boxes of one class overlapping more than this are the same object
    "max_detections": 100,
    "preview_threshold": 0.5,  # used by the wizard preview before a threshold is chosen
    # The detector also sees this share of the region's size on each side, so objects at the
    # edge are seen whole (their bottom centre then decides whether they are inside).
    "context_margin": 0.25,
}

# --- Triggers: when a sensor checks its camera ---------------------------------
#
# 1. The regular interval (SENSOR_DEFAULTS["interval_s"]) is the safety net.
# 2. A change of any listed Home Assistant entity (motion sensor, door contact, cover…)
#    starts a burst: frequent checks for a while, to catch both the movement and the end state.
# 3. Optional change detection compares a small greyscale copy of the region often and only
#    runs the AI (and starts a burst) when enough pixels changed.

TRIGGER_DEFAULTS = {
    # The regular interval check. Off = the sensor only checks when triggered (an entity, a change
    # in the image, the "check now" button) and once after start-up.
    "regular": True,
    "entities": [],  # Home Assistant entity ids that trigger a check when their state changes
    # entity id -> the only new state that triggers (e.g. "Flow finished"); other entities
    # trigger on any state change. Compared without regard to case.
    "only_states": {},
    "burst_interval_s": 2.0,  # seconds between checks during a burst
    "burst_duration_s": 30.0,  # how long a burst lasts after the last trigger
    "change_detection": False,
    "change_interval_s": 2.0,  # how often the region is compared
    "change_threshold": 0.04,  # mean pixel difference (0-1) in the region that counts as a change
    # A light or switch that is turned on before a check takes its frame and off afterwards (a
    # lamp or flash next to the camera). Left alone when it is already on.
    "light_entity": "",
    "light_delay_s": 1.0,  # wait between switching on and taking the frame
}
TRIGGER_LIMITS = {
    "burst_interval_s": (0.5, 60.0),
    "burst_duration_s": (0.0, 600.0),
    "change_interval_s": (0.5, 60.0),
    "change_threshold": (0.005, 0.5),
    "light_delay_s": (0.0, 30.0),
}
TRIGGER_MAX_ENTITIES = 20
TRIGGER_STATE_MAX_LENGTH = 255  # Home Assistant's own limit for a state
# Entity domains offered for the light (anything homeassistant.turn_on / turn_off works on).
# A new sensor sends its values to Home Assistant. Off: it runs and records everything in
# VisionState, but its entities stay unavailable in Home Assistant (no values, no statistics) —
# for tuning a sensor before Home Assistant uses it.
SENSOR_PUBLISH_DEFAULT = True

LIGHT_DOMAINS = ("light", "switch", "input_boolean")
# New entity states that are ignored (the entity going offline is not a real event).
TRIGGER_IGNORED_STATES = {"unavailable", "unknown"}
CHANGE_SIGNATURE_SIZE = 48  # edge length of the greyscale thumbnail used for change detection

# --- Region of interest ------------------------------------------------------------

ROI_MAX_POINTS = 32  # corners of a polygon region
# Colour for everything outside a polygon region and for letterbox padding: the ImageNet
# mean, i.e. "nothing" for the AI model.
NEUTRAL_FILL = (124, 116, 104)

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
    "max_suspects": 50,  # most "possibly mislabelled" samples listed on the Quality tab
}

# --- Storage of history frames -------------------------------------------------------
#
# History frames (state changes, flagged frames, object and reading events) are removed when they
# are older than history_days OR when all history frames together exceed history_max_gb —
# whichever comes first; the oldest go first, frames still waiting for review last. Training
# images are never removed automatically. Both are set in the app (Settings → Storage).

STORAGE_DEFAULTS = {
    "history_days": 7,
    "history_max_gb": 2.0,  # 0 = no size limit
}
STORAGE_LIMITS = {
    "history_days": (1, 365),
    "history_max_gb": (0.0, 1000.0),
}

# --- Runtime ------------------------------------------------------------------

RUNTIME = {
    "max_concurrent_inferences": 2,
    "frame_cache_size": 5,  # recent frames kept per sensor so a label hits the frame the user saw
    "retrain_delay_s": 1.0,  # coalesce rapid label clicks into one retrain
    "cleanup_interval_s": 600,  # history clean-up (age and size limits)
    "http_timeout_s": 15.0,
    "night_colorfulness": 4.0,  # mean channel difference below this = greyscale/IR image
    "ha_reconnect_delay_s": 10.0,  # wait before reconnecting to the Home Assistant event stream
    # The entity IDs Home Assistant gave our entities (its entity registry): read again this often,
    # and this long after discovery was published (so Home Assistant has created the entities).
    "entity_registry_refresh_s": 300.0,
    "entity_registry_delay_s": 5.0,
    # A view with live frames (region editor, labelling, the wizard) holds a sensor's light on with
    # a lease it renews this often; a lease not renewed in time is let go (closed tab, phone put away).
    "light_view_renew_s": 10.0,
    "light_view_lease_s": 30.0,
    "light_sweep_s": 5.0,  # how often expired leases are looked for
}


def merge_review(global_rules: dict | None, sensor_overrides: dict | None) -> dict:
    """Effective review rules: built-in defaults < global rules < non-empty sensor overrides."""
    merged = {**REVIEW_DEFAULTS, **(global_rules or {})}
    merged.update({k: v for k, v in (sensor_overrides or {}).items() if v is not None and k in REVIEW_DEFAULTS})
    return merged


def merge_objects(stored: dict | None) -> dict:
    """Object settings of a sensor: stored values on top of OBJECT_DEFAULTS."""
    merged = {**OBJECT_DEFAULTS, **(stored or {})}
    merged["classes"] = list(merged.get("classes") or [])
    return merged


def merge_reading(stored: dict | None) -> dict:
    """Reading settings of a sensor: stored values on top of READING_DEFAULTS."""
    return {**READING_DEFAULTS, **(stored or {})}


def merge_triggers(stored: dict | None) -> dict:
    """Trigger settings of a sensor: stored values on top of TRIGGER_DEFAULTS."""
    merged = {**TRIGGER_DEFAULTS, **(stored or {})}
    merged["entities"] = list(merged.get("entities") or [])
    merged["only_states"] = dict(merged.get("only_states") or {})
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
        port=int(env.get("VISIONSTATE_PORT", 8099)),
        supervisor_token=env.get("SUPERVISOR_TOKEN", ""),
        ha_url=env.get("HA_URL", ""),
        ha_token=env.get("HA_TOKEN", ""),
    )
