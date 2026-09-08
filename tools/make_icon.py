"""Draw the ScoreShift app icon (no external art needed)."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets"
SIZES = [16, 24, 32, 48, 64, 128, 256]

INK = (17, 24, 39)
PAPER = (252, 252, 250)
BRAND = (37, 99, 235)
ACCENT = (16, 185, 129)


def draw(size: int = 512) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 512.0

    # Rounded brand tile
    d.rounded_rectangle([8 * u, 8 * u, 504 * u, 504 * u], radius=110 * u, fill=BRAND)

    # A sheet of paper, tilted slightly - the "scan" half of the idea
    page = [(150 * u, 96 * u), (392 * u, 128 * u),
            (362 * u, 420 * u), (120 * u, 388 * u)]
    d.polygon([(x + 10 * u, y + 12 * u) for x, y in page], fill=(0, 0, 0, 60))
    d.polygon(page, fill=PAPER)

    # Staff lines following the page tilt
    for i in range(5):
        y = 176 * u + i * 26 * u
        d.line([(168 * u, y), (352 * u, y + 24 * u)], fill=(120, 130, 145), width=int(max(1, 4 * u)))

    # A note head with a stem, sitting on the staff
    cx, cy = 232 * u, 268 * u
    d.ellipse([cx - 36 * u, cy - 26 * u, cx + 36 * u, cy + 26 * u], fill=INK)
    d.line([(cx + 33 * u, cy - 8 * u), (cx + 46 * u, cy - 150 * u)],
           fill=INK, width=int(max(2, 12 * u)))

    # Up-arrow: the key change
    ax, ay = 372 * u, 336 * u
    d.polygon([(ax, ay - 96 * u), (ax + 52 * u, ay - 30 * u), (ax + 20 * u, ay - 30 * u),
               (ax + 20 * u, ay + 54 * u), (ax - 20 * u, ay + 54 * u),
               (ax - 20 * u, ay - 30 * u), (ax - 52 * u, ay - 30 * u)],
              fill=ACCENT)
    return img


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    master = draw(512)
    png = OUT / "scoreshift.png"
    master.save(png)
    ico = OUT / "scoreshift.ico"
    master.save(ico, sizes=[(s, s) for s in SIZES])
    print(f"wrote {png}\nwrote {ico}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
