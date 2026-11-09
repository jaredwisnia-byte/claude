"""The asset factory: work orders in, game-ready assets out, every run measured.

Run under Blender (all stations share one process):
    blender -b --factory-startup -P tools/factory.py -- intake IMAGE NAME --category tv [--height 0.5]
    blender -b --factory-startup -P tools/factory.py -- intake_sheet SHEET_SPEC.json --source-dir DIR
    blender -b --factory-startup -P tools/factory.py -- intake_set MANIFEST.json [--category fridge] [--name N]
    blender -b --factory-startup -P tools/factory.py -- intake_sheetfirst MANIFEST.json [--name N]
    blender -b --factory-startup -P tools/factory.py -- build NAME... | --pending | --all-orders
    blender -b --factory-startup -P tools/factory.py -- qa NAME... | --library
    blender -b --factory-startup -P tools/factory.py -- report

Stations: intake (classify the reference, split views, size from the category table,
write a work order) -> build (the order's production line) -> bake/export (propkit.bake)
-> gate (propkit.qa against factory/standards.json, one codified automatic correction,
then flag for a person) -> ledger (factory/ledger.jsonl) -> report (factory/REPORT.md).
Work orders live in factory/orders/<name>.json; lines read them, so a new asset needs
no code. See factory/README.md.
"""
import glob
import hashlib
import importlib
import json
import os
import pkgutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.normpath(os.path.join(HERE, ".."))
FACT = os.path.join(ROOT, "factory")
ORDERS = os.path.join(FACT, "orders")
LEDGER = os.path.join(FACT, "ledger.jsonl")
LIB = os.path.join(ROOT, "assets", "props")
SRC = os.path.join(ROOT, "assets", "source_textures")
STANDARDS = json.load(open(os.path.join(FACT, "standards.json")))
CATS = json.load(open(os.path.join(FACT, "categories.json")))

import propkit  # noqa: E402
from propkit import bake, core, mats, qa  # noqa: E402
import props  # noqa: E402

for _m in pkgutil.iter_modules(props.__path__):
    importlib.import_module("props." + _m.name)


def load_order(name):
    p = os.path.join(ORDERS, name + ".json")
    return json.load(open(p)) if os.path.exists(p) else None


def save_order(o):
    os.makedirs(ORDERS, exist_ok=True)
    with open(os.path.join(ORDERS, o["name"] + ".json"), "w") as f:
        json.dump(o, f, indent=1)


def ledger(entry):
    entry["t"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(LEDGER, "a") as f:
        f.write(json.dumps(entry) + "\n")


def file_hash(paths):
    h = hashlib.sha1()
    for p in sorted(paths):
        if os.path.exists(p):
            h.update(open(p, "rb").read())
    return h.hexdigest()[:12]


# ------------------------------------------------------------------ intake

def split_views(image_path, out_dir):
    """Finds the top row of object views on a reference sheet (REFERENCE_SPEC.md) and
    saves them as v_front/right/back/left/top. Returns the list of views found."""
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    im = Image.open(image_path).convert("RGB")
    a = np.asarray(im).astype(int)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, axis=0)
    mask = np.abs(a - bg).sum(2) > 40
    mask = ndimage.binary_closing(mask, iterations=3)
    lab, n = ndimage.label(mask)
    boxes = ndimage.find_objects(lab)
    H, W = mask.shape
    blobs = []
    for i, sl in enumerate(boxes):
        if sl is None:
            continue
        ys, xs = sl
        h, w = ys.stop - ys.start, xs.stop - xs.start
        if h * w > H * W * 0.004 and h > H * 0.06:
            blobs.append((ys.start, ys.stop, xs.start, xs.stop))
    if not blobs:
        return []
    top = min(b[0] for b in blobs)
    row_h = max(b[1] - b[0] for b in blobs if b[0] < top + H * 0.08)
    row = sorted([b for b in blobs if b[0] < top + row_h * 0.5], key=lambda b: b[2])
    names = ["front", "right", "back", "left", "top"]
    os.makedirs(out_dir, exist_ok=True)
    found = []
    for nm, (y0, y1, x0, x1) in zip(names, row):
        pad = 4
        im.crop((max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad))).save(os.path.join(out_dir, "v_%s.png" % nm))
        found.append(nm)
    return found


