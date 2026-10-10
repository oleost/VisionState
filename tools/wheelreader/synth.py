"""Drawn wheel cells with a known position (0 <= p < 10), as a camera would see them."""

import io
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from paths import WORK

FONTS = sorted((WORK / "fonts").glob("*.ttf"))
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

# (wheel, digit) colours
SCHEMES = [
    ((240, 240, 236), (20, 20, 22)),  # black on white
    ((225, 225, 215), (35, 35, 40)),
    ((205, 210, 215), (25, 25, 30)),  # black on grey
    ((22, 22, 26), (240, 240, 236)),  # white on black
    ((40, 40, 44), (220, 220, 210)),
    ((172, 44, 48), (240, 240, 236)),  # white on red
    ((200, 40, 40), (250, 250, 250)),
    ((240, 240, 236), (190, 30, 35)),  # red on white
    ((230, 225, 215), (170, 40, 40)),
    ((235, 230, 210), (30, 30, 30)),  # black on cream
    ((30, 40, 90), (235, 235, 235)),  # white on dark blue
]


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    key = (str(path), size)
    if key not in _font_cache:
        f = ImageFont.truetype(str(path), size)
        try:  # variable fonts: pick a random-ish weight once per size
            axes = f.get_variation_axes()
            if axes:
                f.set_variation_by_axes(
                    [
                        random.uniform(
                            a["minimum"] + 0.3 * (a["maximum"] - a["minimum"]),
                            a["maximum"] * 0.85 + a["minimum"] * 0.15,
                        )
                        for a in axes
                    ]
                )
        except Exception:
            pass
        _font_cache[key] = f
    return _font_cache[key]


