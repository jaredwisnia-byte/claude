"""Pack a short kit description into a full FACTORY_V1 package (asset.json, assembly.json, maps).

    python3 long-lines/tools/v1_pack.py path/to/kit.json [--out DIR]

Writing a V1 package by hand costs ~70 values per closed box (6 panels with rects, sizes,
origins, axes, plus 12 welds). A kit says the same thing in one line per part: a box by
its min/max corners, a cylinder by its cap side, centre, radius and length, and for each
face where its pixels come from on a source sheet. This tool works out every panel's
placement, packs the panels into one atlas at a single pixels-per-metre (so the V1 scale
rule holds by construction), resamples the painted crops into it, re-encodes signed depth,
paints part IDs and drafts the welds. Nothing here is object-specific.

Kit format (metres; sheet rects are [x0, y0, x1, y1] pixels on the source image):
{
  "asset_id": "...", "brief": {...V1 brief...}, "pixels_per_metre": 160,
  "source": "sheet.png", "depth_range_m": {"inward": 0.01, "outward": 0.03},
  "background_rgb": [30, 30, 32],              # sheet gutter colour, filled from the fabric
  "parts": [
    {"id": "base", "kind": "box", "min": [x, y, z], "max": [x, y, z], "class": "main_body",
     "parent": "...", "faces": {"top": FACE, "front": FACE, "*": FACE}},
    {"id": "leg", "kind": "cylinder", "cap_face": "bottom", "centre": [x, y, z_of_cap],
     "radius": r, "length": h, "class": "support", "parent": "...", "cap": FACE, "wrap": FACE}
  ],
  "cavities": [ {V1 cavity, with "owner_part" and "face": "top" instead of opening_panel} ],
  "orthographic": {"source": "sheet.png", "views": {"front": [x0,y0,x1,y1], ...}},  # optional
  "concept": "sheet.png"
}
FACE = {"src": [x0, y0, x1, y1], "sub": [fx0, fy0, fx1, fy1] (optional fraction of src),
        "depth": [x0, y0, x1, y1] (optional rect on depth_source), "rotate": 0|90|180|270}
"""
import argparse
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from propkit.v1_geometry import PALETTE, suggest_welds  # noqa: E402

GUTTER = 6