def intake(image, name, category, height=None, notes=""):
    cat = CATS[category]
    line = cat["line"]
    order = {"name": name, "category": category, "line": line, "source": os.path.basename(image),
             "class": cat.get("class"), "assumptions": [], "status": "pending", "notes": notes}
    if line == "hull":
        d = os.path.join(SRC, name)
        found = split_views(image, d)
        order["views_folder"] = name
        order["views"] = found
        order["height_m"] = height or cat["height_m"]
        if height is None:
            order["assumptions"].append("height %.2f m from category '%s' (typical period size)" % (cat["height_m"], category))
        order["hull"] = dict(cat.get("hull", {}))
        missing = [v for v in ("front", "right", "top") if v not in found]
        if missing:
            order["assumptions"].append("views missing: %s (fewer constraints on shape)" % ", ".join(missing))
        if "front" not in found or ("right" not in found and "left" not in found):
            order["status"] = "needs_person"
            order["assumptions"].append("sheet does not follow REFERENCE_SPEC: need front and a side view")
    save_order(order)
    print("ORDER", json.dumps(order))
    return order


def intake_sheet(spec_path, source_dir):
    """A multi-asset sheet spec: one work order per listed item. Facade items crop an
    elevation; atlas items keep the whole parts sheet and name which numbered panel
    (propkit.atlas.panels) is which face. Sources go to source_textures/<name>/."""
    import shutil
    from PIL import Image
    spec = json.load(open(spec_path))
    out = []
    for name, it in spec["items"].items():
        category = it.get("category", spec.get("category"))
        cat = CATS[category]
        line = it.get("line", cat["line"])
        d = os.path.join(SRC, name)
        os.makedirs(d, exist_ok=True)
        if line == "facade":
            Image.open(os.path.join(source_dir, it["sheet"])).convert("RGB").crop(tuple(it["box"])).save(os.path.join(d, "facade.png"))
        else:  # one shared copy of the sheet; many assets come from it
            shared = os.path.join(SRC, "_sheets")
            os.makedirs(shared, exist_ok=True)
            if not os.path.exists(os.path.join(shared, it["sheet"])):
                shutil.copy(os.path.join(source_dir, it["sheet"]), os.path.join(shared, it["sheet"]))
        with open(os.path.join(d, "SOURCE.md"), "w") as f:
            f.write("From Jared's painted sheet %s (%s).\n" % (it["sheet"], os.path.basename(spec_path)))
        order = dict(it)
        order.update({"name": name, "category": category, "line": line, "source": it["sheet"],
                      "class": it.get("class", cat.get("class")), "views_folder": name, "status": "pending",
                      "assumptions": [], "notes": it.get("notes", "")})
        if line == "facade":
            order.setdefault("facade", dict(cat.get("facade", {})))
            order["assumptions"].append("storey height 3.4 m (lower Manhattan loft/tenement)")
        else:
            order.setdefault("height_m", cat["height_m"])
            if cat.get("depth_m"):
                order.setdefault("cat_depth_m", cat["depth_m"])
            order["assumptions"].append("height %.2f m from category '%s'" % (order["height_m"], category))
        save_order(order)
        out.append(name)
    print("ORDERS", out)
    return out


