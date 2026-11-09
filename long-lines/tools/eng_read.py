"""Read orthographic engineering drawings back into a solid (rectilinear objects, stage 1).

    python3 tools/eng_read.py DRAWING_DIR [--out result.json]

DRAWING_DIR holds <view>.png (front, top, right, left, back, bottom: any subset with front)
and drawing.json (view list and overall dimensions in metres). Ink is dark; visible edges are
solid lines, hidden edges dashed (third-angle convention, front looks toward +depth).

How it reads, like an engineer:
1. Scale: each view's ink extent is mapped to the overall dimensions (views drawn at slightly
   different scales still line up).
2. Lines: every long horizontal/vertical line in every view, solid or dashed.
3. Blades: each line is a plane through the object (a front-view horizontal line is a
   horizontal plane, a top-view vertical line a plane across the depth...). All planes
   together chop space into blocks.
4. Start from the silhouette solid (blocks inside every outline: what the hull lines build).
5. Carve: repeatedly remove the block region that makes the solid's own drawings agree best
   with the given ones, line by line (present or not, visible or hidden), until no removal
   helps. A line drawn across a flat continuous surface is accepted as a seam between parts.
The result lists the planes, the solid blocks and how well each view is explained.
Stage 1 is rectilinear: curves (a round leg) become boxes.
"""
import argparse
import json
import os

import numpy as np
from PIL import Image

AXES = {"x": 0, "y": 1, "z": 2}
# view -> (screen-right world axis, sign), (screen-up axis, sign), (viewing axis, sign: +1 = looks toward +)
VIEWS = {
    "front": (("x", 1), ("z", 1), ("y", 1)),
    "back": (("x", -1), ("z", 1), ("y", -1)),
    "right": (("y", 1), ("z", 1), ("x", -1)),
    "left": (("y", -1), ("z", 1), ("x", 1)),
    "top": (("x", 1), ("y", 1), ("z", -1)),
    "bottom": (("x", 1), ("y", -1), ("z", 1)),
}
COST = {"missing": 1.0, "extra": 1.0, "seam": 0.02, "hidden_missing": 0.5, "type": 0.5}


