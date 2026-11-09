"""Validate a FACTORY_V1 painted construction package before any Blender build.

Usage:
    python long-lines/tools/validate_package.py path/to/asset.json
    python long-lines/tools/validate_package.py path/to/asset.json --report /tmp/qa.json

Requires: Pillow, jsonschema. No Blender and no AI call required.

This is a *preflight* gate. It checks files, maps, UV scale, annotated parts,
view sizes, and declared panel joins. Only Blender's post-build geometry gate
can prove that the exported GLB is closed/manifold and visually correct.
"""
import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

from PIL import Image
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "factory" / "schemas"
PALETTE = {
    "background": (0, 0, 0),
    "main_body": (255, 255, 255),
    "moving_part": (34, 197, 94),
    "raised_hard": (239, 68, 68),
    "cavity": (59, 130, 246),
    "glass": (250, 204, 21),
    "soft": (249, 115, 22),
    "trim": (168, 85, 247),
    "support": (20, 184, 166),
}
FACES = {"front", "back", "left", "right", "top", "bottom"}
CLOSED_BOX_ADJACENCIES = {
    frozenset((a, b)) for a, b in (
        ("front", "left"), ("front", "right"), ("front", "top"),
        ("front", "bottom"), ("back", "left"), ("back", "right"),
        ("back", "top"), ("back", "bottom"), ("left", "top"),
        ("left", "bottom"), ("right", "top"), ("right", "bottom")
    )
}


def safe_path(folder, filename):
    """Reject traversal, absolute paths and symlink escapes."""
    if not isinstance(filename, str) or not filename or "\\" in filename:
        raise ValueError("asset path must be a relative POSIX path")
    home = folder.resolve()
    target = (home / filename).resolve()
    if not target.is_relative_to(home):
        raise ValueError("asset path escapes the package directory")
    return target


def schema_errors(data, filename):
    schema = json.loads((SCHEMAS / filename).read_text(encoding="utf-8"))
    results = Draft202012Validator(schema).iter_errors(data)
    return [str(e.json_path) + ": " + e.message for e in sorted(results, key=str)]


def rect_ok(rect, size):
    return (len(rect) == 4 and 0 <= rect[0] < rect[2] <= size[0] and
            0 <= rect[1] < rect[3] <= size[1])


def overlap_with_gutter(a, b, gutter=4):
    return not (a[2] + gutter <= b[0] or b[2] + gutter <= a[0] or
                a[3] + gutter <= b[1] or b[3] + gutter <= a[1])


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def mul(a, s):
    return tuple(x * s for x in a)


def length(v):
    return math.sqrt(sum(x * x for x in v))


def endpoints(panel, edge):
    """Physical 3D corners of a flat UV panel, in metres, never image pixels."""
    u = panel["u_axis"]
    v = panel["v_axis"]
    w, h = panel["size_m"]
    o = panel["origin_m"]
    if edge == "u0":
        return (tuple(o), add(o, mul(v, h)))
    if edge == "u1":
        return (add(o, mul(u, w)), add(add(o, mul(u, w)), mul(v, h)))
    if edge == "v0":
        return (tuple(o), add(o, mul(u, w)))
    if edge == "v1":
        return (add(o, mul(v, h)), add(add(o, mul(v, h)), mul(u, w)))
    raise ValueError("invalid edge")


def edge_gap(a, b, ae, be):
    a0, a1 = endpoints(a, ae)
    b0, b1 = endpoints(b, be)
    return min(max(length(tuple(x - y for x, y in zip(a0, b0))),
                   length(tuple(x - y for x, y in zip(a1, b1)))),
               max(length(tuple(x - y for x, y in zip(a0, b1))),
                   length(tuple(x - y for x, y in zip(a1, b0)))))


def dimension_error_px(rect, size_m, ppm):
    return max(abs(rect[2] - rect[0] - size_m[0] * ppm),
               abs(rect[3] - rect[1] - size_m[1] * ppm))


