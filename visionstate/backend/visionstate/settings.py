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
    "use_taught": True,  # compare boxes with what the user taught (see TEACH); off = the detector alone
    # The user's own labels, each a kind of one of the classes: [{"key", "name", "parent"}], e.g.
    # "Our car" under "car". Each gets the same two entities as a class. Made by teaching a box.
    "custom": [],
    "teach": {},  # this sensor's own values of the tunable teaching limits (TEACH_TUNABLE); missing = global
    "faces": {},  # recognising people by their face: see FACE_DEFAULTS (missing = default)
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
    "debounce": 1,  # a counter in use changes every reading: 2 held it back until the water stopped
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
# What reads a "counter" display: "wheels" gives each wheel's position (readers.WheelReader, a wheel
# half way between two digits is read as such); "ocr" reads the wheels as text with the number reader.
COUNTER_READERS = ("wheels", "ocr")
# Home Assistant device classes offered for readings ("" = none).
READING_DEVICE_CLASSES = ("", "energy", "water", "gas", "volume", "monetary", "duration", "power", "temperature")
# Counters: what limits how fast the value may go up (see READING_DEFAULTS["change_limit"]).
READING_CHANGE_LIMITS = ("auto", "fixed", "step", "off")
READING_DEFAULTS = {
    "mode": "counter",
    "decimals": 0,  # digits after the decimal point; the reader's own dots and commas are ignored
    "unit": "",
    "device_class": "",
    "display": "auto",
    "digits": 6,  # "counter" display only: wheels inside the region; other digit counts are rejected
    "counter_reader": "wheels",  # "counter" display only: see COUNTER_READERS
    # The wheel reader (an id in readers.json) a counter read by "wheels" uses. A new sensor
    # stores the registry's default, so a later default never changes it; "" = that default.
    "wheel_reader": "",
    # Values and times left: the largest plausible change between two readings (0 = no limit).
    # Counters use change_limit instead (see COUNTER_LIMIT).
    "max_step": 0.0,
    # Counters: how fast the counter may go up. "auto" learns it from the counter's own readings
    # (max_rate until then; 0: COUNTER_LIMIT["standard_steps_per_min"]), "fixed" takes max_rate
    # (per hour, in the counter's unit; 0: no limit), "step" takes max_step for every reading,
    # however long since the last one (0: no limit), "off" sets no limit.
    "change_limit": "auto",
    "max_rate": 0.0,
    # Share of accepted readings that is also sent to the review queue, to find misreads that
    # passed every check. Rejected readings always go there.
    "spot_rate": 0.0,
    # Counters: the rate entity (off by default in Home Assistant) is the change over about this
    # many minutes — long enough not to jump with every step of the counter, short enough to see a leak.
    "rate_window_min": 15.0,
}
READING_LIMITS = {
    "decimals": (0, 4),
    "digits": (1, 12),
    "max_step": (0.0, 1e9),
    "max_rate": (0.0, 1e9),
    "spot_rate": (0.0, 1.0),
    "rate_window_min": (1.0, 1440.0),
}
# The limit on how fast a counter may go up (change_limit.py). The time since the last accepted
# reading allows more change, but only time the counter was not seen: down, or read unsure.
# Readings that were read with confidence but went over the limit hold that time still, so a
# misread that repeats can never wait its way through.
COUNTER_LIMIT = {
    # "auto": the largest rise over this window per day, and the day's total rise, of the last
    # learn_days days; the second largest of them (one misread that slipped through does not
    # count). Learned after min_days days with readings: a whole week, wash day and weekend.
    "window_s": 600,
    "learn_days": 14,
    "min_days": 7,
    "safety": 2.0,  # "auto": the learned rate and day times this
    # "auto" until it learned, and what the UI fills in for "fixed" and "step": this many steps of
    # the counter's last digit a minute (0.04 m³ a minute for a water meter with three decimals).
    "standard_steps_per_min": 40,
    "margin_steps": 2,  # always allowed: this many steps of the last digit (a wheel half way)
    # A gap between two readings longer than gap_checks intervals (at least gap_min_s) is time the
    # counter was not seen (the app was down, the camera away).
    "gap_checks": 3,
    "gap_min_s": 300.0,
    "relearn_s": 3600.0,  # how often the learned limit is worked out again
}
# Diagnostic entities of a reading sensor (off by default in Home Assistant). "problem" is "ok" or
# why the last reading was rejected, as a lower-case key.
READING_PROBLEMS = (
    "ok",
    "nothing_read",
    "unsure",
    "wrong_digit_count",
    "went_down",
    "changed_too_much",
    "camera_changed",
)
# Unit of a counter -> (unit of its rate, Home Assistant device class, factor from "per hour").
# Other units get "<unit>/h" without a device class.
READING_RATE_UNITS = {
    "kWh": ("kW", "power", 1.0),
    "Wh": ("W", "power", 1.0),
    "m³": ("m³/h", "volume_flow_rate", 1.0),
    "L": ("L/min", "volume_flow_rate", 1 / 60),
}
# The units Home Assistant accepts per device class of READING_DEVICE_CLASSES (its sensor
# DEVICE_CLASS_UNITS; classes not listed take any unit). MQTT discovery refuses an entity whose
# unit is not in its class's list, exactly as written: "m3" or "kwh" make the entity disappear.
HA_DEVICE_CLASS_UNITS = {
    "energy": ("Wh", "kWh", "MWh", "GWh", "TWh", "mWh", "J", "kJ", "MJ", "GJ", "cal", "kcal", "Mcal", "Gcal", "thm"),
    "water": ("m³", "L", "gal", "ft³", "CCF", "MCF"),
    "gas": ("m³", "L", "ft³", "CCF", "MCF"),
    "volume": ("m³", "L", "mL", "gal", "fl. oz.", "ft³", "CCF", "MCF"),
    "duration": ("d", "h", "min", "s", "ms", "μs"),
    "power": ("mW", "W", "kW", "MW", "GW", "TW"),
    "temperature": ("°C", "°F", "K"),
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
    # Wheel reader: how many position bins (0.1 of a digit each) a wheel at rest may sit off its
    # digit, how far all wheels may appear shifted together (a region drawn a little above or
    # below the digits) and what each bin of such a shift costs (log probability).
    "wheel_slack_bins": 1,
    "wheel_max_shift_bins": 2,
    "wheel_shift_penalty": 0.3,
    # Wheel readers whose confidence is the weakest wheel ("confidence": "weakest" in the
    # registry): a wheel's share is the model's probability within this many bins of where the
    # value puts it.
    "wheel_weakest_bins": 4,
    "accepted_window_s": 86_400,  # the "accepted" entity: share of the readings in the last 24 h
    "rate_min_span_s": 30.0,  # no rate until two accepted readings are at least this far apart
    # "Export readings" (to share, e.g. on GitHub): the checked readings, then the accepted ones
    # nobody checked whose frame is kept, each only the region plus this share of its size around
    # it — not the whole picture. One ZIP of at most this size (GitHub takes 25 MB per file; about
    # 1,000 readings).
    "export_margin": 0.15,
    "export_max_mb": 24,
    "export_meter_max_chars": 300,  # the optional "What meter is this?" text
    "export_wheel_top": 3,  # wheel reader: the most likely positions kept per wheel
    # The notification in Home Assistant when a camera's picture changed size (+ the sensor's slug)
    "camera_notification_id": f"{APP_SLUG}_camera_changed_",
}

