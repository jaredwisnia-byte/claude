"""Sheet-first surface construction (pure Python; no Blender dependency).

A spec declares physical panel sizes, pixel rectangles on one painted atlas, axes,
curvature and optional signed depth. The *same* UV coordinates drive geometry and
the final material. No automatic unwrap or post-hoc texture projection.

This v1 builds surface patches, not watertight solids. Joining parts and modelling
hidden thickness/recessed booleans are explicit later stages, not inferred here.
"""
import json
import math
import os

from PIL import Image


def _vec(value, length, label):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError("%s must contain %d numbers" % (label, length))
    result = tuple(float(x) for x in value)
    if not all(math.isfinite(x) for x in result):
        raise ValueError("%s contains non-finite values" % label)
    return result


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _mul(v, a):
    return tuple(x * a for x in v)


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(v):
    return math.sqrt(_dot(v, v))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _sample(image, x, y):
    """Bilinear RGB pixel sample with ordinary top-left raster coordinates."""
    w, h = image.size
    x = min(max(x, 0.0), w - 1.0)
    y = min(max(y, 0.0), h - 1.0)
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    x1, y1 = min(w - 1, x0 + 1), min(h - 1, y0 + 1)
    fx, fy = x - x0, y - y0
    p = image.load()
    return tuple((1 - fy) * ((1 - fx) * p[x0, y0][k] + fx * p[x1, y0][k]) +
                 fy * ((1 - fx) * p[x0, y1][k] + fx * p[x1, y1][k]) for k in range(3))


def _depth_value(rgb, inward, outward):
    """White=zero; red=inward; blue=outward. RGB is a visualization, not geometry.

    Red (255,0,0) -> -inward, white (255,255,255) -> 0,
    blue (0,0,255) -> +outward. Greys have zero displacement.
    """
    t = max(-1.0, min(1.0, (rgb[2] - rgb[0]) / 255.0))
    return (outward if t >= 0.0 else inward) * t


def _safe_source(root, filename):
    if not isinstance(filename, str) or not filename or os.path.isabs(filename):
        raise ValueError("atlas filenames must be relative to the manifest")
    path = os.path.realpath(os.path.join(root, filename))
    if os.path.commonpath((root, path)) != root:
        raise ValueError("atlas path must stay inside the manifest directory")
    return path


