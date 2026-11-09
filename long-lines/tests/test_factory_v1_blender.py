"""End-to-end FACTORY_V1 test: validate -> build -> export GLB -> fresh-Blender reimport QA.

Skipped when Blender is not installed (set BLENDER=/path/to/blender to choose one). Runs
the real factory command on both example packages and asserts on its qa.json, which is
measured on the *reimported* GLB, not on the in-memory build.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLENDER = os.environ.get("BLENDER") or shutil.which("blender")
EXAMPLES = ROOT / "factory" / "examples"


def regenerate_maps():
    for script, arg in ((EXAMPLES / "factory_v1_crt_tv" / "generate_demo_maps.py", None),
                        (EXAMPLES / "factory_v1_toaster" / "generate_maps.py", None)):
        subprocess.run([sys.executable, str(script)], check=True, capture_output=True)
    subprocess.run([sys.executable, str(ROOT / "tools" / "v1_pack.py"), str(EXAMPLES / "factory_v1_sofa" / "kit.json")],
                   check=True, capture_output=True)


@unittest.skipUnless(BLENDER, "Blender not installed")
class V1BlenderEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        regenerate_maps()
        cls.out = tempfile.TemporaryDirectory()
        spec = importlib.util.spec_from_file_location("factory_v1", ROOT / "tools" / "factory_v1.py")
        cls.cmd = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cmd)

    @classmethod
    def tearDownClass(cls):
        cls.out.cleanup()

    def run_package(self, name, n_parts, n_openings):
        qa = self.cmd.run(EXAMPLES / name / "asset.json", os.path.join(self.out.name, name), BLENDER, render=False)
        self.assertTrue(qa["pass"], json.dumps(qa["failures"], indent=1))
        self.assertEqual(len(qa["parts"]), n_parts)
        self.assertTrue(qa["all_parts_built"] and qa["manifold_where_required"] and qa["uvs_survived_glb"])
        self.assertEqual(len(qa["openings"]), n_openings)
        self.assertTrue(all(o["ok"] for o in qa["openings"]))
        self.assertEqual(qa["unsupported"], [])
        self.assertTrue(all(p["in_glb"] and p["class_kept"] for p in qa["parts"].values()))
        self.assertTrue(all(p["touches_parent"] in (None, True) for p in qa["parts"].values()))
        return qa

    def test_crt(self):
        self.run_package("factory_v1_crt_tv", 4, 1)

    def test_toaster(self):
        self.run_package("factory_v1_toaster", 7, 2)

    def test_sofa(self):
        self.run_package("factory_v1_sofa", 14, 0)


if __name__ == "__main__":
    unittest.main()