DETECTION = {
    "nms_iou": 0.7,  # boxes of one class overlapping more than this are the same object
    "max_detections": 100,
    "preview_threshold": 0.5,  # used by the wizard preview before a threshold is chosen
    # The detector also sees this share of the region's size on each side, so objects at the
    # edge are seen whole (their bottom centre then decides whether they are inside).
    "context_margin": 0.25,
    # An object that was there and is missing is looked for again soon, and only switches off once
    # it was missing this many checks in a row (and for clear_after_s): one missed check, a box
    # taken for something else, a person behind a post, does not switch it off.
    "clear_misses": 2,
    "recheck_s": 5.0,
}

# Teaching an object sensor. The user marks a box as wrong ("none"), as another of the sensor's
# objects, as one of their own labels ("Our car"), or draws a box around something the detector
# missed. Later boxes of those classes are compared with the taught ones (DINOv2 embeddings of
# the box, cosine similarity) and take the label of the closest taught box when they are clearly
# that one; otherwise the detector's answer stands. Measured on real photos: the same object in
# other light or framing scores 0.87-0.97, other objects of the same kind mostly below 0.7.
TEACH = {
    "match_similarity": 0.88,
    "margin": 0.03,  # the closest label must beat the next one by this much, else the detector decides
    # An object that stays where it was (a parked car) keeps the answer it got in the last check
    # while it is still this close to that taught box: light and the detector's box move the
    # similarity by a few hundredths between checks, which would flip it at match_similarity.
    "keep_similarity": 0.8,
    "keep_iou": 0.6,  # "where it was": its box overlaps the box of the last check this much
    # Between this and match_similarity a box of an own label's class is unsure: it neither
    # switches the label on nor off, and the sensor asks in the review queue ("Is this Our car?";
    # the answer is taught). At most one question per label per review cooldown (review rules).
    "ask_similarity": 0.75,
    "rescue_similarity": 0.9,  # a box the detector was unsure about counts only when this close to a taught one
    "rescue_floor": 0.25,  # weakest boxes looked at again, once a box the detector missed was taught
    "max_checked": 6,  # boxes compared per check (most certain first), to bound the extra work
    "max_rescue": 4,  # unsure boxes compared per check
    "rescue_overlap_iou": 0.5,  # an unsure box overlapping a counted one this much is that same object
    "crop_margin": 0.1,  # share of a box's size added on each side of what is compared
    "min_box": 0.01,  # smallest taught box side, as a share of the frame
    # A drawn box counts as "seen faintly" when one of the detector's boxes this weak overlaps it.
    "seen_floor": 0.05,
    "seen_iou": 0.3,
    "filtered_record_cooldown_s": 600,  # a class filtered away again soon is not stored in the history again
    "frames_kept": 6,  # analysed frames kept per object sensor, so a box can still be taught a while later
    "max_examples": 500,  # taught boxes per sensor
    "max_labels": 10,  # own labels per sensor (two entities each)
    "list_limit": 50,  # filtered-away boxes listed on the Quality tab
}
NONE_LABEL = "none"  # taught label of a box that is not what the detector said

