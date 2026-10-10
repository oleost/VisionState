"""The evaluation sets: whole readings with the right value, as (set, id, digits, truth, region).

``truth`` is in units of the last wheel (bolausson: litres with the tenths read by eye).
"""

import csv
import json

from PIL import Image

from common import export_region
from paths import DRYAD, LOCAL

BOL = LOCAL / "bolausson-watermeter" / "training"  # GPL-3.0: test only
SPIKE = LOCAL / "spike" / "out"

BOL_LABELS = {
    "cold": [
        89932.0, 89932.0, 89932.0, 89932.0, 89939.5, 89945.0, 89945.3, 89945.8, 89949.8, 90040.0,
        90046.5, 90046.8, 90049.0, 90050.0, 90050.2, 90058.0, 90059.5, 90060.0, 90061.0, 90069.4,
        90071.8, 90079.8, 90081.4, 90082.8, 90085.8, 90089.5, 90092.8, 90100.0, 90102.2, 90105.8,
        90108.8, 90112.7, 90931.0, 90940.0, 90940.2,
    ],
    "warm": [
        45568.9, 45569.8, 45572.8, 45668.0, 45678.7, 45679.0, 45682.0, 45690.8, 45695.0, 45696.0,
        45696.9, 45702.8, 45727.0, 45729.0, 45730.0, 45734.5, 45740.0, 46150.0,
    ],
}  # fmt: skip


def pedromfa(folder: str, only: set[int] | None = None):
    data = json.loads((LOCAL / folder / "readings.json").read_text(encoding="utf-8"))
    for r in data["readings"]:
        i = int(r["file"][-8:-4])
        if only and i not in only:
            continue
        if r["answer"] == "unchecked" or (r["answer"] == "misread" and not r["right_value"]):
            continue  # only answers given by a person are labels
        v = float(r["right_value"] or r["value"])
        if v > 10000:
            v /= 1000
        yield (
            f"{folder}/{i:04d}",
            7,
            float(round(v * 1000)),
            export_region(Image.open(LOCAL / folder / r["file"]).convert("RGB")),
        )


def bolausson(meter: str, first_only: bool = True):
    groups = json.loads((SPIKE / f"{meter}-groups.json").read_text(encoding="utf-8"))
    files = {p.name: p for p in BOL.glob(f"*test_images/{meter}/*/*-01-Original.png")}
    for g, (group, value) in enumerate(zip(groups, BOL_LABELS[meter], strict=True)):
        for name in group["files"][:1] if first_only else group["files"]:
            yield f"{meter}/{g:02d}/{name}", 7, value, Image.open(files[name]).convert("RGB")


def dryad_test(limit: int | None = None):
    n = 0
    for nd in (5, 6):
        folder = DRYAD / f"recognition_{nd}-digit" / "test"
        for row in csv.reader(open(folder / f"{nd}-digit_test_rec_label_CSV.csv", encoding="utf-8-sig")):
            yield (
                f"dryad{nd}/{row[0]}",
                nd,
                float(row[1]),
                Image.open(folder / f"{nd}-digit_test_img" / row[0]).convert("RGB"),
            )
            n += 1
            if limit and n >= limit:
                return


SETS = {
    "pedro40": lambda: pedromfa("pedromfa-watermeter-issue40", set(range(1, 18))),  # held out
    "pedro_esp": lambda: pedromfa("pedromfa-watermeter-esp"),  # trained on
    "pedro32": lambda: pedromfa("pedromfa-watermeter"),  # trained on
    "bol_cold": lambda: bolausson("cold"),
    "bol_warm": lambda: bolausson("warm"),
    "bol_cold_all": lambda: bolausson("cold", False),
    "bol_warm_all": lambda: bolausson("warm", False),
    "dryad_test": lambda: dryad_test(),
}
