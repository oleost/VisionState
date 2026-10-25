"""Number reader registry and inference (ONNX Runtime) for reading sensors.

Available readers are declared in ``readers.json``; adding a model is a new entry there.
Entries are never changed or removed once released, so a stored choice keeps working.
Bundled readers are downloaded together with the backbones (``python -m visionstate.backbones``).

A reader is a text recognition model with CTC output (PP-OCR). Decoding is limited to the
characters in ``settings.READING["chars"]``, so a reading can never contain a letter.

Mechanical counters are read by default with a wheel reader (``wheel_readers`` in the same
file): a small model trained for VisionState (``tools/wheelreader``) that gives each wheel's
position, so a wheel half way between two digits is read as such.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .backbones import Footprint, cpu_session
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
    footprint: Footprint = field(default_factory=lambda: Footprint())

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
            footprint=Footprint.of(v),
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
    # What the reader can tell about the read beyond the text (kept with the reading, exported):
    # its id, and for the wheel reader each wheel's position (WheelReader.read_counter).
    details: dict | None = None


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


# --- wheel reader (mechanical counters) -------------------------------------------------------------


WHEEL_CONFIDENCES = ("mean", "weakest")  # see WheelReader.confidence


@dataclass(frozen=True)
class WheelReaderSpec:
    id: str
    name: str
    description: str
    url: str
    sha256: str
    size: int
    cell_width: int
    cell_height: int
    bins: int  # positions around a wheel: bin b is position b / (bins / 10)
    license: str
    source: str
    bundled: bool
    footprint: Footprint = field(default_factory=lambda: Footprint())
    # A model with a second input: every cell again with this share of a cell on each side (the
    # neighbouring wheels, cut from the whole frame: ``wheel_context_cells``), scaled to
    # context_width x cell_height. 0 = the cells alone, cut from the region.
    context: float = 0.0
    context_width: int = 0
    # The confidence of a read: "mean" (geometric mean of the wheels' decoded probabilities) or
    # "weakest" (the weakest wheel's probability near where the value puts it).
    confidence: str = "mean"

    @property
    def filename(self) -> str:
        return f"{self.id}.onnx"

    def __post_init__(self) -> None:
        # A registry mistake fails at start-up (and in the tests), not at a counter's first read.
        if self.confidence not in WHEEL_CONFIDENCES:
            raise ValueError(f"Wheel reader {self.id}: unknown confidence {self.confidence!r}")
        if self.context < 0 or (self.context > 0) != (self.context_width > 0):
            raise ValueError(f"Wheel reader {self.id}: context and context_width go together")


def load_wheel_registry() -> tuple[str, dict[str, WheelReaderSpec]]:
    """The default wheel reader's id and every wheel reader in ``readers.json``."""
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["wheel_readers"]
    specs = {
        key: WheelReaderSpec(
            id=key,
            name=v["name"],
            description=v.get("description", ""),
            url=v["url"],
            sha256=v["sha256"],
            size=int(v.get("size", 0)),
            cell_width=int(v["cell_width"]),
            cell_height=int(v["cell_height"]),
            bins=int(v["bins"]),
            license=v.get("license", ""),
            source=v.get("source", ""),
            bundled=bool(v.get("bundled", False)),
            footprint=Footprint.of(v),
            context=float(v.get("context", 0.0)),
            context_width=int(v.get("context_width", 0)),
            confidence=v.get("confidence", "mean"),
        )
        for key, v in raw["readers"].items()
    }
    return raw["default"], specs


# A region in a frame in pixels: left, top, right, bottom.
Pixels = tuple[int, int, int, int]

DEFAULT_WHEEL_READER, WHEEL_READERS = load_wheel_registry()


def uses_wheels(reading: dict) -> bool:
    """Whether a reading sensor's settings read a mechanical counter with a wheel reader."""
    return reading.get("display") == "counter" and reading.get("counter_reader") == "wheels"