def read_manifest(path):
    root = os.path.dirname(os.path.realpath(path))
    with open(path, encoding="utf-8") as stream:
        spec = json.load(stream)
    if spec.get("schema") != 1:
        raise ValueError("sheetfirst requires schema 1")
    atlas = spec.get("atlas", {})
    dimensions = _vec(atlas.get("size_px"), 2, "atlas.size_px")
    if any(v < 16 or int(v) != v for v in dimensions):
        raise ValueError("atlas.size_px must be positive integer pixel dimensions")
    paint_path = _safe_source(root, atlas.get("albedo"))
    if not os.path.isfile(paint_path):
        raise ValueError("missing painted atlas: " + paint_path)
    with Image.open(paint_path) as im:
        if im.size != tuple(int(x) for x in dimensions):
            raise ValueError("painted atlas pixel dimensions disagree with manifest")
    depth_path = None
    if atlas.get("depth"):
        depth_path = _safe_source(root, atlas["depth"])
        with Image.open(depth_path) as im:
            if im.size != tuple(int(x) for x in dimensions):
                raise ValueError("depth atlas must match painted atlas dimensions")
    if atlas.get("vector"):
        vector_path = _safe_source(root, atlas["vector"])
        with Image.open(vector_path) as im:
            if im.size != tuple(int(x) for x in dimensions):
                raise ValueError("vector atlas must match painted atlas dimensions")
    panels = spec.get("panels")
    if not isinstance(panels, list) or not panels:
        raise ValueError("provide at least one surface panel")
    seen = set()
    for panel in panels:
        name = panel.get("name")
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("panel names must be unique and nonempty")
        seen.add(name)
        width, height = _vec(panel.get("size_m"), 2, name + ".size_m")
        if min(width, height) <= 0.0:
            raise ValueError(name + " physical dimensions must be positive")
        x0, y0, x1, y1 = _vec(panel.get("rect_px"), 4, name + ".rect_px")
        aw, ah = dimensions
        if not (0 <= x0 < x1 <= aw and 0 <= y0 < y1 <= ah):
            raise ValueError(name + " UV rectangle outside atlas")
        if not panel.get("allow_stretch", False):
            mismatch = abs((x1 - x0) / (y1 - y0) / (width / height) - 1.0)
            if mismatch > .05:
                raise ValueError(name + " UV region and physical proportions differ by >5%")
        ua = _vec(panel.get("u_axis"), 3, name + ".u_axis")
        va = _vec(panel.get("v_axis"), 3, name + ".v_axis")
        _vec(panel.get("origin_m"), 3, name + ".origin_m")
        if abs(_norm(ua) - 1) > .001 or abs(_norm(va) - 1) > .001 or abs(_dot(ua, va)) > .001:
            raise ValueError(name + " UV directions must be unit length and perpendicular")
        nx, ny = _vec(panel.get("grid", [8, 8]), 2, name + ".grid")
        if any(int(x) != x or x < 1 or x > 128 for x in (nx, ny)):
            raise ValueError(name + " grid subdivisions must be integers in 1..128")
        curve = panel.get("curve", {"type": "flat"})
        if curve.get("type", "flat") not in ("flat", "cylinder"):
            raise ValueError(name + " unsupported curve type")
        if curve.get("type") == "cylinder":
            radius = float(curve.get("radius_m", 0))
            if not math.isfinite(radius) or abs(radius) < .005:
                raise ValueError(name + " curved radius_m must have magnitude >=5mm")
            if curve.get("axis", "u") not in ("u", "v"):
                raise ValueError(name + " curve.axis must be u or v")
    allowed = set(seen)
    for seam in spec.get("seams", []):
        if seam.get("a") not in allowed or seam.get("b") not in allowed:
            raise ValueError("seam references unknown panel")
        if seam.get("a_edge") not in ("u0", "u1", "v0", "v1") or seam.get("b_edge") not in ("u0", "u1", "v0", "v1"):
            raise ValueError("seam edges must be u0/u1/v0/v1")
    return spec, paint_path, depth_path


def _position(panel, fu, fv, offset, vector=(0.0, 0.0, 0.0)):
    width, height = panel["size_m"]
    origin = _vec(panel["origin_m"], 3, "origin")
    u_axis = _vec(panel["u_axis"], 3, "u_axis")
    v_axis = _vec(panel["v_axis"], 3, "v_axis")
    normal = _cross(u_axis, v_axis)
    curve = panel.get("curve", {})
    mode = curve.get("type", "flat")
    if mode == "cylinder":
        radius = float(curve["radius_m"])
        if curve.get("axis", "u") == "u":
            theta = fu * width / radius
            pos = _add(origin, _add(_mul(u_axis, radius * math.sin(theta)),
                                    _add(_mul(normal, radius * (1 - math.cos(theta))),
                                         _mul(v_axis, fv * height))))
            direction = _sub(_mul(normal, math.cos(theta)), _mul(u_axis, math.sin(theta)))
            tangent_u = _add(_mul(u_axis, math.cos(theta)), _mul(normal, math.sin(theta)))
            tangent_v = v_axis
        else:
            theta = fv * height / radius
            pos = _add(origin, _add(_mul(v_axis, radius * math.sin(theta)),
                                    _add(_mul(normal, radius * (1 - math.cos(theta))),
                                         _mul(u_axis, fu * width))))
            direction = _sub(_mul(normal, math.cos(theta)), _mul(v_axis, math.sin(theta)))
            tangent_v = _add(_mul(v_axis, math.cos(theta)), _mul(normal, math.sin(theta)))
            tangent_u = u_axis
    else:
        pos = _add(origin, _add(_mul(u_axis, fu * width), _mul(v_axis, fv * height)))
        direction = normal
        tangent_u, tangent_v = u_axis, v_axis
    return _add(pos, _add(_mul(direction, offset + vector[2]),
                          _add(_mul(tangent_u, vector[0]), _mul(tangent_v, vector[1]))))