class View:
    def __init__(self, name, path, dims):
        self.name = name
        a = np.asarray(Image.open(path).convert("L"))
        self.ink = a < 128
        ys, xs = np.nonzero(self.ink)
        self.x0, self.x1, self.y0, self.y1 = xs.min(), xs.max(), ys.min(), ys.max()
        (self.ra, self.rs), (self.ua, self.us), (self.va, self.vs) = VIEWS[name]
        self.ext_r = dims[self.ra]
        self.ext_u = dims[self.ua]

    # screen pixel <-> normalised world coordinate (0..1 along that world axis)
    def r_of_px(self, px):
        f = (px - self.x0) / max(1, self.x1 - self.x0)
        return f if self.rs > 0 else 1 - f

    def u_of_py(self, py):
        f = (self.y1 - py) / max(1, self.y1 - self.y0)
        return f if self.us > 0 else 1 - f

    def px_of_r(self, r):
        f = r if self.rs > 0 else 1 - r
        return self.x0 + f * (self.x1 - self.x0)

    def py_of_u(self, u):
        f = u if self.us > 0 else 1 - u
        return self.y1 - f * (self.y1 - self.y0)

    def lines(self, min_frac=0.025, gap=7):
        """Long horizontal and vertical lines (solid or dashed): {h|v: [(position px, length px)]}.
        Scraps shorter than min_frac of the view (and 12 px) are ignored."""
        out = {"h": [], "v": []}
        for key, img in (("h", self.ink), ("v", self.ink.T)):
            extent = (self.x1 - self.x0) if key == "h" else (self.y1 - self.y0)
            min_px = max(12, int(min_frac * extent))
            rows = []
            for i, row in enumerate(img):
                idx = np.nonzero(row)[0]
                if len(idx) < min_px:
                    continue
                breaks = np.nonzero(np.diff(idx) > gap)[0]
                starts = np.r_[idx[0], idx[breaks + 1]]
                ends = np.r_[idx[breaks], idx[-1]]
                longest = (ends - starts).max()
                if longest >= min_px:
                    rows.append((i, longest / max(1, extent)))
            clusters = []
            for i, ln in rows:
                if clusters and i - clusters[-1][-1][0] <= 2:
                    clusters[-1].append((i, ln))
                else:
                    clusters.append([(i, ln)])
            out[key] = [(float(np.mean([c[0] for c in cl])), max(c[1] for c in cl)) for cl in clusters]
        return out

    def silhouette(self):
        """Filled outline: everything the border flood fill can't reach."""
        from collections import deque
        H, W = self.ink.shape
        ink = self.ink.copy()
        # close dashes so the outline is watertight
        k = 4
        for _ in range(k):
            ink[:, 1:] |= ink[:, :-1]
        for _ in range(k):
            ink[1:, :] |= ink[:-1, :]
        outside = np.zeros_like(ink)
        q = deque([(0, 0)])
        outside[0, 0] = True
        while q:
            y, x = q.popleft()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                Y, X = y + dy, x + dx
                if 0 <= Y < H and 0 <= X < W and not outside[Y, X] and not ink[Y, X]:
                    outside[Y, X] = True
                    q.append((Y, X))
        filled = ~outside
        # A broken outline lets the flood fill leak inside; then fill row-wise AND column-wise.
        rows = np.zeros_like(ink)
        cols = np.zeros_like(ink)
        for y in range(H):
            xs = np.nonzero(ink[y])[0]
            if len(xs):
                rows[y, xs[0]:xs[-1] + 1] = True
        for x in range(W):
            ys = np.nonzero(ink[:, x])[0]
            if len(ys):
                cols[ys[0]:ys[-1] + 1, x] = True
        rc = rows & cols
        if filled.sum() < 0.6 * rc.sum():
            return rc
        return filled

    def sample_segment(self, p0, p1, band=1):
        """Ink coverage along a screen segment (ends trimmed), band px either side."""
        n = int(max(abs(p1[0] - p0[0]), abs(p1[1] - p0[1])))
        if n < 6:
            return None, n
        lo, hi = int(0.2 * n), int(0.8 * n)
        hits = 0
        H, W = self.ink.shape
        horiz = abs(p1[0] - p0[0]) > abs(p1[1] - p0[1])
        for k in range(lo, hi + 1):
            t = k / n
            x = int(round(p0[0] + (p1[0] - p0[0]) * t))
            y = int(round(p0[1] + (p1[1] - p0[1]) * t))
            if horiz:
                win = self.ink[max(0, y - band):min(H, y + band + 1), min(max(x, 0), W - 1)]
            else:
                win = self.ink[min(max(y, 0), H - 1), max(0, x - band):min(W, x + band + 1)]
            hits += bool(win.any())
        return hits / (hi - lo + 1), n


def cluster(vals, tol):
    vals = sorted(vals)
    out = []
    for v in vals:
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [float(np.mean(c)) for c in out]


def observe(v, planes):
    """Observed line type for every grid segment of a view: 0 none, 1 visible, 2 hidden, -1 unknown/any."""
    A, B = planes[v.ra], planes[v.ua]
    obs = {}
    for name, (alongA, P, Q) in (("vert", (False, A, B)), ("horz", (True, B, A))):
        arr = np.full((len(P), len(Q) - 1), -2, dtype=int)
        for i, pv in enumerate(P):
            for k in range(len(Q) - 1):
                if not alongA:     # vertical screen line at A=pv, from B=Q[k] to Q[k+1]
                    x = v.px_of_r(pv)
                    p0, p1 = (x, v.py_of_u(Q[k])), (x, v.py_of_u(Q[k + 1]))
                else:              # horizontal screen line at B=pv, from A=Q[k] to Q[k+1]
                    y = v.py_of_u(pv)
                    p0, p1 = (v.px_of_r(Q[k]), y), (v.px_of_r(Q[k + 1]), y)
                f, n = v.sample_segment(p0, p1)
                if f is None:
                    arr[i, k] = -2                      # too short to judge
                elif f >= 0.9:
                    arr[i, k] = 1 if n >= 26 else -1
                elif f >= 0.35:
                    arr[i, k] = 2 if n >= 26 else -1
                elif f < 0.25:
                    arr[i, k] = 0
                else:
                    arr[i, k] = -2
        obs[name] = arr
    return obs


