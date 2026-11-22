"""Geometric-invariant tests for structure.measure_structure — check the metric behaves correctly on
synthetic meshes (not brittle string checks): widening -> higher fWHR, taller -> lower fWHR, confidence
tags present, projection features flagged low-confidence."""
import os, sys, unittest
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from genetic_mesh import structure


def _synthetic_face(width=1.0, height=1.0, depth=0.5, n=12):
    """A crude but valid face point cloud with the five named regions, parameterised by proportions."""
    rng = np.random.default_rng(0)
    pts, regions, cursor = [], {}, 0

    def blob(cx, cy, cz, sx, sy, sz, k):
        nonlocal cursor
        p = np.column_stack([rng.normal(cx, sx, k), rng.normal(cy, sy, k), rng.normal(cz, sz, k)])
        idx = list(range(cursor, cursor + k)); cursor += k
        pts.append(p); return idx

    regions["brows"] = blob(0, 0.40 * height, 0.1 * depth, 0.30 * width, 0.03 * height, 0.02 * depth, n)
    regions["eyes"] = blob(0, 0.25 * height, 0.0, 0.30 * width, 0.04 * height, 0.02 * depth, n)
    regions["nose"] = blob(0, 0.05 * height, 0.5 * depth, 0.06 * width, 0.12 * height, 0.25 * depth, n)
    regions["mouth"] = blob(0, -0.20 * height, 0.1 * depth, 0.12 * width, 0.03 * height, 0.02 * depth, n)
    # jaw oval: a rim around the lower face at full width
    jaw = []
    for t in np.linspace(-np.pi, 0, 3 * n):
        jaw.append([0.5 * width * np.cos(t), (-0.5 * height) * abs(np.sin(t)) + 0.1 * height, 0.05 * depth])
    idx = list(range(cursor, cursor + len(jaw))); cursor += len(jaw)
    pts.append(np.array(jaw)); regions["jaw_oval"] = idx
    return np.vstack(pts), regions


class StructureTests(unittest.TestCase):
    def test_shape_and_tags(self):
        V, R = _synthetic_face()
        f = structure.measure_structure(V, R)
        for feat in ("fwhr", "gonial_width", "jaw_taper", "cheek_projection", "nasal_projection"):
            self.assertIn(feat, f)
            self.assertIn("value", f[feat]); self.assertIn("confidence", f[feat]); self.assertIn("kind", f[feat])
        self.assertEqual(f["fwhr"]["kind"], "inplane")
        self.assertEqual(f["cheek_projection"]["kind"], "proj")
        self.assertGreater(f["fwhr"]["confidence"], f["cheek_projection"]["confidence"])

    def test_wider_face_raises_fwhr(self):
        narrow = structure.measure_structure(*_synthetic_face(width=1.0, height=1.4))
        wide = structure.measure_structure(*_synthetic_face(width=1.6, height=1.0))
        self.assertGreater(wide["fwhr"]["value"], narrow["fwhr"]["value"])

    def test_scale_invariance(self):
        V, R = _synthetic_face(width=1.0, height=1.0)
        base = structure.measure_structure(V, R)["fwhr"]["value"]
        scaled = structure.measure_structure(V * 3.0, R)["fwhr"]["value"]   # uniform scale
        self.assertAlmostEqual(base, scaled, places=5)


if __name__ == "__main__":
    unittest.main()
