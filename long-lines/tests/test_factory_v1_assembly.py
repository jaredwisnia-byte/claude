"""Regression tests for the FACTORY_V1 assembly interpreter (tools/propkit/v1_geometry.py).

Pure Python + Pillow: no Blender. They check that geometry follows the schema and nothing
else: closed welded solids, real cavities only where declared, unsupported features
reported, names irrelevant, curved panels by arc length, registration against the maps.
The Blender build/export/reimport path is covered by test_factory_v1_blender.py.
"""
import copy
import importlib.util
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from propkit import v1_geometry as g  # noqa: E402

CRT = ROOT / "factory" / "examples" / "factory_v1_crt_tv"
TOASTER = ROOT / "factory" / "examples" / "factory_v1_toaster"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def copy_package(src, dst, generator):
    for f in ("asset.json", "assembly.json"):
        shutil.copy(src / f, dst / f)
    generator(dst)


class Package:
    """A temporary copy of an example package that a test may edit."""

    def __init__(self, folder):
        self.folder = Path(folder)

    def load(self, name):
        return json.loads((self.folder / name).read_text())

    def save(self, name, data):
        (self.folder / name).write_text(json.dumps(data, indent=1))

    def build(self, settings=None):
        return g.interpret(self.folder / "asset.json", settings)


