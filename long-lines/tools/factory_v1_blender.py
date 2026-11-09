"""Blender entry point for the FACTORY_V1 interpreter (called by tools/factory_v1.py).

    blender -b --factory-startup -P tools/factory_v1_blender.py -- build  <asset.json> <out_dir>
    blender -b --factory-startup -P tools/factory_v1_blender.py -- verify <out_dir>

`build` interprets the package and exports <asset_id>.glb; `verify` must run in a separate,
fresh Blender process so it only sees what is inside the exported file.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from propkit import v1_geometry  # noqa: E402
from propkit import v1_blender  # noqa: E402


def main(args):
    cmd = args[0]
    if cmd == "build":
        asset, out = os.path.realpath(args[1]), os.path.realpath(args[2])
        os.makedirs(out, exist_ok=True)
        pkg, meshes, report = v1_geometry.interpret(asset)
        json.dump(report, open(os.path.join(out, "core.json"), "w"), indent=1)
        record = v1_blender.build(pkg, meshes, report, out)
        json.dump(record, open(os.path.join(out, "build.json"), "w"))
        print("V1_BUILD_DONE", record["glb"])
    elif cmd == "verify":
        out = os.path.realpath(args[1])
        record = json.load(open(os.path.join(out, "build.json")))
        core = json.load(open(os.path.join(out, "core.json")))
        res = v1_blender.verify(record["glb"], record, core, out, render="--no-render" not in args)
        json.dump(res, open(os.path.join(out, "verify.json"), "w"), indent=1)
        print("V1_VERIFY_DONE", len(res["failures"]), "failures")
    else:
        raise SystemExit("unknown command " + cmd)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:])
