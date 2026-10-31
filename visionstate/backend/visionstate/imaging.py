"""Image helpers: decoding, region cropping, night detection and perceptual hashing."""

from __future__ import annotations

import hashlib
import io
import math
import weakref
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .settings import (
    CHANGE_SIGNATURE_DECODE,
    CHANGE_SIGNATURE_SIZE,
    JPEG_QUALITY,
    NEUTRAL_FILL,
    ROI_MAX_POINTS,
    RUNTIME,
    THUMB_SIZE,
)

FULL_FRAME_KEY = "full"


class ImageTooLarge(OSError):
    """An image with more pixels than RUNTIME["max_image_megapixels"]; handled like an unreadable one."""


# The camera's own JPEG of a frame decode() made, by id() of that image while it lives (see
# original_jpeg); an entry goes with its image (weakref.finalize), so an id is never reused here.
_ORIGINALS: dict[int, bytes] = {}

EXIF_ORIENTATION = 0x0112


def decode(data: bytes, at_least: tuple[int, int] | None = None) -> Image.Image:
    """An image (RGB). ``at_least``: a JPEG may be decoded smaller, down to this size (its decoder
    scales by 1/2, 1/4 or 1/8 while decoding, several times faster than decoding it whole).

    Without copies: a frame already upright and RGB (most camera pictures) is the decoded image
    itself — exif_transpose and convert would each copy it whole. A JPEG decoded whole and
    upright is remembered as it came (original_jpeg), so the history keeps the camera's own file.
    """
    image = Image.open(io.BytesIO(data))
    # Checked from the header, before the pixels are decoded (they could take gigabytes).
    if image.width * image.height > RUNTIME["max_image_megapixels"] * 1_000_000:
        raise ImageTooLarge(f"Image too large ({image.width}×{image.height})")
    if at_least is not None:
        image.draft("RGB", at_least)  # only JPEG does anything with it
    upright = image.getexif().get(EXIF_ORIENTATION, 1) == 1
    jpeg = image.format == "JPEG"
    if not upright:
        ImageOps.exif_transpose(image, in_place=True)
    image.load()
    if image.mode != "RGB":
        image = image.convert("RGB")
    if jpeg and upright and at_least is None:
        _ORIGINALS[id(image)] = data
        weakref.finalize(image, _ORIGINALS.pop, id(image), None)
    return image


def original_jpeg(image: Image.Image) -> bytes | None:
    """The camera's JPEG ``image`` was decoded from, when it is exactly that picture (not a crop
    or another copy of it: those are other images)."""
    return _ORIGINALS.get(id(image))


def decode_for_signature(data: bytes, roi: dict | None) -> Image.Image:
    """A frame decoded just large enough for region_signature (change detection): the region keeps
    CHANGE_SIGNATURE_DECODE times the signature's size on each side."""
    roi = normalise_roi(roi)
    side = CHANGE_SIGNATURE_SIZE * CHANGE_SIGNATURE_DECODE
    w, h = (roi["w"], roi["h"]) if roi else (1.0, 1.0)
    return decode(data, at_least=(math.ceil(side / w), math.ceil(side / h)))


def load(path: Path, at_least: tuple[int, int] | None = None) -> Image.Image:
    """A stored image (RGB), with its file closed right away (it may be deleted meanwhile);
    ``at_least`` as for decode."""
    with Image.open(path) as image:
        if at_least is not None:
            image.draft("RGB", at_least)
        return image.convert("RGB")