def wheel_reader_of(reading: dict) -> str:
    """The wheel reader a counter reads with: its own (``wheel_reader``), else the default."""
    chosen = reading.get("wheel_reader")
    return chosen if chosen in WHEEL_READERS else DEFAULT_WHEEL_READER


def wheel_cells(image: Image.Image, digits: int, width: int, height: int) -> np.ndarray:
    """The region split into ``digits`` equal cells, each as the wheel model sees it.

    Grey from the darkest colour channel (coloured wheels look like black ones), scaled to
    ``width`` x ``height`` and stretched to full contrast; float32 in 0..1, cells x height x width.
    """
    grey = Image.fromarray(np.asarray(image.convert("RGB")).min(axis=2))
    cell = grey.width / max(1, digits)
    out = []
    for i in range(max(1, digits)):
        part = grey.crop((round(i * cell), 0, max(round(i * cell) + 1, round((i + 1) * cell)), grey.height))
        part = ImageOps.autocontrast(part.resize((width, height), Image.Resampling.BILINEAR), cutoff=1)
        out.append(np.asarray(part, dtype=np.float32) / 255.0)
    return np.stack(out)


def wheel_context_cells(
    image: Image.Image, box: Pixels, digits: int, width: int, height: int, context: float
) -> np.ndarray:
    """The region ``box`` (pixels in ``image``) split into ``digits`` equal cells, each with
    ``context`` of a cell on both sides, as the wheel model sees them.

    The context comes from the picture around the region (beyond the picture's edge, the edge
    is repeated), so a wheel the region cuts is still seen whole. Grey, scale and contrast as
    ``wheel_cells``.
    """
    x0, y0, x1, y1 = box
    count = max(1, digits)
    cell = (x1 - x0) / count
    pad = math.ceil(cell * context) + 2
    grey = np.pad(np.asarray(image.convert("RGB")).min(axis=2), ((0, 0), (pad, pad)), mode="edge")
    wide = Image.fromarray(grey)
    out = []
    for i in range(count):
        a = round(x0 + (i - context) * cell + pad)
        b = max(a + 1, round(x0 + (i + 1 + context) * cell + pad))
        part = wide.crop((a, y0, b, max(y0 + 1, y1)))
        part = ImageOps.autocontrast(part.resize((width, height), Image.Resampling.BILINEAR), cutoff=1)
        out.append(np.asarray(part, dtype=np.float32) / 255.0)
    return np.stack(out)


def wheel_value(logp: np.ndarray) -> tuple[float, float]:
    """The most likely counter value for per-wheel position log probabilities (wheels x 100 bins).

    A mechanical counter's wheels are not independent: the last wheel turns freely and every
    other wheel stands on its digit, turning only while the wheel to its right goes from 9 to 0
    (by as much as that wheel is past 9). A dynamic programme from right to left finds the
    value whose wheel positions are most likely together. Returns the value in steps of the
    last wheel, with its fraction (0089949.8 = last wheel at 9.8), and its log probability.
    """
    value, score, _ = wheel_decode(logp)
    return value, score


def wheel_decode(logp: np.ndarray) -> tuple[float, float, list[int]]:
    """``wheel_value`` plus the bin every wheel stands at in that value (left to right)."""
    wheels, bins = logp.shape
    tenth = bins // 10
    best = logp[-1].copy()  # best score of the wheels to the right, by this wheel's bin
    back = []
    for k in range(wheels - 2, -1, -1):
        # The right neighbour at bin b moves this wheel by f = max(0, b - 9 * tenth) bins.
        rest = int(best[: 9 * tenth + 1].argmax())
        lead = np.concatenate([[best[rest]], best[9 * tenth + 1 :]])  # best score per offset f
        lead_bin = np.concatenate([[rest], np.arange(9 * tenth + 1, bins)])
        best = (logp[k].reshape(10, tenth) + lead[None, :]).reshape(bins)
        back.append(np.tile(lead_bin, 10))
    b = int(best.argmax())
    score = float(best[b])
    at = [b]
    for arg in reversed(back):
        b = int(arg[b])
        at.append(b)
    value = 0.0
    for a in at[:-1]:
        value = value * 10 + a // tenth
    return value * 10 + at[-1] / tenth, score, at


