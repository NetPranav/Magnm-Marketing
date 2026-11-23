#!/usr/bin/env python3
"""Make the Android splash logo from the Magnm app icon (needs Pillow; run by hand when the icon changes).

Writes apps/mobile/android/app/src/main/res/drawable-<density>/splash_icon.png: the logo on a transparent
288dp square, so it sits on the splash background colour (#14151A) with no visible box. The icon's own dark
backdrop is keyed out by brightness. Android 12 shows the middle 192dp circle, so the logo stays inside it.
"""

import os
import sys

from PIL import Image, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "apps", "desktop", "src-tauri", "icons", "icon.png")
RES = os.path.join(ROOT, "apps", "mobile", "android", "app", "src", "main", "res")
DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}
CANVAS_DP = 288
LOGO_WIDTH_DP = 136  # corner-to-corner this stays inside the 192dp circle
KEY_LOW, KEY_HIGH = 52, 88  # brightness range that fades the dark backdrop into transparency


def key_out_backdrop(icon):
    """Return the icon with the dark backdrop made transparent (soft edge) and cropped to the logo."""
    rgb = icon.convert("RGB")
    luma = rgb.convert("L")
    alpha = luma.point(lambda v: 0 if v <= KEY_LOW else 255 if v >= KEY_HIGH else int(255 * (v - KEY_LOW) / (KEY_HIGH - KEY_LOW)))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    box = ImageChops.invert(alpha.point(lambda v: 255 if v > 24 else 0)).point(lambda v: 255 - v).getbbox()
    return out.crop(box)


def main():
    logo = key_out_backdrop(Image.open(SOURCE))
    for density, scale in DENSITIES.items():
        side = int(round(CANVAS_DP * scale))
        width = int(round(LOGO_WIDTH_DP * scale))
        height = int(round(logo.height * width / logo.width))
        resized = logo.resize((width, height), Image.LANCZOS)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(resized, ((side - width) // 2, (side - height) // 2), resized)
        folder = os.path.join(RES, f"drawable-{density}")
        os.makedirs(folder, exist_ok=True)
        canvas.save(os.path.join(folder, "splash_icon.png"), optimize=True)
        print(f"wrote {density}: {side}x{side}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
