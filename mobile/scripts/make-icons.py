"""Draws the ErnestOS launcher icons and splash screens into the Android project.

The mark is the Mini App's: a white "E" on the primary blue (#2B66D6).
Run from the repo root with Pillow installed:  python mobile/scripts/make-icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RES = Path(__file__).resolve().parent.parent / "android" / "app" / "src" / "main" / "res"
BLUE, WHITE, GROUND = (43, 102, 214), (255, 255, 255), (243, 244, 246)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
LEGACY = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def letter(draw, box, size, colour):
    font = ImageFont.truetype(FONT, size)
    x0, y0, x1, y1 = box
    left, top, right, bottom = draw.textbbox((0, 0), "E", font=font)
    draw.text(((x0 + x1 - (right - left)) / 2 - left, (y0 + y1 - (bottom - top)) / 2 - top),
              "E", font=font, fill=colour)


def tile(size, scale=4):
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.22), fill=BLUE)
    letter(d, (0, 0, big, big), int(big * 0.56), WHITE)
    return img.resize((size, size), Image.LANCZOS)


def round_tile(size, scale=4):
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, big - 1, big - 1), fill=BLUE)
    letter(d, (0, 0, big, big), int(big * 0.5), WHITE)
    return img.resize((size, size), Image.LANCZOS)


def foreground(size, scale=4):
    # Adaptive icon: 108dp canvas, the visible circle is the middle 72dp.
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    letter(ImageDraw.Draw(img), (0, 0, big, big), int(big * 0.36), WHITE)
    return img.resize((size, size), Image.LANCZOS)


for density, px in LEGACY.items():
    folder = RES / f"mipmap-{density}"
    tile(px).save(folder / "ic_launcher.png")
    round_tile(px).save(folder / "ic_launcher_round.png")
    foreground(px * 108 // 48).save(folder / "ic_launcher_foreground.png")

(RES / "values" / "ic_launcher_background.xml").write_text(
    '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
    '    <color name="ic_launcher_background">#2B66D6</color>\n</resources>\n')

for splash in RES.glob("drawable*/splash.png"):
    w, h = Image.open(splash).size
    img = Image.new("RGB", (w, h), GROUND)
    mark = tile(int(min(w, h) * 0.28))
    img.paste(mark, ((w - mark.width) // 2, (h - mark.height) // 2), mark)
    img.save(splash, optimize=True)
print("icons and splash screens written")