def wheel_details(
    probs: np.ndarray, p: list[float], rule: str, value: float, score: float, at: list[int], shift: int
) -> dict:
    """How a wheel reading was found, in positions (0.0-9.9): per wheel where the value puts it
    as seen in the image (``at``, with the common ``shift``), its probability as the confidence
    counts it (``p``, by the spec's ``confidence_rule``: ``WheelReader.wheel_p``) and the model's
    most likely positions (``top``); the value in steps of the last wheel with its fraction, and
    its log probability."""
    bins = probs.shape[1]
    tenth = bins // 10
    top = READING["export_wheel_top"]
    wheels = []
    for k, b in enumerate(at):
        order = np.argsort(probs[k])[::-1][:top]
        wheels.append(
            {
                "at": round((b + shift) % bins / tenth, 1),
                "p": round(p[k], 4),
                "top": [[round(int(i) / tenth, 1), round(float(probs[k, i]), 4)] for i in order],
            }
        )
    return {
        "value": round(value, 1),
        "shift": round(shift / tenth, 1),
        "log_p": round(score, 3),
        "confidence_rule": rule,
        "per_wheel": wheels,
    }


class WheelReader:
    """Reads a mechanical counter wheel by wheel: one small ONNX model gives each wheel's position.

    ``image`` and ``box`` below: the frame and the region in it in pixels (no ``box``: the image
    is the region). Only a model with context (``WheelReaderSpec.context``) looks beyond the region.
    """

    def __init__(self, spec: WheelReaderSpec, model_path: Path):
        self.spec = spec
        self.session = cpu_session(model_path)
        # The cells, then (with context) the cells with their neighbours.
        self.input_names = [i.name for i in self.session.get_inputs()]
        # The first output is the positions; a later one (such as each cell's embedding) is not used yet.
        self.output_name = self.session.get_outputs()[0].name
        if len(self.input_names) != (2 if spec.context else 1):
            raise ValueError(f"Wheel reader {spec.id}: the model takes {len(self.input_names)} inputs")
        self._lock = threading.Lock()

    def inputs(self, image: Image.Image, digits: int, box: Pixels | None = None) -> list[np.ndarray]:
        """What the model is given: the cells, and with context the cells with their neighbours
        (each cells x height x width, 0..1)."""
        h = self.spec.cell_height
        out = [wheel_cells(image.crop(box) if box else image, digits, self.spec.cell_width, h)]
        if self.spec.context:
            whole = box or (0, 0, *image.size)
            out.append(wheel_context_cells(image, whole, digits, self.spec.context_width, h, self.spec.context))
        return out

    def probabilities(self, image: Image.Image, digits: int, box: Pixels | None = None) -> np.ndarray:
        """The model's probability of every wheel's position (wheels x bins)."""
        feed = {name: x[:, None] for name, x in zip(self.input_names, self.inputs(image, digits, box), strict=True)}
        with self._lock:
            out = self.session.run([self.output_name], feed)
        logits = np.asarray(out[0], dtype=np.float64)
        logits -= logits.max(axis=1, keepdims=True)
        probs = np.exp(logits)
        return probs / probs.sum(axis=1, keepdims=True)

    @staticmethod
    def with_slack(probs: np.ndarray) -> np.ndarray:
        """Log probabilities for decoding: a wheel at rest may sit a little off its digit."""
        slack = READING["wheel_slack_bins"]
        near = sum(np.roll(probs, s, axis=1) for s in range(-slack, slack + 1))
        return np.log(np.maximum(near, 1e-12))

    def positions(self, image: Image.Image, digits: int, box: Pixels | None = None) -> np.ndarray:
        """Log probabilities of every wheel's position (wheels x bins), as decoded."""
        return self.with_slack(self.probabilities(image, digits, box))

    def read_counter(self, image: Image.Image, digits: int, box: Pixels | None = None) -> tuple[Text, Image.Image]:
        """Read ``digits`` wheels; returns the read (all digits, last wheel rounded) and the cells used.

        A region drawn a little above or below the digits shifts every wheel alike, so a few
        common shifts are tried (each costs a little) and the most likely reading wins. The
        confidence is the spec's (``WheelReaderSpec.confidence``). ``details`` keeps how the
        value was found (``wheel_details``), so it can be checked without the model.
        """
        probs = self.probabilities(image, digits, box)
        logp = self.with_slack(probs)
        best: tuple[float, float, list[int], int] | None = None
        for shift in range(-READING["wheel_max_shift_bins"], READING["wheel_max_shift_bins"] + 1):
            value, score, at = wheel_decode(np.roll(logp, -shift, axis=1))
            score -= READING["wheel_shift_penalty"] * abs(shift)
            if best is None or score > best[1]:
                best = (value, score, at, shift)
        assert best is not None
        value, score, at, shift = best
        number = math.floor(value + 0.5) % 10**digits
        p = self.wheel_p(probs, logp, at, shift)
        text = Text(
            f"{number:0{digits}d}",
            self.confidence(score, p),
            {"wheels": wheel_details(probs, p, self.spec.confidence, value, score, at, shift)},
        )
        return text, self.cells_image(image, digits, box)

    def wheel_p(self, probs: np.ndarray, logp: np.ndarray, at: list[int], shift: int) -> list[float]:
        """Every wheel's probability at where the value puts it, as the confidence counts it:
        "mean" the decoder's (a little slack included), "weakest" the mass within
        ``wheel_weakest_bins``."""
        bins = probs.shape[1]
        if self.spec.confidence != "weakest":
            return [float(np.exp(logp[k, (b + shift) % bins])) for k, b in enumerate(at)]
        near = np.arange(-READING["wheel_weakest_bins"], READING["wheel_weakest_bins"] + 1)
        return [float(probs[k, (b + shift + near) % bins].sum()) for k, b in enumerate(at)]

    def confidence(self, score: float, p: list[float]) -> float:
        """How sure the read is, from the decoder's ``score`` and the wheels' ``wheel_p``: "mean"
        is the geometric mean of the wheels' decoded probabilities (the score, shift penalty
        included); "weakest" the smallest (one unclear wheel is not hidden by the clear ones)."""
        if self.spec.confidence != "weakest":
            return math.exp(score / max(1, len(p)))
        return min(p)

    def cells_image(self, image: Image.Image, digits: int, box: Pixels | None = None) -> Image.Image:
        """The cells side by side as the model sees them (for the "what the reader saw" image);
        with context, the cells with their neighbours faded."""
        cells = (self.inputs(image, digits, box)[-1] * 255).astype(np.uint8)
        if self.spec.context:
            side = round(cells.shape[2] * self.spec.context / (1 + 2 * self.spec.context))
            faded = (cells * 0.35 + 255 * 0.65).astype(np.uint8)
            cells[:, :, :side] = faded[:, :, :side]
            cells[:, :, -side:] = faded[:, :, -side:]
        gap = 2
        h, w = cells.shape[1:]
        line = Image.new("L", (len(cells) * (w + gap) - gap, h), 255)
        for i, cell in enumerate(cells):
            line.paste(Image.fromarray(cell), (i * (w + gap), 0))
        return line.resize((line.width * 2, h * 2), Image.Resampling.NEAREST).convert("RGB")


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


def implausible(value: float, last: float | None, settings: dict, limit: float | None = None) -> str | None:
    """Why a new value can not be right compared with the last accepted one, or None.

    A counter may go up by ``limit`` at most (change_limit.limit; None: no limit), any other
    value change by ``max_step`` either way (0: no limit)."""
    if last is None:
        return None
    if settings["mode"] == "counter":
        if value < last:
            return "went down"
        step = limit or 0.0
    else:
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
