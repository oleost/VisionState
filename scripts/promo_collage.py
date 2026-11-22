"""The promo screenshots on one picture, to share on social media: docs/promo/collage.png.

Usage: python scripts/promo_collage.py   (after the screenshots in docs/promo/ were taken again)

What it is made of: docs/promo/README.md. Uses the Segoe UI font on Windows and DejaVu Sans
elsewhere (Pillow's own font when neither is found).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROMO = Path(__file__).resolve().parent.parent / "docs" / "promo"
OUT = PROMO / "collage.png"

WIDTH = 2048  # what Facebook keeps of a shared picture
MARGIN, GAP, RADIUS = 64, 32, 18
BG, BORDER, TEXT, MUTED = "#0f1216", "#29303a", "#e9edf1", "#a0a9b4"
TAGLINE = "Turn your cameras and microphones into Home Assistant sensors, with a local AI you teach in minutes."

# Rows of (screenshot, share of the row's width); the row's height is its tallest picture's.
ROWS = [
    [("objects.png", 0.72), ("ha-objects.png", 0.28)],
    [("dashboard.png", 0.72), ("ha-states.png", 0.28)],
    [("people.png", 1 / 3), ("reading.png", 1 / 3), ("sound.png", 1 / 3)],
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ["segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "DejaVuSans.ttf"]
    for name in names:
        for folder in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (folder / name).exists():
                return ImageFont.truetype(str(folder / name), size)
    return ImageFont.load_default(size)


def tile(name: str, width: int) -> Image.Image:
    """A screenshot ``width`` wide with rounded corners and a thin border."""
    shot = Image.open(PROMO / name).convert("RGB")
    shot = shot.resize((width, round(shot.height * width / shot.width)), Image.Resampling.LANCZOS)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), RADIUS, fill=255)
    out = Image.new("RGBA", shot.size, (0, 0, 0, 0))
    out.paste(shot, (0, 0), mask)
    ImageDraw.Draw(out).rounded_rectangle((0, 0, shot.width - 1, shot.height - 1), RADIUS, outline=BORDER, width=2)
    return out


def main() -> None:
    inner = WIDTH - 2 * MARGIN
    rows = []
    for row in ROWS:
        free = inner - GAP * (len(row) - 1)
        tiles = [tile(name, round(free * share)) for name, share in row]
        rows.append(tiles)
    logo = Image.open(PROMO / "logo-dark.png").convert("RGBA")
    logo = logo.resize((720, round(logo.height * 720 / logo.width)), Image.Resampling.LANCZOS)
    tagline = font(40)
    header = logo.height + 24 + 56
    height = MARGIN + header + GAP + sum(max(t.height for t in r) for r in rows) + GAP * (len(rows) - 1) + MARGIN

    canvas = Image.new("RGBA", (WIDTH, height), BG)
    canvas.alpha_composite(logo, ((WIDTH - logo.width) // 2, MARGIN))
    draw = ImageDraw.Draw(canvas)
    draw.text((WIDTH // 2, MARGIN + logo.height + 24), TAGLINE, fill=MUTED, font=tagline, anchor="ma")
    y = MARGIN + header + GAP
    for tiles in rows:
        tallest = max(t.height for t in tiles)
        x = MARGIN
        for t in tiles:
            shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            ImageDraw.Draw(shadow).rounded_rectangle(
                (x, y + 8, x + t.width, y + t.height + 8), RADIUS, fill=(0, 0, 0, 140)
            )
            canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
            canvas.alpha_composite(t, (x, y))  # top aligned: the pictures of a row start on one line
            x += t.width + GAP
        y += tallest + GAP
    canvas.convert("RGB").save(OUT, optimize=True)
    print(f"{OUT} ({WIDTH}x{height})")


if __name__ == "__main__":
    main()