# The limits of TEACH a user can change: globally (Settings → Teaching object sensors, DB setting
# "teach") and per sensor (objects["teach"]); empty = inherit. Effective: merge_teach.
TEACH_TUNABLE = ("match_similarity", "keep_similarity", "ask_similarity", "rescue_similarity", "rescue_floor")
TEACH_TUNABLE_LIMITS = {
    "match_similarity": (0.5, 0.99),
    "keep_similarity": (0.5, 0.99),
    "ask_similarity": (0.5, 0.99),
    "rescue_similarity": (0.5, 0.99),
    "rescue_floor": (0.05, 0.6),
}
# Defaults of a backbone whose similarities spread differently from DINOv2 small's (TEACH was
# measured on that). DINOv2 base, on the CC0 test photos (14 objects; the same object with other
# light, framing, blur, JPEG or night colours vs other objects of its kind): at 0.88 one pair of
# different dogs counted as the same (0.891); at 0.90 none did, and 93 % of the changed copies
# of an object were still recognised, as with DINOv2 small 8-bit at 0.88.
TEACH_BY_BACKBONE: dict[str, dict[str, float]] = {
    "dinov2-base": {"match_similarity": 0.9, "rescue_similarity": 0.92},
}

# Recognising people by their face (object sensors that look for "person"; off by default, see
# docs/FACE_RECOGNITION_PLAN.md). The faces of each person box are looked for in its upper part,
# checked (a face that fails is "no face", never someone unknown), aligned and compared with the
# faces the user taught. Similarity thresholds belong to the face model (faces.json).
FACE_DEFAULTS = {
    "enabled": False,
    # Smallest usable face: the width of the face in the camera's frame, in pixels (not the
    # distance between the eyes, which shrinks as a head turns). On a 480x640 living room camera
    # and the test photos scaled down, SFace still told the person (0.53-0.60) from a stranger
    # (<=0.31) at a face 28-31 px wide (about 13 px between the eyes); at 21 px it fell to 0.45.
    "min_face_px": 30.0,
    "ask_unknown": False,  # put faces nobody was taught for in the review queue ("Who is this?")
}
# What the user can tune, for all sensors (Settings) and per sensor (its Faces card): a sensor's
# unset value follows the global one, which follows the default (min_face_px above; the
# similarities: the face model's, faces.json).
FACE_TUNABLE = ("min_face_px", "match_similarity", "sure_similarity", "ask_similarity", "keep_similarity")
FACE_LIMITS = {
    "min_face_px": (16.0, 240.0),
    "match_similarity": (0.2, 0.95),
    "sure_similarity": (0.2, 0.95),
    "ask_similarity": (0.1, 0.95),
    "keep_similarity": (0.1, 0.95),
}
FACES = {
    "stats_window": 40,  # "a usable face in 9 of the last 40 person boxes looked at"
    "max_faces": 4,  # person boxes searched for a face per check (most certain first): one detector run
    # A person box that keeps a sure name is searched again only every this many checks.
    "recheck_every": 3,
    # The upper part of a person box where the face is looked for: its height, in box widths
    # (a standing person is about 3 widths tall), and a margin on every side, in box widths.
    "region_height": 1.3,
    "region_margin": 0.1,
    "detect_score": 0.6,  # a face the face detector finds at all
    "nms_iou": 0.3,
    # Quality gate. The face detector's score, how far the face is turned sideways (see faces.turn:
    # 0 straight, ~0.35 about 30 degrees, ~0.6 about 45) and the sharpness of the aligned face
    # (variance of the Laplacian at 112 px: sharp faces 32+ down to 13 px between the eyes, the
    # same faces blurred by 2 px at most 19). A profile has its eyes close together for its width:
    # 0.41-0.50 of it looking at the camera, a profile 0.08; below turned_eye_share it is turned.
    "min_score": 0.75,
    "max_turn": 0.55,
    "min_sharpness": 25.0,
    "turned_eye_share": 0.3,
    # A face that matched nobody counts towards Unknown person only when it is clearly seen: this
    # many times the smallest face, turned at most this far, and found with at least this detector
    # score. A smaller or more turned face can be named (a person's own face still looks like them
    # a bit turned, 0.66-0.78 at 0.26-0.35), but not matching anyone may only mean the angle: "not
    # sure who", never a stranger. On a phone camera real faces scored 0.86-0.94 and things taken
    # for a face (a wall, a second face on a profile with the eyes in the wrong place) 0.60-0.79.
    "unknown_size": 1.5,
    "unknown_max_turn": 0.35,
    "unknown_min_score": 0.85,
    "top_k": 3,  # a person's score: the mean similarity to their this many closest taught faces
    "margin": 0.05,  # a match must beat the next person by this much
    # Unknown person: a person box whose usable face matched nobody in this many checks in a row
    # (the same box: it overlaps the last check's by keep_iou in TEACH) and that never had a name.
    "unknown_after": 2,
    "crop_margin": 0.5,  # what is stored of a taught face: the face box plus this share of its size around it
    "max_faces_per_person": 50,
    "max_people": 10,  # people VisionState knows (shared; two entities on each sensor that recognises faces, and one of their own)
    "suggest_faces": 5,  # a person with fewer taught faces gets "teach a few more"
    "look_alike": 0.9,  # two people whose closest faces are this share of the match threshold apart look alike
    # A usable face nobody is named for keeps its frame in the history, to name them there later
    # (someone at a doorbell is gone before the Live tab can be tapped): at most one per this many
    # seconds per sensor, unless the face came this many times closer (wider) since the last one.
    "record_cooldown_s": 60,
    "record_closer": 1.5,
    # "Is this Kari?": a person moves, so each question shows another face to teach. Up to this many
    # questions about one person per review cooldown (and waiting at once), at least this many
    # seconds apart; an answer opens the next one right away. Own labels ask one at a time.
    "ask_burst": 5,
    "ask_gap_s": 3.0,
    # Looking for a face while a person is there: someone walking up to a doorbell shows a usable
    # face for a few seconds only, often between two regular checks. A check that finds a person
    # nobody is named for checks again every burst_interval_s (or the sensor's burst pace if
    # faster), until everyone in view is named, nobody was seen in burst_absent_checks checks in a
    # row, burst_idle_checks checks in a row brought neither a usable face nor a person coming
    # closer (a box or face burst_progress times larger than before), or after burst_max_s. Then not
    # again until the person has gone: someone sitting on the steps gets one burst, not one an hour.
    "burst_interval_s": 1.0,
    "burst_max_s": 30.0,
    "burst_idle_checks": 6,
    "burst_absent_checks": 2,
    "burst_progress": 1.1,
    # Photos and short videos from a phone (People page; the same upload as a state sensor's, so
    # images, ZIP archives and videos): at most this many faces are added from one upload, and a
    # video gives a frame every this many seconds (turning the head gives several angles).
    "max_photos": 10,
    "photo_video_interval_s": 0.5,
}
FACE_UNKNOWN_KEY = "unknown_person"  # the key (and entity) of a face nobody was taught for
# A person's own entity (sensor.visionstate_<key>) says on which sensor they are seen: its slug,
# the one that saw them last when several do, or this when none sees them now.
PERSON_NOT_SEEN = "not_seen"

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
    # Wait between switching on and taking the frame. Frames are thrown away meanwhile (see
    # Checks._grab_in_light); an ESP32 camera at 1600x1200 takes about a second per frame, so 1 s
    # threw away only the stale one and the frame read was often dark.
    "light_delay_s": 2.0,
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
# A probe decodes a JPEG only this many times larger than the signature (over its region), not
# whole: the same signature (to about 0.001), four to six times faster (imaging.decode_for_signature).
CHANGE_SIGNATURE_DECODE = 4

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
REVIEW_QUEUE = {"page_size": 50}  # items the Review page loads at once (the newest, or one sensor's)
REVIEW_LIMITS = {
    "below": (0.0, 1.0),
    "cooldown_s": (0, 86_400),
    "flip_limit": (2, 50),
    "flip_window_s": (60, 86_400),
    "spot_rate": (0.0, 0.5),
}

