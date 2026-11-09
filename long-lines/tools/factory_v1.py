"""FACTORY_V1 command: validate -> build -> export -> reimport QA -> renders -> qa.json.

    python3 long-lines/tools/factory_v1.py path/to/asset.json [--out DIR] [--blender PATH] [--no-render]

Runs with the system Python (needs Pillow + jsonschema for the preflight) and calls Blender
twice: once to build and export the GLB, once in a fresh process to inspect only the
exported file. Outputs go to long-lines/factory/out/<asset_id>/ (git-ignored), never into
the asset library. Exit code 0 only when every QA check passes.
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "factory", "out")

spec = importlib.util.spec_from_file_location("validate_package", os.path.join(HERE, "validate_package.py"))
validate_package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate_package)

DIM_TOL = (0.01, 0.003)   # 1% or 3 mm, whichever is larger


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def blender(binary, *args):
    cmd = [binary, "-b", "--factory-startup", "--python-exit-code", "1",
           "-P", os.path.join(HERE, "factory_v1_blender.py"), "--"] + list(args)
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout[-4000:] + p.stderr[-4000:], round(time.time() - t, 1)


def contact_sheet(previews, path, title):
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    names = ["front", "side", "rear", "top", "threequarter"]
    tiles = [Image.open(previews[n]).convert("RGB") for n in names if n in previews]
    if not tiles:
        return None
    w, h = tiles[0].size
    sheet = Image.new("RGB", (w * 3, h * 2 + 50), (20, 21, 24))
    d = ImageDraw.Draw(sheet)
    d.text((14, 12), title, font=font, fill=(240, 222, 180))
    for i, (n, t) in enumerate(zip(names, tiles)):
        x, y = (i % 3) * w, 50 + (i // 3) * h
        sheet.paste(t, (x, y))
        d.text((x + 10, y + 8), n, font=font, fill=(235, 235, 235))
    sheet.save(path)
    return path


def run(asset_path, out=None, binary="blender", render=True):
    asset_path = os.path.realpath(asset_path)
    asset = read_json(asset_path)
    out = os.path.realpath(out or os.path.join(OUT, asset.get("asset_id", "unknown")))
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    qa = {"asset_id": asset.get("asset_id"), "package": asset_path, "output_dir": out, "pass": False,
          "stages": {}, "failures": []}
    fail = qa["failures"].append

    def finish():
        qa["pass"] = not qa["failures"]
        write_json(os.path.join(out, "qa.json"), qa)
        return qa

    # 1 preflight
    pre = validate_package.validate(asset_path)
    qa["stages"]["preflight"] = {"pass": pre["pass"], "errors": pre["errors"], "warnings": pre["warnings"]}
    if not pre["pass"]:
        for e in pre["errors"]:
            fail("[preflight] " + e)
        return finish()
    # 2 build + export
    code, log, secs = blender(binary, "build", asset_path, out)
    qa["stages"]["build"] = {"exit_code": code, "seconds": secs}
    if code != 0 or not os.path.exists(os.path.join(out, "build.json")):
        fail("[builder] Blender build failed: " + log[-1500:])
        return finish()
    core = read_json(os.path.join(out, "core.json"))
    record = read_json(os.path.join(out, "build.json"))
    qa["operations_used"] = core["operations_used"]
    qa["unsupported"] = core["unsupported"]
    qa["warnings"] = core["warnings"]
    qa["registration"] = core["registration"]
    for e in core["errors"]:
        fail("[manifest/builder] " + e)
    for u in core["unsupported"]:
        fail("[unsupported] %s %s: %s" % (u["what"], u["item"], u["why"]))
    qa["stages"]["export"] = {"glb": record["glb"], "bytes": record["glb_bytes"]}
    # 3 fresh-Blender inspection of the exported GLB
    args = ["verify", out] + ([] if render else ["--no-render"])
    code, log, secs = blender(binary, *args)
    qa["stages"]["reimport"] = {"exit_code": code, "seconds": secs}
    if code != 0 or not os.path.exists(os.path.join(out, "verify.json")):
        fail("[export] reimport/QA failed: " + log[-1500:])
        return finish()
    ver = read_json(os.path.join(out, "verify.json"))
    for e in ver["failures"]:
        fail("[export/geometry] " + e)
    parts = {}
    for pid, c in core["parts"].items():
        v = ver["parts"].get(pid, {})
        parts[pid] = {
            "construction": c["construction"], "semantic_class": c["semantic_class"], "built": c.get("built", False),
            "in_glb": v.get("present", False), "class_kept": v.get("class_kept"),
            "closed_required": c.get("closed_required"), "manifold_after_reimport": v.get("manifold"),
            "open_edges": v.get("open_edges"), "triangles": v.get("triangles"),
            "uv_samples_matched": v.get("uv_samples_matched"), "texel_density": v.get("texel_density"),
            "bbox_m": v.get("bbox_m"), "declared_dimensions_m": c.get("declared_dimensions_m"),
            "gap_to_parent_mm": v.get("gap_to_parent_mm"), "touches_parent": v.get("touches_parent")}
    qa["parts"] = parts
    qa["all_parts_built"] = all(p["built"] and p["in_glb"] for p in parts.values())
    qa["manifold_where_required"] = all(p["manifold_after_reimport"] for p in parts.values() if p["closed_required"])
    qa["uvs_survived_glb"] = all(v.get("uv_roundtrip_ok") for v in ver["parts"].values() if v.get("present"))
    qa["openings"] = ver["openings"]
    qa["triangles"] = ver["triangles"]
    # 4 dimensions. The brief is a nominal envelope: per axis it must match either the main
    # body or the whole assembly measured before painted relief. The exported GLB must
    # have the size the builder made (catches unit/axis errors in export).
    d = asset["brief"]["dimensions_m"]
    declared = [d["width"], d["depth"], d["height"]]
    built = [p for p in core["parts"].values() if p.get("built")]
    roots = [p for p in built if not p.get("parent")]

    def extent(ps, lo_key, hi_key):
        return [round(max(p[hi_key][i] for p in ps) - min(p[lo_key][i] for p in ps), 4) for i in range(3)]
    nominal_all = extent(built, "nominal_bbox_min", "nominal_bbox_max")
    nominal_body = extent(roots, "nominal_bbox_min", "nominal_bbox_max") if roots else None
    actual_core = extent(built, "bbox_min", "bbox_max")
    per_axis = []
    for i, name in enumerate(("width", "depth", "height")):
        tol = max(DIM_TOL[0] * declared[i], DIM_TOL[1])
        match = ("main_body" if nominal_body and abs(nominal_body[i] - declared[i]) <= tol else
                 "assembly" if abs(nominal_all[i] - declared[i]) <= tol else None)
        per_axis.append(match)
        if not match:
            fail("[manifest] %s: brief %.4f m but body %.4f / assembly %.4f m (nominal)" % (
                name, declared[i], nominal_body[i] if nominal_body else -1, nominal_all[i]))
    export_err = [round(abs(a - b) * 1000, 2) for a, b in zip(actual_core, ver["overall_bbox_m"])]
    if max(export_err) > 0.5:
        fail("[export] exported size %s differs from the built size %s" % (ver["overall_bbox_m"], actual_core))
    qa["dimensions"] = {"declared_m": declared, "matched_by_axis": per_axis,
                        "nominal_main_body_m": nominal_body, "nominal_assembly_m": nominal_all,
                        "actual_with_relief_m": ver["overall_bbox_m"], "export_vs_build_mm": export_err}
    for pid, p in parts.items():
        dd = p.get("declared_dimensions_m")
        if dd and p.get("bbox_m"):
            over = [round((b - a) * 1000, 1) for a, b in zip(dd, p["bbox_m"])]
            p["size_minus_declared_mm"] = over
            if min(over) < -3:
                fail("[manifest/builder] part %s is smaller than declared by %s mm" % (pid, over))
            if max(over) > 3:
                qa["warnings"].append("part %s exceeds its declared base size by %s mm (painted relief on its face)" % (pid, over))
    # triangle budget from the production size classes (factory/standards.json)
    std = read_json(os.path.join(ROOT, "factory", "standards.json"))["size_classes"]
    biggest = max(ver["overall_bbox_m"])
    cls = next((k for k, v in sorted(std.items(), key=lambda kv: kv[1]["max_dim_m"]) if biggest <= v["max_dim_m"]), None)
    budget = std[cls]["tri_budget"] if cls else None
    qa["triangle_budget"] = {"size_class": cls, "budget": budget, "triangles": ver["triangles"]}
    if budget and ver["triangles"] > budget:
        fail("[builder] %d triangles over the %s budget of %d" % (ver["triangles"], cls, budget))
    # 5 previews
    if render:
        qa["previews"] = ver.get("previews", {})
        sheet = contact_sheet(qa["previews"], os.path.join(out, "%s_views.png" % qa["asset_id"]),
                              "%s  (FACTORY_V1 interpreter, reimported GLB)" % qa["asset_id"])
        qa["previews"]["sheet"] = sheet
        if not sheet:
            fail("[export] no previews rendered")
    return finish()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package")
    ap.add_argument("--out")
    ap.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--suggest-welds", metavar="PART",
                    help="print draft weld connections for PART's panels (edges that meet in 3D) and exit")
    a = ap.parse_args(argv)
    if a.suggest_welds:
        sys.path.insert(0, HERE)
        from propkit.v1_geometry import suggest_welds
        asset = read_json(a.package)
        ids = [p["id"] for p in asset["panels"] if p["part_id"] == a.suggest_welds]
        print(json.dumps(suggest_welds(asset, ids), indent=1))
        return 0
    qa = run(a.package, a.out, a.blender, not a.no_render)
    print(json.dumps({k: qa.get(k) for k in ("asset_id", "pass", "failures", "triangles", "dimensions", "openings", "output_dir")}, indent=1))
    return 0 if qa["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