def oriented(O, v):
    """Occupancy as [a (screen right), b (screen up), c (from the viewer away)]."""
    ia, ib, ic = AXES[v.ra], AXES[v.ua], AXES[v.va]
    X = np.transpose(O, (ia, ib, ic))
    if v.vs < 0:
        X = X[:, :, ::-1]
    return X


def predict_pair(L, R):
    """Line type between two neighbouring columns of cells along the view: 0 none, 1 visible, 2 hidden;
    also whether both columns show the same surface depth (a seam there is plausible)."""
    diff = L != R
    anyd = diff.any(-1)
    first = np.argmax(diff, -1)
    cum = np.cumsum(L, -1) - L                          # solid cells strictly in front
    occl = np.take_along_axis(cum, first[..., None], -1)[..., 0] > 0
    t = np.where(anyd, np.where(occl, 2, 1), 0)
    fl = np.where(L.any(-1), np.argmax(L, -1), -1)
    fr = np.where(R.any(-1), np.argmax(R, -1), -1)
    same_surface = (fl == fr) & (fl >= 0)
    return t, same_surface


def predict(O, v):
    X = oriented(O, v).astype(bool)
    na, nb, nc = X.shape
    Z = np.zeros((1, nb, nc), bool)
    Lv, Rv = np.concatenate([Z, X], 0), np.concatenate([X, Z], 0)       # vertical lines at A planes
    Zh = np.zeros((na, 1, nc), bool)
    Lh, Rh = np.concatenate([Zh, X], 1), np.concatenate([X, Zh], 1)    # horizontal lines at B planes
    tv, sv = predict_pair(Lv, Rv)
    th, sh = predict_pair(Lh, Rh)
    return {"vert": (tv, sv), "horz": (th.T, sh.T)}


def cost(O, views, obs):
    total, per = 0.0, {}
    for v in views:
        c = 0.0
        P = predict(O, v)
        for k in ("vert", "horz"):
            t, same = P[k]
            o = obs[v.name][k]
            known = o >= -1
            present = (o == 1) | (o == 2) | (o == -1)
            c += COST["missing"] * np.sum(known & (o == 0) & (t == 1))
            c += COST["hidden_missing"] * np.sum(known & (o == 0) & (t == 2))
            # A drawn line with no edge behind it: a seam between parts if it is a visible line on a
            # flush surface; a dashed (hidden) line always means a real edge, never a seam.
            seam = known & ((o == 1) | (o == -1)) & (t == 0)
            c += np.sum(np.where(same[seam], COST["seam"], COST["extra"]))
            c += COST["extra"] * np.sum(known & (o == 2) & (t == 0))
            # a visible line over a hidden edge where the surfaces are flush is a part seam
            c += COST["type"] * np.sum(((o == 1) & (t == 2) & ~same) | ((o == 2) & (t == 1)))
            c += COST["seam"] * np.sum((o == 1) & (t == 2) & same)
        per[v.name] = round(float(c), 2)
        total += c
    return total, per


def regions(v, obs, shape):
    """Groups of a view's screen cells not separated by a drawn visible line."""
    ov, oh = obs[v.name]["vert"], obs[v.name]["horz"]
    na, nb = shape
    lab = -np.ones((na, nb), int)
    n = 0
    for a in range(na):
        for b in range(nb):
            if lab[a, b] >= 0:
                continue
            stack = [(a, b)]
            lab[a, b] = n
            while stack:
                x, y = stack.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    X, Y = x + dx, y + dy
                    if not (0 <= X < na and 0 <= Y < nb) or lab[X, Y] >= 0:
                        continue
                    line = ov[max(x, X), y] if dx else oh[max(y, Y), x]
                    if line in (1, -1):
                        continue
                    lab[X, Y] = n
                    stack.append((X, Y))
            n += 1
    return lab, n


