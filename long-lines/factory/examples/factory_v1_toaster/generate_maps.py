"""Make the FACTORY_V1 maps for the toaster from PR #3's painted toaster net.

    python3 long-lines/factory/examples/factory_v1_toaster/generate_maps.py

Art-stage adapter only (no geometry): runs PR #3's painter (pr3_source/; unchanged except the front label, now an invented name), then
writes albedo / signed_depth / part_ids / orthographic / concept in V1 form, driven by
asset.json and assembly.json:
- the six body faces keep PR #3's pixels (same rects, same 1600 px/m);
- component panels (knob cap + knurled wrap, lever face, four feet) are resampled from
  PR #3's component tiles to their physical size, since V1 has one pixel scale;
- part_ids use the V1 palette: each panel in its part's class, PR #3's own slot masks as
  cavity, and the knob/lever/feet footprints on the body faces in their classes.
"""
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "pr3_source")
PALETTE = {"background": (0, 0, 0), "main_body": (255, 255, 255), "moving_part": (34, 197, 94),
           "raised_hard": (239, 68, 68), "cavity": (59, 130, 246), "glass": (250, 204, 21),
           "soft": (249, 115, 22), "trim": (168, 85, 247), "support": (20, 184, 166)}
PR3_SLOT = (237, 48, 53)
TILE_FOR = {"knob_face": "knob", "lever_face": "lever"}   # V1 panel -> PR #3 component tile


def frac_rect(panel, r):
    """Panel-fraction rect (u0, u1, v0, v1) -> pixel box (inclusive) on the atlas."""
    x0, y0, x1, y1 = panel["rect_px"]
    return (x0 + r[0] * (x1 - x0), y1 - r[3] * (y1 - y0), x0 + r[1] * (x1 - x0) - 1, y1 - r[2] * (y1 - y0) - 1)


def footprint(panel, part, faces):
    """Where a separate part's base box covers a flat body panel, as panel fractions."""
    face = faces[part["panels"][0]]
    n = [face["u_axis"][1] * face["v_axis"][2] - face["u_axis"][2] * face["v_axis"][1],
         face["u_axis"][2] * face["v_axis"][0] - face["u_axis"][0] * face["v_axis"][2],
         face["u_axis"][0] * face["v_axis"][1] - face["u_axis"][1] * face["v_axis"][0]]
    k = max(range(3), key=lambda i: abs(n[i]))
    t = part["dimensions_m"][k]
    o, u, v = face["origin_m"], face["u_axis"], face["v_axis"]
    w, h = face["size_m"]
    corners = []
    for a in (0, 1):
        for b in (0, 1):
            c = [o[i] + u[i] * a * w + v[i] * b * h for i in range(3)]
            corners += [c, [c[i] - n[i] * t for i in range(3)]]
    P = panel
    pn = [P["u_axis"][1] * P["v_axis"][2] - P["u_axis"][2] * P["v_axis"][1],
          P["u_axis"][2] * P["v_axis"][0] - P["u_axis"][0] * P["v_axis"][2],
          P["u_axis"][0] * P["v_axis"][1] - P["u_axis"][1] * P["v_axis"][0]]
    rel = [[c[i] - P["origin_m"][i] for i in range(3)] for c in corners]
    dn = [sum(r[i] * pn[i] for i in range(3)) for r in rel]
    if min(dn) > 0.005 or max(dn) < -0.005:
        return None
    fu = [sum(r[i] * P["u_axis"][i] for i in range(3)) / P["size_m"][0] for r in rel]
    fv = [sum(r[i] * P["v_axis"][i] for i in range(3)) / P["size_m"][1] for r in rel]
    r = (min(fu), max(fu), min(fv), max(fv))
    if r[1] <= 0 or r[0] >= 1 or r[3] <= 0 or r[2] >= 1:
        return None
    return r


def knurl(size):
    w, h = size
    im = Image.new("RGB", size, (52, 52, 50))
    d = ImageDraw.Draw(im)
    for x in range(0, w, 4):
        d.line([(x, 0), (x, h)], fill=(30, 30, 29), width=1)
        d.line([(x + 2, 0), (x + 2, h)], fill=(88, 86, 80), width=1)
    d.rectangle((0, 0, w - 1, 2), fill=(150, 142, 120))
    return im