def intake_sheetfirst(manifest, name=None):
    """Create a work order from a paint-first assembly manifest and aligned PNGs.

    The source specification and both atlas images are copied intact into the
    asset's input folder. No UV region is inferred from the painting.
    """
    import shutil
    from propkit.sheetfirst_engine import read_manifest
    spec, paint, depth = read_manifest(manifest)
    name = name or spec.get("asset_id")
    if not name or not isinstance(name, str) or not all(
            c.isalnum() or c == "_" for c in name):
        raise ValueError("sheetfirst asset name must contain only letters, numbers, underscores")
    dest = os.path.join(SRC, name)
    os.makedirs(dest, exist_ok=True)
    files = [(os.path.realpath(manifest), os.path.join(dest, "manifest.json")),
             (paint, os.path.join(dest, spec["atlas"]["albedo"]))]
    if depth:
        files.append((depth, os.path.join(dest, spec["atlas"]["depth"])))
    if spec["atlas"].get("vector"):
        vector_source = os.path.join(os.path.dirname(os.path.realpath(manifest)),
                                     spec["atlas"]["vector"])
        files.append((vector_source, os.path.join(dest, spec["atlas"]["vector"])))
    for origin, target in files:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.realpath(origin) != os.path.realpath(target):
            shutil.copyfile(origin, target)
    order = {
        "name": name, "category": "sheetfirst", "line": "sheetfirst",
        "views_folder": name, "source": "manifest.json", "class": None,
        "status": "pending", "notes": spec.get("description", ""),
        "assumptions": ["dimensions, UV rectangles and assembly from source manifest"],
    }
    save_order(order)
    print("SHEETFIRST_ORDER", name, len(spec["panels"]), "panels")
    return order


def intake_set(manifest, category=None, name=None):
    """A v2 sheet set (painted + depth + part map + section + manifest, REFERENCE_SPEC.md):
    files copied to source_textures/<name>/, size from the manifest's dimensions."""
    import shutil
    m = json.load(open(manifest))
    name = name or m["asset_id"]
    d = os.path.join(SRC, name)
    os.makedirs(d, exist_ok=True)
    src = os.path.dirname(os.path.abspath(manifest))
    for k in ("painted_sheet", "depth_map", "part_map", "section"):
        if k in m:
            shutil.copy(os.path.join(src, m[k]["filename"]), os.path.join(d, m[k]["filename"]))
    shutil.copy(manifest, os.path.join(d, "manifest.json"))
    dims = m.get("dimensions_cm", {})
    category = category or m.get("object_type")
    order = {"name": name, "category": category, "line": "sheetset", "source": os.path.basename(manifest),
             "class": None, "views_folder": name, "status": "pending",   # size class from the real size
             "height_m": dims.get("height", 100) / 100, "depth_m": dims.get("depth", 60) / 100,
             "object_type": m.get("object_type"), "notes": m.get("display_name", ""),
             "assumptions": ["size from the manifest (%s)" % ("approximate" if dims.get("approximate") else "given")]}
    from propkit import sizing   # factory/sizing_manual.json: is this a believable size?
    for w in sizing.envelope(category, dims.get("width", 0) / 100 or None, order["depth_m"], order["height_m"]):
        order["assumptions"].append("check size: " + w)
    save_order(order)
    print("ORDER", json.dumps(order))
    return order


# ------------------------------------------------------------------ lines

def run_line(order):
    """Builds the order's geometry in the current scene. One function per line."""
    line = order["line"]
    if line == "hull":
        from propkit.hull import hull_prop
        d = os.path.join(SRC, order["views_folder"])
        views = {k: os.path.join(d, "v_%s.png" % k) for k in order.get("views", [])}
        hull_prop(views, order["height_m"], name=order["name"], **order.get("hull", {}))
    elif line == "car":
        from props.cars_painted import painted_car
        painted_car(order["name"], order.get("body", "car_coupe"))
    elif line == "can":
        from props.goods_painted import painted_can
        painted_can(order["label"], order["radius_m"], order["height_m"])
    elif line == "facade":
        from propkit.facade import facade_prop
        info = facade_prop(os.path.join(SRC, order["views_folder"], "facade.png"), order["name"],
                           storeys=order.get("storeys"), **order.get("facade", {}))
        order["measured"] = info
    elif line == "atlas":
        from propkit.atlas import appliance
        d = os.path.join(SRC, order["views_folder"])
        order["measured"] = appliance(os.path.join(SRC, "_sheets", order["sheet"]), order, d, order["height_m"])
    elif line == "extrude":
        from propkit import extrude
        d = os.path.join(SRC, order["views_folder"])
        sheet = os.path.join(SRC, "_sheets", order["sheet"])
        if order["category"] == "moulding":
            order["measured"] = extrude.moulding(sheet, order["strip"], order.get("profile"), order["kind"],
                                                 order["height_m"], d, order.get("strip_frac"), order["name"],
                                                  order.get("section_tolerance", 1.0))
        else:
            order["measured"] = extrude.frame(sheet, order["panel"], order["height_m"], d,
                                              order.get("depth_m", 0.022), order["name"],
                                               order.get("opening_mode", "strict"))
    elif line == "sheetset":
        from propkit.sheetset import build_set
        d = os.path.join(SRC, order["views_folder"])
        order["measured"] = build_set(os.path.join(d, "manifest.json"), order["name"], order["height_m"],
                                      order["depth_m"], order.get("object_type"))
    elif line == "kit":
        from propkit.kit import build as kit_build
        d = os.path.join(SRC, order["views_folder"])
        order["measured"] = kit_build(os.path.join(SRC, "_sheets", order["sheet"]), order, d)
    elif line == "recipe":
        propkit.PROPS[order["name"]]["fn"]()
    else:
        raise ValueError("unknown line " + line)