def jitter(c: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    return tuple(max(0, min(255, v + random.randint(-amount, amount))) for v in c)  # type: ignore[return-value]


def wheel_strip(
    position: float, w: int, h: int, scheme, fnt, digit_h: float, pitch: float, curve: float
) -> Image.Image:
    """The visible part of one wheel, ``h`` tall, digits rolling by ``position``."""
    bg, fg = scheme
    big_h = int(h * 2.2)
    strip = Image.new("RGB", (w, big_h), bg)
    d = ImageDraw.Draw(strip)
    cy = big_h / 2
    base = math.floor(position)
    for step in range(-3, 4):
        digit = (base + step) % 10
        y = cy + (base + step - position) * pitch
        d.text((w / 2, y), str(digit), font=fnt, fill=fg, anchor="mm")
    # cylinder: rows far from the centre are compressed (sample with arcsin)
    arr = np.asarray(strip)
    out_h = h
    ys = (np.arange(out_h) + 0.5 - out_h / 2) / (out_h / 2)  # -1..1 across the window
    if curve > 0:  # window row -> arc length on the wheel (same scale in the middle)
        radius = out_h / (2 * math.sin(curve))
        arc = radius * np.arcsin(np.clip(ys * math.sin(curve), -1, 1))
        rows = np.clip(np.round(cy + arc).astype(int), 0, big_h - 1)
    else:
        rows = np.clip(np.round(cy + ys * out_h / 2).astype(int), 0, big_h - 1)
    shaded = arr[rows].astype(np.float32)
    if curve > 0:  # darker towards the top and bottom of the wheel
        shade = 1 - random.uniform(0.0, 0.45) * (ys**2)
        shaded *= shade[:, None, None]
    return Image.fromarray(np.clip(shaded, 0, 255).astype(np.uint8))


def render(position: float) -> Image.Image:
    """One wheel cell as cut from a region: wheel, dividers, frame, light, blur, noise."""
    cw = random.randint(40, 90)
    ch = int(cw * random.uniform(1.2, 2.4))
    scheme = random.choice(SCHEMES)
    scheme = (jitter(scheme[0], 18), jitter(scheme[1], 18))
    # the wheel inside the cell
    wheel_w = int(cw * random.uniform(0.55, 0.95))
    window_top = int(ch * random.uniform(-0.05, 0.2))
    window_bot = int(ch * random.uniform(0.8, 1.05))
    win_h = max(8, window_bot - window_top)
    digit_h = win_h * random.uniform(0.42, 0.78)
    pitch = digit_h * random.uniform(1.15, 1.7)
    fnt = font(random.choice(FONTS), max(8, int(digit_h * 1.32)))
    curve = random.choice([0, random.uniform(0.3, 1.1)])
    strip = wheel_strip(position, wheel_w, win_h, scheme, fnt, digit_h, pitch, curve)
    # horizontal squash/stretch of the digits
    strip = strip.resize((wheel_w, win_h))

    frame_c = jitter(
        random.choice([(30, 30, 34), (200, 200, 205), (60, 60, 66), (120, 120, 125), (230, 230, 230), scheme[0]]), 25
    )
    cell = Image.new("RGB", (cw, ch), frame_c)
    d = ImageDraw.Draw(cell)
    x0 = (cw - wheel_w) // 2 + int(cw * random.uniform(-0.12, 0.12))
    cell.paste(strip, (x0, window_top))
    # neighbouring wheels peeking in at the sides
    if random.random() < 0.5:
        nb = jitter(scheme[0], 10)
        side = int(cw * random.uniform(0.0, 0.12))
        if side:
            d.rectangle((0, window_top, side, window_bot), fill=nb)
            d.rectangle((cw - side, window_top, cw, window_bot), fill=nb)
    # dividers: lines, dots, gear teeth
    if random.random() < 0.5:
        for xx in (x0 - 1, x0 + wheel_w):
            d.line((xx, 0, xx, ch), fill=jitter(frame_c, 40), width=random.randint(1, 3))
    if random.random() < 0.3:
        dot = jitter((150, 60, 50), 40)
        for xx in (x0 - 3, x0 + wheel_w + 3):
            for yy in (ch * 0.35, ch * 0.65):
                r = max(1, cw // 30)
                d.ellipse((xx - r, yy - r, xx + r, yy + r), fill=dot)
    if random.random() < 0.25:  # gear teeth seen through the gap
        tooth = jitter(frame_c, 60)
        gx = x0 + wheel_w + 1
        for yy in range(0, ch, max(3, ch // 12)):
            d.rectangle((gx, yy, gx + max(1, cw // 20), yy + max(1, ch // 30)), fill=tooth)
    # a printed decimal mark or red frame around the decimal wheels
    if random.random() < 0.15:
        d.rectangle((0, 0, cw - 1, ch - 1), outline=jitter((180, 40, 40), 30), width=random.randint(1, 3))

    img = cell
    # perspective-ish: small rotation and shear
    if random.random() < 0.7:
        img = img.rotate(random.uniform(-5, 5), resample=Image.Resampling.BILINEAR, expand=False, fillcolor=frame_c)
    if random.random() < 0.4:
        sh = random.uniform(-0.12, 0.12)
        img = img.transform(
            img.size,
            Image.Transform.AFFINE,
            (1, sh, -sh * ch / 2, 0, 1, 0),
            Image.Resampling.BILINEAR,
            fillcolor=frame_c,
        )
    # vertical crop jitter: the region drawn a bit off
    if random.random() < 0.5:
        t = int(ch * random.uniform(-0.1, 0.15))
        b = int(ch * random.uniform(0.85, 1.1))
        img = img.crop((0, t, cw, b))
    arr = np.asarray(img).astype(np.float32)
    H, W = arr.shape[:2]
    # light: gradient, flash hotspot, glare, window shadow
    yy, xx = np.mgrid[0:H, 0:W]
    light = np.ones((H, W), np.float32)
    if random.random() < 0.7:
        gx, gy = random.uniform(-0.5, 0.5), random.uniform(-0.6, 0.6)
        light *= 1 + gx * (xx / W - 0.5) + gy * (yy / H - 0.5)
    if random.random() < 0.3:
        cx, cy, r = random.uniform(0, W), random.uniform(0, H), random.uniform(0.2, 0.8) * max(W, H)
        light += random.uniform(0.2, 0.9) * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (r * r)))
    if random.random() < 0.3:
        top_shadow = random.uniform(0.1, 0.35) * H
        light *= 1 - random.uniform(0.2, 0.6) * np.clip(1 - yy / top_shadow, 0, 1)
    arr *= light[:, :, None]
    if random.random() < 0.15:  # specular glare
        cx, cy = random.uniform(0, W), random.uniform(0, H)
        rx, ry = random.uniform(2, W / 2), random.uniform(2, H / 3)
        g = np.exp(-(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2))
        arr = arr * (1 - g[:, :, None]) + 255 * g[:, :, None]
    if random.random() < 0.3:  # haze / dirty glass: lower contrast
        k = random.uniform(0.3, 0.8)
        arr = arr * k + arr.mean() * (1 - k)
    if random.random() < 0.2:  # tint (IR night view is grey; ESP32 colours are off)
        arr *= np.array([random.uniform(0.8, 1.2) for _ in range(3)], np.float32)
    if random.random() < 0.2:  # dirt specks
        for _ in range(random.randint(1, 8)):
            px, py = random.randint(0, W - 1), random.randint(0, H - 1)
            r = random.randint(1, 3)
            arr[max(0, py - r) : py + r, max(0, px - r) : px + r] *= random.uniform(0.3, 0.8)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    # blur / low resolution / noise / JPEG
    if random.random() < 0.6:
        img = img.filter(ImageFilter.GaussianBlur(random.uniform(0.3, 2.2) * W / 40))
    if random.random() < 0.15:  # motion blur vertically (wheel turning while exposing)
        k = random.randint(2, 6)
        a = np.asarray(img).astype(np.float32)
        a = sum(np.roll(a, s, axis=0) for s in range(k)) / k
        img = Image.fromarray(a.astype(np.uint8))
    if random.random() < 0.6:
        f = random.uniform(0.25, 0.8)
        small = img.resize((max(6, int(img.width * f)), max(8, int(img.height * f))), Image.Resampling.BILINEAR)
        img = small
    if random.random() < 0.6:
        a = np.asarray(img).astype(np.float32)
        a += np.random.normal(0, random.uniform(2, 14), a.shape)
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    if random.random() < 0.7:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=random.randint(15, 90))
        img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    if random.random() < 0.3:
        img = ImageEnhance.Contrast(img).enhance(random.uniform(0.5, 1.4))
    return img


if __name__ == "__main__":
    import sys

    random.seed(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    cols, rows = 16, 8
    sheet = Image.new("RGB", (cols * 70, rows * 110), "white")
    d = ImageDraw.Draw(sheet)
    for k in range(cols * rows):
        p = random.uniform(0, 10)
        im = render(p).resize((60, 90))
        x, y = (k % cols) * 70, (k // cols) * 110
        sheet.paste(im, (x, y))
        d.text((x, y + 92), f"{p:.1f}", fill="black")
    sheet.save(sys.argv[1])
