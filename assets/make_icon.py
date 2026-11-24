"""Draws the Saguaro tab icon (assets/icon.png): a saguaro cactus against a desert sunset.

Run once from the project folder:  py assets/make_icon.py
Drawn at 4x and scaled down so the edges stay smooth at browser-tab size.
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # working size; saved at 256 px
OUT = Path(__file__).with_name("icon.png")


def vgradient(size, top, bottom):
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return g.resize((size, size))


def main():
    # sunset sky inside a rounded square
    sky = vgradient(S, (250, 176, 92), (180, 83, 42))  # warm gold -> the app's desert orange (#B4532A)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=230, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(sky, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # setting sun
    d.ellipse([560, 250, 860, 550], fill=(255, 226, 150))

    # desert ground (low hills)
    ground = (122, 52, 28)
    d.ellipse([-300, 760, 700, 1300], fill=ground)
    d.ellipse([380, 800, 1400, 1340], fill=(140, 62, 32))

    # saguaro cactus: trunk + two arms, rounded ends
    green, w = (34, 110, 74), 150
    d.rounded_rectangle([437, 210, 437 + w, 900], radius=w // 2, fill=green)          # trunk
    d.rounded_rectangle([250, 430, 250 + 120, 700], radius=60, fill=green)           # left arm (up)
    d.rounded_rectangle([250, 600, 470, 700], radius=50, fill=green)                 # left elbow
    d.rounded_rectangle([660, 330, 660 + 120, 620], radius=60, fill=green)           # right arm (up)
    d.rounded_rectangle([560, 520, 780, 620], radius=50, fill=green)                 # right elbow
    # subtle ribs
    rib = (28, 92, 62)
    for x in (480, 512, 544):
        d.line([x, 260, x, 880], fill=rib, width=10)

    # re-apply the rounded corners (hills overflow the edges)
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (S, S), 0), mask))
    img.resize((256, 256), Image.LANCZOS).save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
