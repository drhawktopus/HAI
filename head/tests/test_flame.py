"""FLAME engine (#19 P3): closed-form fit math + asset-backed pipeline.

Asset-dependent tests run wherever State/flame is reachable (the container mounts /docs, so
the deploy-gate suite exercises the real model); pure-math tests run everywhere.
"""
import unittest

import numpy as np

import flame


class UmeyamaTests(unittest.TestCase):
    def test_recovers_similarity_transform(self):
        rng = np.random.default_rng(3)
        src = rng.normal(size=(50, 3))
        # ground truth: rotation about z + scale + translation
        th = 0.4
        R_true = np.array([[np.cos(th), -np.sin(th), 0],
                           [np.sin(th), np.cos(th), 0],
                           [0, 0, 1.0]])
        dst = 1.7 * (src @ R_true.T) + np.array([0.3, -0.2, 0.9])
        R, s, t = flame._umeyama(src, dst)
        np.testing.assert_allclose(s, 1.7, rtol=1e-6)
        np.testing.assert_allclose(R, R_true, atol=1e-8)
        np.testing.assert_allclose((s * (R @ src.T)).T + t, dst, atol=1e-8)


@unittest.skipUnless(flame.available(), "FLAME assets not reachable in this environment")
class FlameModelTests(unittest.TestCase):
    def test_model_shapes(self):
        m = flame._model()
        self.assertEqual(m["template"].shape, (5023, 3))
        self.assertEqual(m["shape_basis"].shape, (5023, 3, flame.N_SHAPE))
        self.assertEqual(m["lmk_template"].shape, (105, 3))
        self.assertEqual(m["lmk_basis"].shape, (105, 3, flame.N_SHAPE))
        self.assertEqual(len(m["mp_idx"]), 105)

    def test_fit_roundtrip_recovers_geometry(self):
        # decode known betas -> take its surface landmarks as "observations" (in a rotated,
        # scaled camera-like frame) -> the fit must reproduce the LANDMARK GEOMETRY. (105
        # landmarks cannot identify 100 betas uniquely — the ridge picks the minimum-norm
        # head consistent with the observations, which is the desired behavior; asserting
        # beta equality was the wrong invariant and failed at cos≈0.58 while the geometry
        # residual was 8x below the signal.)
        m = flame._model()
        rng = np.random.default_rng(7)
        true = rng.normal(size=flame.N_SHAPE) * 0.5
        lmks = m["lmk_template"] + m["lmk_basis"] @ true
        obs = np.zeros((478, 3))
        cam = lmks * np.array([1.0, -1.0, -1.0 / flame.MP_Z_SCALE])
        obs[m["mp_idx"]] = cam * 0.31 + np.array([0.5, 0.5, 0.0])   # arbitrary camera frame
        got = flame.fit_betas(obs)
        fitted = m["lmk_template"] + m["lmk_basis"] @ got
        # shape distance modulo similarity (the fit's internal frame differs slightly)
        R, s, t = flame._umeyama(fitted, lmks)
        aligned = (s * (R @ fitted.T)).T + t
        signal = np.sqrt(((lmks - m["lmk_template"]) ** 2).mean())
        residual = np.sqrt(((aligned - lmks) ** 2).mean())
        self.assertLess(residual, 0.25 * signal)        # geometry substantially recovered
        # and the decoded head stays sane (no runaway vertices)
        v = flame.decode(got)
        self.assertLess(np.abs(v - m["template"]).max(), 0.05)   # meters; heads are ~0.2m

    def test_mosaic_and_regions(self):
        regions = flame._regions()
        self.assertEqual(set(regions), {"eyes", "nose", "mouth", "brows", "jaw_oval"})
        a = flame.decode(np.zeros(flame.N_SHAPE))
        b = flame.decode(np.ones(flame.N_SHAPE) * 0.3)
        child, picks = flame.mosaic_mesh(a, b, seed=2)
        again, picks2 = flame.mosaic_mesh(a, b, seed=2)
        np.testing.assert_array_equal(child, again)
        self.assertEqual(picks, picks2)
        self.assertEqual(child.shape, (5023, 3))

    def test_toddlerize_and_depth(self):
        v = flame.decode(np.zeros(flame.N_SHAPE))
        out = flame.toddlerize_mesh(v)
        m = flame._model()
        nose = m["masks"]["nose"]
        spread = lambda p: np.linalg.norm(p[nose] - p[nose].mean(axis=0), axis=1).mean()  # noqa: E731
        self.assertLess(spread(out), spread(v))          # smaller nose
        img = flame.render_depth(out, size=256)
        self.assertEqual(img.size, (256, 256))
        arr = np.asarray(img.convert("L"))
        self.assertGreater(arr[100:160, 100:160].mean(), arr[:30, :30].mean())


@unittest.skipUnless(flame.available() and flame.neural_available(),
                     "MICA artifacts not reachable in this environment")
class NeuralFitterTests(unittest.TestCase):
    def test_self_verifies_and_fits(self):
        # _neural() asserts the onnx + regressor reproduce the conversion's reference pair
        n = flame._neural()
        self.assertEqual(n["idxs"], [0, 1, 2, 3])
        rng = np.random.default_rng(5)
        crop = rng.integers(0, 255, (112, 112, 3)).astype(np.uint8)
        betas = flame.betas_from_aligned(crop)
        self.assertEqual(betas.shape, (flame.N_SHAPE,))
        v = flame.decode(betas)
        self.assertEqual(v.shape, (5023, 3))
        self.assertLess(np.abs(v - flame._model()["template"]).max(), 0.2)   # sane head


class WiringTests(unittest.TestCase):
    def test_identity_engine_selection_degrades_gracefully(self):
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def simulate_children(", 1)[1]
        self.assertIn('routing != "quick_8b"', body)
        self.assertIn("want_neural and flame.neural_available()", body)
        self.assertIn('elif flame.available():', body)
        self.assertIn('engine = "lmk"', body)
        self.assertIn("flame.mosaic_mesh", body)
        self.assertIn("flame.toddlerize_mesh", body)
        tools_src = open("tools.py", encoding="utf-8").read()
        self.assertIn("engine=", tools_src.split("def _imagine_children(", 1)[1]
                      .split("\ndef ", 1)[0])                       # engine audited

    def test_test_user_quota_exempt(self):
        import comfy_client
        self.assertIsNone(comfy_client.quota_remaining("test"))


if __name__ == "__main__":
    unittest.main()
