"""Draw the header logos (assets/wordmark.png, and wordmark-dark.png for dark mode): the saguaro icon followed by the name "Saguaro".

    py assets/make_wordmark.py

Needs Georgia Bold (Windows: C:/Windows/Fonts/georgiab.ttf); run make_icon.py first.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
FONT = Path("C:/Windows/Fonts/georgiab.ttf")


def main():
    draw("wordmark.png", (42, 36, 32, 255))         # light mode: dark text
    draw("wordmark-dark.png", (243, 236, 228, 255))  # dark mode: light text


def draw(name, color):
    h = 96  # drawn at 2x for sharp edges on high-density screens
    icon = Image.open(HERE / "icon.png").convert("RGBA").resize((h, h), Image.LANCZOS)
    font = ImageFont.truetype(str(FONT), 64)
    text = "Saguaro"
    box = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
    w = h + 22 + (box[2] - box[0]) + 8
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.paste(icon, (0, 0), icon)
    ImageDraw.Draw(img).text((h + 22 - box[0], (h - (box[3] - box[1])) // 2 - box[1]), text, font=font, fill=color)
    img.save(HERE / name, optimize=True)
    print("wrote", HERE / name, img.size)


if __name__ == "__main__":
    main()
