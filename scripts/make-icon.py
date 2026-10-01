#!/usr/bin/env python3
"""Draw the Refract Image app icon source.

The icon is a macOS-style rounded square with a soft top highlight, holding a two-pane
"reference beside result" mark — the one idea the app is about. Run it from the repo root:

    .runtime/bin/python scripts/make-icon.py

That writes `assets/app-icon.png`, the 1024 px source. The bundle uses an `.icns`, which macOS
builds from an iconset — do this once and commit the result to `native/Resources/icon.icns`:

    mkdir -p /tmp/refract.iconset
    sips -z 16 16 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_16x16.png
    sips -z 32 32 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_16x16@2x.png
    sips -z 32 32 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_32x32.png
    sips -z 64 64 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_32x32@2x.png
    sips -z 128 128 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_128x128.png
    sips -z 256 256 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_128x128@2x.png
    sips -z 256 256 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_256x256.png
    sips -z 512 512 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_256x256@2x.png
    sips -z 512 512 -s format png assets/app-icon.png --out /tmp/refract.iconset/icon_512x512.png
    cp assets/app-icon.png /tmp/refract.iconset/icon_512x512@2x.png
    iconutil -c icns /tmp/refract.iconset -o native/Resources/icon.icns
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
OUT = Path(__file__).resolve().parents[1] / "assets" / "app-icon.png"


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    gradient = Image.new("RGB", (1, size))
    for y in range(size):
        blend = y / max(1, size - 1)
        gradient.putpixel(
            (0, y),
            (
                round(top[0] + (bottom[0] - top[0]) * blend),
                round(top[1] + (bottom[1] - top[1]) * blend),
                round(top[2] + (bottom[2] - top[2]) * blend),
            ),
        )
    return gradient.resize((size, size))


def main() -> int:
    radius = round(SIZE * 0.2237)  # macOS squircle corner proportion
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    body = vertical_gradient(SIZE, (10, 132, 255), (94, 92, 230)).convert("RGBA")
    icon.paste(body, (0, 0), rounded_mask(SIZE, radius))

    # Soft specular highlight across the top third.
    highlight = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(highlight).ellipse(
        (-SIZE * 0.25, -SIZE * 0.85, SIZE * 1.25, SIZE * 0.42), fill=46
    )
    highlight = highlight.filter(ImageFilter.GaussianBlur(SIZE * 0.06))
    icon.paste(Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 255)), (0, 0), highlight)

    draw = ImageDraw.Draw(icon)
    white = (255, 255, 255, 238)
    faint = (255, 255, 255, 140)

    # Two panes: the reference (left, outlined) and the result (right, solid), with a
    # divider between them — the comparison the app exists to make.
    pad = round(SIZE * 0.235)
    pane_w = round((SIZE - pad * 2 - SIZE * 0.075) / 2)
    pane_h = round(SIZE * 0.34)
    top = round((SIZE - pane_h) / 2)
    left = pad
    right = left + pane_w + round(SIZE * 0.075)

    draw.rounded_rectangle(
        (left, top, left + pane_w, top + pane_h),
        radius=round(SIZE * 0.035),
        outline=white,
        width=round(SIZE * 0.022),
    )
    draw.rounded_rectangle(
        (right, top, right + pane_w, top + pane_h),
        radius=round(SIZE * 0.035),
        fill=white,
    )

    # A prompt line beneath, sized like the text field it stands for.
    line_top = top + pane_h + round(SIZE * 0.115)
    draw.rounded_rectangle(
        (left, line_top, right + pane_w * 0.62, line_top + round(SIZE * 0.032)),
        radius=round(SIZE * 0.016),
        fill=faint,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    icon.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
