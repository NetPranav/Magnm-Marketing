#!/usr/bin/env python3
"""Magnm text card renderer - makes post images with no AI model calls.

Usage:
  python3 templates/card.py card.json out_dir/
card.json:
{
  "id": "20261023-asks-first",
  "field": "sky",                 # one of the PALETTE keys below (palette v3)
  "kicker": "CERO",               # small mono label, top-left (optional)
  "big": ["ASKS", "FIRST."],      # 1-3 short lines, Anton, huge
  "serif": "before anything changes your files.",   # one italic connector line (optional)
  "foot": "magnm-org.vercel.app", # small mono line, bottom (optional)
  "image": "path/to/screenshot.png",  # optional real product screenshot, boxed
  "sizes": ["4x5", "1x1", "16x9"] # which formats to export
}
Rules (taste): one field colour per card, ink text, no gradients/shadows/glow.
Needs: pip install pillow
"""
import json, os, sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
F = lambda n: os.path.join(HERE, "fonts", n)

# Palette v3 (see video-skills/magnm-video/references/magnm-brand.md)
PALETTE = {
    "stone": "#EFEFEC", "ink": "#0A0A0A", "lime": "#C8F31D", "sky": "#BCE3FF",
    "mint": "#C7F5D9", "butter": "#FFE89E", "coral": "#FF9F80", "lilac": "#DCCBFF",
}
SIZES = {"4x5": (1080, 1350), "1x1": (1080, 1080), "16x9": (1600, 900), "9x16": (1080, 1920)}


def fit(draw, text, font_path, max_w, start):
    s = start
    while s > 20:
        f = ImageFont.truetype(font_path, s)
        if draw.textlength(text, font=f) <= max_w:
            return f
        s -= 4
    return ImageFont.truetype(font_path, s)


def text_block(d, lines, serif, box_w, box_h, start):
    """Pick the largest Anton size so all big lines + serif line fit in box."""
    s = start
    while s >= 40:
        big = ImageFont.truetype(F("anton.ttf"), s)
        ser = ImageFont.truetype(F("serif-italic.ttf"), max(28, int(s * 0.32)))
        lh = int(s * 0.98)
        ser_h = int(ser.size * 1.5) if serif else 0
        widths = [d.textlength(l, font=big) for l in lines] + ([d.textlength(serif, font=ser)] if serif else [])
        if max(widths or [0]) <= box_w and lh * len(lines) + ser_h <= box_h:
            return big, ser, lh, ser_h
        s -= 6
    return big, ser, lh, ser_h


def draw_text(d, x, y_bottom, lines, serif, big, ser, lh, ser_h, fg):
    """Bottom-anchored text block ending at y_bottom."""
    y = y_bottom - (lh * len(lines) + ser_h)
    for ln in lines:
        top = big.getbbox(ln)[1]
        d.text((x, y - top), ln, font=big, fill=fg)
        y += lh
    if serif:
        d.text((x, y + int(ser.size * 0.25)), serif, font=ser, fill=fg)


def render(spec, size_key, out_dir):
    W, H = SIZES[size_key]
    bg = PALETTE.get(spec.get("field", "stone"), spec.get("field", "#EFEFEC"))
    fg = PALETTE["stone"] if spec.get("field") == "ink" else PALETTE["ink"]
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    m = int(min(W, H) * 0.075)
    mono = ImageFont.truetype(F("mono.ttf"), max(22, min(W, H) // 40))
    if spec.get("kicker"):
        d.text((m, m), spec["kicker"].upper(), font=mono, fill=fg)
    foot_h = 0
    if spec.get("foot"):
        foot_h = int(mono.size * 2.2)
        d.text((m, H - m - mono.size), spec["foot"], font=mono, fill=fg)
    lines = spec.get("big", [])[:3]
    serif = spec.get("serif")
    img_path = spec.get("image")
    shot = Image.open(img_path).convert("RGB") if img_path and os.path.exists(img_path) else None
    top = m + int(mono.size * 2.5)
    bottom = H - m - foot_h
    landscape = W > H
    if shot and landscape:
        col = int((W - 3 * m) * 0.5)
        shot.thumbnail((col, bottom - top))
        sx, sy = W - m - shot.width, top + (bottom - top - shot.height) // 2
        im.paste(shot, (sx, sy))
        d.rectangle([sx - 2, sy - 2, sx + shot.width + 1, sy + shot.height + 1], outline=fg, width=2)
        big, ser, lh, ser_h = text_block(d, lines, serif, col, bottom - top, int(H * 0.30))
        draw_text(d, m, bottom, lines, serif, big, ser, lh, ser_h, fg)
    else:
        if shot:
            shot.thumbnail((W - 2 * m, int((bottom - top) * 0.45)))
            im.paste(shot, (m, top))
            d.rectangle([m - 2, top - 2, m + shot.width + 1, top + shot.height + 1], outline=fg, width=2)
            text_top = top + shot.height + int(H * 0.04)
        else:
            text_top = top
        big, ser, lh, ser_h = text_block(d, lines, serif, W - 2 * m, bottom - text_top, int(H * 0.22))
        draw_text(d, m, bottom, lines, serif, big, ser, lh, ser_h, fg)
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f"{spec.get('id', 'card')}_{size_key}.png")
    im.save(p, optimize=True)
    return p


if __name__ == "__main__":
    spec = json.load(open(sys.argv[1]))
    out = sys.argv[2] if len(sys.argv) > 2 else "."
    for k in spec.get("sizes", ["4x5"]):
        print(render(spec, k, out))
