"""Engineering drawings of a 3D model: orthographic views, visible edges solid, hidden dashed.

    blender -b --factory-startup -P tools/eng_draw.py -- MODEL.glb OUT_DIR [--ppm 400] [--views front,top,right]

Used as a test bench for tools/eng_read.py: models with a known shape are drawn the way an
engineer would draw them, so the reader can be measured against the real solid. Edges drawn:
creases (faces meeting at > 25 degrees), part outlines, and the silhouettes of curved
surfaces as seen from each view. Visibility by ray casting toward the viewer. Drafting
conventions: an edge of the overall shape is solid where visible and dashed where hidden; a
flush join between two parts (a seam) is drawn only where visible; buried contact edges
between touching parts are not drawn; a visible line wins over a hidden one in the same place.

Writes <view>.png per view and drawing.json (view list, overall size in metres; no world
coordinates: the reader only gets what is on paper plus the overall dimensions).
"""
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from PIL import Image, ImageDraw

# view: (direction the viewer looks, screen-right axis, screen-up axis), world x right, y back, z up
VIEWS = {
    "front": (Vector((0, 1, 0)), Vector((1, 0, 0)), Vector((0, 0, 1))),
    "back": (Vector((0, -1, 0)), Vector((-1, 0, 0)), Vector((0, 0, 1))),
    "right": (Vector((-1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))),
    "left": (Vector((1, 0, 0)), Vector((0, -1, 0)), Vector((0, 0, 1))),
    "top": (Vector((0, 0, -1)), Vector((1, 0, 0)), Vector((0, 1, 0))),
    "bottom": (Vector((0, 0, 1)), Vector((1, 0, 0)), Vector((0, -1, 0))),
}
CREASE_DEG = 25.0
PARTS = []      # per-part triangle lists (set by load) for the inside test
KIND = {}


