"""Number reader registry and inference (ONNX Runtime) for reading sensors.

Available readers are declared in ``readers.json``; adding a model is a new entry there.
Entries are never changed or removed once released, so a stored choice keeps working.
Bundled readers are downloaded together with the backbones (``python -m visionstate.backbones``).

A reader is a text recognition model with CTC output (PP-OCR). Decoding is limited to the
characters in ``settings.READING["chars"]``, so a reading can never contain a letter.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .backbones import cpu_session
from .settings import READING

REGISTRY_FILE = Path(__file__).with_name("readers.json")
MAX_WIDTH = 1600  # widest input (pixels at height 48); longer numbers are squeezed


@dataclass(frozen=True)
class ReaderSpec:
    id: str
    name: str
    description: str
    url: str
    sha256: str
    size: int
    input_height: int
    classes: int
    chars: dict[str, int]  # character -> output class (0 is the CTC blank)
    license: str
    source: str
    bundled: bool

    @property
    def filename(self) -> str:
        return f"{self.id}.onnx"


def load_registry() -> tuple[str, dict[str, ReaderSpec]]:
    """The default reader's id and every reader in ``readers.json``."""
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    specs = {
        key: ReaderSpec(
            id=key,
            name=v["name"],
            description=v.get("description", ""),
            url=v["url"],
            sha256=v["sha256"],
            size=int(v.get("size", 0)),
            input_height=int(v["input_height"]),
            classes=int(v["classes"]),
            chars={c: int(i) for c, i in v["chars"].items()},
            license=v.get("license", ""),
            source=v.get("source", ""),
            bundled=bool(v.get("bundled", False)),
        )
        for key, v in raw["readers"].items()
    }
    return raw["default"], specs


DEFAULT_READER, READERS = load_registry()


# --- preprocessing ----------------------------------------------------------------------------


def _otsu(values: np.ndarray) -> float | None:
    """Threshold that best splits ``values`` (0-255) into two groups; None when there is only one level."""
    hist, _ = np.histogram(values, bins=256, range=(0, 256))
    p = hist / max(1, hist.sum())  # float: pixel counts squared overflow integers on large images
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = ((mu[-1] * omega - mu) ** 2 / (omega * (1.0 - omega)))[:-1]
    if not np.isfinite(between).any():
        return None
    return float(np.nanargmax(np.where(np.isfinite(between), between, np.nan)))


