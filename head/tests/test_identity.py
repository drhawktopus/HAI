"""Pure-math invariants and hardening constants of the identity pipeline - the heavy parts
(mediapipe inference, ComfyUI generation) are exercised live, not here."""
import pathlib
import tempfile
import unittest

import numpy as np

import comfy_client
import identity


class MosaicTests(unittest.TestCase):
    def test_regions_inherit_from_one_parent(self):
        a = np.zeros((478, 3))
        b = np.ones((478, 3))
        child, picks = identity.mosaic_child(a, b, seed=7)
        self.assertEqual(set(picks), set(identity.REGIONS))
        # a few border landmarks sit on two region rings (jaw/mouth share 0 and 17; the later
        # region's pick wins there) — assert on the indices unique to each region
        from collections import Counter
        counts = Counter(i for v in identity.REGIONS.values() for i in v)
        for region, idx in identity.REGIONS.items():
            unique = [i for i in idx if counts[i] == 1]
            want = 0.30 if picks[region] == "a" else 0.70   # 70/30 toward the picked parent
            np.testing.assert_allclose(child[unique], want)
        untouched = sorted(set(range(478)) - {i for v in identity.REGIONS.values() for i in v})
        np.testing.assert_allclose(child[untouched], 0.5)   # everything else blends evenly

    def test_deterministic_per_seed_and_siblings_differ(self):
        a, b = np.zeros((478, 3)), np.ones((478, 3))
        c1, p1 = identity.mosaic_child(a, b, seed=1)
        c1again, p1again = identity.mosaic_child(a, b, seed=1)
        np.testing.assert_array_equal(c1, c1again)
        self.assertEqual(p1, p1again)
        picks = [identity.mosaic_child(a, b, seed=s)[1] for s in range(12)]
        self.assertGreater(len({tuple(sorted(p.items())) for p in picks}), 1)   # siblings vary


class ToddlerizeTests(unittest.TestCase):
    def test_structural_transforms(self):
        rng = np.random.default_rng(0)
        pts = rng.normal(size=(478, 3)) * 0.3
        out = identity.toddlerize(pts)
        nose_y = pts[4, 1]
        below = pts[:, 1] > nose_y
        # lower face compressed toward the nose line (short mandible)
        self.assertLess((out[below, 1] - nose_y).max(), (pts[below, 1] - nose_y).max())
        # nose shrinks about its centroid
        nose = identity.REGIONS["nose"]
        spread = lambda p: np.linalg.norm(p[nose] - p[nose].mean(axis=0), axis=1).mean()  # noqa: E731
        self.assertLess(spread(out), spread(pts))


class DepthRenderTests(unittest.TestCase):
    def test_depth_render_shape_and_content(self):
        rng = np.random.default_rng(1)
        pts = rng.normal(size=(478, 3)) * 0.3
        img = identity.render_depth(pts, size=256)
        self.assertEqual(img.size, (256, 256))
        self.assertEqual(img.mode, "RGB")
        arr = np.asarray(img.convert("L"))
        self.assertGreater(arr[96:160, 96:160].mean(), arr[:32, :32].mean())   # face > background