def read(folder, max_iter=60, verbose=True):
    info = json.load(open(os.path.join(folder, "drawing.json")))
    dims = {"x": info["dimensions_m"]["width"], "y": info["dimensions_m"]["depth"], "z": info["dimensions_m"]["height"]}
    views = [View(n, os.path.join(folder, n + ".png"), dims) for n in info["views"]
             if os.path.exists(os.path.join(folder, n + ".png"))]
    # 2-3. lines -> blades -> grid planes (normalised 0..1 per world axis), merged within 6 mm
    # A real edge plane shows in at least two views (a plane x=a is a vertical line in the front
    # AND the top view), or is a long line (>= 15% of the view) in one; scraps are noise.
    raw = {"x": [], "y": [], "z": []}
    for v in views:
        L = v.lines()
        raw[v.ua] += [(v.u_of_py(py), ln, v.name) for py, ln in L["h"]]
        raw[v.ra] += [(v.r_of_px(px), ln, v.name) for px, ln in L["v"]]
    planes = {}
    for k in raw:
        tol = 0.006 / dims[k]
        groups = []
        for pos, ln, vn in sorted(raw[k]):
            if groups and pos - groups[-1][-1][0] <= tol:
                groups[-1].append((pos, ln, vn))
            else:
                groups.append([(pos, ln, vn)])
        keep = [float(np.mean([g[0] for g in grp])) for grp in groups
                if len({g[2] for g in grp}) >= 2 or max(g[1] for g in grp) >= 0.15]
        planes[k] = [0.0] + [min(1.0, max(0.0, p)) for p in keep if tol < p < 1 - tol] + [1.0]
    for k in planes:
        planes[k][0], planes[k][-1] = 0.0, 1.0
    shape = tuple(len(planes[k]) - 1 for k in "xyz")
    cx = [np.array([(p[i] + p[i + 1]) / 2 for i in range(len(p) - 1)]) for p in (planes["x"], planes["y"], planes["z"])]
    # 4. silhouette solid
    O = np.ones(shape, bool)
    for v in views:
        sil = v.silhouette()
        ra, ua = AXES[v.ra], AXES[v.ua]
        for i in range(shape[ra]):
            for j in range(shape[ua]):
                px, py = int(round(v.px_of_r(cx[ra][i]))), int(round(v.py_of_u(cx[ua][j])))
                if not sil[min(max(py, 0), sil.shape[0] - 1), min(max(px, 0), sil.shape[1] - 1)]:
                    idx = [slice(None)] * 3
                    idx[ra], idx[ua] = i, j
                    O[tuple(idx)] = False
    hull = O.copy()
    obs = {v.name: observe(v, planes) for v in views}
    best, per = cost(O, views, obs)
    hull_cost = (best, per)
    if verbose:
        print("grid", shape, "silhouette solid cost", round(best, 2), per)
    # 5. carve whole regions from each view, best improvement first
    regs = {}
    for v in views:
        X = oriented(O, v)
        regs[v.name] = regions(v, obs, X.shape[:2])
    log = []
    for it in range(max_iter):
        cand = None
        for v in views:
            lab, n = regs[v.name]
            X = oriented(O, v)
            nc = X.shape[2]
            for r in range(n):
                mask = lab == r
                if not X[mask].any():
                    continue
                for depth in range(1, nc + 1):
                    O2 = O.copy()
                    X2 = oriented(O2, v)            # a view of O2: writing to it edits O2
                    X2[mask, :depth] = False
                    if (O2 == O).all() or not O2.any():
                        continue
                    c, _ = cost(O2, views, obs)
                    if c < best - 1e-9 and (cand is None or c < cand[0]):
                        cand = (c, O2, v.name, r, depth)
        if cand is None:
            break
        best, O = cand[0], cand[1]
        log.append({"view": cand[2], "region": int(cand[3]), "carve_cells": int(cand[4]), "cost": round(best, 2)})
        if verbose:
            print("carve", log[-1])
    final_cost, per = cost(O, views, obs)
    return {"dimensions_m": dims, "planes": planes, "solid": O.astype(int).tolist(), "silhouette_solid": hull.astype(int).tolist(),
            "cost_silhouette": {"total": round(hull_cost[0], 2), "per_view": hull_cost[1]},
            "cost_final": {"total": round(final_cost, 2), "per_view": per}, "carves": log}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = read(a.folder)
    out = a.out or os.path.join(a.folder, "read.json")
    json.dump(res, open(out, "w"))
    print("ENG_READ", out, "cost", res["cost_silhouette"]["total"], "->", res["cost_final"]["total"], "carves", len(res["carves"]))