def validate(asset_file):
    path = Path(asset_file).resolve()
    report = {"asset": str(path), "pass": False, "errors": [], "warnings": [], "metrics": {}}
    bad, warn, metrics = report["errors"], report["warnings"], report["metrics"]
    if not path.is_file():
        bad.append("asset.json does not exist: " + str(path))
        return report
    try:
        asset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        bad.append("cannot read asset.json: " + str(exc))
        return report
    bad.extend(schema_errors(asset, "asset_schema.json"))
    if bad:
        return report
    folder = path.parent
    try:
        assembly_path = safe_path(folder, asset["assembly_file"])
        assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        bad.append("cannot read assembly.json: " + str(exc))
        return report
    bad.extend(schema_errors(assembly, "assembly_schema.json"))
    if bad:
        return report
    if assembly["asset_id"] != asset["asset_id"]:
        bad.append("assembly asset_id does not match asset.json")

    maps = asset["maps"]
    w, h = maps["canvas_px"]
    ppm = maps["pixels_per_metre"]
    ortho_size = maps.get("reference_canvas_px")
    ortho_ppm = maps.get("reference_pixel_scale")
    if bool(ortho_size) != bool(ortho_ppm):
        bad.append("orthographic reference_canvas_px and reference_pixel_scale must be supplied together")
    assets = {}
    for key in ("albedo", "signed_depth", "part_ids", "orthographic", "concept"):
        if not maps.get(key):
            continue
        try:
            source = safe_path(folder, maps[key])
            # Pillow requires PNG verify() before any pixel reads/copy().
            # Verify the raw image stream, then open a fresh decoder.
            with Image.open(source) as stream:
                stream.verify()
            with Image.open(source) as img:
                if img.format != "PNG":
                    bad.append(key + " is not a PNG")
                exp = tuple(ortho_size) if key == "orthographic" and ortho_size else (w, h)
                if key != "concept" and img.size != exp:
                    bad.append("%s dimensions %r do not match expected %r" % (key, img.size, exp))
                if key in ("signed_depth", "part_ids") and img.mode != "RGB":
                    bad.append(key + " must be exact RGB PNG with no alpha/palette conversion")
                if key in ("signed_depth", "part_ids"):
                    assets[key] = img.copy()
        except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
            bad.append("cannot read %s: %s" % (key, exc))
    if bad:
        return report

    # Exact colours in the part-ID image. Antialiased palette boundaries are
    # invalid because they introduce unknown classes; use nearest-neighbour.
    part_img = assets["part_ids"]
    colors = part_img.getcolors(maxcolors=256)
    if colors is None:
        bad.append("part_ids contains more than 256 colours; use exact semantic IDs without antialiasing")
    else:
        unknown = [rgb for n, rgb in colors if rgb not in PALETTE.values()]
        if unknown:
            bad.append("part_ids uses unknown semantic colours: " + repr(unknown[:10]))
        metrics["part_id_colors"] = len(colors)

    # The depth colour curve is red -> white -> blue, not a freely painted
    # colour image. Inspect an evenly subsampled set to keep 4k validation cheap.
    depth = assets["signed_depth"]
    if depth.size == (w, h):
        step = max(1, math.ceil(max(w, h) / 512))
        sampled = depth.crop((0, 0, w, h)).resize(
            (max(1, w // step), max(1, h // step)), Image.Resampling.NEAREST)
        invalid = 0
        red = blue = white = 0
        # Pillow 13+ renamed getdata(); retain compatibility with Pillow 10.
        samples = (sampled.get_flattened_data() if hasattr(sampled, "get_flattened_data")
                   else sampled.getdata())
        for r, g, b in samples:
            if (abs(g - b) > 3 and abs(r - g) > 3) or (r != 255 and b != 255):
                invalid += 1
            if r == 255 and b < 255:
                red += 1
            elif b == 255 and r < 255:
                blue += 1
            else:
                white += 1
        metrics["depth_sampled_pixels"] = sampled.width * sampled.height
        metrics["depth_invalid_sampled_pixels"] = invalid
        if invalid:
            bad.append("signed_depth contains colours outside red-white-blue encoding")
        metrics["recessed_samples"] = red
        metrics["raised_samples"] = blue
    if not any(asset["depth_range_m"].values()):
        warn.append("depth range is zero: red/blue pixels cannot change geometry")

    panel_by_id = {}
    rects = []
    for panel in asset["panels"]:
        name = panel["id"]
        if name in panel_by_id:
            bad.append("duplicate panel id " + name)
        panel_by_id[name] = panel
        rect = panel["rect_px"]
        if not rect_ok(rect, (w, h)):
            bad.append("%s rectangle outside painted canvas" % name)
            continue
        err = dimension_error_px(rect, panel["size_m"], ppm)
        if err > 2.0:
            bad.append("%s UV size disagrees with physical dimensions by %.2f pixels" % (name, err))
        for older, older_rect in rects:
            if overlap_with_gutter(rect, older_rect):
                bad.append("%s overlaps or has <4px gutter from %s" % (name, older))
        rects.append((name, rect))
        u, v = panel["u_axis"], panel["v_axis"]
        dot = sum(a * b for a, b in zip(u, v))
        if (abs(length(u) - 1) > 0.001 or abs(length(v) - 1) > 0.001
                or abs(dot) > 0.001):
            bad.append("%s physical UV axes are not perpendicular unit vectors" % name)
        curve = panel.get("curve", {"type": "flat"})
        if curve["type"] == "cylinder" and (abs(curve.get("radius_m", 0)) < .005
                                           or curve.get("axis") not in ("u", "v")):
            bad.append("%s cylindrical curve needs axis and radius >=5mm" % name)
        if part_img.size == (w, h):
            hist = part_img.crop(tuple(rect)).getcolors(maxcolors=256)
            if hist is None:
                bad.append(name + " part-ID region contains too many semantic colours")
            else:
                count = sum(n for n, c in hist if c != PALETTE["background"])
                total = (rect[2] - rect[0]) * (rect[3] - rect[1])
                if count / total < .5:
                    bad.append("%s painted UV region is mostly background in part_ids" % name)
    metrics["panel_count"] = len(asset["panels"])
    metrics["UV_rect_occupied"] = round(sum((r[2]-r[0])*(r[3]-r[1])
                                               for _, r in rects)/(w*h), 5)

    # Orthographic reference views have their own (declared) pixel density.
    # Unlike UV regions, these are projections and are NOT used as mesh UVs.
    expected_sizes = {
        "front": ("width","height"), "back": ("width","height"),
        "left": ("depth","height"), "right": ("depth","height"),
        "top": ("width","depth"), "bottom": ("width","depth")
    }
    d = asset["brief"]["dimensions_m"]
    ref_rects = []
    for face, rect in asset["orthographic_views"].items():
        if not ortho_size:
            bad.append("cannot validate six orthographic views without reference_canvas_px")
            break
        if not rect_ok(rect, ortho_size):
            bad.append(face + " orthographic crop outside reference canvas")
            continue
        size = [d[k] for k in expected_sizes[face]]
        err = dimension_error_px(rect, size, ortho_ppm)
        if err > 2.0:
            bad.append("%s orthographic crop dimension mismatch %.2f px" % (face, err))
        for other, other_rect in ref_rects:
            if overlap_with_gutter(rect, other_rect, gutter=1):
                bad.append("%s overlaps orthographic view %s" % (face, other))
        ref_rects.append((face, rect))

    parts = {}
    assigned = Counter()
    for part in assembly["parts"]:
        pid = part["id"]
        if pid in parts:
            bad.append("duplicate part id " + pid)
        parts[pid] = part
        for name in part["panels"]:
            if name not in panel_by_id:
                bad.append("part %s references unknown UV panel %s" % (pid, name))
            assigned[name] += 1
            if name in panel_by_id and panel_by_id[name]["part_id"] != pid:
                bad.append("UV panel %s claims another part than %s" % (name, pid))
        if part.get("parent") == pid:
            bad.append("part cannot parent itself: " + pid)
        if part["construction"] == "closed_box":
            faces = [panel_by_id[n]["face"] for n in part["panels"] if n in panel_by_id]
            if set(faces) != FACES or len(faces) != 6:
                bad.append("closed_box %s requires exactly six front/back/left/right/top/bottom panels" % pid)
            if not part["closed_solid"]:
                bad.append("closed_box %s must declare closed_solid=true" % pid)
    for name in panel_by_id:
        if assigned[name] != 1:
            bad.append("UV panel %s must belong to exactly one assembly part (found %d)" %
                       (name, assigned[name]))
    # Verify *semantic map registration*: the physical atlas island for a
    # glass/knob/soft part should actually be painted in that part's ID colour.
    # Body panels may contain smaller apertures, trim, vents, etc., hence a
    # majority is not strictly required, but an absent class is a real error.
    for panel in asset["panels"]:
        part = parts.get(panel["part_id"])
        if not part or part_img.size != (w, h):
            continue
        colour = PALETTE[part["semantic_class"]]
        rect = panel["rect_px"]
        if not rect_ok(rect, (w, h)):
            continue
        count = part_img.crop(tuple(rect)).getcolors(maxcolors=256)
        if count is None:
            continue  # separately flagged above as an invalid part-ID image
        count_expected = sum(n for n, c in count if c == colour)
        total = (rect[2] - rect[0]) * (rect[3] - rect[1])
        if count_expected / total < .35:
            bad.append("panel %s part-ID map does not represent expected class %s" %
                       (panel["id"], part["semantic_class"]))
    for part in parts.values():
        parent = part.get("parent")
        if parent and parent not in parts:
            bad.append("%s has missing parent part %s" % (part["id"], parent))
    for pid in parts:
        seen = set()
        cur = pid
        while cur in parts and parts[cur].get("parent"):
            if cur in seen:
                bad.append("cyclic part parent hierarchy involving " + pid)
                break
            seen.add(cur)
            cur = parts[cur]["parent"]

    join_by_pair = set()
    for j in assembly["connections"]:
        a, b = j["a"], j["b"]
        if a not in panel_by_id or b not in panel_by_id:
            bad.append("connection references unknown panel %s/%s" % (a, b))
            continue
        if a == b:
            bad.append("connection joins panel to itself")
            continue
        join_by_pair.add(frozenset((a, b)))
        pa, pb = panel_by_id[a], panel_by_id[b]
        if pa.get("curve", {}).get("type", "flat") == "flat" and pb.get("curve", {}).get("type", "flat") == "flat":
            gap = edge_gap(pa, pb, j["a_edge"], j["b_edge"])
            tolerance = j.get("tolerance_m", .003)
            if gap > tolerance:
                bad.append("declared %s/%s edge gap %.5f m exceeds %.5f m" %
                           (a, b, gap, tolerance))
        else:
            warn.append("curved connection %s/%s needs post-build sampling" % (a, b))
    for part in parts.values():
        if part["construction"] != "closed_box":
            continue
        by_face = {panel_by_id[n]["face"]: n for n in part["panels"] if n in panel_by_id}
        for pair in CLOSED_BOX_ADJACENCIES:
            if len(pair) == 2 and all(f in by_face for f in pair):
                pa, pb = list(pair)
                if frozenset((by_face[pa], by_face[pb])) not in join_by_pair:
                    bad.append("closed_box %s missing join between %s and %s" % (part["id"], pa, pb))
    for cavity in assembly["cavities"]:
        owner = cavity["owner_part"]
        panel_id = cavity["opening_panel"]
        if owner not in parts:
            bad.append("cavity %s has unknown owner" % cavity["id"])
        if panel_id not in panel_by_id:
            bad.append("cavity %s has unknown opening panel" % cavity["id"])
        elif owner in parts and panel_id not in parts[owner]["panels"]:
            bad.append("cavity %s opening panel not part of its owner" % cavity["id"])
        if not cavity["closed_bottom"] and assembly["expected_manifold"]:
            bad.append("cavity %s has no bottom but manifold is required" % cavity["id"])
        if panel_id in panel_by_id:
            p = panel_by_id[panel_id]
            cw, ch = cavity["size_m"]
            if cw >= p["size_m"][0] or ch >= p["size_m"][1]:
                bad.append("cavity %s dimensions exceed surface" % cavity["id"])
            cu, cv = cavity["centre_uv"]
            if (cu - cw/(2*p["size_m"][0]) <= 0 or cu + cw/(2*p["size_m"][0]) >= 1 or
                    cv - ch/(2*p["size_m"][1]) <= 0 or cv + ch/(2*p["size_m"][1]) >= 1):
                bad.append("cavity %s clips beyond opening panel" % cavity["id"])

    if assembly["expected_manifold"] and any(
            not p["closed_solid"] and p["construction"] not in ("applique",)
            for p in parts.values()):
        warn.append("some listed parts are open surfaces: post-build manifold QA is essential")
    metrics["part_count"] = len(parts)
    metrics["connections"] = len(assembly["connections"])
    metrics["cavities"] = len(assembly["cavities"])
    report["pass"] = not bad
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="path to asset.json")
    parser.add_argument("--report", help="optional JSON report file")
    args = parser.parse_args(argv)
    result = validate(args.package)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
