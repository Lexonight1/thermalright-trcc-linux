#!/usr/bin/env python3
"""Generate the Settings panel's Box icon -- ``display_mode_icon_inset.png``.

The Background box's third action (a module's picture in a box over the
theme) is ours, so the Windows app has no art for it; it showed the bare word.
This draws it in its neighbours' style by DERIVING it from one of them
(``display_mode_icon_video.png``): the same page, fold and band, pixel for
pixel, with the band's symbol replaced by a picture-in-picture -- a frame and,
in its bottom-right corner, a small filled box.  That is what Box does.

    python3 dev/tools/gen_inset_icon.py          # writes the asset
    python3 dev/tools/gen_inset_icon.py --check  # exit 1 if it would change
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parents[2] / "src" / "trcc" / "ui" / "gui" / "assets"
SOURCE = ASSETS / "display_mode_icon_video.png"
TARGET = ASSETS / "display_mode_icon_inset.png"

#: The source's symbol, inside its band (measured on the 40 x 40 source):
#: columns 6..27, rows 17..30.  The band's white is at (3, 20), the symbol's
#: dark at (6, 21) -- (35, 34, 39); its corners are anti-aliased greys.
SYMBOL = (6, 17, 27, 30)
BAND_AT, DARK_AT = (3, 20), (6, 21)
#: The new symbol: a frame (the panel) and a filled box in its corner.
FRAME = (8, 18, 25, 29)
INSET = (17, 23, 23, 27)


def build() -> Image.Image:
    """The icon, from the source."""
    image = Image.open(SOURCE).convert("RGBA")
    band, dark = image.getpixel(BAND_AT), image.getpixel(DARK_AT)
    draw = ImageDraw.Draw(image)
    draw.rectangle(SYMBOL, fill=band)
    draw.rectangle(FRAME, outline=dark, width=2)
    draw.rectangle(INSET, fill=dark)
    return image


def main() -> int:
    image = build()
    if "--check" in sys.argv:
        same = TARGET.is_file() and Image.open(TARGET).convert("RGBA").tobytes() == image.tobytes()
        print(f"{TARGET.name}: {'current' if same else 'STALE -- run without --check'}")
        return 0 if same else 1
    image.save(TARGET)
    print(f"wrote {TARGET.relative_to(ASSETS.parents[4])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