class V1AssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        demo = load_module("crt_maps", CRT / "generate_demo_maps.py")
        cls.crt = base / "crt"
        cls.crt.mkdir()
        copy_package(CRT, cls.crt, demo.generate)
        toaster = load_module("toaster_maps", TOASTER / "generate_maps.py")
        toaster.main()
        cls.toaster = base / "toaster"
        cls.toaster.mkdir()
        for f in ("asset.json", "assembly.json", "albedo.png", "signed_depth.png", "part_ids.png",
                  "orthographic.png", "concept.png"):
            shutil.copy(TOASTER / f, cls.toaster / f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)

    def edit(self, src):
        dst = Path(self.work.name) / "pkg"
        shutil.copytree(src, dst)
        return Package(dst)

    # --- both examples through one module
    def check_clean(self, report, n_parts):
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["unsupported"], [])
        self.assertEqual(len(report["parts"]), n_parts)
        for pid, p in report["parts"].items():
            self.assertTrue(p["built"], pid)
            if p["closed_required"]:
                self.assertTrue(p["closed"], pid)
            self.assertEqual(p["inconsistent_winding"], 0, pid)

    def test_crt_builds_closed_parts(self):
        _, meshes, r = Package(self.crt).build()
        self.check_clean(r, 4)
        self.assertEqual(r["operations_used"]["weld"], 12)
        self.assertEqual(r["operations_used"]["cavity_rect"], 1)
        self.assertEqual(r["operations_used"]["cylinder"], 2)
        lo, hi = meshes["body"].bbox()
        for a, b, want in zip(lo, hi, (0.58, 0.41, 0.45)):
            self.assertAlmostEqual(b - a, want, places=4)

    def test_toaster_builds_with_the_same_module(self):
        _, meshes, r = Package(self.toaster).build()
        self.check_clean(r, 7)
        self.assertEqual(r["operations_used"]["cavity_rect"], 2)
        self.assertEqual(r["operations_used"]["applique_solid"], 5)
        self.assertEqual(r["operations_used"]["cylinder_wrap_panel"], 1)
        top = max(v[2] for v in meshes["body"].verts)
        self.assertGreater(top, 0.198 + 0.005, "the painted dome must raise the top")

    def test_cavity_is_real_geometry_with_floor(self):
        _, meshes, r = Package(self.crt).build()
        cav = r["cavities"][0]
        n, c, d = cav["normal"], cav["centre_m"], cav["depth_m"]
        floor = [v for v in meshes["body"].verts
                 if abs(sum((v[i] - c[i]) * n[i] for i in range(3)) + d) < 1e-6]
        self.assertGreaterEqual(len(floor), 4, "cavity floor vertices at the declared depth")

    def test_open_bottom_cavity_leaves_an_opening(self):
        pkg = self.edit(self.crt)
        s = pkg.load("assembly.json")
        s["cavities"][0]["closed_bottom"] = False
        s["expected_manifold"] = False
        pkg.save("assembly.json", s)
        _, _, r = pkg.build()
        body = r["parts"]["body"]
        self.assertGreater(body["boundary_edges"], 0)
        self.assertTrue(any("should be closed" in e for e in r["errors"]))

    # --- schema, not names or paint
    def test_painted_hole_without_declaration_is_not_cut(self):
        pkg = self.edit(self.crt)
        s = pkg.load("assembly.json")
        s["cavities"] = []
        pkg.save("assembly.json", s)
        _, meshes, r = pkg.build()
        self.assertNotIn("cavity_rect", r["operations_used"])
        self.assertTrue(r["parts"]["body"]["closed"])
        ys = {round(v[1], 4) for v in meshes["body"].verts}
        self.assertNotIn(-0.193, ys, "no recess floor without a declared cavity")

    def test_names_do_not_matter(self):
        pkg = self.edit(self.crt)
        a, s = pkg.load("asset.json"), pkg.load("assembly.json")
        ren = {p["id"]: "zz_%d" % i for i, p in enumerate(a["panels"])}
        prt = {p["id"]: "qq_%d" % i for i, p in enumerate(s["parts"])}
        for p in a["panels"]:
            p["id"], p["part_id"] = ren[p["id"]], prt[p["part_id"]]
        a["panels"].reverse()
        for p in s["parts"]:
            p["id"] = prt[p["id"]]
            p["panels"] = [ren[x] for x in p["panels"]]
            if "parent" in p:
                p["parent"] = prt[p["parent"]]
        for c in s["connections"]:
            c["a"], c["b"] = ren[c["a"]], ren[c["b"]]
        for c in s["cavities"]:
            c["owner_part"], c["opening_panel"] = prt[c["owner_part"]], ren[c["opening_panel"]]
        pkg.save("asset.json", a)
        pkg.save("assembly.json", s)
        _, renamed, r2 = pkg.build()
        _, orig, r1 = Package(self.crt).build()
        self.assertEqual(r2["errors"], [])
        for old, new in prt.items():
            self.assertEqual(orig[old].triangles(), renamed[new].triangles(), old)
            self.assertEqual([round(x, 6) for x in orig[old].bbox()[0]], [round(x, 6) for x in renamed[new].bbox()[0]])

    def test_unsupported_operations_are_reported_not_approximated(self):
        pkg = self.edit(self.crt)
        s = pkg.load("assembly.json")
        s["parts"][2]["construction"] = "lathe"
        s["cavities"][0]["shape"] = "ellipse"
        s["parts"][3]["rotation_deg"] = [0, 0, 15]
        pkg.save("assembly.json", s)
        _, meshes, r = pkg.build()
        whats = {(u["what"], u["item"]) for u in r["unsupported"]}
        self.assertIn(("construction", s["parts"][2]["id"]), whats)
        self.assertIn(("cavity", s["cavities"][0]["id"]), whats)
        self.assertIn(("part", s["parts"][3]["id"]), whats)
        self.assertNotIn(s["parts"][2]["id"], meshes)
        self.assertNotIn("cavity_rect", r["operations_used"])

    def test_hinge_between_parts_is_reported(self):
        pkg = self.edit(self.crt)
        s = pkg.load("assembly.json")
        s["connections"].append({"a": "front", "a_edge": "u1", "b": "dial_face_1", "b_edge": "u0", "operation": "hinge"})
        pkg.save("assembly.json", s)
        _, _, r = pkg.build()
        self.assertTrue(any(u["what"] == "connection" and "hinge" in u["why"] for u in r["unsupported"]))

    # --- registration against the painted maps
    def test_misplaced_cavity_is_caught(self):
        pkg = self.edit(self.crt)
        s = pkg.load("assembly.json")
        s["cavities"][0]["centre_uv"][1] += 0.04          # 18 mm too high
        pkg.save("assembly.json", s)
        _, _, r = pkg.build()
        self.assertTrue(any("disagrees with the painted cavity mask" in e for e in r["errors"]))

    def test_misplaced_part_is_caught(self):
        pkg = self.edit(self.crt)
        a = pkg.load("asset.json")
        for p in a["panels"]:
            if p["id"] == "dial_face_1":
                p["origin_m"][0] -= 0.01
        pkg.save("asset.json", a)
        _, _, r = pkg.build()
        self.assertTrue(any("dial_1" in e and "raised_hard" in e for e in r["errors"]))

    def test_relief_is_suppressed_under_separate_parts(self):
        _, meshes, r = Package(self.crt).build()
        self.assertGreaterEqual(r["operations_used"]["relief_suppressed_under_part"], 2)
        front_y = min(v[1] for v in meshes["body"].verts)
        self.assertAlmostEqual(front_y, -0.205, places=4, msg="no painted knob bumps on the cabinet")

    def test_weld_gap_is_reported(self):
        pkg = self.edit(self.crt)
        a = pkg.load("asset.json")
        for p in a["panels"]:
            if p["id"] == "top":
                p["origin_m"][2] += 0.004
        pkg.save("asset.json", a)
        _, _, r = pkg.build()
        self.assertTrue(any("gap" in e for e in r["errors"]))

    def test_suggest_welds_reproduces_authored_box(self):
        a = json.loads((self.crt / "asset.json").read_text())
        s = json.loads((self.crt / "assembly.json").read_text())
        key = lambda c: frozenset([(c["a"], c["a_edge"]), (c["b"], c["b_edge"])])
        sug = g.suggest_welds(a, s["parts"][0]["panels"])
        self.assertEqual(set(map(key, sug)), set(map(key, s["connections"])))

    def test_kit_packer_builds_the_sofa(self):
        pack = load_module("v1_pack", ROOT / "tools" / "v1_pack.py")
        out = Path(self.work.name) / "sofa"
        shutil.copytree(ROOT / "factory" / "examples" / "factory_v1_sofa", out)
        asset = pack.pack(out / "kit.json", out)
        validate = load_module("validate_package", ROOT / "tools" / "validate_package.py")
        pre = validate.validate(asset)
        self.assertTrue(pre["pass"], pre["errors"])
        _, meshes, r = g.interpret(asset)
        self.check_clean(r, 14)
        self.assertEqual(r["operations_used"]["weld"], 120)
        lo = [min(m.bbox()[0][i] for m in meshes.values()) for i in range(3)]
        hi = [max(m.bbox()[1][i] for m in meshes.values()) for i in range(3)]
        for a, b, want in zip(lo, hi, (1.98, 0.86, 0.81)):
            self.assertAlmostEqual(b - a, want, delta=0.035)

    # --- curved panels: arc length and curved welds
    def test_curved_panels_weld_into_a_tube(self):
        folder = Path(self.work.name) / "tube"
        folder.mkdir()
        r = 0.05
        half = math.pi * r
        ppm = 400
        W, H = int(round(half * ppm)), int(round(0.2 * ppm))
        canvas = [2 * W + 40, H + 20]
        rect_a = [10, 10, 10 + W, 10 + H]
        rect_b = [30 + W, 10, 30 + 2 * W, 10 + H]
        panels = [
            {"id": "half_a", "part_id": "tube", "face": "custom", "rect_px": rect_a, "size_m": [round(half, 5), 0.2],
             "origin_m": [0, -r, 0], "u_axis": [1, 0, 0], "v_axis": [0, 0, 1], "curve": {"type": "cylinder", "axis": "u", "radius_m": -r}},
            {"id": "half_b", "part_id": "tube", "face": "custom", "rect_px": rect_b, "size_m": [round(half, 5), 0.2],
             "origin_m": [0, r, 0], "u_axis": [-1, 0, 0], "v_axis": [0, 0, 1], "curve": {"type": "cylinder", "axis": "u", "radius_m": -r}}]
        asset = {"format": "FACTORY_V1", "asset_id": "tube_test", "maps": {"canvas_px": canvas, "pixels_per_metre": ppm,
                 "albedo": "albedo.png", "signed_depth": "signed_depth.png", "part_ids": "part_ids.png"},
                 "depth_range_m": {"inward": 0.01, "outward": 0.01}, "panels": panels, "assembly_file": "assembly.json"}
        assembly = {"format": "FACTORY_V1_ASSEMBLY", "asset_id": "tube_test", "expected_manifold": False,
                    "parts": [{"id": "tube", "construction": "panel_shell", "panels": ["half_a", "half_b"],
                               "closed_solid": False, "semantic_class": "main_body"}],
                    "connections": [{"a": "half_a", "a_edge": "u1", "b": "half_b", "b_edge": "u0", "operation": "weld", "tolerance_m": 0.001},
                                    {"a": "half_a", "a_edge": "u0", "b": "half_b", "b_edge": "u1", "operation": "weld", "tolerance_m": 0.001}],
                    "cavities": []}
        (folder / "asset.json").write_text(json.dumps(asset))
        (folder / "assembly.json").write_text(json.dumps(assembly))
        Image.new("RGB", canvas, (128, 128, 128)).save(folder / "albedo.png")
        Image.new("RGB", canvas, (255, 255, 255)).save(folder / "signed_depth.png")
        ids = Image.new("RGB", canvas, (0, 0, 0))
        ImageDraw.Draw(ids).rectangle((rect_a[0], rect_a[1], rect_a[2] - 1, rect_a[3] - 1), fill=(255, 255, 255))
        ImageDraw.Draw(ids).rectangle((rect_b[0], rect_b[1], rect_b[2] - 1, rect_b[3] - 1), fill=(255, 255, 255))
        ids.save(folder / "part_ids.png")
        _, meshes, rep = g.interpret(folder / "asset.json")
        self.assertEqual(rep["errors"], [])
        m = meshes["tube"]
        st = m.edge_stats()
        self.assertEqual(st["nonmanifold_edges"], 0)
        ends = [v for v in m.verts if abs(v[2]) < 1e-9 or abs(v[2] - 0.2) < 1e-9]
        self.assertEqual(st["boundary_edges"], len(ends), "only the two open ends remain")
        self.assertGreaterEqual(len(ends) // 2, 24, "<= 7.5 degrees per segment round the tube")
        for v in m.verts:                              # every vertex on the 5 cm radius
            self.assertAlmostEqual(math.hypot(v[0], v[1]), r, places=6)


if __name__ == "__main__":
    unittest.main()