def main():
    subprocess.run([sys.executable, os.path.join(SRC, "generate_toaster_maps.py")], check=True)
    asset = json.load(open(os.path.join(HERE, "asset.json")))
    assembly = json.load(open(os.path.join(HERE, "assembly.json")))
    pr3 = json.load(open(os.path.join(SRC, "manifest.json")))
    art = Image.open(os.path.join(SRC, "toaster_albedo.png")).convert("RGB")
    depth_src = Image.open(os.path.join(SRC, "toaster_depth_rgb.png")).convert("RGB")
    parts_src = Image.open(os.path.join(SRC, "toaster_parts.png")).convert("RGB")
    size = tuple(asset["maps"]["canvas_px"])
    albedo = Image.new("RGB", size, (39, 39, 42))
    depth = Image.new("RGB", size, (255, 255, 255))
    ids = Image.new("RGB", size, PALETTE["background"])
    by_part = {p["id"]: p for p in assembly["parts"]}
    faces = {p["id"]: p for p in asset["panels"]}
    tiles = pr3["component_atlas"]
    for p in asset["panels"]:
        r = tuple(p["rect_px"])
        cls = by_part[p["part_id"]]["semantic_class"]
        if p["face"] != "custom":                       # body faces: PR #3 pixels as they are
            albedo.paste(art.crop(r), r[:2])
            depth.paste(depth_src.crop(r), r[:2])
        else:
            dims = (r[2] - r[0], r[3] - r[1])
            if p["id"] in TILE_FOR:
                tile = art.crop(tuple(tiles[TILE_FOR[p["id"]]]))
            elif p["id"].startswith("foot_"):
                tile = art.crop(tuple(tiles["foot"]))
            else:
                tile = knurl(dims)
            albedo.paste(tile.resize(dims, Image.Resampling.LANCZOS), r[:2])
        ImageDraw.Draw(ids).rectangle((r[0], r[1], r[2] - 1, r[3] - 1), fill=PALETTE[cls])
    # PR #3's own slot masks become the cavity class (independent evidence for the cavities).
    for p in asset["panels"]:
        if p["face"] == "custom":
            continue
        x0, y0, x1, y1 = p["rect_px"]
        sp, ip = parts_src.load(), ids.load()
        for X in range(x0, x1):
            for Y in range(y0, y1):
                if sp[X, Y] == PR3_SLOT:
                    ip[X, Y] = PALETTE["cavity"]
    # Separate parts' footprints on the body faces, in the part's class.
    d = ImageDraw.Draw(ids)
    for part in assembly["parts"]:
        if part["construction"] not in ("cylinder", "applique") or not part.get("parent"):
            continue
        for pid in by_part[part["parent"]]["panels"]:
            fr = footprint(faces[pid], part, faces)
            if fr is None:
                continue
            box = frac_rect(faces[pid], fr)
            col = PALETTE[part["semantic_class"]]
            (d.ellipse if part["construction"] == "cylinder" else d.rectangle)(box, fill=col)
    # Orthographic six-view sheet at the declared reference scale (feet drawn under the body).
    ppm_r = asset["maps"]["reference_pixel_scale"]
    ortho = Image.new("RGB", tuple(asset["maps"]["reference_canvas_px"]), (30, 31, 34))
    od = ImageDraw.Draw(ortho)
    body_h = round(0.18 * ppm_r)
    for view, box in asset["orthographic_views"].items():
        w, h = box[2] - box[0], box[3] - box[1]
        tile = albedo.crop(tuple(faces[view]["rect_px"]))
        if view in ("top", "bottom"):
            ortho.paste(tile.resize((w, h), Image.Resampling.LANCZOS), box[:2])
        else:
            ortho.paste(tile.resize((w, body_h), Image.Resampling.LANCZOS), box[:2])
            for fx in (0.06, 0.94):                      # feet strip: 18 mm
                cx = box[0] + fx * w
                od.rectangle((cx - 0.017 * ppm_r, box[1] + body_h, cx + 0.017 * ppm_r, box[3] - 1), fill=(54, 50, 47))
    concept = Image.open(os.path.join(SRC, "toaster_six_views.png")).convert("RGB")
    for name, img in (("albedo.png", albedo), ("signed_depth.png", depth), ("part_ids.png", ids),
                      ("orthographic.png", ortho), ("concept.png", concept)):
        img.save(os.path.join(HERE, name), optimize=True)
    print("FACTORY_V1_TOASTER_MAPS", HERE)


if __name__ == "__main__":
    main()
