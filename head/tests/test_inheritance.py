"""Invariant tests for inheritance.inherit_structure — polygenic blend behaves sanely."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from genetic_mesh import inheritance


def _fp(**vals):
    """Build a fingerprint dict; projection features get low confidence."""
    proj = {"cheek_projection", "brow_projection", "chin_projection", "nasal_projection"}
    return {k: {"value": v, "confidence": 0.4 if k in proj else 0.9,
                "kind": "proj" if k in proj else "inplane"} for k, v in vals.items()}


A = _fp(fwhr=2.0, jaw_taper=0.9, cheek_projection=0.5)
B = _fp(fwhr=2.4, jaw_taper=0.8, cheek_projection=-0.2)


class InheritanceTests(unittest.TestCase):
    def test_deterministic(self):
        c1, _ = inheritance.inherit_structure(A, B, seed=7)
        c2, _ = inheritance.inherit_structure(A, B, seed=7)
        self.assertEqual(c1, c2)

    def test_siblings_differ(self):
        c1, _ = inheritance.inherit_structure(A, B, seed=1)
        c2, _ = inheritance.inherit_structure(A, B, seed=2)
        self.assertNotEqual(c1, c2)

    def test_within_parental_envelope(self):
        for seed in range(40):
            child, _ = inheritance.inherit_structure(A, B, seed=seed)
            for f in A:
                lo, hi = min(A[f]["value"], B[f]["value"]), max(A[f]["value"], B[f]["value"])
                margin = 0.15 * (hi - lo) + 1e-3
                self.assertGreaterEqual(child[f], lo - margin - 1e-6)
                self.assertLessEqual(child[f], hi + margin + 1e-6)

    def test_identical_parents_no_variation(self):
        child, story = inheritance.inherit_structure(A, A, seed=5)
        for f in A:
            self.assertAlmostEqual(child[f], A[f]["value"], places=3)
        self.assertEqual(story, [])   # nothing leans when parents match

    def test_low_conf_pulls_central(self):
        """Uncertainty -> conservatism: a low-confidence feature leans LESS (stays nearer the midpoint)
        than a high-confidence one with the same parent gap, so it does NOT spread more."""
        P = _fp(jaw_taper=0.5, cheek_projection=0.5)   # jaw_taper high-conf, cheek_projection low-conf
        Q = _fp(jaw_taper=1.0, cheek_projection=1.0)   # equal 0.5 gap on both
        import statistics
        inplane = statistics.pstdev([inheritance.inherit_structure(P, Q, seed=s)[0]["jaw_taper"] for s in range(300)])
        proj = statistics.pstdev([inheritance.inherit_structure(P, Q, seed=s)[0]["cheek_projection"] for s in range(300)])
        self.assertLessEqual(proj, inplane + 1e-9)


if __name__ == "__main__":
    unittest.main()