def correct(order, result):
    """Codified corrections for known failure modes (docs/LESSONS.md). Returns True if
    the order was changed and is worth one more attempt."""
    changed = False
    fails = " ".join(result["fails"])
    if order["line"] == "hull":
        h = order.setdefault("hull", {})
        for v in ("back", "left", "right", "top"):
            if (v + " outline") in fails and v not in h.get("paint_only", []):
                h.setdefault("paint_only", []).append(v)  # unreliable view: paint, don't carve
                changed = True
        if "holes" in fails and not h.get("convex"):
            h["convex"] = True
            changed = True
        if "triangle budget" in fails:
            h["decimate"] = round(h.get("decimate", 0.35) * 0.5, 3)
            changed = True
    if order["line"] == "extrude":
        if "trim section differs" in fails and order.get("profile") is not None:
            if order.get("section_tolerance", 1.0) > 0.31:
                order["section_tolerance"] = 0.3  # follow the drawing more closely
                changed = True
        if "frame reference opening not found" in fails or "frame opening is blocked" in fails:
            if order.get("opening_mode", "strict") != "interior":
                order["opening_mode"] = "interior"
                changed = True
    if changed:
        order.setdefault("corrections", []).append(result["fails"])
    return changed


def tex_for(order):
    cls = order.get("class") or "medium"
    return STANDARDS["size_classes"].get(cls, STANDARDS["size_classes"]["medium"])["tex"]


def render_trim_preview(name):
    """Always make a PNG turnaround of a shipped extruded asset. No source sheets."""
    import bpy
    import subprocess
    script = os.path.join(HERE, "preview_trim.py")
    proc = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup",
                           "-P", script, "--", name], text=True, capture_output=True,
                          timeout=180)
    if proc.returncode:
        raise RuntimeError("preview Blender failed: " + (proc.stderr or proc.stdout)[-1200:])
    out = os.path.join(LIB, name, "_preview.png")
    if not os.path.exists(out):
        raise RuntimeError("render completed but preview PNG is missing")
    return out


