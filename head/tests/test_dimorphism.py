"""Standalone tests for Stage-1 gender dimorphism — pure numpy, no FLAME/orchestrator/ComfyUI.
Run: cd E:\\SSDShared\\genetic-mesh && python -m pytest -q"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from genetic_mesh import dimorphism


def _fixture():
    rng = np.random.default_rng(0)
    jaw = rng.normal(0, 1, (20, 3))
    brows = rng.normal(0, 1, (10, 3)); brows[:, 1] += 3.0   # brow region sits higher (+y)
    verts = np.vstack([jaw, brows])
    regions = {"jaw_oval": np.arange(0, 20), "brows": np.arange(20, 30)}
    return verts, regions


def _jaw_width(v, r):
    j = v[r["jaw_oval"]]
    return float(j[:, 0].max() - j[:, 0].min())


class DimorphismTests(unittest.TestCase):
    def test_boy_widens_the_jaw(self):
        v, r = _fixture()
        out = dimorphism.apply_gender_dimorphism(v, r, "boy", strength=1.0)
        self.assertGreater(_jaw_width(out, r), _jaw_width(v, r))

    def test_girl_narrows_the_jaw(self):
        v, r = _fixture()
        out = dimorphism.apply_gender_dimorphism(v, r, "girl", strength=1.0)
        self.assertLess(_jaw_width(out, r), _jaw_width(v, r))

    def test_boy_and_girl_are_opposite(self):
        v, r = _fixture()
        boy = dimorphism.apply_gender_dimorphism(v, r, "boy", strength=1.0)
        girl = dimorphism.apply_gender_dimorphism(v, r, "girl", strength=1.0)
        self.assertGreater(_jaw_width(boy, r), _jaw_width(girl, r))

    def test_brow_ridge_projects_more_for_boys(self):
        v, r = _fixture()
        out = dimorphism.apply_gender_dimorphism(v, r, "boy", strength=1.0)
        cb = v[r["brows"]].mean(axis=0)[2]
        spread_in = float(np.abs(v[r["brows"]][:, 2] - cb).sum())
        spread_out = float(np.abs(out[r["brows"]][:, 2] - cb).sum())
        self.assertGreater(spread_out, spread_in)

    def test_noops(self):
        v, r = _fixture()
        for gender, strength in [("child", 1.0), ("boy", 0), ("", 1.0), (None, 1.0)]:
            out = dimorphism.apply_gender_dimorphism(v, r, gender, strength=strength)
            np.testing.assert_array_equal(out, v)

    def test_does_not_mutate_input(self):
        v, r = _fixture()
        v0 = v.copy()
        dimorphism.apply_gender_dimorphism(v, r, "boy", strength=1.0)
        np.testing.assert_array_equal(v, v0)

    def test_missing_brows_region_is_fine(self):
        v, r = _fixture()
        out = dimorphism.apply_gender_dimorphism(v, {"jaw_oval": r["jaw_oval"]}, "boy")
        self.assertGreater(_jaw_width(out, r), _jaw_width(v, r))


if __name__ == "__main__":
    unittest.main()