def load(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    tris, edges = [], []          # edges: (a, b, n1, n2) world space; n2 None for open edges
    PARTS.clear()
    for ob in [o for o in bpy.data.objects if o.type == "MESH"]:
        start = len(tris)
        bm = bmesh.new()
        bm.from_mesh(ob.data)
        bm.transform(ob.matrix_world)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        bm.normal_update()
        for f in bm.faces:
            vs = [v.co.copy() for v in f.verts]
            for k in range(1, len(vs) - 1):
                tris.append((vs[0], vs[k], vs[k + 1]))
        for e in bm.edges:
            fs = e.link_faces
            a, b = e.verts[0].co.copy(), e.verts[1].co.copy()
            if len(fs) == 2:
                edges.append((a, b, fs[0].normal.copy(), fs[1].normal.copy()))
            else:
                edges.append((a, b, fs[0].normal.copy() if fs else None, None))
        bm.free()
        PARTS.append(tris[start:])
    return tris, edges


def draw(tris, edges, out, ppm=400, names=("front", "top", "right", "left", "back", "bottom")):
    verts = [v for t in tris for v in t]
    lo = Vector([min(v[i] for v in verts) for i in range(3)])
    hi = Vector([max(v[i] for v in verts) for i in range(3)])
    flat = [tuple(v) for v in verts]
    bvh = BVHTree.FromPolygons(flat, [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(tris))])
    cos_crease = math.cos(math.radians(CREASE_DEG))
    parts = [BVHTree.FromPolygons([tuple(v) for t in T for v in t], [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(T))])
             for T in PARTS] if PARTS else [bvh]

    def inside(p):
        for t in parts:
            h = t.find_nearest(p)
            if h[0] is not None and (h[0] - p).dot(h[1]) > 1e-9:
                return True
        return False

    def edge_kind(a, b, m=None):
        """'outer' (an edge of the overall shape), 'seam' (two parts meeting flush) or 'buried', at m."""
        m = (a + b) / 2 if m is None else m
        t = (b - a).normalized()
        e1 = t.orthogonal().normalized()
        e2 = t.cross(e1)
        pat = [inside(m + (e1 * math.cos((k + 0.5) * math.pi / 4) + e2 * math.sin((k + 0.5) * math.pi / 4)) * 1.5e-3) for k in range(8)]
        n_in = sum(pat)
        if n_in == 8 or n_in == 0:
            return "buried"
        if n_in == 4 and all(pat[k] != pat[(k + 4) % 8] for k in range(4)):
            return "seam"                     # inside on one half-plane only: a flat surface
        return "outer"
    margin = 30
    os.makedirs(out, exist_ok=True)
    for name in names:
        d, r, u = VIEWS[name]
        corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
        rs, us = [c.dot(r) for c in corners], [c.dot(u) for c in corners]
        r0, u1 = min(rs), max(us)
        W = int(math.ceil((max(rs) - r0) * ppm)) + 2 * margin
        H = int(math.ceil((u1 - min(us)) * ppm)) + 2 * margin
        vis_img, hid_img = Image.new("L", (W, H), 255), Image.new("L", (W, H), 255)
        dv, dh = ImageDraw.Draw(vis_img), ImageDraw.Draw(hid_img)
        to_px = lambda p: (margin + (p.dot(r) - r0) * ppm, margin + (u1 - p.dot(u)) * ppm)
        for a, b, n1, n2 in edges:
            if n2 is not None:
                crease = n1.dot(n2) < cos_crease
                contour = (n1.dot(d) > 1e-6) != (n2.dot(d) > 1e-6)     # silhouette of a curved surface
                if not (crease or contour):
                    continue
                if abs(n1.dot(d)) < 1e-6 and abs(n2.dot(d)) < 1e-6:
                    pass
            seg = b - a
            L = (seg - seg.dot(d) * d).length * ppm                    # projected length, px
            if L < 0.5:
                continue
            key = (tuple(a), tuple(b))
            if key not in KIND:     # classified along the edge: one edge can be free in part, buried elsewhere
                m_ = max(2, int((b - a).length / 0.01))
                KIND[key] = [edge_kind(a, b, a + (b - a) * ((j + 0.5) / m_)) for j in range(m_)]
            kinds = KIND[key]
            if all(k_ == "buried" for k_ in kinds):
                continue
            n = max(2, int(L / 1.5))
            vis = []
            for k in range(n + 1):
                p = a + seg * (k / n)
                hit = bvh.ray_cast(p - d * 2e-4, -d, 100.0)
                vis.append(hit[0] is None)
            # draw: visible runs solid, hidden runs dashed (8 px on, 5 px off along the edge)
            pa, pb = to_px(a), to_px(b)
            for k in range(n):
                q0 = (pa[0] + (pb[0] - pa[0]) * k / n, pa[1] + (pb[1] - pa[1]) * k / n)
                q1 = (pa[0] + (pb[0] - pa[0]) * (k + 1) / n, pa[1] + (pb[1] - pa[1]) * (k + 1) / n)
                kind = kinds[min(len(kinds) - 1, int((k + 0.5) / n * len(kinds)))]
                if kind == "buried":
                    continue
                if vis[k] and vis[k + 1]:
                    dv.line([q0, q1], fill=0, width=2)
                elif kind == "outer":
                    # dash phase from the page position, so hidden lines in the same place coincide
                    mx, my = (q0[0] + q1[0]) / 2, (q0[1] + q1[1]) / 2
                    t = mx if abs(q1[0] - q0[0]) >= abs(q1[1] - q0[1]) else my
                    if (t % 13.0) < 8.0:
                        dh.line([q0, q1], fill=0, width=2)
        # Drafting rule: a visible line takes precedence over a hidden line in the same place.
        from PIL import ImageChops, ImageFilter
        keep_out = vis_img.filter(ImageFilter.MinFilter(5))            # visible lines, dilated
        hid_img = ImageChops.lighter(hid_img, ImageChops.invert(keep_out))
        ImageChops.darker(vis_img, hid_img).save(os.path.join(out, name + ".png"))
    size = hi - lo
    info = {"views": list(names), "dimensions_m": {"width": round(size.x, 4), "depth": round(size.y, 4),
                                                    "height": round(size.z, 4)},
            "convention": "third-angle; solid = visible edge, dashed = hidden edge; front looks toward +depth"}
    json.dump(info, open(os.path.join(out, "drawing.json"), "w"), indent=1)
    return info


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:]
    ppm = float(a[a.index("--ppm") + 1]) if "--ppm" in a else 400
    names = a[a.index("--views") + 1].split(",") if "--views" in a else list(VIEWS)
    tris, edges = load(a[0])
    print("ENG_DRAW", json.dumps(draw(tris, edges, a[1], ppm, names)))