def box_faces(lo, hi):
    """The six faces of an axis-aligned box: (name, origin, u, v, size); u x v points out."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    W, D, H = x1 - x0, y1 - y0, z1 - z0
    return [
        ("front", (x0, y0, z0), (1, 0, 0), (0, 0, 1), (W, H)),
        ("back", (x1, y1, z0), (-1, 0, 0), (0, 0, 1), (W, H)),
        ("left", (x0, y1, z0), (0, -1, 0), (0, 0, 1), (D, H)),
        ("right", (x1, y0, z0), (0, 1, 0), (0, 0, 1), (D, H)),
        ("top", (x0, y0, z1), (1, 0, 0), (0, 1, 0), (W, D)),
        ("bottom", (x0, y1, z0), (1, 0, 0), (0, -1, 0), (W, D)),
    ]


CAP = {   # cap side -> (u, v, normal axis index, sign)
    "bottom": ((1, 0, 0), (0, -1, 0), 2, -1), "top": ((1, 0, 0), (0, 1, 0), 2, 1),
    "front": ((1, 0, 0), (0, 0, 1), 1, -1), "back": ((-1, 0, 0), (0, 0, 1), 1, 1),
    "left": ((0, -1, 0), (0, 0, 1), 0, -1), "right": ((0, 1, 0), (0, 0, 1), 0, 1),
}


def rnd(v):
    return [round(float(x), 5) for x in v]


def fill_background(img, bg, tol=14):
    """Replace sheet-gutter pixels inside a crop with the nearest painted pixel."""
    px = img.load()
    w, h = img.size
    bad = [[max(abs(px[x, y][k] - bg[k]) for k in range(3)) <= tol for x in range(w)] for y in range(h)]
    if not any(any(r) for r in bad) or all(all(r) for r in bad):
        return img
    out = img.copy()
    po = out.load()
    for _ in range(max(w, h)):                     # grow painted pixels into the holes
        changed = False
        nxt = [r[:] for r in bad]
        for y in range(h):
            for x in range(w):
                if not bad[y][x]:
                    continue
                acc = [0, 0, 0]
                n = 0
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    X, Y = x + dx, y + dy
                    if 0 <= X < w and 0 <= Y < h and not bad[Y][X]:
                        c = po[X, Y]
                        acc = [acc[k] + c[k] for k in range(3)]
                        n += 1
                if n:
                    po[x, y] = tuple(a // n for a in acc)
                    nxt[y][x] = False
                    changed = True
        bad = nxt
        if not changed:
            break
    return out


def crop_face(img, face, bg=None):
    x0, y0, x1, y1 = face["src"]
    if "sub" in face:
        fx0, fy0, fx1, fy1 = face["sub"]
        w, h = x1 - x0, y1 - y0
        x0, y0, x1, y1 = x0 + fx0 * w, y0 + fy0 * h, x0 + fx1 * w, y0 + fy1 * h
    c = img.crop(tuple(int(round(v)) for v in (x0, y0, x1, y1)))
    if bg is not None:
        c = fill_background(c, bg)
    rot = face.get("rotate", 0)
    if rot:
        c = c.rotate(rot, expand=True)
    return c


def encode_depth(img):
    """Any red/white/blue-ish picture -> exact V1 signed-depth encoding."""
    img = img.convert("RGB").filter(ImageFilter.GaussianBlur(1.2))
    out = Image.new("RGB", img.size)
    pi, po = img.load(), out.load()
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            r, g, b = pi[x, y]
            s = max(-1.0, min(1.0, (b - r) / 255.0))
            if s >= 0:
                k = int(round(255 * (1 - s)))
                po[x, y] = (k, k, 255)
            else:
                k = int(round(255 * (1 + s)))
                po[x, y] = (255, k, k)
    return out


def shelf_pack(sizes, width):
    """sizes: list of (w, h) px. Returns positions and total height."""
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    pos = [None] * len(sizes)
    x = y = GUTTER
    row_h = 0
    for i in order:
        w, h = sizes[i]
        if x + w + GUTTER > width:
            x, y = GUTTER, y + row_h + GUTTER
            row_h = 0
        pos[i] = (x, y)
        x += w + GUTTER
        row_h = max(row_h, h)
    return pos, y + row_h + GUTTER


def pack(kit_path, out=None):
    kit_path = os.path.realpath(kit_path)
    kdir = os.path.dirname(kit_path)
    kit = json.load(open(kit_path, encoding="utf-8"))
    out = os.path.realpath(out or kdir)
    os.makedirs(out, exist_ok=True)
    ppm = float(kit["pixels_per_metre"])
    src = Image.open(os.path.join(kdir, kit["source"])).convert("RGB")
    dsrc = Image.open(os.path.join(kdir, kit.get("depth_source", kit["source"]))).convert("RGB")
    bg = tuple(kit["background_rgb"]) if kit.get("background_rgb") else None
    panels, parts, jobs = [], [], []
    for part in kit["parts"]:
        pid, cls = part["id"], part["class"]
        entry = {"id": pid, "panels": [], "closed_solid": True, "semantic_class": cls}
        if part.get("parent"):
            entry["parent"] = part["parent"]
        faces = part.get("faces", {})
        if part["kind"] == "box":
            entry["construction"] = "closed_box"
            for name, o, u, v, size in box_faces(part["min"], part["max"]):
                face = faces.get(name, faces.get("*"))
                if face is None:
                    raise SystemExit("%s: no source for face %s (give it or a '*' default)" % (pid, name))
                panels.append({"id": "%s_%s" % (pid, name), "part_id": pid, "face": name, "size_m": rnd(size),
                               "origin_m": rnd(o), "u_axis": list(u), "v_axis": list(v), "curve": {"type": "flat"}})
                entry["panels"].append(panels[-1]["id"])
                jobs.append(face)
        elif part["kind"] == "cylinder":
            entry["construction"] = "cylinder"
            u, v, k, sgn = CAP[part["cap_face"]]
            r, L = part["radius"], part["length"]
            c = part["centre"]
            o = [c[i] - r * u[i] - r * v[i] for i in range(3)]
            dims = [2 * r, 2 * r, 2 * r]
            dims[k] = L
            lo = [min(c[i] - r * abs(u[i]) - r * abs(v[i]), c[i]) for i in range(3)]
            lo[k] = c[k] if sgn < 0 else c[k] - L
            entry["anchor_m"], entry["dimensions_m"] = rnd(lo), rnd(dims)
            panels.append({"id": pid + "_cap", "part_id": pid, "face": "custom", "size_m": rnd((2 * r, 2 * r)),
                           "origin_m": rnd(o), "u_axis": list(u), "v_axis": list(v), "curve": {"type": "flat"}})
            entry["panels"].append(pid + "_cap")
            jobs.append(part["cap"])
            if part.get("wrap"):
                panels.append({"id": pid + "_wrap", "part_id": pid, "face": "custom", "size_m": rnd((2 * math.pi * r, L)),
                               "origin_m": rnd(o), "u_axis": list(u), "v_axis": list(v), "curve": {"type": "flat"},
                               "notes": "wrap-around side texture (u = circumference)"})
                entry["panels"].append(pid + "_wrap")
                jobs.append(part["wrap"])
        else:
            raise SystemExit("%s: kit kind %s not supported (box, cylinder)" % (pid, part["kind"]))
        parts.append(entry)
    sizes = [(max(4, int(round(p["size_m"][0] * ppm))), max(4, int(round(p["size_m"][1] * ppm)))) for p in panels]
    area = sum((w + GUTTER) * (h + GUTTER) for w, h in sizes)
    width = 256
    while width < 8192:
        _, height = shelf_pack(sizes, width)
        if height <= width:
            break
        width *= 2
    pos, height = shelf_pack(sizes, width)
    height = max(16, height)
    canvas = (width, height)
    albedo = Image.new("RGB", canvas, (24, 24, 26))
    depth = Image.new("RGB", canvas, (255, 255, 255))
    ids = Image.new("RGB", canvas, PALETTE["background"])
    di = ImageDraw.Draw(ids)
    cls_of = {p["id"]: p["semantic_class"] for p in parts}
    for p, (w, h), (x, y), face in zip(panels, sizes, pos, jobs):
        p["rect_px"] = [x, y, x + w, y + h]
        tile = crop_face(src, face, bg).resize((w, h), Image.Resampling.LANCZOS)
        albedo.paste(tile, (x, y))
        if face.get("depth"):
            dface = {"src": face["depth"], "rotate": face.get("rotate", 0)}
            if "sub" in face:
                dface["sub"] = face["sub"]
            depth.paste(encode_depth(crop_face(dsrc, dface).resize((w, h), Image.Resampling.BILINEAR)), (x, y))
        di.rectangle((x, y, x + w - 1, y + h - 1), fill=PALETTE[cls_of[p["part_id"]]])
    connections = []
    for part in parts:
        if part["construction"] == "closed_box":
            connections += suggest_welds({"maps": {"canvas_px": list(canvas)}, "panels": panels}, part["panels"])
    by_face = {(p["part_id"], p["face"]): p["id"] for p in panels}
    cavities = []
    for c in kit.get("cavities", []):
        c = dict(c)
        c["opening_panel"] = by_face[(c["owner_part"], c.pop("face"))]
        cavities.append(c)
    files = {"albedo": "albedo.png", "signed_depth": "signed_depth.png", "part_ids": "part_ids.png",
             "orthographic": "orthographic.png", "concept": "concept.png"}
    # orthographic reference: the source views resampled to the declared size at one scale
    d = kit["brief"]["dimensions_m"]
    W, D, H = d["width"], d["depth"], d["height"]
    rs = kit.get("orthographic", {}).get("pixels_per_metre", 200)
    view_m = {"front": (W, H), "back": (W, H), "left": (D, H), "right": (D, H), "top": (W, D), "bottom": (W, D)}
    vsz = {k: (int(round(a * rs)), int(round(b * rs))) for k, (a, b) in view_m.items()}
    vpos, vh = shelf_pack([vsz[k] for k in view_m], max(vsz["front"][0] * 2 + 3 * GUTTER, 64))
    ortho = Image.new("RGB", (max(vsz["front"][0] * 2 + 3 * GUTTER, 64), max(vh, 16)), (24, 24, 26))
    oviews = {}
    osrc = kit.get("orthographic", {})
    oimg = Image.open(os.path.join(kdir, osrc["source"])).convert("RGB") if osrc.get("source") else None
    for k, (x, y) in zip(view_m, vpos):
        w, h = vsz[k]
        oviews[k] = [x, y, x + w, y + h]
        if oimg is not None and k in osrc.get("views", {}):
            ortho.paste(oimg.crop(tuple(osrc["views"][k])).resize((w, h), Image.Resampling.LANCZOS), (x, y))
    asset = {"format": "FACTORY_V1", "asset_id": kit["asset_id"], "brief": kit["brief"],
             "maps": dict(files, canvas_px=list(canvas), pixels_per_metre=ppm, reference_pixel_scale=rs,
                          reference_canvas_px=list(ortho.size)),
             "depth_range_m": kit["depth_range_m"], "panels": panels, "orthographic_views": oviews,
             "assembly_file": "assembly.json",
             "source_notes": ["Generated by tools/v1_pack.py from %s." % os.path.basename(kit_path)] + kit.get("source_notes", [])}
    assembly = {"format": "FACTORY_V1_ASSEMBLY", "asset_id": kit["asset_id"], "expected_manifold": True,
                "parts": parts, "connections": connections, "cavities": cavities,
                "assembly_notes": ["Generated by tools/v1_pack.py; welds drafted from coincident edges."] + kit.get("assembly_notes", [])}
    for name, img in (("albedo.png", albedo), ("signed_depth.png", depth), ("part_ids.png", ids), ("orthographic.png", ortho)):
        img.save(os.path.join(out, name), optimize=True)
    Image.open(os.path.join(kdir, kit.get("concept", kit["source"]))).convert("RGB").save(os.path.join(out, "concept.png"))
    for name, data in (("asset.json", asset), ("assembly.json", assembly)):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
            f.write("\n")
    print("V1_PACK", out, "panels", len(panels), "parts", len(parts), "welds", len(connections), "atlas", canvas)
    return os.path.join(out, "asset.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kit")
    ap.add_argument("--out")
    a = ap.parse_args()
    pack(a.kit, a.out)
