"""Seven-segment displays drawn from scratch, for reading-sensor tests and the fake camera.

``render("74590", "lcd")`` draws dark digits on a light LCD; ``"led"`` draws lit segments on a
dark panel. Unlit segments stay faintly visible, like on real displays.
``render_counter(89939.5)`` draws a mechanical counter with rolling digit wheels.
"""

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
