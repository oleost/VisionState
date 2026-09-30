"""Seven-segment displays drawn from scratch, for reading-sensor tests and the fake camera.

``render("74590", "lcd")`` draws dark digits on a light LCD; ``"led"`` draws lit segments on a
dark panel. Unlit segments stay faintly visible, like on real displays.
"""

from PIL import Image, ImageDraw

SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g",
}  # fmt: skip
STYLES = {
    # background, lit segment, unlit segment
    "lcd": ((178, 190, 170), (28, 32, 30), (160, 172, 153)),
    "led": ((18, 8, 8), (255, 60, 40), (60, 16, 12)),
}


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
