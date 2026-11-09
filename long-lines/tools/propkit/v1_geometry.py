"""FACTORY_V1 assembly interpreter: package -> meshes (pure Python + Pillow, no Blender).

Reads asset.json + assembly.json + the registered maps and turns the *schema* into closed
meshes. Nothing here knows what the object is: a TV, a toaster and a cabinet go through the
same code, driven only by panels, parts, connections and cavities.

What each schema element becomes
- panel: a grid over its (u, v) rectangle in metres (arc length for cylinder panels). Vertex
  UVs come straight from the panel's rect_px (half-texel inset), so the authored UV net is the
  final UV net: nothing is unwrapped or packed again.
- signed_depth: displacement along the panel's local normal (red in, white 0, blue out),
  forced to zero on panel borders so welded seams cannot tear. Suppressed under separately
  built parts that sit on the panel (the part supplies that geometry) and inside cavities.
- connections (weld/fold): the two edges get identical subdivisions (union-find over panel
  axes, with direction flips) and their vertices are merged, so the panels become one mesh.
- closed_box / panel_shell: a welded group of panels. closed_box must come out closed.
- cavities (rect): grid cells inside the opening are removed and replaced with inward walls
  and a floor (or no floor when closed_bottom is false). Walls/floor take their UVs from the
  painted opening, folded in by the cavity depth, so texel density is kept where it fits.
- applique: its face panel as a sheet; with closed_solid, extruded back by its thickness.
- cylinder: a disc on its face panel extruded back along the panel normal; a second panel,
  if given, is the wrap-around wall texture, otherwise the wall reuses the cap's rim.

Anything the schema allows but this module does not build (sweep, lathe, cavity_liner,
ellipse cavities, rotations, hinge/fasten geometry) is listed in report["unsupported"]; it
is never approximated silently.
"""
import json
import math
import os
from collections import Counter

from PIL import Image

PALETTE = {
    "background": (0, 0, 0), "main_body": (255, 255, 255), "moving_part": (34, 197, 94),
    "raised_hard": (239, 68, 68), "cavity": (59, 130, 246), "glass": (250, 204, 21),
    "soft": (249, 115, 22), "trim": (168, 85, 247), "support": (20, 184, 166),
}
WELD_OPS = ("weld", "fold")
SETTINGS = {
    "max_cells": 64,             # per panel axis, where the panel carries relief
    "coarse_step_m": 0.05,       # flat panels: the builder dissolves these later anyway
    "min_cell_texels": 3,
    "relief_zero_m": 0.0005,     # below this a sampled displacement counts as flat
    "relief_tolerance_m": 0.0005,  # max deviation of the grid from the painted relief
    "footprint_margin_texels": 2,
    "cavity_band_texels": 4,
    "contact_distance_m": 0.005,
    "cylinder_rings": 6,
    "cylinder_min_segments": 24,
    "registration_iou": 0.85,
    "registration_offset_px": 6,
}


# ---------- small vector helpers ----------
def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def norm(a): return math.sqrt(dot(a, a))


# ---------- package ----------
def load_package(asset_path):
    asset_path = os.path.realpath(asset_path)
    folder = os.path.dirname(asset_path)
    asset = json.load(open(asset_path, encoding="utf-8"))
    assembly = json.load(open(os.path.join(folder, asset["assembly_file"]), encoding="utf-8"))
    maps = asset["maps"]
    imgs = {}
    for key in ("signed_depth", "part_ids"):
        with Image.open(os.path.join(folder, maps[key])) as im:
            imgs[key] = im.convert("RGB").copy()
    return {"asset": asset, "assembly": assembly, "folder": folder,
            "albedo": os.path.join(folder, maps["albedo"]), "maps": imgs,
            "canvas": tuple(maps["canvas_px"]), "ppm": float(maps["pixels_per_metre"])}


