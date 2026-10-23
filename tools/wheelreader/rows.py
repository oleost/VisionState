"""Drawn rows of wheels (wheels-v2): a cell is cut from a drawn row of five wheels, so a region
drawn a little off shows the wheel off-centre with a part of its neighbour (digits and all) at the
side, and printed text above or below the windows. Photometric steps as ``synth.render``.

python rows.py sheet out.png [seed]     a sheet to look at
python rows.py build N                  data/rows.npz (one cell, 32 x 48) and data/rows_ctx.npz
                                        (the cell with context, 64 x 48), N cells each
"""

import io
import random
import string
import sys
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from synth import FONTS, SCHEMES, font, jitter, wheel_strip

MAX_OFFSET = 0.38  # of the cell width


def row_cell(position: float, context: float = 0.0) -> Image.Image:
    """One cell (``context``: plus that share of a cell on each side) cut from a drawn row of five wheels."""
    cw = random.randint(40, 90)
    ch = int(cw * random.uniform(1.2, 2.4))
    scheme = random.choice(SCHEMES)
    scheme = (jitter(scheme[0], 18), jitter(scheme[1], 18))
    # neighbours often share the scheme; a red/black boundary sometimes
    other = scheme if random.random() < 0.7 else (jitter(random.choice(SCHEMES)[0], 18), jitter(random.choice(SCHEMES)[1], 18))
    wheel_w = int(cw * random.uniform(0.5, 0.85))
    pitch_x = cw  # wheels are one cell apart when the region fits
    window_top = int(ch * random.uniform(-0.05, 0.2))
    window_bot = int(ch * random.uniform(0.8, 1.05))
    win_h = max(8, window_bot - window_top)
    digit_h = win_h * random.uniform(0.42, 0.78)
    pitch = digit_h * random.uniform(1.15, 1.7)
    fnt = font(random.choice(FONTS), max(8, int(digit_h * 1.32)))
    curve = random.choice([0, random.uniform(0.3, 1.1)])
    crossed = random.random() < 0.3  # the whole row has crossed sevens or none
    frame_c = jitter(
        random.choice([(30, 30, 34), (200, 200, 205), (60, 60, 66), (120, 120, 125), (230, 230, 230), scheme[0]]), 25
    )
    W = 5 * pitch_x
    canvas = Image.new("RGB", (W, ch), frame_c)
    d = ImageDraw.Draw(canvas)
    # neighbours: at rest (any digit), the right one maybe turning freely
    def at_rest():
        return random.randint(0, 9) + (random.gauss(0, 0.06) if random.random() < 0.8 else 0)

    right_p = random.uniform(0, 10) if random.random() < 0.4 else at_rest()
    right_s = other if random.random() < 0.5 else scheme
    wheels = ((at_rest(), other), (at_rest(), other), (position, scheme), (right_p, right_s), (random.uniform(0, 10), right_s))
    for j, (p, sch) in enumerate(wheels):
        strip = wheel_strip(p % 10, wheel_w, win_h, sch, fnt, digit_h, pitch, curve, crossed).resize((wheel_w, win_h))
        x0 = j * pitch_x + (pitch_x - wheel_w) // 2
        canvas.paste(strip, (x0, window_top))
        if random.random() < 0.5:
            for xx in (x0 - 1, x0 + wheel_w):
                d.line((xx, 0, xx, ch), fill=jitter(frame_c, 40), width=random.randint(1, 3))
    # printed text below or above the windows (type plates, serial numbers)
    if random.random() < 0.35:
        size = max(6, int(ch * random.uniform(0.1, 0.2)))
        tf = font(random.choice(FONTS), size)
        text = "".join(random.choice(string.ascii_uppercase + string.digits + "-/,") for _ in range(random.randint(6, 16)))
        y = random.choice([random.uniform(0.82, 0.98), random.uniform(-0.05, 0.08)]) * ch
        d.text((random.uniform(-W / 3, W / 3), y), text, font=tf, fill=jitter((30, 30, 30), 30), anchor="lm")
    # the cell as the app cuts it: one cell wide, off by up to MAX_OFFSET, sometimes a little narrower or wider
    off = random.uniform(-MAX_OFFSET, MAX_OFFSET) * cw if random.random() < 0.8 else random.uniform(-0.1, 0.1) * cw
    width = cw * random.uniform(0.85, 1.15)
    left = 2 * pitch_x + (pitch_x - width) / 2 + off - context * width
    img = canvas.crop((round(left), 0, round(left + width * (1 + 2 * context)), ch))
    return finish(img, frame_c)