def build(order, samples=16):
    t0 = time.time()
    attempt, result = 0, None
    while True:
        attempt += 1
        try:
            core.reset()
            mats.reset()
            if order["line"] == "sheetfirst":
                from propkit import sheetfirst_blender
                manifest = os.path.join(SRC, order["views_folder"], "manifest.json")
                info = sheetfirst_blender.build_and_export(manifest, order["name"], LIB)
            else:
                run_line(order)
                info = bake.finish(order["name"], LIB, tex_for(order), samples, tags=[order["category"]])
            info.update({"tags": [order["category"], "factory"], "desc": order.get("notes") or order["name"],
                         "kind": order.get("kind", "static")})
            bake.update_manifest(os.path.join(LIB, "library.json"), order["name"], info)
            vd = os.path.join(SRC, order["views_folder"]) if order.get("views_folder") else None
            result = qa.check(order["name"], vd, order.get("class"), tuple(order.get("hull", {}).get("paint_only", ())),
                              solid=order["line"] in STANDARDS["solid_lines"], mount=order.get("mount", "floor"),
                               trim_order=order)
            if order["line"] == "sheetfirst":
                from propkit import sheetfirst_engine
                spec, _, _ = sheetfirst_engine.read_manifest(manifest)
                uv_fails, uv_accuracy = sheetfirst_blender.verify_imported_uvs(order["name"], spec)
                result["metrics"]["sheetfirst_uv_max_error_px"] = uv_accuracy
                result["fails"].extend(uv_fails)
                result["pass"] = not result["fails"]
        except Exception as e:
            traceback.print_exc()
            result = {"pass": False, "fails": ["build error: %s" % e], "metrics": {}}
        if result["pass"] or attempt > STANDARDS["minimum_acceptable"]["max_retries"] or not correct(order, result):
            break
    if result["pass"] and order["line"] in ("extrude", "sheetfirst"):
        try:
            preview = render_trim_preview(order["name"])
            result["metrics"]["preview_png"] = os.path.relpath(preview, ROOT)
        except Exception as e:
            result["pass"] = False
            result["fails"].append("preview generation failed: %s" % e)
    order["status"] = "shipped" if result["pass"] else "needs_person"
    order["attempts"] = attempt
    save_order(order)
    srcs = glob.glob(os.path.join(SRC, order.get("views_folder", "-"), "*.png"))
    if order["line"] == "sheetfirst":
        srcs.append(os.path.join(SRC, order["views_folder"], "manifest.json"))
    ledger({"op": "build", "name": order["name"], "line": order["line"], "pass": result["pass"],
            "attempts": attempt, "auto": result["pass"] and attempt == 1, "seconds": round(time.time() - t0, 1),
            "fails": result["fails"], "metrics": result["metrics"],
            "input_hash": file_hash(srcs + [os.path.join(ORDERS, order["name"] + ".json")]),
            "output_hash": file_hash([os.path.join(LIB, order["name"], order["name"] + ".glb")]),
            # Reproducibility is judged on the gauges, not bytes: Blender's UV packer is not
            # deterministic, so GLB bytes differ run to run while the product is the same.
            "gauge_hash": hashlib.sha1(json.dumps([result["metrics"].get(k) for k in ("tris", "size_m", "silhouette_iou", "geometry_signature", "texture_signature",
                                                              "trim_section_iou", "frame_opening_occlusion", "trim_colour_difference")],
                                                  sort_keys=True).encode()).hexdigest()[:12]})
    print("BUILT" if result["pass"] else "FLAGGED", order["name"], result["fails"])
    return result


def mount_of(name):
    mt = STANDARDS["mounting"]
    if name in mt["by_name"]:
        return mt["by_name"][name]
    tags = json.load(open(os.path.join(LIB, "library.json"))).get(name, {}).get("tags", [])
    for t, m in mt["by_tag"].items():
        if t in tags:
            return m
    return "floor"


def gate(names):
    for n in names:
        t0 = time.time()
        o = load_order(n) or {}
        vd = os.path.join(SRC, o["views_folder"]) if o.get("views_folder") else None
        line = o.get("line") or ("painted" if n.startswith(("car_coupe", "can_", "tv_painted", "range_")) else "recipe")
        try:
            r = qa.check(n, vd, o.get("class"), tuple(o.get("hull", {}).get("paint_only", ())),
                         solid=line in STANDARDS["solid_lines"] and n not in STANDARDS["open_by_design"]["names"],
                         mount=mount_of(n), trim_order=o)
        except Exception as e:
            r = {"pass": False, "fails": ["qa error: %s" % e], "metrics": {}}
        ledger({"op": "qa", "name": n, "line": line, "pass": r["pass"], "seconds": round(time.time() - t0, 1),
                "fails": r["fails"], "metrics": r["metrics"]})
        print("PASS" if r["pass"] else "FAIL", n, r["fails"])