class Panel:
    """One authored UV rectangle with its physical placement."""

    def __init__(self, spec, canvas, depth_img, depth_range):
        self.spec = spec
        self.id = spec["id"]
        self.rect = spec["rect_px"]
        self.w, self.h = (float(x) for x in spec["size_m"])
        self.o = tuple(float(x) for x in spec["origin_m"])
        self.u = tuple(float(x) for x in spec["u_axis"])
        self.v = tuple(float(x) for x in spec["v_axis"])
        self.n = cross(self.u, self.v)            # u x v is the outward normal by convention
        self.curve = spec.get("curve", {"type": "flat"})
        self.canvas = canvas
        self.depth = depth_img
        self.inward = float(depth_range.get("inward", 0.0))
        self.outward = float(depth_range.get("outward", 0.0))
        self.suppress = []                         # fraction rects where relief is off
        self.cavities = []                         # fraction rects owned by cavities (+band)
        x0, y0, x1, y1 = self.rect
        self.ppm_u = (x1 - x0) / self.w
        self.ppm_v = (y1 - y0) / self.h

    @property
    def flat(self):
        return self.curve.get("type", "flat") == "flat"

    def length(self, axis):
        return self.w if axis == "u" else self.h

    def point(self, fu, fv, offset=0.0):
        """Position at panel fractions (fu, fv), displaced `offset` m along the local normal."""
        if self.flat:
            p = add(self.o, add(mul(self.u, fu * self.w), mul(self.v, fv * self.h)))
            return add(p, mul(self.n, offset))
        r = float(self.curve["radius_m"])
        if self.curve.get("axis", "u") == "u":
            t = fu * self.w / r
            p = add(self.o, add(mul(self.u, r * math.sin(t)), add(mul(self.n, r * (1 - math.cos(t))), mul(self.v, fv * self.h))))
            d = sub(mul(self.n, math.cos(t)), mul(self.u, math.sin(t)))
        else:
            t = fv * self.h / r
            p = add(self.o, add(mul(self.v, r * math.sin(t)), add(mul(self.n, r * (1 - math.cos(t))), mul(self.u, fu * self.w))))
            d = sub(mul(self.n, math.cos(t)), mul(self.v, math.sin(t)))
        return add(p, mul(d, offset))

    def uv(self, fu, fv):
        """Atlas UV (origin bottom-left) for panel fractions; v0 is the rect's bottom row."""
        x0, y0, x1, y1 = self.rect
        W, H = self.canvas
        px = x0 + 0.5 + fu * (x1 - x0 - 1)
        py = y1 - 0.5 - fv * (y1 - y0 - 1)
        return (px / W, 1.0 - py / H)

    def texel(self, fu, fv):
        x0, y0, x1, y1 = self.rect
        return (x0 + 0.5 + fu * (x1 - x0 - 1), y1 - 0.5 - fv * (y1 - y0 - 1))

    def sample(self, fu, fv):
        """Signed displacement in metres from the depth map (bilinear, clamped to the rect)."""
        x0, y0, x1, y1 = self.rect
        px, py = self.texel(min(max(fu, 0), 1), min(max(fv, 0), 1))
        px = min(max(px, x0 + 0.5), x1 - 1.0)
        py = min(max(py, y0 + 0.5), y1 - 1.0)
        img = self.depth
        X0, Y0 = int(math.floor(px - 0.5)), int(math.floor(py - 0.5))
        fx, fy = px - 0.5 - X0, py - 0.5 - Y0
        acc = 0.0
        pix = img.load()
        for dx, dy, wgt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
            X = min(max(X0 + dx, x0), x1 - 1)
            Y = min(max(Y0 + dy, y0), y1 - 1)
            r, g, b = pix[X, Y]
            acc += wgt * (b - r) / 255.0
        return (self.outward if acc >= 0 else self.inward) * acc

    def relief(self, fu, fv, zero_border=True):
        """Displacement the mesh uses: zero on borders and under parts, cavity-aware."""
        eps = 1e-9
        if zero_border and (fu < eps or fu > 1 - eps or fv < eps or fv > 1 - eps):
            return 0.0
        for a, b, c, d in self.suppress:
            if a <= fu <= b and c <= fv <= d:
                return 0.0
        for a, b, c, d in self.cavities:           # band round an opening: continue the
            if a - eps <= fu <= b + eps and c - eps <= fv <= d + eps:   # surrounding surface
                du = min(fu - a, b - fu)
                dv = min(fv - c, d - fv)
                if du <= dv:
                    fu = a - 1e-4 if fu - a < b - fu else b + 1e-4
                else:
                    fv = c - 1e-4 if fv - c < d - fv else d + 1e-4
                if fu <= 0 or fu >= 1 or fv <= 0 or fv >= 1:
                    return 0.0
                break
        return self.sample(fu, fv)

    def has_relief(self):
        if getattr(self, "_relief", None) is None:
            self._relief = self._has_relief()
        return self._relief

    def _has_relief(self):
        x0, y0, x1, y1 = self.rect
        nx, ny = max(2, (x1 - x0) // 3), max(2, (y1 - y0) // 3)
        for i in range(1, nx):
            for j in range(1, ny):
                if abs(self.relief(i / nx, j / ny)) >= SETTINGS["relief_zero_m"]:
                    return True
        return False


# ---------- union-find ----------
class AxisClasses:
    """Union-find over (panel, axis) with a direction flip, so welded edges share breaks."""

    def __init__(self):
        self.parent, self.flip, self.breaks = {}, {}, {}

    def add(self, key, fracs):
        if key not in self.parent:
            self.parent[key], self.flip[key], self.breaks[key] = key, False, set()
        r, f = self.find(key)
        self.breaks[r].update(round(1 - x if f else x, 9) for x in fracs)

    def find(self, key):
        flip = False
        while self.parent[key] != key:
            flip ^= self.flip[key]
            key = self.parent[key]
        return key, flip

    def union(self, a, b, flipped):
        ra, fa = self.find(a)
        rb, fb = self.find(b)
        if ra == rb:
            return (fa ^ fb) == flipped
        rel = fa ^ fb ^ flipped                     # orientation of rb relative to ra
        self.parent[rb], self.flip[rb] = ra, rel
        self.breaks[ra].update(round(1 - x if rel else x, 9) for x in self.breaks.pop(rb))
        return True

    def fractions(self, key):
        r, f = self.find(key)
        vals = sorted(set(round(1 - x if f else x, 9) for x in self.breaks[r]) | {0.0, 1.0})
        out = []
        for x in vals:
            if not out or x - out[-1] > 1e-6:
                out.append(x)
        out[-1] = 1.0
        return out


def uniform(n):
    return [i / n for i in range(n + 1)]


def clean(values):
    """Sorted fractions in [0, 1] with near-duplicates removed and exact end points."""
    out = []
    for x in sorted(min(max(round(v, 9), 0.0), 1.0) for v in set(values) | {0.0, 1.0}):
        if not out or x - out[-1] > 1e-6:
            out.append(x)
    out[0], out[-1] = 0.0, 1.0
    return out


# ---------- mesh accumulation ----------
class Mesh:
    def __init__(self, part):
        self.part = part
        self.verts, self.faces, self.uvs = [], [], []

    def vert(self, co):
        self.verts.append(tuple(float(c) for c in co))
        return len(self.verts) - 1

    def face(self, ids, uvs):
        self.faces.append(tuple(ids))
        self.uvs.append(tuple(uvs))

    def compact(self, alias=None):
        """Apply a vertex alias map (welds), drop unused vertices, drop degenerate faces."""
        alias = alias or {}

        def canon(i):
            while i in alias:
                i = alias[i]
            return i
        faces, uvs, used = [], [], {}
        for f, uv in zip(self.faces, self.uvs):
            ids = [canon(i) for i in f]
            if len(set(ids)) < len(ids):
                raise ValueError("weld collapsed a face in part %s" % self.part)
            faces.append(ids)
            uvs.append(uv)
            for i in ids:
                used.setdefault(i, len(used))
        verts = [None] * len(used)
        for old, new in used.items():
            verts[new] = self.verts[old]
        self.verts = verts
        self.faces = [tuple(used[i] for i in f) for f in faces]
        self.uvs = uvs

    def edge_stats(self):
        count = Counter()
        directed = Counter()
        for f in self.faces:
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                count[(min(a, b), max(a, b))] += 1
                directed[(a, b)] += 1
        boundary = sum(1 for c in count.values() if c == 1)
        over = sum(1 for c in count.values() if c > 2)
        flipped = sum(1 for (a, b), c in directed.items() if c > 1)
        return {"boundary_edges": boundary, "nonmanifold_edges": over, "inconsistent_winding": flipped}

    def triangles(self):
        return sum(len(f) - 2 for f in self.faces)

    def bbox(self):
        xs = list(zip(*self.verts))
        return [min(c) for c in xs], [max(c) for c in xs]


def inset_map(lo, hi, d):
    """Map [lo, hi] onto [lo + d, hi - d] (fold-in UVs for walls, floors and backs)."""
    span = hi - lo
    d = min(d, 0.4 * span)
    k = (span - 2 * d) / span
    return (lambda f: lo + d + (f - lo) * k), d


# ---------- the interpreter ----------
class Interpreter:
    def __init__(self, pkg, settings=None):
        self.pkg = pkg
        self.S = dict(SETTINGS, **(settings or {}))
        a, s = pkg["asset"], pkg["assembly"]
        self.panels = {p["id"]: Panel(p, pkg["canvas"], pkg["maps"]["signed_depth"], a["depth_range_m"]) for p in a["panels"]}
        self.parts = {p["id"]: p for p in s["parts"]}
        self.report = {"operations_used": Counter(), "unsupported": [], "warnings": [], "errors": [],
                       "registration": [], "parts": {}, "cavities": [], "notes": []}
        self.meshes = {}

    # -- helpers
    def unsupported(self, what, item, why):
        self.report["unsupported"].append({"what": what, "item": item, "why": why})

    def part_of_panel(self, pid):
        return self.panels[pid].spec["part_id"]

    def base_box(self, part):
        """Corners of a separate part's base solid (face panel + thickness), or None."""
        if not part["panels"] or part["construction"] not in ("applique", "cylinder"):
            return None
        f = self.panels[part["panels"][0]]
        t = self.thickness(part, f) or 0.0
        cs = [f.point(a, b) for a in (0, 1) for b in (0, 1)]
        return cs + [sub(c, mul(f.n, t)) for c in cs]

    def thickness(self, part, face):
        dims = part.get("dimensions_m")
        if not dims:
            return None
        n = face.n
        k = max(range(3), key=lambda i: abs(n[i]))
        if abs(abs(n[k]) - 1) > 1e-3:
            return None
        return float(dims[k])

    def footprint(self, P, box):
        """Fraction rect a separate part covers on flat panel P, if it sits on that surface.

        Sits on = its base box reaches the panel plane to within contact distance from either
        side (a knob on the face, glass inside a recess) and its projection overlaps the panel."""
        c = self.S["contact_distance_m"]
        rel = [sub(x, P.o) for x in box]
        dn = [dot(x, P.n) for x in rel]
        if min(dn) > c or max(dn) < -c:
            return None
        fu = [dot(x, P.u) / P.w for x in rel]
        fv = [dot(x, P.v) / P.h for x in rel]
        r = (min(fu), max(fu), min(fv), max(fv))
        if r[1] <= 0 or r[0] >= 1 or r[3] <= 0 or r[2] >= 1:
            return None
        return r

    def nominal_points(self, part):
        """Corners of the part before painted relief (what a tape measure on the plan gives)."""
        box = self.base_box(part)
        if box:
            return box
        pts = []
        for pid in part["panels"]:
            p = self.panels[pid]
            steps = 16 if not p.flat else 1
            pts += [p.point(a / steps, b) for a in range(steps + 1) for b in (0, 1)]
        return pts

    # -- preparation: suppression masks and cavity bands
    def prepare(self):
        S = self.S
        welded_parts = [p for p in self.parts.values() if p["construction"] in ("closed_box", "panel_shell")]
        for cav in self.pkg["assembly"]["cavities"]:
            p = self.panels.get(cav["opening_panel"])
            if p is None:
                continue
            cu, cv = cav["centre_uv"]
            cw, ch = cav["size_m"]
            band_u = S["cavity_band_texels"] / p.ppm_u / p.w
            band_v = S["cavity_band_texels"] / p.ppm_v / p.h
            p.cavities.append((cu - cw / 2 / p.w - band_u, cu + cw / 2 / p.w + band_u,
                               cv - ch / 2 / p.h - band_v, cv + ch / 2 / p.h + band_v))
        for host in welded_parts:
            for pid in host["panels"]:
                P = self.panels[pid]
                if not P.flat:
                    continue
                for other in self.parts.values():
                    if other["id"] == host["id"]:
                        continue
                    box = self.base_box(other)
                    if not box:
                        continue
                    r = self.footprint(P, box)
                    if r is None:
                        continue
                    mu = S["footprint_margin_texels"] / P.ppm_u / P.w
                    mv = S["footprint_margin_texels"] / P.ppm_v / P.h
                    P.suppress.append((r[0] - mu, r[1] + mu, r[2] - mv, r[3] + mv))
                    self.report["operations_used"]["relief_suppressed_under_part"] += 1
                    self.report["notes"].append("relief on %s suppressed under part %s" % (pid, other["id"]))

    # -- welded groups (closed_box, panel_shell)
    def build_welded(self, part):
        mesh, alias, _ = self.welded_grid(part)
        mesh.compact(alias)
        return mesh

    def cells_for(self, p, ax):
        """Cells along one panel axis: as few as keep painted relief within tolerance.

        The relief is sampled every 1.5 texels on 24 lines; a candidate count is accepted when
        piecewise-linear interpolation between its grid lines stays within relief_tolerance_m
        everywhere visible (cavity openings and surfaces under separate parts are ignored).
        Flat panels get coarse cells (the Blender stage dissolves them); curved axes keep at
        most 7.5 degrees per cell."""
        S = self.S
        L = p.length(ax)
        ppm = p.ppm_u if ax == "u" else p.ppm_v
        n_curve = 1
        if not p.flat and p.curve.get("axis", "u") == ax:
            n_curve = math.ceil(L / abs(float(p.curve["radius_m"])) / math.radians(7.5))
        if not p.has_relief():
            return max(n_curve, math.ceil(L / S["coarse_step_m"]))
        nmax = max(1, min(S["max_cells"], int(L * ppm / S["min_cell_texels"])))
        M = max(8, int(L * ppm / 1.5))
        lines = [(t + 0.5) / 24 for t in range(24)]
        hidden = [(a, b, c, d) for a, b, c, d in p.cavities + p.suppress]

        def uv(x, o):
            return (x, o) if ax == "u" else (o, x)

        def visible(x, o):
            q = uv(x, o)
            return not any(a <= q[0] <= b and c <= q[1] <= d for a, b, c, d in hidden)
        f = lambda x, o: p.relief(*uv(x, o))
        prof = [[(j / M, f(j / M, o), visible(j / M, o)) for j in range(M + 1)] for o in lines]
        n, self._last_fit = nmax, None
        for cand in (2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128):
            if cand >= nmax:
                break
            errs = []
            for o, row in zip(lines, prof):
                nodes = [f(k / cand, o) for k in range(cand + 1)]
                for x, val, vis in row:
                    if not vis:
                        continue
                    k = min(int(x * cand), cand - 1)
                    t = x * cand - k
                    errs.append(abs(val - (nodes[k] * (1 - t) + nodes[k + 1] * t)))
            if (max(errs) if errs else 0.0) <= S["relief_tolerance_m"]:
                n = cand
                break
        return max(n, n_curve)

    def welded_grid(self, part):
        """Grid every panel of the part, weld the declared edges, cut the cavities.

        Each panel keeps its own interior grid; only the vertices along a welded edge are
        shared (the union of both sides' breaks), so a finely relieved panel does not force
        its neighbours' interiors to the same density. Border cells become n-gons that
        include the extra edge vertices; the exporter triangulates them.
        Returns (mesh, alias, grids); the mesh is not compacted yet so callers can add to it."""
        ids = part["panels"]
        P = {i: self.panels[i] for i in ids}
        cavs = [c for c in self.pkg["assembly"]["cavities"] if c["opening_panel"] in P]
        own = {}
        for i, p in P.items():
            fu, fv = set(uniform(self.cells_for(p, "u"))), set(uniform(self.cells_for(p, "v")))
            for c in cavs:
                if c["opening_panel"] == i:
                    cu, cv = c["centre_uv"]
                    cw, ch = c["size_m"]
                    fu.update((cu - cw / 2 / p.w, cu + cw / 2 / p.w))
                    fv.update((cv - ch / 2 / p.h, cv + ch / 2 / p.h))
            own[i] = (clean(fu), clean(fv))
        edges = AxisClasses()
        for i in P:
            fu, fv = own[i]
            for e in ("u0", "u1"):
                edges.add((i, e), fv)
            for e in ("v0", "v1"):
                edges.add((i, e), fu)
        conns = []
        for j in self.pkg["assembly"]["connections"]:
            if j["a"] not in P and j["b"] not in P:
                continue
            if j["a"] not in P or j["b"] not in P:
                self.unsupported("connection", "%s/%s" % (j["a"], j["b"]), "joins panels of different parts (%s)" % j["operation"])
                continue
            if j["operation"] not in WELD_OPS:
                self.unsupported("connection", "%s/%s" % (j["a"], j["b"]), "operation %s has no geometry in this builder" % j["operation"])
                continue
            pa, pb = P[j["a"]], P[j["b"]]
            axa = "v" if j["a_edge"] in ("u0", "u1") else "u"
            axb = "v" if j["b_edge"] in ("u0", "u1") else "u"
            a0, a1 = self.edge_ends(pa, j["a_edge"])
            b0, b1 = self.edge_ends(pb, j["b_edge"])
            flip = norm(sub(a0, b1)) + norm(sub(a1, b0)) < norm(sub(a0, b0)) + norm(sub(a1, b1))
            if abs(pa.length(axa) - pb.length(axb)) > j.get("tolerance_m", 0.003):
                self.report["errors"].append("connection %s.%s/%s.%s joins edges of different length (%.4f vs %.4f m)" % (
                    j["a"], j["a_edge"], j["b"], j["b_edge"], pa.length(axa), pb.length(axb)))
                continue
            if not edges.union((pa.id, j["a_edge"]), (pb.id, j["b_edge"]), flip):
                self.report["errors"].append("connections give %s/%s contradictory edge directions" % (j["a"], j["b"]))
            conns.append((j, flip))
            self.report["operations_used"][j["operation"]] += 1
        mesh = Mesh(part["id"])
        vat = {i: {} for i in P}

        def V(i, a, b):
            key = (round(a, 9), round(b, 9))
            d = vat[i]
            if key not in d:
                p = P[i]
                d[key] = mesh.vert(p.point(a, b, p.relief(a, b)))
            return d[key]
        E = {(i, e): edges.fractions((i, e)) for i in P for e in ("u0", "u1", "v0", "v1")}
        for i, p in P.items():
            if p.has_relief():
                self.report["operations_used"]["signed_depth_relief"] += 1
        # weld: vertices along each declared edge pair are merged
        alias = {}

        def root(x):
            while x in alias:
                x = alias[x]
            return x
        worst = 0.0
        for j, flip in conns:
            ea = [V(j["a"], *xy) for xy in self.edge_params(j["a_edge"], E[(j["a"], j["a_edge"])])]
            eb = [V(j["b"], *xy) for xy in self.edge_params(j["b_edge"], E[(j["b"], j["b_edge"])])]
            if flip:
                eb = eb[::-1]
            if len(ea) != len(eb):
                self.report["errors"].append("weld %s/%s: edge vertex counts differ" % (j["a"], j["b"]))
                continue
            tol = j.get("tolerance_m", 0.003)
            for x, y in zip(ea, eb):
                gap = norm(sub(mesh.verts[x], mesh.verts[y]))
                worst = max(worst, gap)
                if gap > tol:
                    self.report["errors"].append("weld %s/%s gap %.4f m > %.4f" % (j["a"], j["b"], gap, tol))
                    break
                rx, ry = root(x), root(y)
                if rx != ry:
                    alias[ry] = rx
        # faces
        grids = {}
        eps = 1e-7
        for i, p in P.items():
            fu, fv = own[i]
            nu, nv = len(fu) - 1, len(fv) - 1
            G = [[V(i, a, b) for b in fv] for a in fu]
            grids[i] = (G, (fu, fv))
            holes = []
            for c in cavs:
                if c["opening_panel"] != i:
                    continue
                if c["shape"] != "rect":
                    self.unsupported("cavity", c["id"], "shape %s is not built yet (rect only)" % c["shape"])
                    continue
                cu, cv = c["centre_uv"]
                cw, ch = c["size_m"]
                holes.append((c, (cu - cw / 2 / p.w, cu + cw / 2 / p.w, cv - ch / 2 / p.h, cv + ch / 2 / p.h)))
            for a in range(nu):
                for b in range(nv):
                    mu, mv = (fu[a] + fu[a + 1]) / 2, (fv[b] + fv[b + 1]) / 2
                    if any(r[0] < mu < r[1] and r[2] < mv < r[3] for _, r in holes):
                        continue
                    pts = [(fu[a], fv[b])]
                    if b == 0:
                        pts += [(x, 0.0) for x in E[(i, "v0")] if fu[a] + eps < x < fu[a + 1] - eps]
                    pts.append((fu[a + 1], fv[b]))
                    if a + 1 == nu:
                        pts += [(1.0, y) for y in E[(i, "u1")] if fv[b] + eps < y < fv[b + 1] - eps]
                    pts.append((fu[a + 1], fv[b + 1]))
                    if b + 1 == nv:
                        pts += [(x, 1.0) for x in reversed(E[(i, "v1")]) if fu[a] + eps < x < fu[a + 1] - eps]
                    pts.append((fu[a], fv[b + 1]))
                    if a == 0:
                        pts += [(0.0, y) for y in reversed(E[(i, "u0")]) if fv[b] + eps < y < fv[b + 1] - eps]
                    mesh.face([V(i, x, y) for x, y in pts], [p.uv(x, y) for x, y in pts])
            for c, r in holes:
                self.build_cavity(mesh, p, G, fu, fv, c, r)
        self.report["notes"].append("part %s: worst weld gap %.5f m" % (part["id"], worst))
        return mesh, alias, grids

    @staticmethod
    def edge_params(edge, fr):
        if edge == "u0":
            return [(0.0, y) for y in fr]
        if edge == "u1":
            return [(1.0, y) for y in fr]
        if edge == "v0":
            return [(x, 0.0) for x in fr]
        return [(x, 1.0) for x in fr]

    @staticmethod
    def edge_ends(p, edge):
        if edge == "u0":
            return p.point(0, 0), p.point(0, 1)
        if edge == "u1":
            return p.point(1, 0), p.point(1, 1)
        if edge == "v0":
            return p.point(0, 0), p.point(1, 0)
        return p.point(0, 1), p.point(1, 1)

    def build_cavity(self, mesh, p, G, fu, fv, cav, rect):
        ua, ub, va, vb = rect
        ia, ib = fu.index(min(fu, key=lambda x: abs(x - ua))), fu.index(min(fu, key=lambda x: abs(x - ub)))
        ja, jb = fv.index(min(fv, key=lambda x: abs(x - va))), fv.index(min(fv, key=lambda x: abs(x - vb)))
        depth = float(cav["depth_m"])
        map_u, du = inset_map(ua, ub, depth / p.w)
        map_v, dv = inset_map(va, vb, depth / p.h)
        squeeze = max(depth / p.w / du if du else 1, depth / p.h / dv if dv else 1)
        bottom = {}
        for a in range(ia, ib + 1):
            for b in range(ja, jb + 1):
                bottom[(a, b)] = mesh.vert(p.point(fu[a], fv[b], -depth))
        buv = lambda a, b: p.uv(map_u(fu[a]), map_v(fv[b]))
        loop = ([(a, ja) for a in range(ia, ib)] + [(ib, b) for b in range(ja, jb)] +
                [(a, jb) for a in range(ib, ia, -1)] + [(ia, b) for b in range(jb, ja, -1)])
        for k in range(len(loop)):
            s, e = loop[k], loop[(k + 1) % len(loop)]
            mesh.face([G[s[0]][s[1]], G[e[0]][e[1]], bottom[e], bottom[s]],
                      [p.uv(fu[s[0]], fv[s[1]]), p.uv(fu[e[0]], fv[e[1]]), buv(*e), buv(*s)])
        if cav["closed_bottom"]:
            for a in range(ia, ib):
                for b in range(ja, jb):
                    cs = [(a, b), (a + 1, b), (a + 1, b + 1), (a, b + 1)]
                    mesh.face([bottom[c] for c in cs], [buv(*c) for c in cs])
        self.report["operations_used"]["cavity_rect"] += 1
        self.report["cavities"].append({
            "id": cav["id"], "owner_part": cav["owner_part"], "panel": p.id, "depth_m": depth, "closed_bottom": cav["closed_bottom"],
            "size_m": cav["size_m"], "wall_uv_squeeze": round(squeeze, 2),
            "centre_m": [round(x, 5) for x in p.point(sum(rect[:2]) / 2, sum(rect[2:]) / 2)],
            "normal": [round(x, 6) for x in p.n]})
        if squeeze > 1.5:
            self.report["warnings"].append("cavity %s: walls deeper than the painted opening allows; wall texels squeezed %.1fx" % (cav["id"], squeeze))

    # -- single-panel parts
    def build_applique(self, part):
        if len(part["panels"]) != 1:
            self.unsupported("part", part["id"], "applique takes exactly one face panel")
            return None
        face = self.panels[part["panels"][0]]
        mesh, alias, grids = self.welded_grid(part)      # one panel, no welds: a relief sheet
        if part["closed_solid"]:
            t = self.thickness(part, face)
            if not t:
                self.unsupported("part", part["id"], "closed applique needs axis-aligned dimensions_m for its thickness")
            else:
                G, (fu, fv) = grids[face.id]
                self.extrude_back(mesh, face, t, G, fu, fv)
                self.report["operations_used"]["applique_solid"] += 1
        else:
            self.report["operations_used"]["applique_sheet"] += 1
        mesh.compact(alias)
        return mesh

    def extrude_back(self, mesh, face, t, G, fu, fv):
        """Close a single-panel sheet into a slab: back face + side walls, fold-in UVs."""
        map_u, du = inset_map(0, 1, t / face.w)
        map_v, dv = inset_map(0, 1, t / face.h)
        squeeze = max(t / face.w / du, t / face.h / dv)
        if squeeze > 1.5:
            self.report["warnings"].append("%s: sides deeper than the face allows; side texels squeezed %.1fx (no side panels in the schema)" % (face.spec["part_id"], squeeze))
        back = [[mesh.vert(face.point(a, b, -t)) for b in fv] for a in fu]
        nu, nv = len(fu) - 1, len(fv) - 1
        buv = lambda a, b: face.uv(map_u(fu[a]), map_v(fv[b]))
        for a in range(nu):
            for b in range(nv):
                cs = [(a, b), (a, b + 1), (a + 1, b + 1), (a + 1, b)]      # reversed: faces -n
                mesh.face([back[x][y] for x, y in cs], [buv(x, y) for x, y in cs])
        loop = ([(a, 0) for a in range(nu)] + [(nu, b) for b in range(nv)] +
                [(a, nv) for a in range(nu, 0, -1)] + [(0, b) for b in range(nv, 0, -1)])
        for k in range(len(loop)):
            s, e = loop[k], loop[(k + 1) % len(loop)]
            mesh.face([G[e[0]][e[1]], G[s[0]][s[1]], back[s[0]][s[1]], back[e[0]][e[1]]],
                      [face.uv(fu[e[0]], fv[e[1]]), face.uv(fu[s[0]], fv[s[1]]), buv(*s), buv(*e)])

    def build_cylinder(self, part):
        S = self.S
        face = self.panels[part["panels"][0]]
        wrap = self.panels[part["panels"][1]] if len(part["panels"]) > 1 else None
        t = self.thickness(part, face)
        if not t:
            self.unsupported("part", part["id"], "cylinder needs axis-aligned dimensions_m for its depth")
            return None
        if not face.flat:
            self.unsupported("part", part["id"], "cylinder face panel must be flat")
            return None
        mesh = Mesh(part["id"])
        R = S["cylinder_rings"]
        seg = max(S["cylinder_min_segments"], 4 * math.ceil(math.pi * max(face.w, face.h) / 0.006 / 4))
        ang = [2 * math.pi * s / seg for s in range(seg)]
        fpos = lambda k, s: (0.5 + 0.5 * k / R * math.cos(ang[s]), 0.5 + 0.5 * k / R * math.sin(ang[s]))
        centre = mesh.vert(face.point(0.5, 0.5, face.relief(0.5, 0.5)))
        rings = []
        for k in range(1, R + 1):
            ring = []
            for s in range(seg):
                a, b = fpos(k, s)
                off = 0.0 if k == R else face.relief(a, b)
                ring.append(mesh.vert(face.point(a, b, off)))
            rings.append(ring)
        cuv = face.uv(0.5, 0.5)
        for s in range(seg):                              # cap: centre fan + ring quads
            e = (s + 1) % seg
            mesh.face([centre, rings[0][s], rings[0][e]], [cuv, face.uv(*fpos(1, s)), face.uv(*fpos(1, e))])
            for k in range(R - 1):
                mesh.face([rings[k][s], rings[k + 1][s], rings[k + 1][e], rings[k][e]],
                          [face.uv(*fpos(k + 1, s)), face.uv(*fpos(k + 2, s)), face.uv(*fpos(k + 2, e)), face.uv(*fpos(k + 1, e))])
        rim = rings[-1]
        back = [mesh.vert(sub(mesh.verts[i], mul(face.n, t))) for i in rim]
        bcentre = mesh.vert(sub(face.point(0.5, 0.5), mul(face.n, t)))
        if wrap is not None:
            self.report["operations_used"]["cylinder_wrap_panel"] += 1
            wuv = lambda s, top: wrap.uv(s / seg, 1.0 if top else 0.0)
            kback = 1.0
        else:
            kback = 1.0 - min(t / (0.5 * min(face.w, face.h)), 0.3)
            wuv = None
            squeeze = t / (0.5 * min(face.w, face.h) * (1 - kback))
            if squeeze > 1.5:
                self.report["warnings"].append("cylinder %s: no wrap panel, wall uses the cap rim (texels squeezed %.1fx)" % (part["id"], squeeze))
        inner = lambda s: face.uv(0.5 + 0.5 * kback * math.cos(ang[s]), 0.5 + 0.5 * kback * math.sin(ang[s]))
        for s in range(seg):
            e = (s + 1) % seg
            if wuv:
                uvs = [wuv(s, True), wuv(s, False), wuv(s + 1, False), wuv(s + 1, True)]
            else:
                uvs = [face.uv(*fpos(R, s)), inner(s), inner(e), face.uv(*fpos(R, e))]
            mesh.face([rim[s], back[s], back[e], rim[e]], uvs)
            mesh.face([bcentre, back[e], back[s]], [cuv, inner(e), inner(s)])
        mesh.compact()
        self.report["operations_used"]["cylinder"] += 1
        return mesh

    # -- registration checks against the part-ID map (evidence vs declaration)
    def mask_iou(self, panel, shape_fn, cls):
        """IoU between a declared footprint and the painted class pixels around it."""
        x0, y0, x1, y1 = panel.rect
        pix = self.pkg["maps"]["part_ids"].load()
        col = PALETTE[cls]
        inside = [(X, Y) for X in range(x0, x1) for Y in range(y0, y1)]
        F = M = I = 0
        sx = sy = 0
        fsx = fsy = 0
        win = shape_fn("window")
        for X, Y in inside:
            fu = (X + 0.5 - x0) / (x1 - x0)
            fv = (y1 - (Y + 0.5)) / (y1 - y0)
            if not (win[0] <= fu <= win[1] and win[2] <= fv <= win[3]):
                continue
            f = shape_fn((fu, fv))
            m = pix[X, Y] == col
            F += f
            M += m
            I += f and m
            if m:
                sx += X
                sy += Y
            if f:
                fsx += X
                fsy += Y
        if not M:
            return None
        union = F + M - I
        off = math.hypot(sx / M - fsx / max(F, 1), sy / M - fsy / max(F, 1))
        return {"iou": round(I / union, 3), "centre_offset_px": round(off, 1), "painted_px": M, "declared_px": F}

    def registration(self):
        S = self.S
        for c in self.pkg["assembly"]["cavities"]:
            p = self.panels.get(c["opening_panel"])
            if p is None or c["shape"] != "rect":
                continue
            cu, cv = c["centre_uv"]
            cw, ch = c["size_m"]
            r = (cu - cw / 2 / p.w, cu + cw / 2 / p.w, cv - ch / 2 / p.h, cv + ch / 2 / p.h)
            pad = (0.5 * (r[1] - r[0]), 0.5 * (r[3] - r[2]))
            fn = lambda q, r=r, pad=pad: ((r[0] - pad[0], r[1] + pad[0], r[2] - pad[1], r[3] + pad[1]) if q == "window"
                                          else r[0] <= q[0] <= r[1] and r[2] <= q[1] <= r[3])
            self.record_registration("cavity", c["id"], p, fn, "cavity")
        for part in self.parts.values():
            box = self.base_box(part)
            if not box or not part.get("parent") or part["parent"] not in self.parts:
                continue
            for pid in self.parts[part["parent"]]["panels"]:
                P = self.panels[pid]
                if not P.flat:
                    continue
                r = self.footprint(P, box)
                if r is None:
                    continue
                pad = (0.5 * (r[1] - r[0]), 0.5 * (r[3] - r[2]))
                round_ = part["construction"] == "cylinder"
                cx, cy, rx, ry = (r[0] + r[1]) / 2, (r[2] + r[3]) / 2, (r[1] - r[0]) / 2, (r[3] - r[2]) / 2

                def fn(q, r=r, pad=pad, round_=round_, cx=cx, cy=cy, rx=rx, ry=ry):
                    if q == "window":
                        return (r[0] - pad[0], r[1] + pad[0], r[2] - pad[1], r[3] + pad[1])
                    if round_:
                        return ((q[0] - cx) / rx) ** 2 + ((q[1] - cy) / ry) ** 2 <= 1
                    return r[0] <= q[0] <= r[1] and r[2] <= q[1] <= r[3]
                self.record_registration("part", part["id"], P, fn, part["semantic_class"])

    def record_registration(self, kind, item, panel, fn, cls):
        S = self.S
        res = self.mask_iou(panel, fn, cls)
        entry = {"kind": kind, "item": item, "panel": panel.id, "class": cls}
        if res is None:
            entry.update(status="no_painted_evidence")
        else:
            entry.update(res)
            ok = res["iou"] >= S["registration_iou"] and res["centre_offset_px"] <= S["registration_offset_px"]
            entry["status"] = "ok" if ok else "misregistered"
            if not ok:
                self.report["errors"].append(
                    "%s %s on %s disagrees with the painted %s mask (IoU %.2f, centre off %.1f px): fix the manifest or the maps" % (
                        kind, item, panel.id, cls, res["iou"], res["centre_offset_px"]))
        self.report["registration"].append(entry)

    # -- run
    def run(self):
        self.prepare()
        for part in self.parts.values():
            c = part["construction"]
            if part.get("rotation_deg") and any(abs(x) > 1e-9 for x in part["rotation_deg"]):
                self.unsupported("part", part["id"], "rotation_deg is not applied yet")
                continue
            try:
                if c in ("closed_box", "panel_shell"):
                    m = self.build_welded(part)
                    self.report["operations_used"][c] += 1
                elif c == "applique":
                    m = self.build_applique(part)
                elif c == "cylinder":
                    m = self.build_cylinder(part)
                else:
                    self.unsupported("construction", part["id"], "construction '%s' is not built yet" % c)
                    m = None
            except ValueError as exc:
                self.report["errors"].append("part %s failed to build: %s" % (part["id"], exc))
                m = None
            if m is None:
                self.report["parts"][part["id"]] = {"built": False, "construction": c, "semantic_class": part["semantic_class"]}
                continue
            self.meshes[part["id"]] = m
            st = m.edge_stats()
            lo, hi = m.bbox()
            closed = st["boundary_edges"] == 0 and st["nonmanifold_edges"] == 0
            info = {"built": True, "construction": c, "semantic_class": part["semantic_class"],
                    "parent": part.get("parent"), "closed_required": bool(part["closed_solid"]),
                    "closed": closed, **st, "triangles": m.triangles(), "vertices": len(m.verts),
                    "bbox_min": [round(x, 5) for x in lo], "bbox_max": [round(x, 5) for x in hi]}
            nom = self.nominal_points(part)
            if nom:
                info["nominal_bbox_min"] = [round(min(c[i] for c in nom), 5) for i in range(3)]
                info["nominal_bbox_max"] = [round(max(c[i] for c in nom), 5) for i in range(3)]
            if part.get("dimensions_m"):
                info["declared_dimensions_m"] = part["dimensions_m"]
            if part.get("anchor_m"):
                info["declared_anchor_m"] = part["anchor_m"]
                info["anchor_is_bbox_min"] = all(abs(a - b) < 0.002 for a, b in zip(part["anchor_m"], lo))
            if part["closed_solid"] and not closed:
                self.report["errors"].append("part %s should be closed but has %d open and %d over-shared edges" % (
                    part["id"], st["boundary_edges"], st["nonmanifold_edges"]))
            if st["inconsistent_winding"]:
                self.report["errors"].append("part %s has %d edges with inconsistent winding" % (part["id"], st["inconsistent_winding"]))
            self.report["parts"][part["id"]] = info
        for j in self.pkg["assembly"]["connections"]:
            if j["operation"] in ("hinge", "fasten") and self.part_of_panel(j["a"]) != self.part_of_panel(j["b"]):
                self.unsupported("connection", "%s/%s" % (j["a"], j["b"]), "%s between parts is recorded, not modelled" % j["operation"])
        self.registration()
        self.report["operations_used"] = dict(self.report["operations_used"])
        return self.meshes, self.report


def interpret(asset_path, settings=None):
    pkg = load_package(asset_path)
    it = Interpreter(pkg, settings)
    meshes, report = it.run()
    return pkg, meshes, report


def suggest_welds(asset, panel_ids, tolerance=1e-4):
    """Weld connections for panels whose edges coincide in 3D (saves authoring 5 values/edge).

    Every pair of panels is tested edge against edge; a pair is proposed when both ends meet
    within `tolerance` and the edges have the same length. The result is a draft to paste
    into assembly.json; the preflight still checks it."""
    dummy = Image.new("RGB", (1, 1), (255, 255, 255))
    canvas = tuple(asset["maps"]["canvas_px"])
    P = [Panel(p, canvas, dummy, {"inward": 0, "outward": 0}) for p in asset["panels"] if p["id"] in panel_ids]
    out = []
    for i, a in enumerate(P):
        for b in P[i + 1:]:
            best = None
            for ea in ("u0", "u1", "v0", "v1"):
                for eb in ("u0", "u1", "v0", "v1"):
                    a0, a1 = Interpreter.edge_ends(a, ea)
                    b0, b1 = Interpreter.edge_ends(b, eb)
                    gap = min(max(norm(sub(a0, b0)), norm(sub(a1, b1))), max(norm(sub(a0, b1)), norm(sub(a1, b0))))
                    if gap <= tolerance and (best is None or gap < best[0]):
                        best = (gap, ea, eb)
            if best:
                out.append({"a": a.id, "a_edge": best[1], "b": b.id, "b_edge": best[2], "operation": "weld",
                            "tolerance_m": 0.0005})
    return out