def finish(img: Image.Image, frame_c) -> Image.Image:
    """synth.render's geometry and photometric steps, unchanged."""
    cw, ch = img.size
    if random.random() < 0.7:
        img = img.rotate(random.uniform(-5, 5), resample=Image.Resampling.BILINEAR, expand=False, fillcolor=frame_c)
    if random.random() < 0.4:
        sh = random.uniform(-0.12, 0.12)
        img = img.transform(img.size, Image.Transform.AFFINE, (1, sh, -sh * ch / 2, 0, 1, 0), Image.Resampling.BILINEAR, fillcolor=frame_c)
    if random.random() < 0.5:
        t = int(ch * random.uniform(-0.1, 0.15))
        b = int(ch * random.uniform(0.85, 1.1))
        img = img.crop((0, t, cw, b))
    arr = np.asarray(img).astype(np.float32)
    H, W = arr.shape[:2]
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
    if random.random() < 0.2:  # shadow at the bottom of the window too
        bot = random.uniform(0.1, 0.3) * H
        light *= 1 - random.uniform(0.2, 0.6) * np.clip((yy - (H - bot)) / bot, 0, 1)
    arr *= light[:, :, None]
    if random.random() < 0.15:
        cx, cy = random.uniform(0, W), random.uniform(0, H)
        rx, ry = random.uniform(2, W / 2), random.uniform(2, H / 3)
        g = np.exp(-(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2))
        arr = arr * (1 - g[:, :, None]) + 255 * g[:, :, None]
    if random.random() < 0.3:
        k = random.uniform(0.3, 0.8)
        arr = arr * k + arr.mean() * (1 - k)
    if random.random() < 0.2:
        arr *= np.array([random.uniform(0.8, 1.2) for _ in range(3)], np.float32)
    if random.random() < 0.2:
        for _ in range(random.randint(1, 8)):
            px, py = random.randint(0, W - 1), random.randint(0, H - 1)
            r = random.randint(1, 3)
            arr[max(0, py - r) : py + r, max(0, px - r) : px + r] *= random.uniform(0.3, 0.8)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if random.random() < 0.6:
        img = img.filter(ImageFilter.GaussianBlur(random.uniform(0.3, 2.2) * W / 40))
    if random.random() < 0.15:
        k = random.randint(2, 6)
        a = np.asarray(img).astype(np.float32)
        a = sum(np.roll(a, s, axis=0) for s in range(k)) / k
        img = Image.fromarray(a.astype(np.uint8))
    if random.random() < 0.6:
        f = random.uniform(0.25, 0.8)
        img = img.resize((max(6, int(img.width * f)), max(8, int(img.height * f))), Image.Resampling.BILINEAR)
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


def _chunk(args):
    seed, n, context = args
    from common import cell_input
    from context import CTX, ctx_input

    random.seed(seed)
    np.random.seed(seed % 2**32)
    xs, ps = [], []
    for _ in range(n):
        p = random.uniform(0, 10)
        if random.random() < 0.4:
            p = (round(p) + random.gauss(0, 0.06)) % 10
        if context:
            xs.append(ctx_input(row_cell(p, context=CTX)))
        else:
            xs.append((cell_input(row_cell(p)) * 255).astype(np.uint8))
        ps.append(p)
    return np.stack(xs), np.array(ps, np.float32)


def build(n: int) -> None:
    from paths import WORK

    for name, context, seed0 in (("rows", False, 10_000), ("rows_ctx", True, 20_000)):
        with Pool() as pool:
            parts = pool.map(_chunk, [(seed0 + i, 2000, context) for i in range(n // 2000)])
        x = np.concatenate([p[0] for p in parts])
        p = np.concatenate([p[1] for p in parts])
        np.savez_compressed(WORK / "data" / f"{name}.npz", X=x, mid=p, half=np.full(len(p), 0.05, np.float32))
        print(name, x.shape)


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(int(sys.argv[2]))
    else:
        random.seed(int(sys.argv[3]) if len(sys.argv) > 3 else 0)
        cols, nrows = 16, 8
        sheet = Image.new("RGB", (cols * 70, nrows * 110), "white")
        dr = ImageDraw.Draw(sheet)
        for k in range(cols * nrows):
            p = random.uniform(0, 10)
            x, y = (k % cols) * 70, (k // cols) * 110
            sheet.paste(row_cell(p).resize((60, 90)), (x, y))
            dr.text((x, y + 92), f"{p:.1f}", fill="black")
        sheet.save(sys.argv[2])
