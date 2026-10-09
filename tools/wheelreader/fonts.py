"""Download the fonts the synthetic wheels are drawn with (SIL Open Font License, Google Fonts).

python fonts.py      -> VisionStateLocal/wheel/fonts/ (pinned commit, checked against SHA-256)
"""

import hashlib
import urllib.request

from paths import WORK

COMMIT = "51303ca9e8ac9dcea7b12d307ba568fd0e6fcfca"  # github.com/google/fonts
FONTS = {  # file -> (path in google/fonts, sha256)
    "Oswald.ttf": (
        "ofl/oswald/Oswald%5Bwght%5D.ttf",
        "5b38c246e255a12f5712d640d56bcced0472466fc68983d2d0410ec0457c2817",
    ),
    "BarlowCondensed-Medium.ttf": (
        "ofl/barlowcondensed/BarlowCondensed-Medium.ttf",
        "262bd143292ce479ee0cd09a42b47ab173fca8e9c6eb5ed0b5c8a845bc371d17",
    ),
    "BarlowCondensed-SemiBold.ttf": (
        "ofl/barlowcondensed/BarlowCondensed-SemiBold.ttf",
        "7b619d14bc2327509a9ef32b0890f709626f7ecc9ff61191c2a4314c5499d2d9",
    ),
    "Barlow-Medium.ttf": (
        "ofl/barlow/Barlow-Medium.ttf",
        "f8906f762cb73dca441da034bc363b2d8e2e68bc10d5c05e58717646c20cc4b4",
    ),
    "RobotoCondensed.ttf": (
        "ofl/robotocondensed/RobotoCondensed%5Bwght%5D.ttf",
        "dace262afcee68a5276f200d8026c57221735c0118ab5fda8c2c0d3dc409a8d0",
    ),
    "ShareTechMono-Regular.ttf": (
        "ofl/sharetechmono/ShareTechMono-Regular.ttf",
        "9ceab1f87414829af259c0f537573ae03ef7dd3147c0b27a36a1a0beb6732677",
    ),
    "B612Mono-Regular.ttf": (
        "ofl/b612mono/B612Mono-Regular.ttf",
        "b98cb96cc8a6206dae08c063d60902df7e6d40f86139ebdb97256704253c9c69",
    ),
    "ArchivoNarrow.ttf": (
        "ofl/archivonarrow/ArchivoNarrow%5Bwght%5D.ttf",
        "adbe027f625c8393ae0f6e174e32e233dda485bc3eda5153ce428275394ef97f",
    ),
    "SairaCondensed-Medium.ttf": (
        "ofl/sairacondensed/SairaCondensed-Medium.ttf",
        "a02d8fe45b8b7d952cb0dd341683b02ddc1b55dbd0ed89d9d438868be614b66f",
    ),
    "Teko.ttf": ("ofl/teko/Teko%5Bwght%5D.ttf", "d1321889f262bbbff632e7976349853399cd097b6f382d4b19790c915c13c1ae"),
    "PT_Sans-Narrow-Web-Regular.ttf": (
        "ofl/ptsansnarrow/PT_Sans-Narrow-Web-Regular.ttf",
        "4102edda03059163771869d258df54ac8563c408fa6e9ef75b2ddc85eabea6f4",
    ),
    "FiraSansCondensed-Medium.ttf": (
        "ofl/firasanscondensed/FiraSansCondensed-Medium.ttf",
        "6385967fe921a6ee837221cde461e695e77f8931a3ca4bc82c7c1c1a5fe47f86",
    ),
    "FiraSans-Regular.ttf": (
        "ofl/firasans/FiraSans-Regular.ttf",
        "c29556a2719bf613ef3d5e070e40d903a8965d9c081beca1375dc1e6e0f93c23",
    ),
    "NotoSans.ttf": (
        "ofl/notosans/NotoSans%5Bwdth,wght%5D.ttf",
        "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d",
    ),
    "IBMPlexMono-Medium.ttf": (
        "ofl/ibmplexmono/IBMPlexMono-Medium.ttf",
        "a9b4c49bb299e05b5f6c481e7fb5e78943d2793249a0c8874ab574a2d1ea6755",
    ),
    "Overpass.ttf": (
        "ofl/overpass/Overpass%5Bwght%5D.ttf",
        "970717df17a7f9911dee45f60695d05bfa9d745fa0a11fc5c348371fa21f0073",
    ),
    "Asap.ttf": (
        "ofl/asap/Asap%5Bwdth,wght%5D.ttf",
        "7bf29bcab72f7d00de600e964a3fc206620050d8448ddd976822378d0fe18028",
    ),
    "Rajdhani-Medium.ttf": (
        "ofl/rajdhani/Rajdhani-Medium.ttf",
        "12ff7dcfe4c206e3875ac53b1762eab57de6a2fa7f5a86c26b97b88d6591eac2",
    ),
    "ChakraPetch-Medium.ttf": (
        "ofl/chakrapetch/ChakraPetch-Medium.ttf",
        "d480f4f97405fac3600652e2e14fd0b14339031c0af4da11582994878f04d919",
    ),
    "SourceCodePro.ttf": (
        "ofl/sourcecodepro/SourceCodePro%5Bwght%5D.ttf",
        "b400fc584e10aff25d0e775ce181b4fc1c5ea1b5dc37b81aeb2084375b945790",
    ),
}

if __name__ == "__main__":
    folder = WORK / "fonts"
    folder.mkdir(parents=True, exist_ok=True)
    for name, (path, sha) in FONTS.items():
        target = folder / name
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            data = urllib.request.urlopen(f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/{path}").read()
            if hashlib.sha256(data).hexdigest() != sha:
                raise SystemExit(f"{name}: unexpected SHA-256")
            target.write_bytes(data)
        print(target)
