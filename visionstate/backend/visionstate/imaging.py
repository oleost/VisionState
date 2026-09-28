"""Image helpers: decoding, region cropping, night detection and perceptual hashing."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps

from .settings import JPEG_QUALITY, RUNTIME, THUMB_SIZE

FULL_FRAME_KEY = "full"


def decode(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def encode_jpeg(image: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def normalise_roi(roi: dict | None) -> dict | None:
    """Clamp a region to the unit square. Returns None for a missing or full-frame region."""
    if not roi:
        return None
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
    return f"{roi['x']:.4f},{roi['y']:.4f},{roi['w']:.4f},{roi['h']:.4f}"


def crop(image: Image.Image, roi: dict | None) -> Image.Image:
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
    return image.crop(box)


def letterbox(image: Image.Image, size: int, fill: tuple[int, int, int] = (124, 116, 104)) -> Image.Image:
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
