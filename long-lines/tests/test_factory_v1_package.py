"""Regression tests for the FACTORY_V1 package validator.

These tests use generated *registered* images, then deliberately corrupt one
aspect at a time. They do not require Blender or any generative AI.
"""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "factory" / "examples" / "factory_v1_crt_tv"
spec = importlib.util.spec_from_file_location("validate_package", ROOT / "tools" / "validate_package.py")
validate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate_module)
demo_spec = importlib.util.spec_from_file_location("generate_demo_maps", DEMO / "generate_demo_maps.py")
demo = importlib.util.module_from_spec(demo_spec)
demo_spec.loader.exec_module(demo)


class PackageValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = tempfile.TemporaryDirectory()
        folder = Path(cls.source.name)
        shutil.copy(DEMO / "asset.json", folder / "asset.json")
        shutil.copy(DEMO / "assembly.json", folder / "assembly.json")
        demo.generate(folder)
        cls.baseline = folder

    @classmethod
    def tearDownClass(cls):
        cls.source.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for p in self.baseline.iterdir():
            shutil.copy(p, self.folder / p.name)

    def load(self, filename):
        return json.loads((self.folder / filename).read_text(encoding="utf-8"))

    def save(self, filename, obj):
        (self.folder / filename).write_text(json.dumps(obj, indent=2), encoding="utf-8")

    def check(self):
        return validate_module.validate(self.folder / "asset.json")

    def fail_with(self, phrase):
        result = self.check()
        self.assertFalse(result["pass"], result)
        self.assertIn(phrase, "\n".join(result["errors"]))
        return result

    def test_valid_six_view_crt_package_passes(self):
        r = self.check()
        self.assertTrue(r["pass"], r)
        self.assertEqual(r["metrics"]["panel_count"], 9)
        self.assertEqual(r["metrics"]["part_count"], 4)
        self.assertEqual(r["metrics"]["connections"], 12)
        self.assertEqual(r["metrics"]["cavities"], 1)
        self.assertGreater(r["metrics"]["raised_samples"], 0)
        self.assertGreater(r["metrics"]["recessed_samples"], 0)

    def test_missing_concept_is_rejected_by_completed_package_schema(self):
        j = self.load("asset.json")
        del j["maps"]["concept"]
        self.save("asset.json", j)
        self.fail_with("concept")

    def test_missing_semantic_role_is_rejected(self):
        j = self.load("assembly.json")
        del j["parts"][1]["semantic_class"]
        self.save("assembly.json", j)
        self.fail_with("semantic_class")

    def test_missing_map_fails(self):
        (self.folder / "part_ids.png").unlink()
        self.fail_with("cannot read part_ids")

    def test_dimension_disagreement_fails(self):
        j = self.load("asset.json")
        j["panels"][0]["size_m"][0] += .075
        self.save("asset.json", j)
        self.fail_with("UV size disagrees with physical dimensions")

    def test_missing_box_face_fails(self):
        j = self.load("assembly.json")
        j["parts"][0]["panels"].remove("bottom")
        self.save("assembly.json", j)
        self.fail_with("exactly six")

    def test_missing_required_closed_body_join_fails(self):
        j = self.load("assembly.json")
        j["connections"] = j["connections"][:-1]
        self.save("assembly.json", j)
        self.fail_with("missing join")

    def test_unphysical_joint_gap_fails(self):
        j = self.load("asset.json")
        p = next(p for p in j["panels"] if p["id"] == "right")
        p["origin_m"][0] += .03
        self.save("asset.json", j)
        self.fail_with("edge gap")

    def test_bad_part_id_rgb_fails(self):
        p = self.folder / "part_ids.png"
        with Image.open(p) as im:
            img = im.copy()
        ImageDraw.Draw(img).rectangle((120, 120, 200, 200), fill=(90, 90, 90))
        img.save(p)
        self.fail_with("unknown semantic colours")

    def test_semantic_part_id_mismatch_fails_even_with_valid_rgb(self):
        p = self.folder / "part_ids.png"
        with Image.open(p) as im:
            img = im.copy()
        # Screen UV island must be GLASS yellow, not another valid palette ID.
        rect = next(p for p in self.load("asset.json")["panels"]
                    if p["id"] == "screen_face")["rect_px"]
        ImageDraw.Draw(img).rectangle(tuple(rect), fill=(255, 255, 255))
        img.save(p)
        self.fail_with("does not represent expected class glass")

    def test_unencoded_depth_rgb_fails(self):
        p = self.folder / "signed_depth.png"
        with Image.open(p) as im:
            img = im.copy()
        ImageDraw.Draw(img).rectangle((120, 120, 200, 200), fill=(150, 80, 230))
        img.save(p)
        self.fail_with("outside red-white-blue")

    def test_reference_scale_fails(self):
        j = self.load("asset.json")
        j["orthographic_views"]["front"][2] -= 50
        self.save("asset.json", j)
        self.fail_with("orthographic crop dimension mismatch")

    def test_atlas_registration_fails(self):
        p = self.folder / "signed_depth.png"
        with Image.open(p) as im:
            im.resize((1792, 1024)).save(p)
        self.fail_with("dimensions")

    def test_duplicated_uv_part_ownership_fails(self):
        j = self.load("assembly.json")
        j["parts"][1]["panels"].append("front")
        self.save("assembly.json", j)
        self.fail_with("exactly one assembly part")

    def test_bad_uv_overlap_fails(self):
        j = self.load("asset.json")
        j["panels"][1]["rect_px"] = j["panels"][0]["rect_px"]
        self.save("asset.json", j)
        self.fail_with("overlaps")

    def test_symlink_escape_rejected(self):
        j = self.load("asset.json")
        j["maps"]["albedo"] = "../outside.png"
        self.save("asset.json", j)
        self.fail_with("does not match")  # schema disallows traversal


if __name__ == "__main__":
    unittest.main()
