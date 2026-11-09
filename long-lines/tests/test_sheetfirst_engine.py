"""Pure geometry regression tests: run without Blender."""
import importlib.util
import json
import math
import os
import tempfile
import unittest

from PIL import Image

PATH = os.path.join(os.path.dirname(__file__), "..", "tools", "propkit", "sheetfirst_engine.py")
spec = importlib.util.spec_from_file_location("sheetfirst_engine", PATH)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class SheetFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec = {
            "schema": 1, "asset_id": "test_sheet",
            "atlas": {"albedo": "paint.png", "depth": "depth.png", "size_px": [100, 100]},
            "depth_range_m": {"inward": .02, "outward": .03},
            "panels": [{
                "name": "front", "size_m": [.5, .5], "rect_px": [10, 10, 60, 60],
                "origin_m": [-.25, -.25, 0], "u_axis": [1, 0, 0],
                "v_axis": [0, 0, 1], "grid": [4, 4]
            }]
        }
        Image.new("RGB", (100, 100), (145, 135, 120)).save(self.file("paint.png"))
        Image.new("RGB", (100, 100), (255, 255, 255)).save(self.file("depth.png"))
        self.write_spec()

    def file(self, name):
        return os.path.join(self.tmp.name, name)

    def write_spec(self):
        with open(self.file("manifest.json"), "w") as f:
            json.dump(self.spec, f)

    def read(self):
        return engine.read_manifest(self.file("manifest.json"))

    def test_physical_dimensions_and_uv_preserved(self):
        spec, paint, depth = self.read()
        result = engine.build_geometry(spec, Image.open(depth).convert("RGB"))["front"]
        self.assertEqual(len(result["vertices"]), 25)
        self.assertEqual(len(result["faces"]), 16)
        self.assertAlmostEqual(result["vertices"][0][0], -.25)
        self.assertAlmostEqual(result["vertices"][4][0], .25)
        self.assertAlmostEqual(result["uv"][0][0], .105)
        self.assertAlmostEqual(result["uv"][0][1], .405)
        self.assertAlmostEqual(result["uv"][-1][0], .595)
        self.assertAlmostEqual(result["uv"][-1][1], .895)

    def test_red_white_blue_signed_depth(self):
        a = Image.new("RGB", (100, 100), (255, 255, 255))
        for y in range(10, 60):
            for x in range(10, 35):
                a.putpixel((x, y), (255, 0, 0))
            for x in range(35, 60):
                a.putpixel((x, y), (0, 0, 255))
        a.save(self.file("depth.png"))
        spec, _, path = self.read()
        m = engine.build_geometry(spec, Image.open(path).convert("RGB"))["front"]
        # Front faces -Y. Red goes toward +Y (inward); blue toward -Y.
        self.assertAlmostEqual(m["vertices"][1][1], -.23, places=3)
        self.assertAlmostEqual(m["vertices"][3][1], -.28, places=3)

    def test_curved_panel_uses_arc_length_and_keeps_uv(self):
        self.spec["panels"][0]["curve"] = {"type": "cylinder", "axis": "u", "radius_m": .5}
        self.write_spec()
        spec, _, _ = self.read()
        m = engine.build_geometry(spec)["front"]
        self.assertAlmostEqual(m["vertices"][4][0], -.25 + .5 * math.sin(1), places=5)
        self.assertAlmostEqual(m["vertices"][4][1], -.25 - .5 * (1 - math.cos(1)), places=5)
        self.assertAlmostEqual(m["uv"][-1][0], .595)

    def test_optional_vector_map_moves_vertices_in_local_tangent_coordinates(self):
        s, _, _ = self.read()
        vec = Image.new("RGB", (100, 100), (255, 128, 128))
        s["vector_range_m"] = [.025, 0, 0]
        mesh = engine.build_geometry(s, vector_image=vec)["front"]
        self.assertAlmostEqual(mesh["vertices"][0][0], -.225, places=4)
        self.assertAlmostEqual(mesh["vertices"][0][1], -.25, places=4)

    def test_vector_displacement_on_curved_surface_uses_rotated_basis(self):
        s, _, _ = self.read()
        s["panels"][0]["curve"] = {"type": "cylinder", "axis": "u", "radius_m": .5}
        s["vector_range_m"] = [.03, 0, 0]
        baseline = engine.build_geometry(s)["front"]["vertices"][4]
        shifted = engine.build_geometry(s, vector_image=Image.new("RGB", (100,100),(255,128,128)))["front"]["vertices"][4]
        self.assertAlmostEqual(shifted[0] - baseline[0], .03 * math.cos(1), places=4)
        self.assertAlmostEqual(shifted[1] - baseline[1], -.03 * math.sin(1), places=4)

    def test_wrong_uv_aspect_rejected(self):
        self.spec["panels"][0]["rect_px"] = [10, 10, 75, 60]
        self.write_spec()
        with self.assertRaisesRegex(ValueError, "proportions"):
            self.read()

    def test_intentional_stretch_requires_opt_in(self):
        self.spec["panels"][0]["rect_px"] = [10, 10, 75, 60]
        self.spec["panels"][0]["allow_stretch"] = True
        self.write_spec()
        self.read()

    def test_missing_texture_rejected(self):
        self.spec["atlas"]["albedo"] = "missing.png"
        self.write_spec()
        with self.assertRaisesRegex(ValueError, "missing painted atlas"):
            self.read()

    def test_wrong_image_dimensions_rejected(self):
        self.spec["atlas"]["size_px"] = [200, 200]
        self.write_spec()
        with self.assertRaisesRegex(ValueError, "dimensions disagree"):
            self.read()

    def test_non_unit_axes_rejected(self):
        self.spec["panels"][0]["u_axis"] = [2, 0, 0]
        self.write_spec()
        with self.assertRaisesRegex(ValueError, "unit length"):
            self.read()

    def test_joined_panel_seam_matches(self):
        a = self.spec["panels"][0]
        b = dict(a)
        b["name"] = "right"
        b["origin_m"] = [.25, -.25, 0]
        b["u_axis"] = [0, 1, 0]
        b["v_axis"] = [0, 0, 1]
        self.spec["panels"].append(b)
        self.spec["seams"] = [{"a": "front", "a_edge": "u1", "b": "right", "b_edge": "u0"}]
        self.write_spec()
        s, _, _ = self.read()
        self.assertEqual(engine.validate_seams(s, engine.build_geometry(s)), [])
        self.spec["panels"][1]["origin_m"] = [.27, -.25, 0]
        self.write_spec()
        s, _, _ = self.read()
        self.assertTrue(engine.validate_seams(s, engine.build_geometry(s)))

    def test_depth_range_clamped(self):
        self.spec["depth_range_m"]["outward"] = .8
        self.write_spec()
        s, _, _ = self.read()
        with self.assertRaisesRegex(ValueError, "depth_range"):
            engine.build_geometry(s)

    def test_displacement_is_zero_without_depth_map(self):
        s, _, _ = self.read()
        m = engine.build_geometry(s, None)["front"]
        self.assertTrue(all(abs(v[1] + .25) < 1e-8 for v in m["vertices"]))


if __name__ == "__main__":
    unittest.main()
