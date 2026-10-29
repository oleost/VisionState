"""Seven-segment displays drawn from scratch, for reading-sensor tests and the fake camera.

``render("74590", "lcd")`` draws dark digits on a light LCD; ``"led"`` draws lit segments on a
dark panel. Unlit segments stay faintly visible, like on real displays.
``render_counter(89939.5)`` draws a mechanical counter with rolling digit wheels.
"""

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g",
}  # fmt: skip
STYLES = {
    # background, lit segment, unlit segment
    "lcd": ((178, 190, 170), (28, 32, 30), (160, 172, 153)),
    "led": ((18, 8, 8), (255, 60, 40), (60, 16, 12)),
}


# Mechanical counter (rolling digit wheels): sizes in pixels of the drawn meter face.
COUNTER_SIZE = (800, 360)
COUNTER_WHEEL, COUNTER_DIVIDER = 62, 24  # one cell = a wheel plus a divider (half on each side)
COUNTER_WINDOW = (118, 130)  # top and bottom of the window the wheels show through
COUNTER_PITCH = 104  # distance between two digits on a wheel


def counter_positions(value: float, digits: int) -> list[float]:
    """Position (0 <= p < 10) of every wheel, most significant first, for the number on the wheels.

    The last wheel turns continuously; every other wheel turns only while the wheel to its right
    goes from 9 to 0 — so ``value`` 1239.5 shows 1, 2, a 3 half replaced by 4, and 9 half way to 0.
    """
    out, lower = [], 0.0
    for k in range(digits):
        p = value % 10 if k == 0 else (value // 10**k) % 10 + max(0.0, lower - 9.0)
        out.append(p % 10)
        lower = p
    return out[::-1]


def counter_box(digits: int) -> dict[str, float]:
    """The window with the wheels, as a region (shares of the frame) — what a user would draw."""
    width, height = COUNTER_SIZE
    cell = COUNTER_WHEEL + COUNTER_DIVIDER
    top, bottom = COUNTER_WINDOW
    return {
        "x": (width - digits * cell) / 2 / width,
        "y": top / height,
        "w": digits * cell / width,
        "h": bottom / height,
    }


def _homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> np.ndarray:
    """The 3×3 projective transform taking the four points ``src`` to ``dst``."""
    rows, values = [], []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        rows += [[x, y, 1, 0, 0, 0, -x * u, -y * u], [0, 0, 0, x, y, 1, -x * v, -y * v]]
        values += [u, v]
    return np.append(np.linalg.solve(np.array(rows, float), np.array(values, float)), 1.0).reshape(3, 3)


def turned(
    image: Image.Image,
    angle: float,
    region: dict[str, float],
    tilt: float = 0.0,
    also: tuple[tuple[float, float], ...] = (),
) -> tuple[Image.Image, dict]:
    """``image`` turned ``angle`` degrees (anticlockwise, the whole picture kept: a camera that hangs
    askew) and the rectangle ``region`` (shares of the frame) in it: a region of four corners, its
    top edge first — what a user puts on the turned display (issue #58). ``tilt`` also looks at it
    at an angle: the top of the picture ``tilt`` × its width narrower than the bottom (perspective).
    ``also``: more points of ``image`` (pixels), returned where they are in the turned one (pixels)
    under the region's key "also"."""
    out = image.convert("RGB").rotate(angle, expand=True, fillcolor=(90, 90, 90), resample=Image.Resampling.BICUBIC)
    w, h = image.size
    x, y, rw, rh = region["x"] * w, region["y"] * h, region["w"] * w, region["h"] * h
    rad = math.radians(angle)
    cx, cy, nw, nh = w / 2, h / 2, out.width, out.height
    points = [
        (
            (px - cx) * math.cos(rad) + (py - cy) * math.sin(rad) + nw / 2,
            -(px - cx) * math.sin(rad) + (py - cy) * math.cos(rad) + nh / 2,
        )
        for px, py in ((x, y), (x + rw, y), (x + rw, y + rh), (x, y + rh), *also)
    ]
    if tilt:
        frame = [(0.0, 0.0), (float(nw), 0.0), (float(nw), float(nh)), (0.0, float(nh))]
        seen = [(tilt * nw / 2, 0.0), (nw - tilt * nw / 2, 0.0), (float(nw), float(nh)), (0.0, float(nh))]
        back = _homography(seen, frame)  # each pixel of the tilted picture comes from here
        out = out.transform(
            out.size,
            Image.Transform.PERSPECTIVE,
            tuple(back.flatten()[:8]),
            Image.Resampling.BICUBIC,
            fillcolor=(90, 90, 90),
        )
        forward = _homography(frame, seen)
        moved = [forward @ np.array([px, py, 1.0]) for px, py in points]
        points = [(m[0] / m[2], m[1] / m[2]) for m in moved]
    corners = [[round(float(px) / nw, 4), round(float(py) / nh, 4)] for px, py in points[:4]]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    box = {"x": min(xs), "y": min(ys), "w": round(max(xs) - min(xs), 4), "h": round(max(ys) - min(ys), 4)}
    return out, {**box, "points": corners, **({"also": points[4:]} if also else {})}


def render_counter(value: float, digits: int = 7, decimals: int = 3) -> Image.Image:
    """A meter face with a mechanical counter showing ``value`` (in units of its last wheel).

    White digits on black wheels (red for the ``decimals`` last ones), dark dividers with a dot,
    and the neighbouring digits peeking in above and below the window, like on a real meter.
    """
    width, height = COUNTER_SIZE
    cell = COUNTER_WHEEL + COUNTER_DIVIDER
    top, window = COUNTER_WINDOW
    left = (width - digits * cell) // 2
    image = Image.new("RGB", COUNTER_SIZE, (196, 200, 204))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((left - 26, top - 26, left + digits * cell + 26, top + window + 26), 14, fill=(58, 62, 70))
    draw.rectangle((left, top, left + digits * cell - 1, top + window - 1), fill=(16, 16, 18))
    font = ImageFont.load_default(96)
    for i, position in enumerate(counter_positions(value, digits)):
        red = i >= digits - decimals
        x = left + i * cell + COUNTER_DIVIDER // 2
        wheel = Image.new("RGB", (COUNTER_WHEEL, window), (172, 44, 48) if red else (22, 22, 26))
        wheel_draw = ImageDraw.Draw(wheel)
        for step in (-1, 0, 1, 2):
            digit = (int(position) + step) % 10
            y = window / 2 + (int(position) + step - position) * COUNTER_PITCH
            wheel_draw.text((COUNTER_WHEEL / 2, y), str(digit), font=font, fill=(240, 240, 236), anchor="mm")
        image.paste(wheel, (x, top))
        dot = left + i * cell + cell, top + window // 2
        if i < digits - 1:
            draw.ellipse((dot[0] - 4, dot[1] - 22, dot[0] + 4, dot[1] - 14), fill=(150, 60, 50))
            draw.ellipse((dot[0] - 4, dot[1] + 14, dot[0] + 4, dot[1] + 22), fill=(150, 60, 50))
    draw.text(
        (left + digits * cell + 44, top + window / 2),
        "m3",
        font=ImageFont.load_default(40),
        fill=(40, 44, 50),
        anchor="lm",
    )
    return image


def _segments(x: float, y: float, w: float, h: float, t: float) -> dict[str, list[tuple[float, float]]]:
    m = y + h / 2
    return {
        "a": [(x + t, y), (x + w - t, y), (x + w - 2 * t, y + t), (x + 2 * t, y + t)],
        "d": [(x + 2 * t, y + h - t), (x + w - 2 * t, y + h - t), (x + w - t, y + h), (x + t, y + h)],
        "g": [(x + t, m), (x + 2 * t, m - t / 2), (x + w - 2 * t, m - t / 2), (x + w - t, m), (x + w - 2 * t, m + t / 2), (x + 2 * t, m + t / 2)],
        "f": [(x, y + t), (x + t, y + 2 * t), (x + t, m - t), (x, m - t / 2)],
        "b": [(x + w, y + t), (x + w, m - t / 2), (x + w - t, m - t), (x + w - t, y + 2 * t)],
        "e": [(x, m + t / 2), (x + t, m + t), (x + t, y + h - 2 * t), (x, y + h - t)],
        "c": [(x + w, m + t / 2), (x + w, y + h - t), (x + w - t, y + h - 2 * t), (x + w - t, m + t)],
    }  # fmt: skip


def render(text: str, style: str = "lcd", digit_height: int = 120, margin: int = 40) -> Image.Image:
    """A display showing ``text`` (digits, "-", ":" and "." are supported)."""
    background, lit, unlit = STYLES[style]
    h = digit_height
    w, t, gap = h * 0.55, h * 0.08, h * 0.18
    width = margin * 2 + sum(w + gap if c not in ":." else gap * 1.5 for c in text)
    image = Image.new("RGB", (int(width), int(h + margin * 2)), background)
    draw = ImageDraw.Draw(image)
    x, y = float(margin), float(margin)
    for char in text:
        if char in ":.":
            r = t * 0.6
            dots = [y + h * 0.3, y + h * 0.7] if char == ":" else [y + h - r]
            for cy in dots:
                draw.ellipse((x + gap * 0.3, cy - r, x + gap * 0.3 + 2 * r, cy + r), fill=lit)
            x += gap * 1.5
            continue
        for name, polygon in _segments(x, y, w, h, t).items():
            draw.polygon(polygon, fill=lit if name in SEGMENTS[char] else unlit)
        x += w + gap
    return image


# --- Pointer dials (docs/POINTER_DIAL_PLAN.md) -----------------------------------------------------

DIAL_SPOTS = ((130, 520), (330, 650), (560, 650), (690, 465))  # centres of up to four dials below the counter
DIAL_RADIUS = 92


def render_dial(position: float, radius: int = DIAL_RADIUS, ccw: bool = False) -> Image.Image:
    """A pointer dial: white face, numbers 0-9 round the edge (clockwise, or anticlockwise with
    ``ccw``), a short red needle on a red hub with a little tail, pointing at ``position`` (0.0-9.9)."""
    size = radius * 2 + 8
    image = Image.new("RGB", (size, size), (196, 200, 204))
    draw = ImageDraw.Draw(image)
    c = size / 2
    draw.ellipse((c - radius, c - radius, c + radius, c + radius), fill=(246, 246, 242), outline=(60, 60, 60), width=3)
    font = ImageFont.load_default(max(10, radius // 5))
    sign = -1 if ccw else 1
    for n in range(10):
        a = sign * n / 10 * 2 * math.pi
        x, y = c + math.sin(a) * radius * 0.8, c - math.cos(a) * radius * 0.8
        draw.text((x, y), str(n), fill=(30, 30, 30), font=font, anchor="mm")
        tx, ty = c + math.sin(a) * radius * 0.95, c - math.cos(a) * radius * 0.95
        draw.line(
            [(c + math.sin(a) * radius * 0.88, c - math.cos(a) * radius * 0.88), (tx, ty)], fill=(30, 30, 30), width=2
        )
    a = sign * position / 10 * 2 * math.pi
    tip = (c + math.sin(a) * radius * 0.62, c - math.cos(a) * radius * 0.62)
    tail = (c - math.sin(a) * radius * 0.18, c + math.cos(a) * radius * 0.18)
    side = (math.cos(a) * radius * 0.09, math.sin(a) * radius * 0.09)
    red = (200, 30, 30)
    draw.polygon([tip, (c + side[0], c + side[1]), tail, (c - side[0], c - side[1])], fill=red)
    hub = radius * 0.13
    draw.ellipse((c - hub, c - hub, c + hub, c + hub), fill=red)
    return image


def render_meter(
    value: float, digits: int = 5, dials: int = 4, ccw: tuple[bool, ...] = ()
) -> tuple[Image.Image, dict, list[dict]]:
    """A water meter with a counter of ``digits`` wheels and ``dials`` pointer dials below it
    (×0.1 first), showing ``value`` m³: the wheels the whole cubic metres (the last turning on with
    the first dial), each dial the next decimal, turning on with the one after it (issue #59).
    Returns the picture, the counter's window as a region (what a user draws) and the dials as a
    reading sensor stores them (in units of the window's height from its top-left corner)."""
    counter = render_counter(value, digits, 0)
    width = COUNTER_SIZE[0]
    image = Image.new("RGB", (width, 760), (196, 200, 204))
    image.paste(counter, (0, 0))
    box = counter_box(digits)
    left, top = box["x"] * width, box["y"] * COUNTER_SIZE[1]
    unit = box["h"] * COUNTER_SIZE[1]  # the window's height in pixels
    placed = []
    for k in range(dials):
        turned_ccw = bool(ccw[k]) if k < len(ccw) else False
        position = (value * 10 ** (k + 1)) % 10
        face = render_dial(position, DIAL_RADIUS, turned_ccw)
        cx, cy = DIAL_SPOTS[k]
        image.paste(face, (round(cx - face.width / 2), round(cy - face.height / 2)))
        placed.append(
            {
                "x": round((cx - left) / unit, 4),
                "y": round((cy - top) / unit, 4),
                "r": round(DIAL_RADIUS / unit, 4),
                "ccw": turned_ccw,
            }
        )
    region = {
        "x": box["x"],
        "y": box["y"] * COUNTER_SIZE[1] / image.height,
        "w": box["w"],
        "h": box["h"] * COUNTER_SIZE[1] / image.height,
    }
    return image, region, placed


def turned_meter(
    value: float, angle: float = 0.0, tilt: float = 0.0, digits: int = 5, dials: int = 4, ccw: tuple[bool, ...] = ()
) -> tuple[Image.Image, dict, list[dict]]:
    """``render_meter`` seen turned and at an angle (``turned``), with its dials placed the way a
    user places them on the straightened meter face: in the plane the region's four corners make
    (its width the mean of its top and bottom edges, its height of its sides, as imaging.plane)."""
    image, region, placed = render_meter(value, digits, dials, ccw)
    if not angle and not tilt:
        return image, region, placed
    width, height = image.size
    left, top, unit = region["x"] * width, region["y"] * height, region["h"] * height
    rims = []
    for d in placed:
        cx, cy, r = left + d["x"] * unit, top + d["y"] * unit, d["r"] * unit
        rims += [(cx, cy), (cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    seen, corners = turned(image, angle, region, tilt, tuple(rims))
    p = [(c[0] * seen.width, c[1] * seen.height) for c in corners["points"]]
    plane_w = (math.dist(p[0], p[1]) + math.dist(p[3], p[2])) / 2
    plane_h = (math.dist(p[0], p[3]) + math.dist(p[1], p[2])) / 2
    to_plane = _homography(p, [(0.0, 0.0), (plane_w, 0.0), (plane_w, plane_h), (0.0, plane_h)])

    def flat(point: tuple[float, float]) -> tuple[float, float]:
        m = to_plane @ np.array([point[0], point[1], 1.0])
        return m[0] / m[2] / plane_h, m[1] / m[2] / plane_h

    out = []
    for k, d in enumerate(placed):
        centre, *rim = [flat(q) for q in corners["also"][k * 5 : k * 5 + 5]]
        r = sum(math.dist(centre, q) for q in rim) / len(rim)
        out.append({"x": round(centre[0], 4), "y": round(centre[1], 4), "r": round(r, 4), "ccw": d["ccw"]})
    del corners["also"]
    return seen, corners, out