class WorkflowTests(unittest.TestCase):
    def test_child_workflow_shape_and_hardening(self):
        wf = identity.build_child_workflow("depth.png", "a.png", "b.png", seed=5)["prompt"]
        # negative prompt blocks clothing/headwear bleed
        for word in ("hat", "costume", "hood", "headwear"):
            self.assertIn(word, wf["3"]["inputs"]["text"])
        # both parents chained at the measured weight band
        self.assertEqual(wf["31"]["inputs"]["weight"], identity.IPADAPTER_FACE_WEIGHT)
        self.assertEqual(wf["32"]["inputs"]["weight"], identity.IPADAPTER_FACE_WEIGHT)
        self.assertTrue(0.3 <= identity.IPADAPTER_FACE_WEIGHT <= 0.7)   # 0.9 clones the scene
        self.assertEqual(wf["30"]["inputs"]["preset"], "PLUS FACE (portraits)")
        # geometry via union-controlnet DEPTH, full denoise
        self.assertEqual(wf["22"]["inputs"]["type"], "depth")
        self.assertEqual(wf["23"]["inputs"]["strength"], identity.CN_DEPTH_STRENGTH)
        self.assertEqual(wf["5"]["inputs"]["denoise"], 1.0)

    def test_latest_uploads_ordering(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        orig = comfy_client._images_dir
        comfy_client._images_dir = lambda: tmp
        try:
            d = comfy_client.uploads_dir("F")
            d.mkdir(parents=True)
            for n in ("20260101T000000Z-a.png", "20260711T000000Z-b.jpg",
                      "20260710T000000Z-c.png"):
                (d / n).write_bytes(b"x")
            self.assertEqual(identity.latest_uploads("F", 2),
                             ["20260711T000000Z-b.jpg", "20260710T000000Z-c.png"])
            self.assertEqual(identity.latest_uploads("Nobody"), [])
        finally:
            comfy_client._images_dir = orig


class FamilyPhotoTests(unittest.TestCase):
    """Scene-first architecture (the collage-then-unify approach failed live 4 ways: photo-wall
    tiles, ghost rectangles, washed haze — img2img preserves global structure at identity-safe
    denoise, so the group scene must be GENERATED and the identities inpainted in)."""

    def test_face_mask_geometry(self):
        mask = identity.face_mask_image((1216, 832), (0.5, 0.4, 0.1, 0.15))
        self.assertEqual(mask.size, (1216, 832))
        arr = np.asarray(mask.convert("L"))
        self.assertGreater(arr[int(832 * 0.4), int(1216 * 0.5)], 200)   # face center: white
        self.assertLess(arr[50, 50], 20)                                # far corner: black

    def test_inpaint_workflow_shape(self):
        wf = identity.build_face_inpaint_workflow("scene.png", "mask.png", "ref.png",
                                                  seed=9, subject="parent")["prompt"]
        self.assertEqual(wf["12"]["class_type"], "SetLatentNoiseMask")   # masked region only
        self.assertEqual(wf["12"]["inputs"]["samples"], ["9", 0])        # from encoded scene
        self.assertEqual(wf["5"]["inputs"]["latent_image"], ["12", 0])
        self.assertEqual(wf["5"]["inputs"]["denoise"], identity.FACE_INPAINT_DENOISE)
        self.assertEqual(wf["31"]["inputs"]["weight"], identity.FACE_INPAINT_WEIGHT)
        self.assertEqual(wf["31"]["inputs"]["image"], ["40", 0])         # ONE identity per pass
        self.assertIn("parent", wf["2"]["inputs"]["text"])

    def test_rung1_baseline_engine(self):
        # the bake-off control: engine="baseline" = pure IPAdapter appearance blending, no
        # depth/geometry conditioning (what commodity baby-face apps do)
        wf = identity.build_child_workflow(None, "a.png", "b.png", seed=3,
                                           use_depth=False)["prompt"]
        self.assertNotIn("21", wf)                      # no ControlNet loader
        self.assertNotIn("8", wf)                       # no depth image
        self.assertEqual(wf["5"]["inputs"]["positive"], ["2", 0])   # raw conditioning
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def simulate_children(", 1)[1]
        self.assertIn('engine == "flame"', body)
        self.assertIn('engine == "lmk"', body)
        self.assertIn("use_depth=depth_name is not None", body)     # baseline skips geometry

    def test_structural_inheritance_wired_and_runs(self):
        # guard the Stage 2-4 wiring: a refactor must not silently drop structural inheritance
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def simulate_children(", 1)[1]
        self.assertIn("_parent_fingerprints(meshes", body)
        self.assertIn("inherit_structure(", body)
        self.assertIn("apply_structural_hand(", body)
        # runtime: _parent_fingerprints must return a fingerprint pair on valid input, never silently
        # None (None == a swallowed exception — this is the test that would have caught the flame NameError)
        import pathlib as _pl
        import flame
        saved_r, saved_m = flame._regions, identity._structure.measure_structure
        try:
            flame._regions = lambda: {"eyes": [0], "nose": [0], "mouth": [0], "brows": [0], "jaw_oval": [0]}
            identity._structure.measure_structure = lambda mesh, regions: {
                "fwhr": {"value": 1.0, "confidence": 0.9, "kind": "inplane"}}
            fps = identity._parent_fingerprints([object(), object()], ["a.png", "b.png"], _pl.Path("."), use_vlm=False)
            self.assertIsNotNone(fps)          # None here means an exception was swallowed
            self.assertEqual(len(fps), 2)
        finally:
            flame._regions, identity._structure.measure_structure = saved_r, saved_m

    def test_scene_first_flow(self):
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def family_photo(", 1)[1]
        self.assertIn("simulate_children(user", body)         # kids exist before the scene
        self.assertIn("comfy_client.build_workflow(", body)   # txt2img scene, not a collage
        self.assertIn("detect_faces_ex(scene)", body)
        self.assertIn("for attempt in range(6):", body)       # reroll until EXACT face count
        self.assertIn("len(faces) == n and ages_ok", body)    # count AND composition gate
        self.assertIn("ages_ok=", body)
        self.assertNotIn("min(candidates", body)
        self.assertIn("after 6 tries", body)
        self.assertIn("background people", body)               # crowd negatives (v7: 6,6,5,5)
        self.assertIn("match_faces(scene, faces, ref_paths, ref_sexes=ref_sexes_ordered)",
                      body)                                    # resemblance + SEX consistency
        self.assertIn("clearly visible faces", body)          # structural skeleton is fixed
        self.assertIn("flavor", body)                         # caller prompt = flavor, not replacement
        self.assertNotIn("sorted(boxes[:2]", body)            # v6's coin-flip pairing is gone
        self.assertIn("num_faces=6",
                      src.split("def _multi_landmarker(", 1)[1].split("\ndef ", 1)[0])

    def test_scene_detector_is_scrfd_with_fallback(self):
        # ScrFD detects scenes; MediaPipe is fallback for landmarks.
        # Note: MediaPipe landmarker is selfie-range only (misses group photos).
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def detect_faces_ex(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_scrfd()", body)
        self.assertIn("[:, :, ::-1]", body)               # insightface expects BGR
        self.assertIn("face_landmarks", body)             # mediapipe fallback intact
        self.assertIn('allowed_modules=["detection", "genderage"]',
                      src.split("def _scrfd(", 1)[1].split("\ndef ", 1)[0])
        reqs = open("requirements.txt", encoding="utf-8").read()
        self.assertIn("insightface", reqs)
        self.assertIn("onnxruntime", reqs)

    def test_age_partitioned_matching(self):
        # v7: global shape-distance matching left the two LARGEST faces unmatched; parents
        # now match only ADULT faces and children only child faces (genderage ages), with
        # a global-assignment fallback when ages are missing or pools are too thin
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def match_faces(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("by_age[:n_parents]", body)   # RELATIVE rule: two oldest = parents
        self.assertIn('"adults"', body)
        self.assertIn('"kids"', body)
        self.assertIn('"global"', body)
        self.assertIn("CHILD_MAX_AGE", src.split("def family_photo(", 1)[1])   # scene gate
        ex = src.split("def detect_faces_ex(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"age":', ex)

    def test_swap_pass_chains_after_inpaint(self):
        wf = identity.build_face_inpaint_workflow("s.png", "m.png", "r.png", seed=1,
                                                  swap_source=True)["prompt"]
        self.assertEqual(wf["50"]["class_type"], "ReActorFaceSwap")
        self.assertEqual(wf["50"]["inputs"]["input_image"], ["6", 0])    # the inpaint output
        self.assertEqual(wf["50"]["inputs"]["source_image"], ["40", 0])  # the person's ref
        self.assertEqual(wf["50"]["inputs"]["swap_model"], "inswapper_128.onnx")
        self.assertEqual(wf["50"]["inputs"]["face_restore_model"], "GFPGANv1.4.pth")
        self.assertEqual(wf["52"]["class_type"], "SetLatentNoiseMask")   # fuse stays in-oval
        self.assertEqual(wf["54"]["inputs"]["denoise"], identity.FACE_FUSE_DENOISE)
        self.assertLess(identity.FACE_FUSE_DENOISE, 0.35)                # identity must survive
        self.assertIn("looking directly at the camera", wf["53"]["inputs"]["text"])
        self.assertEqual(wf["7"]["inputs"]["images"], ["55", 0])         # save the fused
        # identity-guarded finisher (fix ⑧): post-SWAP is ALSO saved (node 56) so the loop can
        # keep whichever of {post-fuse, post-swap} scores higher arcface vs the ref
        self.assertEqual(wf["56"]["class_type"], "SaveImage")
        self.assertEqual(wf["56"]["inputs"]["images"], ["50", 0])        # the pre-fuse swap
        plain = identity.build_face_inpaint_workflow("s.png", "m.png", "r.png", seed=1)["prompt"]
        self.assertNotIn("50", plain)                                    # default unchanged
        self.assertNotIn("56", plain)
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def family_photo(", 1)[1]
        self.assertIn("swap_source=use_swap", body)
        self.assertIn("use_swap = swap_available()", body)
        # the guarded finisher fetches BOTH outputs and picks the winner
        self.assertIn('want_nodes=["7", "56"]', body)
        self.assertIn("_guarded_finish(fused, swapped, ref_path", body)

    def test_guarded_finish_keeps_higher_arcface(self):
        # fuse erodes weak binds: keep post-swap when it out-scores post-fuse, else the fused
        import numpy as np
        from PIL import Image
        fused, swapped = Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8))
        ref = Image.new("RGB", (8, 8))
        embs = {id(ref): np.array([1.0, 0.0]), id(fused): np.array([0.2, 0.98]),
                id(swapped): np.array([0.9, 0.44])}   # swapped closer to ref
        orig_fe, orig_trb = identity.face_embedding, identity.tight_ref_bytes
        identity.tight_ref_bytes = lambda p: b""
        identity.face_embedding = lambda im: embs.get(id(im))
        try:
            # ref path is opened via Image.open(BytesIO(tight_ref_bytes(...))) -> patch that too
            _open = Image.open
            Image.open = lambda *a, **k: ref
            try:
                out = identity._guarded_finish(fused, swapped, "ref.png", 0)
            finally:
                Image.open = _open
            self.assertIs(out, swapped)     # swapped out-scored fused -> kept
        finally:
            identity.face_embedding, identity.tight_ref_bytes = orig_fe, orig_trb

    def test_guarded_finish_fails_soft_without_recognizer(self):
        # no recognizer (embedding None) -> return the fused output unchanged, never crash
        from PIL import Image
        fused, swapped = Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8))
        orig_fe, orig_trb = identity.face_embedding, identity.tight_ref_bytes
        identity.tight_ref_bytes = lambda p: b""
        identity.face_embedding = lambda im: None
        import io
        _open = Image.open
        Image.open = lambda *a, **k: fused
        try:
            self.assertIs(identity._guarded_finish(fused, swapped, "ref.png", 0), fused)
        finally:
            Image.open = _open
            identity.face_embedding, identity.tight_ref_bytes = orig_fe, orig_trb

    def test_casting_gate_checks_parent_sexes(self):
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def family_photo(", 1)[1]
        self.assertIn("ref_sexes", body)
        self.assertIn('sorted(f["sex"] for f in oldest) == ref_sexes', body)
        self.assertIn("ages_ok and sexes_ok", body)
        ex = src.split("def detect_faces_ex(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"sex":', ex)

    def test_identity_passes_run_at_detail_res(self):
        # a face gets ~150px at scene resolution and identity mushes; the inpaint runs on an
        # upscaled face REGION and feathers back (the real-photo bake-off's biggest lesson)
        src = open("identity.py", encoding="utf-8").read()
        self.assertGreaterEqual(identity.FACE_DETAIL_RES, 768)
        body = src.split("def family_photo(", 1)[1]
        self.assertIn("_face_region(scene, box)", body)
        self.assertIn("FACE_DETAIL_RES", body)
        self.assertIn("scene.paste(patch, (l, t), feather)", body)   # feathered seam

    def test_box_in_region_roundtrip(self):
        box = (0.5, 0.4, 0.1, 0.12)
        region = (200, 100, 800, 700)
        rel = identity._box_in_region(box, region, (1216, 832))
        self.assertAlmostEqual(rel[0], (0.5 * 1216 - 200) / 600)
        self.assertAlmostEqual(rel[2], 0.1 * 1216 / 600)

    def test_refs_are_face_tight_and_children_single_face(self):
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def simulate_children(", 1)[1]
        self.assertIn("debeard_ref_bytes(d / up)", body)
        db = src.split("def debeard_ref_bytes(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("face_only_ref_bytes", db)                         # …and still face-tight
        self.assertIn("has_facial_hair", db)                            # VLM-gated (only bearded parents)
        self.assertIn("free_memory()", db)                             # frees Flux before the SDXL render
        self.assertLess(identity.tight_ref_bytes.__defaults__[0], 2.5)   # grow is parametrized
        self.assertIn("extra/no face -> reroll", body)
        self.assertIn("seed_base * 7 + i + 1013", body)               # deterministic reroll
        self.assertIn("about three years old", body)                 # age lead (stable)
        # NOTE: deliberately NOT asserting the gender-lean prompt strings — that logic is in flux
        # (the real gender fix is bone structure in the mesh, not prompt tokens); pinning the exact
        # words here would just enshrine an experiment we're moving away from.
        self.assertIn("torso_hint(depth)", body)                      # mirror-blob grounding
        fam = src.split("def family_photo(", 1)[1]
        self.assertIn("tight_ref_bytes(ref_path)", fam)

    def test_pipeline_stage_tracing(self):
        # house logging style: multi-step pipelines leave IMG_PIPE breadcrumbs (scene attempt
        # tallies, matcher pairings with distances, per-inpaint timing, engine choice) so a
        # dead run tells its own story instead of being inferred from response lengths
        src = open("identity.py", encoding="utf-8").read()
        for crumb in ("IMG_PIPE scene attempt", "IMG_PIPE match[", "IMG_PIPE inpaint",
                      "IMG_PIPE children:", "IMG_PIPE match FALLBACK"):
            self.assertIn(crumb, src)

    def test_sex_first_parent_assignment(self):
        src = open("identity.py", encoding="utf-8").read()
        body = src.split("def match_faces(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('adult_sexes.index(ref_sexes[j])', body)
        self.assertIn("SEX-FIRST", body)
        self.assertIn('adult_sexes[0] != adult_sexes[1]', body)   # only when unambiguous
        fam = src.split("def family_photo(", 1)[1]
        self.assertIn("ref_sexes_ordered = [x[0][\"sex\"] for x in det]", fam)
        self.assertIn("HERITAGE_HINTS.get(up", fam)               # style-only heritage hook

    def test_match_faces_falls_back_positionally(self):
        # with FLAME unavailable/failing, pairing degrades to positional order, never crashes
        from PIL import Image
        scene = Image.new("RGB", (400, 300), (120, 120, 120))   # no faces -> fit raises
        boxes = [(0.3, 0.4, 0.1, 0.12), (0.7, 0.4, 0.1, 0.12)]
        out = identity.match_faces(scene, boxes, ["refA.png", "refB.png"])
        self.assertEqual([(r[1], r[2]) for r in out], [("refA.png", 0), ("refB.png", 1)])

    def test_family_tool_wired(self):
        import tools
        for lst in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            names = [t["function"]["name"] for t in lst if isinstance(t, dict)]
            self.assertIn("family_photo", names)
        src = open("tools.py", encoding="utf-8").read()
        self.assertIn('if name == "family_photo":', src)
        body = src.split("def _family_photo(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("units = kids + 1", body)               # child gens + unify pass
        self.assertIn("gaming_busy(min_free_mb=config.IMG_MIN_FREE_MB)", body)
        self.assertIn("mode=family", body)


class ToolWiringTests(unittest.TestCase):
    def test_registered_and_dispatched(self):
        import tools
        for lst in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            names = [t["function"]["name"] for t in lst if isinstance(t, dict)]
            self.assertIn("imagine_children", names)
        src = open("tools.py", encoding="utf-8").read()
        self.assertIn('if name == "imagine_children":', src)
        body = src.split("def _imagine_children(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("quota_remaining", body)          # gated like image gen
        self.assertIn("gaming_busy", body)
        self.assertIn("ensure_up", body)
        self.assertIn("mode=child", body)               # audited with inheritance picks
        self.assertIn("picks=", body)
        self.assertIn("NoFaceError", body)              # user-explainable failures

    def test_image_paths_use_image_vram_threshold(self):
        import config
        self.assertLess(config.IMG_MIN_FREE_MB, config.LEAD_BOX_MIN_FREE_MB)
        self.assertGreaterEqual(config.IMG_MIN_FREE_MB, 6000)   # a real game leaves less
        src = open("tools.py", encoding="utf-8").read()
        for fn in ("_generate_image", "_imagine_children"):
            body = src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
            self.assertIn("gaming_busy(min_free_mb=config.IMG_MIN_FREE_MB)", body, fn)
        ms = open("lead_ssh.py", encoding="utf-8").read()
        self.assertIn("def gaming_busy(min_free_mb=None):", ms)
        self.assertIn("threshold = min_free_mb or config.LEAD_BOX_MIN_FREE_MB", ms)

    def test_container_ships_the_landmarker_model(self):
        if pathlib.Path(".git").is_file():
            # a git worktree's .git is a FILE; the model is git-ignored, so a worktree never holds it. The image and
            # the mirror (where this runs in the deploy gate) do, and are checked.
            self.skipTest("a git worktree never holds the git-ignored face_landmarker.task")
        self.assertTrue(pathlib.Path("face_landmarker.task").exists())

    def test_the_landmarker_dependencies_are_installed_and_declared(self):
        import mediapipe  # noqa: F401  (deps actually installed where the suite runs)
        import scipy      # noqa: F401
        reqs = open("requirements.txt", encoding="utf-8").read()
        self.assertIn("mediapipe", reqs)
        self.assertIn("scipy", reqs)
        if pathlib.Path("Dockerfile").exists():   # mirror/live tree only; not baked into the image
            self.assertIn("face_landmarker.task",
                          open("Dockerfile", encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