def is_light_on_dark(image: Image.Image) -> bool:
    """LED-style display: the border (background) is darker than the image on average."""
    grey = np.asarray(image.convert("L"), dtype=np.float32)
    edge = max(1, min(grey.shape) // 10)
    border = np.concatenate(
        [grey[:edge].ravel(), grey[-edge:].ravel(), grey[:, :edge].ravel(), grey[:, -edge:].ravel()]
    )
    return float(np.median(border)) < float(grey.mean())


def segments(image: Image.Image, light_digits: bool) -> Image.Image:
    """Keep only the digits' own segments, as dark text on white.

    ``light_digits``: lit segments on a dark panel (LED); otherwise dark segments on a light one
    (LCD). Two thresholds: the first separates the background, the second separates the real
    segments from unlit ones that still show faintly (otherwise a "3" reads as "8").
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    strength = rgb.max(axis=2) if light_digits else 255.0 - rgb.min(axis=2)
    threshold = _otsu(strength)
    if threshold is None:
        return plain(image)
    strong = strength[strength > threshold]
    if strong.size > 20:
        second = _otsu(strong)
        threshold = threshold if second is None else second
    mask = Image.fromarray((strength > threshold).astype(np.uint8) * 255)
    # A little blur joins the dots of dot-matrix digits into strokes.
    blurred = mask.filter(ImageFilter.GaussianBlur(max(1.0, image.height / 200)))
    return ImageOps.invert(blurred).convert("RGB")


def plain(image: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(image.convert("L"), cutoff=1).convert("RGB")


def darkest(image: Image.Image) -> Image.Image:
    """Grey from each pixel's darkest colour channel: coloured digits turn as dark as black ones.

    The red decimal wheels of a water meter are only mid-grey in a plain grey image, and the
    reader then takes a "9" for a "5". Black digits on white wheels look as before.
    """
    grey = Image.fromarray(np.asarray(image.convert("RGB")).min(axis=2))
    return ImageOps.autocontrast(grey, cutoff=1).convert("RGB")


def counter_line(image: Image.Image, digits: int) -> Image.Image:
    """The wheels of a mechanical counter side by side, without the dividers between them.

    The region is split into ``digits`` equal cells and the middle of each cell is kept: a divider
    (often with a dot or a gear showing) otherwise reads as a "1".
    """
    digits = max(1, digits)
    cell = image.width / digits
    keep = max(1, round(cell * READING["counter_cell_share"]))
    line = Image.new("RGB", (keep * digits, image.height))
    for i in range(digits):
        left = round(cell * i + (cell - keep) / 2)
        line.paste(image.crop((left, 0, left + keep, image.height)), (i * keep, 0))
    return line


# --- reading ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    text: str
    score: float  # mean probability of the read characters (0 when nothing was read)


class Reader:
    """Reads a line of digits with one ONNX text recognition model."""

    def __init__(self, spec: ReaderSpec, model_path: Path):
        self.spec = spec
        self.session = cpu_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        allowed = [c for c in READING["chars"] if c in spec.chars]
        self._chars = ["", *allowed]  # index 0 = CTC blank
        self._classes = np.array([0, *(spec.chars[c] for c in allowed)])
        self._lock = threading.Lock()

    def preprocess(self, image: Image.Image) -> np.ndarray:
        height = self.spec.input_height
        width = max(height // 3, min(MAX_WIDTH, math.ceil(height * image.width / max(1, image.height))))
        rgb = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32)
        bgr = rgb[:, :, ::-1]  # PP-OCR models are trained on BGR images
        return ((bgr / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None].astype(np.float32)

    def read(self, image: Image.Image) -> Text:
        batch = self.preprocess(image)
        with self._lock:
            probs = np.asarray(self.session.run(None, {self.input_name: batch})[0])[0]  # time steps × classes
        return decode(probs[:, self._classes], self._chars)

    def read_counter(self, image: Image.Image, digits: int) -> tuple[Text, Image.Image]:
        """Read a mechanical counter with ``digits`` wheels; returns the read and the image used.

        The wheels are pasted into one line without their dividers (``counter_line``) and turned
        grey by their darkest colour channel, so coloured wheels read like black ones. Several row
        bands are read and the most confident read with ``digits`` digits wins; when none has
        that many, the read of the whole height is returned so the caller can say what was seen.
        """
        line = darkest(counter_line(image, digits))
        best: tuple[Text, Image.Image] | None = None
        whole: tuple[Text, Image.Image] | None = None
        for top in READING["counter_band_tops"]:
            for bottom in READING["counter_band_bottoms"]:
                if bottom - top < READING["counter_band_min_height"]:
                    continue
                band = line.crop((0, round(top * line.height), line.width, max(1, round(bottom * line.height))))
                text = self.read(band)
                if whole is None:
                    whole = (text, band)
                if digit_count(text.text) == digits and (best is None or text.score > best[0].score):
                    best = (text, band)
        assert whole is not None, "READING has at least one counter band"
        return best or whole

    def read_display(self, image: Image.Image, display: str, digits: int = 0) -> tuple[Text, Image.Image]:
        """Read a region for a display type (settings.READING_DISPLAYS); returns the read and the image used.

        "auto" reads the image as it is (best for most displays and signs when the region is
        tight) and only when that is unsure also tries the digits' own segments, keeping the
        more confident read: removing faint unlit segments rescues some displays but can drop
        thin digits on others, so it is a fallback. "led" / "lcd" always remove unlit segments
        (light digits on dark / dark digits on light). "counter" reads ``digits`` rolling wheels.
        """
        if display == "counter":
            return self.read_counter(image, digits)
        if display in ("led", "lcd"):
            cleaned = segments(image, light_digits=display == "led")
            return self.read(cleaned), cleaned
        first = plain(image)
        result = self.read(first)
        if result.text and result.score >= READING["segments_fallback_below"]:
            return result, first
        cleaned = segments(image, light_digits=is_light_on_dark(image))
        second = self.read(cleaned)
        return (second, cleaned) if second.score > result.score else (result, first)


def decode(probs: np.ndarray, chars: list[str]) -> Text:
    """Greedy CTC decoding: best class per time step, repeats merged, blanks (0) dropped."""
    best = probs.argmax(axis=1)
    conf = probs.max(axis=1)
    text, scores, previous = [], [], -1
    for index, p in zip(best, conf, strict=True):
        if index != previous and index != 0:
            text.append(chars[index])
            scores.append(float(p))
        previous = index
    return Text("".join(text), float(np.mean(scores)) if scores else 0.0)


# --- interpretation -------------------------------------------------------------------------------


def parse(text: str, settings: dict) -> float | None:
    """The number a reading stands for, or None when there is none.

    Only digits count: dots and commas read on the display are ignored and the configured
    number of decimals decides where the decimal point is (a stray dot is a common misread).
    ``time_left`` reads "h:mm" (or plain minutes) and returns minutes.
    """
    if settings["mode"] == "time_left":
        if ":" in text:
            hours, _, minutes = text.rpartition(":")
            h, m = re.sub(r"\D", "", hours), re.sub(r"\D", "", minutes)
            if not m or len(m) > 2 or int(m) > 59:
                return None
            return float(int(h or 0) * 60 + int(m))
        digits = re.sub(r"\D", "", text)
        return float(int(digits)) if digits else None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    return int(digits) / 10 ** int(settings["decimals"])


def right_value(text: str, settings: dict) -> float | None:
    """The value someone typed as the right one, or None when it is not a number.

    With a decimal point or comma it is taken as written ("629.558", "629,558"); digits only are
    taken the way the meter shows them, with the sensor's decimals placed like the reader does
    ("0629558" → 629.558). Time left: minutes or "h:mm".
    """
    text = text.strip()
    if settings["mode"] == "time_left":
        return parse(text, settings) if re.fullmatch(r"\d+(:\d{1,2})?", text) else None
    if re.fullmatch(r"\d+", text):
        return parse(text, settings)
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def digit_count(text: str) -> int:
    return len(re.sub(r"\D", "", text))


def wrong_digit_count(text: str, settings: dict) -> bool:
    """A mechanical counter always shows all its wheels: any other number of digits is a misread."""
    return settings["display"] == "counter" and digit_count(text) != int(settings["digits"])


def implausible(value: float, last: float | None, settings: dict) -> str | None:
    """Why a new value can not be right compared with the last accepted one, or None."""
    if last is None:
        return None
    if settings["mode"] == "counter" and value < last:
        return "went down"
    step = float(settings["max_step"])
    if step and abs(value - last) > step:
        return "changed too much"
    return None


def settling(value: float | None, last: float | None, settings: dict) -> bool:
    """Whether a counter read exactly one step of its last digit below its value: the last wheel turning.

    A wheel between two digits is read as the one or the other; once the higher one was published,
    every right reading until the counter gets there is one step lower. Such a reading keeps the
    value without counting as rejected, so it neither floods the review queue nor the statistics.
    """
    if settings["mode"] != "counter" or value is None or last is None:
        return False
    step = 10 ** -int(settings["decimals"])
    return abs((last - value) - step) < step / 1000


def problem_key(reason: str | None) -> str:
    """The "problem" entity's state: "ok", or why the reading was rejected as a key ("went_down")."""
    return "ok" if reason is None else reason.replace(" ", "_")


def accepted_share(reads: deque[tuple[float, bool]], now: float, window_s: float) -> float | None:
    """Share of accepted readings (in %) among ``reads`` ((time, accepted) pairs) of the last window.

    Drops the older pairs from ``reads`` (a deque).
    """
    while reads and reads[0][0] < now - window_s:
        reads.popleft()
    if not reads:
        return None
    return round(100 * sum(1 for _, ok in reads if ok) / len(reads), 1)


def counter_rate(samples: deque[tuple[float, float]], now: float, window_s: float, min_span_s: float) -> float | None:
    """How fast a counter goes up, per hour, over about the last window.

    ``samples`` (a deque of (time, accepted value)) is trimmed to the samples inside the window
    plus the last one before it, which anchors the start: with readings far apart (a sensor that
    only checks when triggered) that gives the average since the previous reading.
    """
    while len(samples) > 1 and samples[1][0] <= now - window_s:
        samples.popleft()
    if len(samples) < 2:
        return None
    (t0, v0), (t1, v1) = samples[0], samples[-1]
    if t1 - t0 < min_span_s:
        return None
    return max(0.0, (v1 - v0) * 3600 / (t1 - t0))


def format_value(value: float | None, settings: dict) -> str | None:
    """Value as published: fixed decimals for numbers, whole minutes for time left."""
    if value is None:
        return None
    if settings["mode"] == "time_left":
        return str(int(value))
    return f"{value:.{int(settings['decimals'])}f}"