def build_geometry(spec, depth_image=None, vector_image=None):
    """Return per-panel vertices, faces, and stable UVs; no Blender objects required."""
    atlas = spec["atlas"]
    aw, ah = atlas["size_px"]
    range_spec = spec.get("depth_range_m", {})
    inward = float(range_spec.get("inward", 0.0))
    outward = float(range_spec.get("outward", 0.0))
    if any(not math.isfinite(x) or x < 0 or x > .5 for x in (inward, outward)):
        raise ValueError("depth_range_m must be between 0 and 0.5 metres")
    if depth_image is not None and depth_image.size != (aw, ah):
        raise ValueError("depth image dimensions mismatch")
    if vector_image is not None and vector_image.size != (aw, ah):
        raise ValueError("vector image dimensions mismatch")
    vector_range = _vec(spec.get("vector_range_m", [0.0, 0.0, 0.0]), 3,
                        "vector_range_m")
    if any(x < 0 or x > .5 for x in vector_range):
        raise ValueError("vector_range_m must be between 0 and 0.5 metres")
    results = {}
    for panel in spec["panels"]:
        nu, nv = map(int, panel.get("grid", [8, 8]))
        x0, y0, x1, y1 = panel["rect_px"]
        verts, uv, faces = [], [], []
        for j in range(nv + 1):
            fv = j / nv
            for i in range(nu + 1):
                fu = i / nu
                px = x0 + fu * (x1 - x0)
                py = y1 - fv * (y1 - y0)
                sx = min(max(px - .5, x0), x1 - 1)
                sy = min(max(py - .5, y0), y1 - 1)
                offset = _depth_value(_sample(depth_image, sx, sy), inward, outward) if depth_image else 0.0
                rgb = _sample(vector_image, sx, sy) if vector_image else (128, 128, 128)
                vector = tuple(max(-1.0, min(1.0, (c - 128.0) / 127.0)) * scale
                               for c, scale in zip(rgb, vector_range))
                verts.append(_position(panel, fu, fv, offset, vector))
                # Half-pixel insets keep bilinear filtering inside the assigned
                # rectangular island instead of bleeding across atlas gutters.
                uv.append(((x0 + .5 + fu * ((x1 - x0) - 1)) / aw,
                           1.0 - (y1 - .5 - fv * ((y1 - y0) - 1)) / ah))
        for j in range(nv):
            for i in range(nu):
                a = j * (nu + 1) + i
                faces.append((a, a + 1, a + nu + 2, a + nu + 1))
        results[panel["name"]] = {"vertices": verts, "uv": uv, "faces": faces,
                                   "curved": panel.get("curve", {}).get("type") == "cylinder"}
    return results


def _edge_samples(panel, mesh, edge, steps=(0.0, .25, .5, .75, 1.0)):
    """Sample actual displaced boundary vertices, interpolating between grid points."""
    nu, nv = map(int, panel.get("grid", [8, 8]))
    vs = mesh["vertices"]
    if edge == "u0":
        indices = [j * (nu + 1) for j in range(nv + 1)]
    elif edge == "u1":
        indices = [j * (nu + 1) + nu for j in range(nv + 1)]
    elif edge == "v0":
        indices = list(range(nu + 1))
    else:
        indices = [nv * (nu + 1) + i for i in range(nu + 1)]
    coords = [vs[i] for i in indices]
    result = []
    for t in steps:
        f = t * (len(coords) - 1)
        i = min(int(math.floor(f)), len(coords) - 2)
        w = f - i
        result.append(_add(_mul(coords[i], 1 - w), _mul(coords[i + 1], w)))
    return result


def validate_seams(spec, meshes):
    """Check *generated* panel boundaries including signed displacement at the joins."""
    errors = []
    for item in spec.get("seams", []):
        a_name, b_name = item["a"], item["b"]
        p = next(p for p in spec["panels"] if p["name"] == a_name)
        q = next(p for p in spec["panels"] if p["name"] == b_name)
        a = _edge_samples(p, meshes[a_name], item["a_edge"])
        b = _edge_samples(q, meshes[b_name], item["b_edge"])
        direct = max(_norm(_sub(x, y)) for x, y in zip(a, b))
        backward = max(_norm(_sub(x, y)) for x, y in zip(a, reversed(b)))
        error = min(direct, backward)
        tolerance = float(item.get("tolerance_m", .003))
        if not 0 < tolerance < .1:
            raise ValueError("seam tolerance must be 0..0.1 metres")
        if error > tolerance:
            errors.append("%s/%s seam gap %.4f m (max %.4f m)" %
                          (a_name, b_name, error, tolerance))
    return errors
