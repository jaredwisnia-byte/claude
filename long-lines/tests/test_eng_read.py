"""Regression test for the engineering-drawing reader (tools/eng_read.py), no Blender needed.

Fixture: front/back/left/right/top/bottom drawings of a flat-cushion three-seat sofa, drawn by
tools/eng_draw.py from a model with a known shape, plus that model's occupancy on the reader's
block grid. The silhouette solid (what the hull lines build) fills the seat up to arm height;
reading the lines must carve it down to the cushions and keep the arms and back.
"""
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import eng_read  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "eng_sofa"


class EngReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = eng_read.read(str(FIX), verbose=False)
        truth = json.loads((FIX / "truth_cells.json").read_text())
        cls.truth_planes = truth["planes"]
        cls.truth = np.array(truth["solid"], bool)

    def test_reading_explains_the_drawings_better_than_the_silhouette_solid(self):
        self.assertLess(self.res["cost_final"]["total"], 0.5 * self.res["cost_silhouette"]["total"])

    def test_blades_found_the_seat_and_arm_heights(self):
        z = [round(v * 0.81, 2) for v in self.res["planes"]["z"]]
        for want in (0.14, 0.30, 0.43, 0.56, 0.66):
            self.assertTrue(any(abs(v - want) <= 0.01 for v in z), (want, z))

    def test_solid_matches_the_real_sofa(self):
        same_grid = all(len(self.res["planes"][k]) == len(self.truth_planes[k]) and
                        np.allclose(self.res["planes"][k], self.truth_planes[k], atol=0.004) for k in "xyz")
        self.assertTrue(same_grid, "grid planes moved; regenerate the fixture's truth cells")
        got = np.array(self.res["solid"], bool)
        hull = np.array(self.res["silhouette_solid"], bool)
        iou = lambda a, b: (a & b).sum() / (a | b).sum()
        self.assertGreater(iou(got, self.truth), 0.97)
        self.assertGreater(iou(got, self.truth), iou(hull, self.truth) + 0.05)


if __name__ == "__main__":
    unittest.main()
