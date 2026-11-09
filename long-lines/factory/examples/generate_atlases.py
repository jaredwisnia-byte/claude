"""Generate self-contained painted + aligned signed-depth demo atlases.

Run: python long-lines/factory/examples/generate_atlases.py
The committed JSON manifests declare the exact rectangles and dimensions. The
PNGs are deterministic demo input artwork, not AI-generated production textures.
"""
import json
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))


def make(subfolder):
    folder = os.path.join(ROOT, subfolder)
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    size = tuple(spec["atlas"]["size_px"])
    albedo = Image.new("RGB", size, (33, 34, 38))
    depth = Image.new("RGB", size, (255, 255, 255))  # white: zero displacement
    a = ImageDraw.Draw(albedo)
    d = ImageDraw.Draw(depth)
    vector = Image.new("RGB", size, (128, 128, 128)) if spec["atlas"].get("vector") else None
    vdraw = ImageDraw.Draw(vector) if vector else None
    for panel in spec["panels"]:
        x0, y0, x1, y1 = map(int, panel["rect_px"])
        tag = panel["name"]
        if tag == "curved_front":
            a.rectangle((x0, y0, x1 - 1, y1 - 1), fill=(182, 187, 164))
            a.rectangle((x0 + 25, y0 + 15, x0 + 32, y1 - 15), fill=(216, 213, 181))
            a.line((x0 + 20, y0 + 335, x1 - 20, y0 + 335), fill=(81, 91, 74), width=8)
            a.rounded_rectangle((x1 - 62, y0 + 95, x1 - 43, y0 + 390),
                                radius=8, fill=(130, 127, 113), outline=(52, 48, 38), width=3)
            d.rectangle((x1 - 60, y0 + 98, x1 - 45, y0 + 385), fill=(180, 180, 255))
            # Tangent displacement near the right edge of the curved front.
            if vdraw is not None:
                vdraw.rectangle((x1 - 90, y0 + 100, x1 - 42, y0 + 390),
                                fill=(180, 128, 155))
            continue
        paint = (174, 155, 113) if tag in ("right", "left", "back") else (200, 185, 145)
        if tag in ("oven_door", "handle"):
            paint = (143, 128, 102)
        if tag == "top":
            paint = (77, 77, 72)
        a.rectangle((x0, y0, x1 - 1, y1 - 1), fill=paint)
        a.rectangle((x0 + 5, y0 + 5, x1 - 6, y1 - 6), outline=(92, 78, 65), width=5)
        if tag == "front":
            a.rectangle((x0 + 15, y0 + 45, x1 - 15, y0 + 110), fill=(79, 66, 54))
            for i in range(5):
                xx = x0 + 40 + i * 62
                a.ellipse((xx, y0 + 60, xx + 25, y0 + 84), fill=(188, 180, 146),
                          outline=(36, 31, 28), width=3)
            a.text((x0 + 115, y0 + 22), "EMBER RANGE", fill=(45, 44, 39))
        elif tag == "oven_door":
            a.rounded_rectangle((x0 + 28, y0 + 30, x1 - 28, y1 - 25),
                                radius=9, fill=(47, 48, 48), outline=(220, 201, 164), width=7)
            a.rectangle((x0 + 50, y0 + 45, x1 - 50, y1 - 48), fill=(60, 72, 74))
            # A recessed painted window: red means inward.
            d.rectangle((x0 + 50, y0 + 45, x1 - 50, y1 - 48), fill=(255, 180, 180))
        elif tag == "handle":
            a.rounded_rectangle((x0 + 7, y0 + 5, x1 - 7, y1 - 5), radius=8,
                                fill=(188, 173, 141), outline=(56, 55, 47), width=3)
            # Blue means outward.
            d.rectangle((x0 + 12, y0 + 8, x1 - 12, y1 - 8), fill=(170, 170, 255))
        elif tag == "top":
            for yy in (y0 + 88, y0 + 239):
                for xx in (x0 + 93, x0 + 278):
                    a.ellipse((xx - 53, yy - 48, xx + 53, yy + 48), fill=(31, 30, 29),
                              outline=(142, 140, 127), width=8)
                    a.ellipse((xx - 21, yy - 19, xx + 21, yy + 19), fill=(71, 70, 66))
        elif tag in ("left", "right"):
            for yy in range(y0 + 45, y0 + 110, 14):
                a.rectangle((x0 + 75, yy, x0 + 240, yy + 5), fill=(101, 87, 67))
    albedo.save(os.path.join(folder, spec["atlas"]["albedo"]))
    depth.save(os.path.join(folder, spec["atlas"]["depth"]))
    if vector is not None:
        vector.save(os.path.join(folder, spec["atlas"]["vector"]))
    print("Generated", folder)


if __name__ == "__main__":
    for sub in ("sheetfirst_range", "sheetfirst_curved"):
        make(sub)