def report():
    rows = [json.loads(l) for l in open(LEDGER)] if os.path.exists(LEDGER) else []
    latest = {}
    current = set(json.load(open(os.path.join(LIB, "library.json"))))   # deleted assets stay in the ledger's history only
    for r in rows:
        if r["name"] in current:
            latest[(r["op"], r["name"])] = r
    builds = [r for (op, _), r in latest.items() if op == "build"]
    reps = {}
    for r in rows:
        if r["op"] == "build" and r.get("gauge_hash"):
            reps.setdefault((r["name"], r["input_hash"]), set()).add(r["gauge_hash"])
    repeated = [v for v in reps.values()]
    repro = sum(1 for v in repeated if len(v) == 1)
    qas = [r for (op, _), r in latest.items() if op == "qa"]
    assets = {}
    for r in qas + builds:  # a build result supersedes a gate-only result
        assets[r["name"]] = r
    vals = list(assets.values())
    passed = [r for r in vals if r["pass"]]
    tris = sorted(r["metrics"].get("tris", 0) for r in vals if r["metrics"].get("tris"))
    fail_kinds = {}
    for r in vals:
        for f in r["fails"]:
            k = f.split(":")[0].split(" (")[0]
            k = " ".join(w for w in k.split() if not any(c.isdigit() for c in w))
            fail_kinds[k] = fail_kinds.get(k, 0) + 1
    by_line = {}
    for r in vals:
        b = by_line.setdefault(r.get("line", "?"), [0, 0])
        b[0] += 1
        b[1] += r["pass"]
    lines = ["# Factory report", "", "Generated from `factory/ledger.jsonl` (latest result per asset).", "",
             "| Measure | Value |", "| --- | --- |",
             "| Assets measured | %d |" % len(vals),
             "| Pass the minimum standard | %d (%.0f%%) |" % (len(passed), 100 * len(passed) / max(len(vals), 1)),
             "| Factory builds | %d |" % len(builds),
             "| Factory builds passing with no correction | %d |" % sum(1 for r in builds if r.get("auto")),
             "| Factory builds needing a person | %d |" % sum(1 for r in builds if not r["pass"]),
             "| Mean build time (s) | %.0f |" % (sum(r["seconds"] for r in builds) / max(len(builds), 1)),
             "| Same inputs -> same gauges | %d of %d order/input pairs |" % (repro, len(repeated)),
             "| Median triangles | %d |" % (tris[len(tris) // 2] if tris else 0),
             "| Max triangles | %d |" % (tris[-1] if tris else 0), "",
             "## By production line", "", "| Line | Assets | Passing |", "| --- | --- | --- |"]
    lines += ["| %s | %d | %d |" % (k, v[0], v[1]) for k, v in sorted(by_line.items())]
    lines += ["", "## Failure kinds", "", "| Failure | Count |", "| --- | --- |"]
    lines += ["| %s | %d |" % (k, v) for k, v in sorted(fail_kinds.items(), key=lambda kv: -kv[1])]
    lines += ["", "## Failing assets", ""]
    lines += ["- **%s** (%s): %s" % (r["name"], r.get("line", "?"), "; ".join(r["fails"])) for r in vals if not r["pass"]]
    open(os.path.join(FACT, "REPORT.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines[:16]))


if __name__ == "__main__":
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    cmd, rest = (a[0], a[1:]) if a else ("report", [])
    if cmd == "intake":
        kw = {}
        if "--height" in rest:
            kw["height"] = float(rest[rest.index("--height") + 1])
        intake(rest[0], rest[1], rest[rest.index("--category") + 1], **kw)
    elif cmd == "intake_sheetfirst":
        intake_sheetfirst(rest[0], rest[rest.index("--name") + 1] if "--name" in rest else None)
    elif cmd == "intake_set":
        intake_set(rest[0], rest[rest.index("--category") + 1] if "--category" in rest else None,
                   rest[rest.index("--name") + 1] if "--name" in rest else None)
    elif cmd == "intake_sheet":
        intake_sheet(rest[0], rest[rest.index("--source-dir") + 1])
    elif cmd == "build":
        if "--pending" in rest or "--all-orders" in rest:
            names = [os.path.basename(p)[:-5] for p in sorted(glob.glob(os.path.join(ORDERS, "*.json")))]
            if "--pending" in rest:
                names = [n for n in names if load_order(n)["status"] == "pending"]
        else:
            names = rest
        for n in names:
            build(load_order(n))
        report()
    elif cmd == "preview":
        for n in rest:
            print("PREVIEW", render_trim_preview(n))
    elif cmd == "qa":
        names = sorted(json.load(open(os.path.join(LIB, "library.json")))) if "--library" in rest else rest
        gate(names)
        report()
    else:
        report()