# A reminder of a forgotten review queue: a notification in Home Assistant (and, if chosen, a push
# to a phone) once the oldest frame has waited `after_days` and at least `min_items` frames are
# waiting — never when a frame arrives. It goes away by itself when the queue is handled. Global
# (Settings → Review reminder), stored in the database.
REMINDER_DEFAULTS = {
    "enabled": True,
    "after_days": 7,
    "min_items": 1,
    "repeat": False,  # remind again every repeat_days while the frames still wait
    "repeat_days": 7,
    "notify_service": "",  # also push with this notify service ("notify.mobile_app_…"); "" = no push
}
REMINDER_LIMITS = {
    "after_days": (1, 90),
    "min_items": (1, 1000),
    "repeat_days": (1, 90),
}
REMINDER = {
    "check_interval_s": 3600,  # how often the queue's age is looked at (and right after it changes)
    "notification_id": f"{APP_SLUG}_review",  # one notification, replaced instead of piling up
    "test_notification_id": f"{APP_SLUG}_review_test",
    "title": "VisionState",
}

# --- Uploads ----------------------------------------------------------------

UPLOAD_LIMITS = {
    "max_file_mb": 2048,  # per uploaded file (videos can be large)
    "max_zip_members": 5000,  # images read from one ZIP archive
    "max_zip_member_mb": 50,  # uncompressed size of one image inside a ZIP
    "max_manifest_mb": 20,  # manifest.json of an imported sensor (5000 samples take about 2 MB)
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
# Training stops when no gradient component exceeds tol (or after max_iter steps).
CLASSIFIER = {"C": 30.0, "max_iter": 3000, "tol": 1e-6}

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
# The history clean-up (engine/history.py): rows removed per transaction, and how old a frame no row
# lists must be before it is removed (a frame is written just before its row).
HISTORY_CLEANUP = {
    "batch": 500,
    "orphan_age_s": 3600.0,
}

# Training images moved from the media folder to the app's own storage (engine/history.py,
# move_samples). The copies left in the media folder are removed this many days after the move: until
# then, restoring the backup Home Assistant made before the update (it holds the app's storage, not
# the media folder) gives the older version back with every image it knew.
SAMPLES_MOVE = {
    "keep_old_days": 30,
}

# --- Browsing the history (the History page and each sensor's History tab) ---------------

HISTORY = {
    "page_size": 50,
    # Rows of one sensor this close in time are the same moment (a face seen and the question about
    # it): what is taught from one of them shows on the others too, where the box is the same.
    "same_moment_s": 3.0,
    "max_page_size": 200,
    "max_filter_values": 50,  # values of one filter (sensors, keys, events) in one request
    "window_presets_h": (1, 24, 24 * 7, 24 * 30),  # time windows offered besides "any time"
    "poll_s": 10.0,  # how often an open history asks whether newer rows matching its filter arrived
}
# What a history row can be, per sensor kind (a filter takes any of them; names are unique across
# kinds). api/history.py says which rows each one matches, the UI how it is named.
HISTORY_EVENTS: dict[str, tuple[str, ...]] = {
    KIND_STATES: ("change", "flagged"),  # a published change; a frame put in the review queue
    # "asked": "Is this Our car?"; "face_seen": a face nobody is named for (FACES["record_cooldown_s"]).
    KIND_OBJECTS: ("appeared", "cleared", "filtered", "asked", "face_seen"),
    KIND_READING: ("value", "rejected", "verified"),  # verified: the user said whether it read right
}

# --- Runtime ------------------------------------------------------------------

# What is sent to Home Assistant (engine/publishing.py). A sensor's picture (its image entity) and
# an object's attributes (boxes, last seen) are sent when what they show changes, and otherwise this
# often, so a dashboard is never far behind: Home Assistant stores every picture it gets, and every
# change of attributes, as a new state.
PUBLISH = {
    "refresh_s": 300.0,
}

RUNTIME = {
    "max_concurrent_inferences": 2,
    "inference_threads": 0,  # threads per model run (backbones.cpu_session); 0: one per physical core
    "frame_cache_size": 5,  # recent frames kept per sensor so a label hits the frame the user saw
    "retrain_delay_s": 1.0,  # coalesce rapid label clicks into one retrain
    "cleanup_interval_s": 600,  # history clean-up (age and size limits)
    "http_timeout_s": 15.0,
    # A camera picture larger than this is refused (a misconfigured URL streaming without end, a
    # decompression bomb): a 12-megapixel JPEG is a few MB, an 8K frame 33 megapixels.
    "max_frame_mb": 50,
    "rtsp_workers": 4,  # RTSP grabs at once (on threads of their own, see sources.FrameGrabber)
    # An RTSP stream grabbed again within this many seconds is kept open (sources.FrameGrabber); 0: never.
    "rtsp_keep_open_s": 5.0,
    "max_image_megapixels": 60,
    "loop_retry_s": 30.0,  # a sensor's loop failed unexpectedly (database busy): try again after this
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
    # While a check's light warms up, a frame is fetched (and thrown away) this often: a camera
    # that hands out a picture it took earlier (ESP32) gives a fresh one, and adjusts its exposure.
    "light_warmup_interval_s": 1.0,
    # A light VisionState switched off this recently may still be reported "on" by Home Assistant;
    # the next check switches it on (and waits) anyway instead of taking it for somebody else's.
    "light_off_settle_s": 10.0,
}


def merge_review(global_rules: dict | None, sensor_overrides: dict | None) -> dict:
    """Effective review rules: built-in defaults < global rules < non-empty sensor overrides."""
    merged = {**REVIEW_DEFAULTS, **(global_rules or {})}
    merged.update({k: v for k, v in (sensor_overrides or {}).items() if v is not None and k in REVIEW_DEFAULTS})
    return merged


def merge_reminder(stored: dict | None) -> dict:
    """The review reminder: stored values on top of REMINDER_DEFAULTS."""
    return {**REMINDER_DEFAULTS, **{k: v for k, v in (stored or {}).items() if k in REMINDER_DEFAULTS}}


def teach_defaults(backbone: str) -> dict:
    """The tunable teaching limits as they are by default for ``backbone``."""
    return {key: TEACH_BY_BACKBONE.get(backbone, {}).get(key, TEACH[key]) for key in TEACH_TUNABLE}


def merge_teach(backbone: str, global_limits: dict | None, sensor_overrides: dict | None) -> dict:
    """Effective teaching limits (all of TEACH): defaults of the backbone < global < sensor overrides."""
    merged = {**TEACH, **teach_defaults(backbone)}
    for overrides in (global_limits, sensor_overrides):
        merged.update({k: v for k, v in (overrides or {}).items() if v is not None and k in TEACH_TUNABLE})
    return merged


def merge_objects(stored: dict | None) -> dict:
    """Object settings of a sensor: stored values on top of OBJECT_DEFAULTS."""
    merged = {**OBJECT_DEFAULTS, **(stored or {})}
    merged["classes"] = list(merged.get("classes") or [])
    merged["custom"] = [dict(label) for label in merged.get("custom") or []]
    merged["teach"] = {k: v for k, v in (merged.get("teach") or {}).items() if k in TEACH_TUNABLE and v is not None}
    merged["faces"] = merge_faces(merged.get("faces"))
    return merged


def merge_faces(stored: dict | None) -> dict:
    """Face settings of an object sensor: whether it is on and asks (stored on top of
    FACE_DEFAULTS), and its own values of FACE_TUNABLE (only the ones it set; see faces.merge_limits)."""
    stored = stored or {}
    merged = {k: stored.get(k, FACE_DEFAULTS[k]) for k in ("enabled", "ask_unknown")}
    merged.update({k: float(stored[k]) for k in FACE_TUNABLE if stored.get(k) is not None})
    return merged


def faces_on(objects: dict) -> bool:
    """Whether an object sensor recognises faces: switched on, and it looks for people."""
    return bool(objects["faces"]["enabled"]) and "person" in objects["classes"]


def active_custom(objects: dict) -> list[dict]:
    """The sensor's own labels that are in use: those whose class is still selected."""
    return [label for label in objects["custom"] if label["parent"] in objects["classes"]]


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

# --- Backup (export and restore everything, backup.py) ------------------------

# What a backup holds, in parts the user can leave out at export (the ticks in Settings → Backup).
# Every table of db.py and every folder of Settings belongs to exactly one part, or is named in
# BACKUP_LEFT_OUT: tests/test_backup.py fails for a new table or folder that is not, so nothing
# added later is silently missing from a backup. The database goes along whole (new columns
# need nothing here); a left-out part's rows are deleted from the copy.
# "restore": "replace" — a restore empties the part's folders here, also when the backup does not
# hold the part (its rows are gone); "add" — the backup's files are added, nothing here is removed.
BACKUP_PARTS: dict[str, dict] = {
    "sensors": {
        "optional": False,
        "tables": ("sensor", "state", "model_info", "reading_watch", "setting"),
        "folders": ("heads_dir",),
        "restore": "replace",
    },
    "samples": {
        "optional": True,
        "tables": ("sample", "sample_label", "embedding"),
        "folders": ("samples_dir", "old_samples_dir"),
        "restore": "replace",
    },
    "faces": {
        "optional": True,
        "tables": ("person", "face", "face_embedding"),
        "folders": ("faces_dir",),
        "restore": "replace",
    },
    "history": {
        "optional": True,
        "tables": ("prediction", "reading_stat"),
        "folders": ("history_dir",),
        "restore": "replace",
    },
    "models": {"optional": True, "tables": (), "folders": ("models_dir",), "restore": "add"},
}
# Folders never in a backup: made again when asked for. A restore empties them (they are keyed by row IDs).
BACKUP_LEFT_OUT = ("sample_thumbs_dir", "thumbs_dir")
BACKUP = {
    "format": 1,  # the archive's layout (backup.json "format"); a restore refuses a newer one
    "credentials": False,  # camera passwords in the backup: off unless the user ticks it
    "max_restore_gb": 50,  # largest backup a restore takes
    "free_margin_mb": 200,  # free disk space kept on top of what a restore needs
    "chunk_kb": 1024,  # streamed download: size of each piece
    "hold_requests_s": 120,  # requests made while a restore swaps the data wait this long for it
    "release_wait_s": 60,  # a restore waits this long for the stopped runtime's work to let go of the database
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
    port: int = 8099
    supervisor_token: str = ""
    ha_url: str = ""
    ha_token: str = ""

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def faces_dir(self) -> Path:
        """Faces taught to object sensors: in the app's own storage, never in the media folder
        (Home Assistant's media browser shows that to every user)."""
        return self.data_dir / "faces"

    @property
    def heads_dir(self) -> Path:
        return self.data_dir / "heads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "visionstate.db"

    @property
    def samples_dir(self) -> Path:
        """Training images (and the boxes taught to object sensors): in the app's own storage, like
        the database that lists them, so a backup holds both."""
        return self.data_dir / "samples"

    @property
    def old_samples_dir(self) -> Path:
        """Where training images were kept before they moved to ``samples_dir`` (read until moved)."""
        return self.media_dir / "samples"

    @property
    def sample_thumbs_dir(self) -> Path:
        """Thumbnails of training images: next to them, left out of backups (made again when asked for)."""
        return self.data_dir / "thumbs" / "sample"

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
            self.faces_dir,
            self.samples_dir,
            self.sample_thumbs_dir,
            self.history_dir,
            self.thumbs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """The app's settings: its options in Home Assistant (``options.json``), else environment
    variables (``VISIONSTATE_<OPTION>``, for running it outside Home Assistant), else defaults."""
    env = os.environ
    data_dir = Path(env.get("VISIONSTATE_DATA", "/data"))
    options: dict = {}
    options_file = data_dir / "options.json"
    if options_file.exists():
        options = json.loads(options_file.read_text(encoding="utf-8"))

    def opt[T: (str, int)](name: str, default: T) -> T:
        value = options.get(name)
        if value in (None, ""):
            value = env.get(f"VISIONSTATE_{name.upper()}", default)
        return type(default)(value)

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
