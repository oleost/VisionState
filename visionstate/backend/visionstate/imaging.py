"""Image helpers: decoding, region cropping, night detection and perceptual hashing."""

from __future__ import annotations

import hashlib
import io

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .settings import CHANGE_SIGNATURE_SIZE, JPEG_QUALITY, NEUTRAL_FILL, ROI_MAX_POINTS, RUNTIME, THUMB_SIZE

FULL_FRAME_KEY = "full"


class ImageTooLarge(OSError):
    """An image with more pixels than RUNTIME["max_image_megapixels"]; handled like an unreadable one."""


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    # Checked from the header, before the pixels are decoded (they could take gigabytes).
    if image.width * image.height > RUNTIME["max_image_megapixels"] * 1_000_000:
        raise ImageTooLarge(f"Image too large ({image.width}×{image.height})")
    image = ImageOps.exif_transpose(image)
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
    corners = {(x, y), (round(x + w, 4), y), (x, round(y + h, 4)), (round(x + w, 4), round(y + h, 4))}
    return len(points) == 4 and {(p[0], p[1]) for p in points} == corners


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


def crop_box(image: Image.Image, box: Box) -> Image.Image:
    """Crop a normalised box without masking (a detector needs to see whole objects)."""
    x1, y1, x2, y2 = box
    width, height = image.size
    return image.crop((int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)))


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