def encode_jpeg(image: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _normalise_points(points: list | None) -> list[list[float]] | None:
    """Clamped polygon corners, or None when there is no real polygon (missing, <3 points)."""
    if not points or len(points) < 3:
        return None
    return [[round(_clamp(p[0]), 4), round(_clamp(p[1]), 4)] for p in points[:ROI_MAX_POINTS]]


def _is_axis_rectangle(points: list[list[float]], x: float, y: float, w: float, h: float) -> bool:
    """Four corners that are the plain rectangle, clockwise from its top-left. A rectangle whose
    corners start elsewhere is kept as points: a straightened region's top is its first edge."""
    corners = [[x, y], [round(x + w, 4), y], [round(x + w, 4), round(y + h, 4)], [x, round(y + h, 4)]]
    return points == corners


def normalise_roi(roi: dict | None) -> dict | None:
    """Clamp a region to the unit square. Returns None for a missing or full-frame region.

    A region is a rectangle ``{x, y, w, h}``, optionally with polygon ``points`` ([[x, y], ...]).
    For a polygon, ``x/y/w/h`` is recomputed as its bounding box; a polygon that is just the
    rectangle is stored as a plain rectangle.
    """
    if not roi:
        return None
    points = _normalise_points(roi.get("points"))
    if points:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        x, y = min(xs), min(ys)
        w, h = max(xs) - x, max(ys) - y
        if w < 0.01 or h < 0.01:
            return None
        box = {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
        if _is_axis_rectangle(points, box["x"], box["y"], box["w"], box["h"]):
            return normalise_roi(box)
        return {**box, "points": points}
    x = min(max(float(roi.get("x", 0)), 0.0), 1.0)
    y = min(max(float(roi.get("y", 0)), 0.0), 1.0)
    w = min(max(float(roi.get("w", 1)), 0.0), 1.0 - x)
    h = min(max(float(roi.get("h", 1)), 0.0), 1.0 - y)
    if w < 0.01 or h < 0.01:
        return None
    if x == 0 and y == 0 and w >= 0.999 and h >= 0.999:
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def roi_key(roi: dict | None) -> str:
    """Stable cache key for a region, used to invalidate embeddings when the region changes."""
    roi = normalise_roi(roi)
    if roi is None:
        return FULL_FRAME_KEY
    key = f"{roi['x']:.4f},{roi['y']:.4f},{roi['w']:.4f},{roi['h']:.4f}"
    if roi.get("points"):
        digest = hashlib.sha1(repr(roi["points"]).encode(), usedforsecurity=False).hexdigest()[:12]
        key += f",p{digest}"
    return key


def crop(image: Image.Image, roi: dict | None) -> Image.Image:
    """The region of ``image`` (all of it without one); a polygon's outside is painted neutral."""
    roi = normalise_roi(roi)
    if roi is None:
        return image
    width, height = image.size
    box = (
        int(roi["x"] * width),
        int(roi["y"] * height),
        int((roi["x"] + roi["w"]) * width),
        int((roi["y"] + roi["h"]) * height),
    )
    cropped = image.crop(box)
    if not roi.get("points"):
        return cropped
    # Polygon: keep the inside, paint the rest of the bounding box neutral.
    polygon = [(p[0] * width - box[0], p[1] * height - box[1]) for p in roi["points"]]
    mask = Image.new("L", cropped.size, 0)
    ImageDraw.Draw(mask).polygon(polygon, fill=255)
    return Image.composite(cropped, Image.new("RGB", cropped.size, NEUTRAL_FILL), mask)


def letterbox(image: Image.Image, size: int, fill: tuple[int, int, int] = NEUTRAL_FILL) -> Image.Image:
    """Resize keeping aspect ratio and pad to a square (fill = ImageNet mean colour)."""
    image = image.copy()
    image.thumbnail((size, size), Image.Resampling.BICUBIC)
    canvas = Image.new("RGB", (size, size), fill)
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas


def is_night(image: Image.Image) -> bool:
    """IR night images are greyscale: the colour channels are nearly identical."""
    small = np.asarray(image.resize((64, 64)), dtype=np.float32)
    r, g, b = small[..., 0], small[..., 1], small[..., 2]
    colourfulness = float((np.abs(r - g) + np.abs(g - b)).mean() / 2)
    return colourfulness < RUNTIME["night_colorfulness"]


def dhash(image: Image.Image, size: int = 8) -> int:
    grey = np.asarray(image.convert("L").resize((size + 1, size), Image.Resampling.BILINEAR), dtype=np.int16)
    bits = (grey[:, 1:] > grey[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def thumbnail(image: Image.Image, size: int = THUMB_SIZE) -> Image.Image:
    image = image.copy()
    image.thumbnail((size, size), Image.Resampling.BICUBIC)
    return image


def region_signature(image: Image.Image, roi: dict | None, size: int = CHANGE_SIGNATURE_SIZE) -> np.ndarray:
    """Small greyscale copy of the region, used to detect that something changed."""
    small = crop(image, roi).convert("L").resize((size, size), Image.Resampling.BILINEAR)
    return np.asarray(small, dtype=np.float32) / 255.0


def change_score(previous: np.ndarray | None, current: np.ndarray) -> float:
    """Mean absolute pixel difference between two signatures (0 = identical, 1 = inverted)."""
    if previous is None or previous.shape != current.shape:
        return 0.0
    return float(np.abs(current - previous).mean())


# --- Object detection helpers ---------------------------------------------------------------


Box = tuple[float, float, float, float]  # normalised x1, y1, x2, y2


def region_box(roi: dict | None, margin: float = 0.0) -> Box:
    """Bounding box of a region, grown by ``margin`` × its size on each side and clamped to the frame.

    The whole frame when there is no region.
    """
    roi = normalise_roi(roi)
    if roi is None:
        return 0.0, 0.0, 1.0, 1.0
    dx, dy = roi["w"] * margin, roi["h"] * margin
    return (
        max(0.0, roi["x"] - dx),
        max(0.0, roi["y"] - dy),
        min(1.0, roi["x"] + roi["w"] + dx),
        min(1.0, roi["y"] + roi["h"] + dy),
    )


def pixel_box(image: Image.Image, box: Box) -> tuple[int, int, int, int]:
    """A normalised box in the pixels of ``image``."""
    x1, y1, x2, y2 = box
    width, height = image.size
    return int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)


def snapshot(image: Image.Image, boxes: list[list[float]], region: Box, margin: float, min_px: int) -> Image.Image:
    """What covers all ``boxes``: their union plus ``margin`` × its size on each side, at least
    ``min_px`` pixels each way when there is room, and never outside ``region`` (the picture of an
    object shows nothing the sensor's region leaves out)."""
    width, height = image.size

    def span(low: float, high: float, floor: float, ceiling: float) -> tuple[int, int]:
        pad = (high - low) * margin
        low, high = low - pad, high + pad
        grow = max(0.0, min_px - (high - low)) / 2
        low, high = low - grow, high + grow
        # Room missing on one side goes to the other, so a small object at the edge keeps its size.
        if low < floor:
            low, high = floor, high + floor - low
        if high > ceiling:
            low, high = low - (high - ceiling), ceiling
        start = round(max(low, floor))
        return start, max(start + 1, round(min(high, ceiling)))

    x1, x2 = span(
        min(b[0] for b in boxes) * width, max(b[2] for b in boxes) * width, region[0] * width, region[2] * width
    )
    y1, y2 = span(
        min(b[1] for b in boxes) * height, max(b[3] for b in boxes) * height, region[1] * height, region[3] * height
    )
    return image.crop((x1, y1, x2, y2))


def crop_box(image: Image.Image, box: Box) -> Image.Image:
    """Crop a normalised box without masking (a detector needs to see whole objects)."""
    return image.crop(pixel_box(image, box))


def region_corners(roi: dict | None) -> list[list[float]] | None:
    """A region's four corners, its top edge first (from the first corner to the second): a
    rectangle's clockwise from the top-left, or a four-cornered polygon's as drawn. None without a
    region or for a polygon of more corners."""
    roi = normalise_roi(roi)
    if roi is None:
        return None
    points = roi.get("points")
    if points:
        return [list(p) for p in points] if len(points) == 4 else None
    x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def straighten(image: Image.Image, corners: list[list[float]], margin: float = 0.0) -> Image.Image:
    """The four-cornered area of ``image`` (normalised corners, its top edge first) as an upright
    rectangle: rotation and perspective undone in one step, as if the area were a rectangle seen
    straight on. Its width is the mean of the top and bottom edges, its height of the sides.
    ``margin`` × its size is added on each side, straightened the same way (outside the frame: black)."""
    return straightened_box(image, corners, margin)[0]


@dataclass(frozen=True)
class Plane:
    """The straightened plane of a four-cornered area of an image (its top edge first): the area is
    the rectangle (0, 0)-(width, height) of the plane, in pixels; ``coeffs`` take a point of the
    plane to the image (PIL's perspective transform). Pointer dials sit in the same plane as a
    counter's wheels, so they are cut from it (``crop``)."""

    coeffs: tuple[float, ...]
    width: int
    height: int

    def crop(self, image: Image.Image, box: tuple[float, float, float, float], size: tuple[int, int]) -> Image.Image:
        """The part ``box`` (x1, y1, x2, y2 in plane pixels, may reach beyond the area) of the
        plane, at ``size`` pixels; outside the image: black."""
        a, b, c, d, e, f, g, h = self.coeffs
        x0, y0, x1, y1 = box
        sx, sy = (x1 - x0) / size[0], (y1 - y0) / size[1]
        # Output pixel (i, j) is plane point (x0 + sx·i, y0 + sy·j): fold that into the transform,
        # scaled so its constant stays 1.
        k = g * x0 + h * y0 + 1
        coeffs = (
            a * sx / k,
            b * sy / k,
            (a * x0 + b * y0 + c) / k,
            d * sx / k,
            e * sy / k,
            (d * x0 + e * y0 + f) / k,
            g * sx / k,
            h * sy / k,
        )
        return image.transform(size, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)

    def from_image(self, x: float, y: float) -> tuple[float, float]:
        """A point of the image (pixels) in the plane (pixels)."""
        a, b, c, d, e, f, g, h = self.coeffs
        forward = np.linalg.inv(np.array([[a, b, c], [d, e, f], [g, h, 1.0]]))
        u, v, w = forward @ np.array([x, y, 1.0])
        return float(u / w), float(v / w)


def plane(image: Image.Image, corners: list[list[float]]) -> Plane | None:
    """The straightened plane of the four-cornered area ``corners`` (normalised, its top edge
    first); None for corners that do not make an area (three on one line). Its width is the mean of
    the top and bottom edges, its height of the sides, as for a rectangle seen at an angle."""
    width, height = image.size
    p = [(c[0] * width, c[1] * height) for c in corners]
    # Corners going round the other way would mirror it: walk the top edge the other way instead.
    area = sum(p[i][0] * p[(i + 1) % 4][1] - p[(i + 1) % 4][0] * p[i][1] for i in range(4))
    if area < 0:
        p = [p[1], p[0], p[3], p[2]]
    out_w = max(1, round((math.dist(p[0], p[1]) + math.dist(p[3], p[2])) / 2))
    out_h = max(1, round((math.dist(p[0], p[3]) + math.dist(p[1], p[2])) / 2))
    # The perspective transform maps each point of the plane to the image: solve it from the corners.
    rows, values = [], []
    for (u, v), (x, y) in zip(((0, 0), (out_w, 0), (out_w, out_h), (0, out_h)), p, strict=True):
        rows += [[u, v, 1, 0, 0, 0, -u * x, -v * x], [0, 0, 0, u, v, 1, -u * y, -v * y]]
        values += [x, y]
    try:
        coeffs = np.linalg.solve(np.array(rows, dtype=np.float64), np.array(values, dtype=np.float64))
    except np.linalg.LinAlgError:
        return None
    return Plane(tuple(float(c) for c in coeffs), out_w, out_h)


def straightened_box(
    image: Image.Image, corners: list[list[float]], margin: float = 0.0
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """``straighten``, and where the area itself sits in the image it returns (pixels x1, y1, x2, y2)."""
    flat = plane(image, corners)
    if flat is None:  # three corners on one line: nothing to straighten
        box = pixel_box(image, region_box({"points": corners}))
        return image.crop(box), (0, 0, box[2] - box[0], box[3] - box[1])
    mx, my = round(flat.width * margin), round(flat.height * margin)
    out = flat.crop(image, (-mx, -my, flat.width + mx, flat.height + my), (flat.width + 2 * mx, flat.height + 2 * my))
    return out, (mx, my, mx + flat.width, my + flat.height)


def reading_plane(image: Image.Image, roi: dict | None, straightened: bool = False) -> Plane | None:
    """The plane a counter is read in, where its pointer dials are placed: its region straightened
    (``straightened``, four corners), else the region's bounding box (the whole frame without a
    region). None for corners that do not make an area."""
    corners = region_corners(roi) if straightened else None
    if corners is None:
        x0, y0, x1, y1 = region_box(roi)
        corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return plane(image, corners)


def meter_face(image: Image.Image, flat: Plane, reach: float, longest: int) -> tuple[Image.Image, list[float]]:
    """The meter face around a counter, straightened: as much of the plane as the image shows, at
    most ``reach`` × the region's height beyond it on every side, ``longest`` pixels on its longest
    side. Returns the picture and the part of the plane it shows (x1, y1, x2, y2 in units of the
    region's height, from its top-left corner) — where pointer dials are placed."""
    width, height = image.size
    seen = [flat.from_image(x, y) for x, y in ((0, 0), (width, 0), (width, height), (0, height))]
    unit = flat.height
    x0 = max(min(p[0] for p in seen) / unit, -reach)
    y0 = max(min(p[1] for p in seen) / unit, -reach)
    x1 = min(max(p[0] for p in seen) / unit, flat.width / unit + reach)
    y1 = min(max(p[1] for p in seen) / unit, 1 + reach)
    scale = longest / max(x1 - x0, y1 - y0)
    size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
    picture = flat.crop(image, (x0 * unit, y0 * unit, x1 * unit, y1 * unit), size)
    return picture, [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def region_image(image: Image.Image, roi: dict | None, straightened: bool = False) -> Image.Image:
    """What a reading sensor reads: its region straightened (``straightened``, four corners), or
    the region's bounding box."""
    corners = region_corners(roi) if straightened else None
    return straighten(image, corners) if corners else crop_box(image, region_box(roi))


def in_region(x: float, y: float, roi: dict | None) -> bool:
    """Whether a normalised point lies inside the region (polygon or rectangle)."""
    roi = normalise_roi(roi)
    if roi is None:
        return True
    points = roi.get("points")
    if not points:
        return roi["x"] <= x <= roi["x"] + roi["w"] and roi["y"] <= y <= roi["y"] + roi["h"]
    inside = False  # ray casting
    j = len(points) - 1
    for i, (xi, yi) in enumerate(points):
        xj, yj = points[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def draw_detections(image: Image.Image, detections: list[dict], names: dict[str, str], color: str) -> Image.Image:
    """A copy of ``image`` with labelled boxes; detection boxes are normalised to ``image``."""
    out = image.copy()
    draw = ImageDraw.Draw(out)
    width, height = out.size
    line = max(2, round(min(width, height) / 200))
    for det in detections:
        x1, y1, x2, y2 = det["box"]
        box = (x1 * width, y1 * height, x2 * width, y2 * height)
        draw.rectangle(box, outline=color, width=line)
        label = f"{names.get(det['key'], det['key'])} {det['score']:.0%}"
        text_box = draw.textbbox((box[0], box[1]), label)
        top = max(0, box[1] - (text_box[3] - text_box[1]) - 2 * line)
        draw.rectangle((box[0], top, box[0] + text_box[2] - text_box[0] + 2 * line, box[1]), fill=color)
        draw.text((box[0] + line, top + line // 2), label, fill="#12151a")
    return out
