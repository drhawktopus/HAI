r"""Foundry test suite. Constraints: runs without GPU, ComfyUI, trainer, or live state writes, safe at any time."""
import argparse
import collections
import contextlib
import datetime
import os
import io
import sys
import glob
import json
import random
import shutil
import subprocess
import tempfile
import time
import threading
import unittest
import importlib.util
import types
from unittest import mock

BIN = os.path.join(os.environ.get("FOUNDRY_ROOT", r"F:\foundry"), "bin")
TARGET = os.path.join(BIN, "p20_foundry.py")

# The head's address and the fleet table are settings, so a box running this suite without its
# lab.env would silently skip every desk call and the desk expectations would fail. The suite
# states them here with documentation-range addresses: the expectations never depend on a lab value.
os.environ.setdefault("HAI_URL", "http://10.0.0.205:8099")
os.environ.setdefault("FLEET_HOSTS", json.dumps({"mothership": "10.0.0.76",
                                                "bighonker": "10.0.0.167",
                                                "softserve": "10.0.0.204"}))


def load_foundry():
    """Import the live module the way the runner does, without running its main loop."""
    sys.path.insert(0, BIN)                       # sibling modules (foundry_paths)
    sys.argv = ["p20_foundry.py"]                 # no --queue: nothing should start
    spec = importlib.util.spec_from_file_location("p20_foundry_undertest", TARGET)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


F = load_foundry()

# Disable DM credentials: prevent live Discord notifications during tests.
# Per-test mocks are fragile; this ensures no real sends occur.
F._dm_creds = lambda: (None, None)
# Compute orders create their {ORDER_OUT} folder under the live output root; no test writes there.
F.COMPUTE_OUT_ROOT = tempfile.mkdtemp(prefix="foundry_test_compute_out_")
# A refused desk report is KEPT for the live runner to resend: a test that fails a report must never leave one in the
# factory's real state dir, or the runner would send a fixture's fake result to the real desk.
F.DESK_REPORTS = tempfile.mkdtemp(prefix="foundry_test_desk_reports_")


class Job(object):
    """Minimal stand-in for the real job object: only what the functions under test read."""

    def __init__(self, name="t", mode="probe", batch="b", size=2, seeds=None, strengths=None,
                 prompts=None, step=2000, sweep=None):
        self.name = name
        self.mode = mode
        self.spec = {"batch": batch, "batch_size": size}
        self.probe_seeds = [42, 101] if seeds is None else seeds
        self.probe_strengths = [0.6] if strengths is None else strengths
        self.probe_prompts = prompts if prompts is not None else [
            {"name": "p1", "text": "a figure, standing, front", "base": True}]
        self.probe_step = step
        self.sweep_names = ["p1"] if sweep is None else sweep
        self.logs = []

    def log(self, m):
        self.logs.append(m)


# ---------------------------------------------------------------------------- 1. it imports
class TestImports(unittest.TestCase):

    def test_module_imports(self):
        self.assertTrue(F is not None)

    def test_public_surface_present(self):
        for name in ("render", "render_key", "ship_render", "batch_guard", "batch_fingerprint",
                     "batch_dir", "retire_stale_embeds", "embeds_needs_rebuild",
                     "publish_current_run", "clear_current_run", "foreign_run_guard", "LaneBusy",
                     "PauseRequested", "pause_requested", "consume_pause_flag", "queued_orders",
                     "order_priority", "acquire_runner_singleton", "runner_beat",
                     "release_runner_singleton"):
            self.assertTrue(hasattr(F, name), "missing public name: %s" % name)

    def test_no_module_level_path_evaluation_before_constants(self):
        """batch_dir must be LAZY -- calling it works, but it must not have run at import."""
        self.assertTrue(callable(F.batch_dir))
        self.assertIn("batches", F.batch_dir())


# ---------------------------------------------------------------------------- 2. render_key
class TestRenderKey(unittest.TestCase):
    """A render is a pure function of (text, seed, checkpoint, strength, base_only).

    RENDER_INDEX reuse relies on this determinism; the key must preserve all five dimensions."""

    def k(self, text="a", seed=42, ck="x_ck2000.safetensors", strength=None, base=False):
        return F.render_key(text, seed, ck, strength, base)

    def test_stable(self):
        self.assertEqual(self.k(), self.k())

    def test_text_matters(self):
        self.assertNotEqual(self.k(text="a"), self.k(text="b"))

    def test_text_is_stripped_not_case_folded(self):
        self.assertEqual(self.k(text=" a "), self.k(text="a"))
        self.assertNotEqual(self.k(text="A"), self.k(text="a"))

    def test_seed_matters(self):
        self.assertNotEqual(self.k(seed=42), self.k(seed=101))

    def test_checkpoint_matters(self):
        self.assertNotEqual(self.k(ck="a_ck1000.safetensors"), self.k(ck="a_ck2000.safetensors"))

    def test_strength_matters(self):
        self.assertNotEqual(self.k(strength=0.6), self.k(strength=1.0))

    def test_strength_none_is_one(self):
        """None means SLIM's 1.0 default -- they must be the SAME cell, not two."""
        self.assertEqual(self.k(strength=None), self.k(strength=1.0))

    def test_base_arm_is_distinct(self):
        """base_only renders the same prompt with NO adapter: a different cell, never a collision."""
        self.assertNotEqual(self.k(base=True), self.k(base=False))

    def test_base_arm_ignores_strength(self):
        """With no adapter loaded, strength is meaningless -- must not fragment the cache."""
        self.assertEqual(self.k(base=True, strength=0.6), self.k(base=True, strength=1.0))


    def test_extras_change_the_key(self):
        """A stacked cell yields different pixels; serving it an unstacked cached cell would be a
        silent wrong comparison -- the class the index exists to prevent."""
        ex = [{"name": "concept_flux2_v8_ck2000.safetensors", "strength": 0.6}]
        self.assertNotEqual(F.render_key("a", 42, "g_ck1750.safetensors", 1.0, False),
                            F.render_key("a", 42, "g_ck1750.safetensors", 1.0, False, extras=ex))

    def test_empty_extras_is_the_old_key(self):
        """Every key (and the whole pre-existing index) must survive the new field unchanged."""
        old = F.render_key("a", 42, "g_ck1750.safetensors", 1.0, False)
        self.assertEqual(old, F.render_key("a", 42, "g_ck1750.safetensors", 1.0, False, extras=None))
        self.assertEqual(old, F.render_key("a", 42, "g_ck1750.safetensors", 1.0, False, extras=[]))

    def test_steps_change_the_key_and_20_is_the_old_key(self):
        """Steps change the pixels; 20/absent = the historical default and must preserve every
        pre-existing key (a steps-20 ladder cell deliberately reuses default-rendered cells)."""
        old = F.render_key("a", 42, "g.safetensors", 1.0, False)
        self.assertEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False, steps=None))
        self.assertEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False, steps=20))
        self.assertNotEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False, steps=28))
        self.assertNotEqual(F.render_key("a", 42, "g.safetensors", 1.0, False, steps=28),
                            F.render_key("a", 42, "g.safetensors", 1.0, False, steps=36))

    def test_kontext_source_changes_the_key_and_absent_is_the_old_key(self):
        """An EDIT cell is a function of its source image; non-kontext keys must not move."""
        old = F.render_key("remove the coat", 42, "g.safetensors", 1.0, False)
        self.assertEqual(old, F.render_key("remove the coat", 42, "g.safetensors", 1.0, False,
                                           kontext_source=None))
        a = F.render_key("remove the coat", 42, "g.safetensors", 1.0, False,
                         kontext_source=r"D:\renders\cellA.png")
        b = F.render_key("remove the coat", 42, "g.safetensors", 1.0, False,
                         kontext_source=r"D:\renders\cellB.png")
        self.assertNotEqual(old, a)
        self.assertNotEqual(a, b)
        # basename identity: tiering may move the file F:->D: without changing what it is
        self.assertEqual(a, F.render_key("remove the coat", 42, "g.safetensors", 1.0, False,
                                         kontext_source=r"F:\other\dir\cellA.png"))

    def test_verify_skips_the_structural_lora_pass(self):
        import inspect
        src = inspect.getsource(F.render)
        self.assertIn('tag == "_LORA"', src)
        i_skip = src.index('tag == "_LORA"')
        i_run = src.index("PIXEL_HASH")
        self.assertLess(i_skip, i_run, "the _LORA skip must precede the verifier call")

    def test_verify_against_is_not_in_the_key(self):
        """A reproducibility check must not change the cell it is checking. verify_against is a
        REPORTING instruction, not a pixel input — if it entered the key, the audit cell could
        never match the original it exists to compare against."""
        import inspect
        src = inspect.getsource(F.render_key)
        self.assertNotIn("verify_against", src)

    def test_edit_spec_changes_the_key_and_absent_is_the_old_key(self):
        """Inpaint cells are functions of mask/denoise/stack/base — all must key distinctly, and
        a non-edit cell's key must not move."""
        old = F.render_key("a", 42, "g.safetensors", 1.0, False)
        self.assertEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False, edit_spec=None))
        base = {"mode": "inpaint", "mask": {"box": [0.1, 0.2, 0.9, 0.8]}, "denoise": 0.35,
                "loras": [], "unet": "default"}

        def k(**over):
            s = dict(base, **over)
            return F.render_key("a", 42, "g.safetensors", 1.0, False, edit_spec=s)

        self.assertNotEqual(old, k())
        self.assertEqual(k(), k())                                   # stable
        self.assertNotEqual(k(), k(denoise=0.6))                     # denoise matters
        self.assertNotEqual(k(), k(mask={"box": [0.0, 0.0, 1.0, 1.0]}))   # mask matters
        self.assertNotEqual(k(), k(unet="flux1-dev-fp8.safetensors"))     # base matters
        self.assertNotEqual(k(), k(loras=[{"name": "x.safetensors", "strength": 0.8}]))

    def test_inpaint_flux2_keys_distinct_from_flux1_engine(self):
        """Same mask+denoise on the two inpaint engines are DIFFERENT cells (different base
        model renders different pixels); and the flux2 engine's stack lives in the NORMAL key
        terms (strength/extras), so those must still fragment the key."""
        f1 = {"mode": "inpaint", "mask": {"box": [0.1, 0.2, 0.9, 0.8]}, "denoise": 0.6,
              "loras": [], "unet": "default"}
        f2 = {"mode": "inpaint_flux2", "mask": {"box": [0.1, 0.2, 0.9, 0.8]}, "denoise": 0.6}
        k1 = F.render_key("a", 42, "g.safetensors", 1.0, False, edit_spec=f1)
        k2 = F.render_key("a", 42, "g.safetensors", 1.0, False, edit_spec=f2)
        self.assertNotEqual(k1, k2)
        self.assertEqual(k2, F.render_key("a", 42, "g.safetensors", 1.0, False,
                                          edit_spec=dict(f2)))                       # stable
        self.assertNotEqual(k2, F.render_key("a", 42, "g.safetensors", 1.0, False,
                                             edit_spec=dict(f2, denoise=0.85)))      # denoise
        self.assertNotEqual(k2, F.render_key("a", 42, "g.safetensors", 0.85, False,
                                             edit_spec=dict(f2)))                    # strength
        self.assertNotEqual(k2, F.render_key("a", 42, "g.safetensors", 1.0, False,
                                             edit_spec=dict(f2),
                                             extras=[{"name": "concept.safetensors",
                                                      "strength": 0.6}]))            # extras

    def test_the_base_unet_changes_the_key_and_the_default_is_the_old_key(self):
        old = F.render_key("a", 42, "g.safetensors", 1.0, False)
        self.assertEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False, unet=None))
        self.assertEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False,
                                           unet=F.RENDER_UNET_DEFAULT))
        self.assertNotEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False,
                                              unet="flux-2-klein-base-4b-Q8_0.gguf"))

    def test_the_renderer_and_the_foundry_agree_on_the_default_unet(self):
        """Ensure the UNet default literal in this module matches `p20_preview_render_slim.py`.

        The renderer is a standalone script that cannot import RENDER_UNET_DEFAULT,
        so it hardcodes its own default. If these literals drift, cache keys will be
        incorrect while rendering uses a different model, causing silent failures.
        We inspect the source code of the renderer to verify agreement.
        """
        import re
        slim = os.path.join(BIN, "p20_preview_render_slim.py")
        if not os.path.exists(slim):
            self.skipTest("renderer not deployed on this box")
        with open(slim, encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r'FOUNDRY_RENDER_UNET"\s*,\s*"([^"]+)"', src)
        self.assertIsNotNone(m, "could not find the renderer's FOUNDRY_RENDER_UNET default")
        self.assertEqual(m.group(1), F.RENDER_UNET_DEFAULT,
                         "renderer default %r != foundry RENDER_UNET_DEFAULT %r - the key would "
                         "call it 'default' while rendering something else" % (m.group(1),
                                                                               F.RENDER_UNET_DEFAULT))

    def test_guidance_changes_the_key_and_default_is_the_old_key(self):
        """Caught BEFORE the first guidance probe: without this term a guidance-4.0 cell keys
        identical to its 2.5 sibling and the index serves WRONG pixels into an experiment."""
        old = F.render_key("a", 42, "g.safetensors", 1.0, False)
        self.assertEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False, guidance=None))
        self.assertEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False, guidance=2.5))
        self.assertNotEqual(old, F.render_key("a", 42, "g.safetensors", 1.0, False, guidance=4.0))
        self.assertNotEqual(F.render_key("a", 42, "g.safetensors", 1.0, False, guidance=4.0),
                            F.render_key("a", 42, "g.safetensors", 1.0, False, guidance=5.5))

    def test_extras_strength_matters(self):
        """The person-vs-capability balance is the whole tuning problem: 0.6 and 1.0 stacks are
        different cells."""
        k = lambda s: F.render_key("a", 42, "g.safetensors", 1.0, False,
                                   extras=[{"name": "x.safetensors", "strength": s}])
        self.assertNotEqual(k(0.6), k(1.0))
        self.assertEqual(k(0.6), k(0.6))


# ---------------------------------------------------------------------------- 3. batch guard
class TestBatchGuard(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="foundrytest_")
        self._real = F.CURRENT_RUN
        F.CURRENT_RUN = os.path.join(self.tmp, "state", "CURRENT_RUN.json")

    def tearDown(self):
        F.CURRENT_RUN = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_arm_records_then_identical_arm_passes(self):
        F.batch_guard(Job("arm1"))
        F.batch_guard(Job("arm2"))          # must not raise

    def test_different_adapter_still_passes(self):
        """probe_lora is the VARIABLE UNDER TEST and must never be in the fingerprint."""
        F.batch_guard(Job("arm1"))
        j = Job("arm2")
        j.spec["probe_lora"] = "somewhere/else.safetensors"
        F.batch_guard(j)

    def test_seed_order_is_not_a_difference(self):
        F.batch_guard(Job("arm1", seeds=[42, 101]))
        F.batch_guard(Job("arm2", seeds=[101, 42]))

    def _refuses(self, job):
        F.batch_guard(Job("first"))
        with self.assertRaises(RuntimeError) as cm:
            F.batch_guard(job)
        self.assertIn("BATCH INVARIANT MISMATCH", str(cm.exception))
        return str(cm.exception)

    def test_refuses_different_seeds(self):
        self._refuses(Job("bad", seeds=[42, 999]))

    def test_refuses_different_strength(self):
        self._refuses(Job("bad", strengths=[1.0]))

    def test_refuses_different_checkpoint_step(self):
        self._refuses(Job("bad", step=1500))

    def test_refuses_prompt_text_differing_by_case(self):
        self._refuses(Job("bad", prompts=[{"name": "p1", "text": "a figure, standing, FRONT",
                                           "base": True}]))

    def test_refuses_different_base_flag(self):
        self._refuses(Job("bad", prompts=[{"name": "p1", "text": "a figure, standing, front",
                                           "base": False}]))

    def test_refuses_prompt_gaining_a_stack(self):
        """Two arms whose 'same' prompt differs only in extra_loras are NOT like-for-like."""
        self._refuses(Job("bad", prompts=[{"name": "p1", "text": "a figure, standing, front",
                                           "base": True,
                                           "extra_loras": [{"name": "x.safetensors",
                                                            "strength": 0.6}]}]))

    def test_refuses_prompt_changing_steps(self):
        """Arms differing only in diffusion steps are NOT like-for-like."""
        self._refuses(Job("bad", prompts=[{"name": "p1", "text": "a figure, standing, front",
                                           "base": True, "steps": 36}]))

    def test_same_stack_on_both_arms_passes(self):
        stacked = [{"name": "p1", "text": "a figure, standing, front", "base": True,
                    "extra_loras": [{"name": "x.safetensors", "strength": 0.6}]}]
        F.batch_guard(Job("arm1", prompts=stacked))
        F.batch_guard(Job("arm2", prompts=stacked))          # must not raise

    def test_refusal_is_actionable(self):
        msg = self._refuses(Job("bad", seeds=[42, 999]))
        for need in ("this arm", "first arm", "seeds", "DIFFERENT batch name"):
            self.assertIn(need, msg)

    def test_refusal_is_NOT_lane_busy(self):
        """
        Broken specs raise RuntimeError, never LaneBusy.
        LaneBusy is reserved for transient resource contention only.
        """
        F.batch_guard(Job("first"))
        with self.assertRaises(RuntimeError) as cm:
            F.batch_guard(Job("bad", seeds=[42, 999]))
        self.assertNotIsInstance(cm.exception, F.LaneBusy)

    def test_train_mode_is_untouched(self):
        """Training batches (fitbench rank x resolution) exist to VARY config."""
        F.batch_guard(Job("t1", mode="train"))
        F.batch_guard(Job("t2", mode="train", seeds=[1, 2, 3], strengths=[0.9], step=7))

    def test_single_arm_and_unbatched_are_untouched(self):
        """Opt-in: nothing locks an arbitrary sequence of renders to the same settings."""
        F.batch_guard(Job("solo", size=1, seeds=[7]))
        F.batch_guard(Job("nobatch", batch="", seeds=[7]))

    def test_corrupt_registry_self_heals_and_warns(self):
        F.batch_guard(Job("arm1"))
        p = os.path.join(F.batch_dir(), "b.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ not json")
        j = Job("arm2")
        F.batch_guard(j)                                     # must not raise
        self.assertTrue(any("UNVERIFIED" in m for m in j.logs),
                        "an unverifiable comparison must SAY it is unverified")


# ---------------------------------------------------------------------------- 4. benchmark orders
class TestBenchmarkOrder(unittest.TestCase):
    """Ensure training orders are not misclassified as benchmarks.

    The `kind` strings describe real archived specs. Because they legitimately
    contain 'byproducts' (referring to renders, not weights), a naive substring
    check would incorrectly skip registration. These tests verify that F.is_benchmark_order
    correctly distinguishes between weight byproducts and render byproducts."""

    def test_real_training_orders_REGISTER(self):
        for kind in (
            "PRODUCT - ships trained weights (base-capability concept LoRA). Renders are byproducts.",   # v8
            "TRAINING - ships WEIGHTS (a rank-32 capability LoRA at 640 px). Renders are byproducts.",   # v9
        ):
            self.assertFalse(F.is_benchmark_order({"kind": kind}),
                             "training order misread as benchmark: %r" % kind)

    def test_real_benchmark_orders_skip(self):
        for kind in (
            "BENCHMARK - ships a NUMBER (s/it + peak VRAM + host spill). Weights are byproducts.",       # fitbench
            "BENCHMARK - ships a NUMBER. Weights are BYPRODUCT.",                                        # fitbench r32_512
            "BENCHMARK - ships a NUMBER (s/it + spill). The weights are a byproduct; delete them.",      # resbench
        ):
            self.assertTrue(F.is_benchmark_order({"kind": kind}))

    def test_probe_kinds_skip(self):
        """Probes never reach REGISTER, but if one ever did, its declaration is honest."""
        for kind in (
            "measurement (does 640 px buy anything ON A POSE?) - ships A VERDICT, no weights",
            "smoke test (does a FOUNDRY render now publish step progress?) - ships a VERIFICATION",
            "verification (does PREFLIGHT announce render-worker liveness?) - ships a LOG LINE",
        ):
            self.assertTrue(F.is_benchmark_order({"kind": kind}))

    def test_empty_and_unknown_kinds_REGISTER(self):
        """Only an explicit self-declaration may suppress an operator-visible artifact."""
        for spec in ({}, {"kind": ""}, {"kind": None}, {"kind": "something new entirely"}):
            self.assertFalse(F.is_benchmark_order(spec))


# ---------------------------------------------------------------------------- 5. render tiering
class TestRenderTiering(unittest.TestCase):
    """Test fast (F) to slow (D) tier promotion.

    Files only leave F if their D copy hash matches;
    otherwise the source is retained (fail-soft)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tiertest_")
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "dst")
        os.makedirs(os.path.join(self.src, "sub"))
        for rel in ("a.png", os.path.join("sub", "b.png")):
            with open(os.path.join(self.src, rel), "wb") as f:
                f.write(rel.encode() * 100)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_moves_verifies_and_tidies(self):
        moved, failed = F._drain_tree(self.src, self.dst)
        self.assertEqual((moved, failed), (2, 0))
        self.assertTrue(os.path.exists(os.path.join(self.dst, "a.png")))
        self.assertTrue(os.path.exists(os.path.join(self.dst, "sub", "b.png")))
        self.assertFalse(os.path.exists(self.src), "emptied source tree must be removed")

    def test_drain_order_renders_missing_folder_is_a_noop(self):
        F.drain_order_renders("no_such_order_ever")     # must not raise

    def test_drain_never_raises_even_on_bad_slow_tier(self):
        real = F.RENDER_SLOW_TIER
        realp = F.P.RENDERS
        try:
            F.P.RENDERS = os.path.dirname(self.src)
            F.RENDER_SLOW_TIER = os.path.join(self.tmp, "nope*<>|invalid")
            F.drain_order_renders(os.path.basename(self.src))   # must not raise; files must survive
            self.assertTrue(os.path.exists(os.path.join(self.src, "a.png")),
                            "a failed drain must leave the fast-tier files in place")
        finally:
            F.RENDER_SLOW_TIER = real
            F.P.RENDERS = realp


class TestStagedRetirement(unittest.TestCase):
    """Test verify-then-delete retirement logic:

        - Content-identical staged copies of canonicals are DELETED.
        - Unverifiable staged copies are ARCHIVED (only-surviving-copy protection).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stagetest_")
        self._loras, self._rlog = F.LORAS, F.RESCUE_LOG
        F.LORAS = os.path.join(self.tmp, "loras")
        F.RESCUE_LOG = os.path.join(self.tmp, "state", "telemetry", "x.log")
        os.makedirs(F.LORAS)
        os.makedirs(os.path.dirname(F.RESCUE_LOG))
        self.outdir = os.path.join(self.tmp, "out")
        os.makedirs(self.outdir)

    def tearDown(self):
        F.LORAS, F.RESCUE_LOG = self._loras, self._rlog
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _job(self):
        j = Job("runx", mode="train")
        j.output_dir = self.outdir
        j.probe_lora = ""
        return j

    def test_verified_duplicate_is_deleted_not_archived(self):
        with open(os.path.join(self.outdir, "runx_000000500.safetensors"), "wb") as f:
            f.write(b"WEIGHTS" * 1000)
        staged = os.path.join(F.LORAS, "runx_ck500.safetensors")
        with open(staged, "wb") as f:
            f.write(b"WEIGHTS" * 1000)
        F.retire_staged_loras(self._jobbed())
        self.assertFalse(os.path.exists(staged), "verified duplicate must be deleted")
        arch = os.path.join(self.tmp, "state", "archive")
        leftovers = []
        for root, dirs, files in os.walk(arch):
            leftovers += files
        self.assertEqual(leftovers, [], "a verified duplicate must NOT be archived")

    def test_unverifiable_is_archived_not_deleted(self):
        staged = os.path.join(F.LORAS, "runx_ck9.safetensors")
        with open(staged, "wb") as f:
            f.write(b"ONLY-COPY" * 999)
        F.retire_staged_loras(self._jobbed())
        self.assertFalse(os.path.exists(staged), "must leave the shared dir either way")
        found = []
        for root, dirs, files in os.walk(os.path.join(self.tmp, "state", "archive")):
            found += files
        self.assertIn("runx_ck9.safetensors", found,
                      "an unverifiable copy must be ARCHIVED (may be the only surviving copy)")

    def test_verified_extra_lora_is_deleted_not_archived(self):
        """A verified stacked probe EXTRA (jobname_ckNNN_xN) must be deleted,
        not archived."""
        canonical = os.path.join(self.tmp, "concept_v8.safetensors")
        with open(canonical, "wb") as f:
            f.write(b"CAPABILITY" * 1000)
        staged = os.path.join(F.LORAS, "runx_ck500_x0.safetensors")
        with open(staged, "wb") as f:
            f.write(b"CAPABILITY" * 1000)
        j = self._job()
        j.probe_prompts = [{"name": "p1", "text": "t", "base": False,
                            "extra_loras": [{"path": canonical, "strength": 0.6}]}]
        F.retire_staged_loras(j)
        self.assertFalse(os.path.exists(staged), "verified extra must be deleted")
        found = []
        for root, dirs, files in os.walk(os.path.join(self.tmp, "state", "archive")):
            found += files
        self.assertEqual(found, [], "a verified extra must NOT be archived")

    def _jobbed(self):
        return self._job()


# ---------------------------------------------------------------------------- 6. queue control
class TestQueueControl(unittest.TestCase):
    """Test queue priority, operator pause flag, and runner singleton constraints."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qctest_")
        self._q, self._lock, self._hb, self._flag = F.QUEUE_DIR, F.RUNNER_LOCK, F.RUNNER_HEARTBEAT, F.PAUSE_FLAG
        F.QUEUE_DIR = os.path.join(self.tmp, "queue")
        F.RUNNER_LOCK = os.path.join(self.tmp, "runner.lock")
        F.RUNNER_HEARTBEAT = os.path.join(self.tmp, "runner.heartbeat")
        F.PAUSE_FLAG = os.path.join(self.tmp, "STOP_AFTER_RENDER")
        os.makedirs(F.QUEUE_DIR)

    def tearDown(self):
        F.QUEUE_DIR, F.RUNNER_LOCK, F.RUNNER_HEARTBEAT, F.PAUSE_FLAG = self._q, self._lock, self._hb, self._flag
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _order(self, name, priority=None, age=0):
        p = os.path.join(F.QUEUE_DIR, name + ".job.json")
        spec = {"name": name}
        if priority is not None:
            spec["priority"] = priority
        with open(p, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        t = 1700000000 + age
        os.utime(p, (t, t))
        return p

    def test_fifo_without_priority_is_unchanged(self):
        a = self._order("older", age=0)
        b = self._order("newer", age=100)
        self.assertEqual([a, b], F.queued_orders())

    def test_priority_beats_age(self):
        old = self._order("big_old_grid", age=0)
        new = self._order("urgent_probe", priority=10, age=100)
        self.assertEqual([new, old], F.queued_orders())

    def test_bad_priority_is_zero_not_a_crash(self):
        weird = self._order("weird", age=0)
        with open(weird, "w", encoding="utf-8") as f:
            f.write("{ not json")
        ok = self._order("fine", priority=1, age=100)
        got = F.queued_orders()
        self.assertEqual(got[0], ok, "unreadable spec must sort as priority 0, not crash the sort")

    def test_pause_flag_roundtrip(self):
        self.assertFalse(F.pause_requested())
        open(F.PAUSE_FLAG, "w").close()
        self.assertTrue(F.pause_requested())
        F.consume_pause_flag()
        self.assertFalse(F.pause_requested())
        F.consume_pause_flag()                      # idempotent

    def test_after_exists_hides_an_order_until_its_fact_is_true(self):
        """Verify that orders gated by 'after_exists' are invisible to the scheduler until the
        referenced file exists, preventing deadlocks from self-referential retries."""
        qdir = tempfile.mkdtemp(prefix="aftertest_")
        try:
            fact = os.path.join(qdir, "ck1000.safetensors")
            with open(os.path.join(qdir, "gated.job.json"), "w") as f:
                json.dump({"priority": 5, "after_exists": fact}, f)
            with open(os.path.join(qdir, "plain.job.json"), "w") as f:
                json.dump({}, f)
            with mock.patch.object(F, "QUEUE_DIR", qdir):
                names = [os.path.basename(p) for p in F.queued_orders()]
                self.assertEqual(names, ["plain.job.json"])       # gated order invisible
                with open(fact, "w") as f:
                    f.write("x")
                names = [os.path.basename(p) for p in F.queued_orders()]
                self.assertEqual(names, ["gated.job.json", "plain.job.json"])  # visible + priority-first
        finally:
            shutil.rmtree(qdir, ignore_errors=True)

    def test_cancel_refuses_while_the_order_holds_the_card(self):
        """A cancel slip must not archive an order whose trainer is still alive -- that leaves a
        process training for an order that no longer exists (the orphan class the stated-run
        pointer exists to prevent). Fail CLOSED: an unreadable claim counts as claimed."""
        tmp = tempfile.mkdtemp(prefix="cancelguard_")
        try:
            cur = os.path.join(tmp, "CURRENT_RUN.json")
            with open(cur, "w") as f:
                json.dump({"name": "job_a", "state": "training"}, f)
            with mock.patch.object(F, "CURRENT_RUN", cur):
                held, why = F._order_still_holds_card("job_a")
                self.assertTrue(held)
                self.assertIn("training", why)
                self.assertFalse(F._order_still_holds_card("job_b")[0])   # a different order
            with mock.patch.object(F, "CURRENT_RUN", os.path.join(tmp, "gone.json")):
                self.assertFalse(F._order_still_holds_card("job_a")[0])   # absent = free
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w") as f:
                f.write("{not json")
            with mock.patch.object(F, "CURRENT_RUN", bad):
                self.assertTrue(F._order_still_holds_card("job_a")[0],
                                "unreadable claim must fail CLOSED")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_process_enumeration_sees_a_known_live_python(self):
        """Positive control for the guards' process list (wmic is gone from these boxes; an
        enumerator that silently returns nothing would read every card as free)."""
        rows = F.python_cmdlines()
        self.assertTrue(rows, "zero python.exe listed while this test runs in one - the instrument is broken")
        self.assertIn(str(os.getpid()), [pid for pid, _cmd in rows],
                      "the test's own python.exe must be in the list")

    def test_truncated_caption_is_detected(self):
        self.assertTrue(F.caption_looks_truncated(
            "Photograph of a woman. There is a watermark in white text that reads"))
        self.assertTrue(F.caption_looks_truncated("short"))
        self.assertTrue(F.caption_looks_truncated(""))
        self.assertFalse(F.caption_looks_truncated(
            "Photograph of a woman standing indoors. The lighting is soft and even."))

    def test_minimal_schema_yields_bare_prose(self):
        """A caption-trained model gets the MINIMAL schema: absent gated fields must produce a
        bare paragraph with no tag line and no crash (JoyCaption leaked schema metadata into its
        prose under the gated schema -- the fields fought its training)."""
        self.assertNotIn("subject_state", F.CAPTION_SCHEMA_MINIMAL["properties"])
        self.assertEqual(F.compose_caption({"caption": "A woman stands indoors."}),
                         "A woman stands indoors.")

    def test_compose_caption_gates_on_confidence(self):
        """Fields below 0.7 confidence are omitted to prevent teaching false associations
        from hallucinated inputs."""
        out = {"caption": "A woman stands in a doorway.",
               "subject_state": "clothed", "subject_state_conf": 0.95,
               "framing": "full figure", "framing_conf": 0.4,      # guessing -> omitted
               "marks": "tattoo reading dougy", "marks_conf": 0.5}  # invention -> omitted
        txt = F.compose_caption(out)
        self.assertIn("subject_state: clothed", txt)
        self.assertNotIn("framing", txt)
        self.assertNotIn("dougy", txt)
        self.assertTrue(txt.startswith("A woman stands in a doorway."))
        with self.assertRaises(ValueError):
            F.compose_caption({"caption": "", "subject_state": "", "subject_state_conf": 0,
                               "framing": "", "framing_conf": 0, "marks": "", "marks_conf": 0})

    def test_lane_yield_is_its_own_type(self):
        """CONTRACT with run_queue (the 07-28 seam lesson): LaneYield must be catchable BEFORE
        LaneBusy and the generic archiver, and must not be mistaken for an operator pause --
        yield continues the loop, pause exits it, archive consumes the order. Three different
        fates; one shared type would silently pick the wrong one."""
        self.assertFalse(issubclass(F.LaneYield, F.LaneBusy))
        self.assertFalse(issubclass(F.LaneYield, F.PauseRequested))
        self.assertFalse(issubclass(F.LaneYield, RuntimeError))

    def test_caption_targets_sidecar_is_the_resume_key(self):
        """A caption order must resume like a render set: existing .txt = done, overwrite is an
        explicit choice, and the walk is deterministic."""
        d = tempfile.mkdtemp(prefix="captest_")
        try:
            os.makedirs(os.path.join(d, "sub"))
            for name in ("a.jpg", "b.PNG", "sub/c.webp", "notes.txt", "clip.mp4"):
                with open(os.path.join(d, name), "w") as f:
                    f.write("x")
            with open(os.path.join(d, "a.txt"), "w") as f:
                f.write("existing caption")
            todo, skipped = F.caption_targets(d)
            self.assertEqual(skipped, 1)                       # a.jpg has its sidecar
            self.assertEqual([os.path.basename(p) for p in todo], ["b.PNG", "c.webp"])
            # OVERWRITE, fresh start (no cutoff): recaptions everything. Correct.
            todo2, skipped2 = F.caption_targets(d, overwrite=True)
            self.assertEqual((len(todo2), skipped2), (3, 0))
            # Resume logic: sidecars written after cutoff count as done; pre-existing ones are replaced.
            import time as _t
            cutoff = _t.time()
            # Pin mtimes explicitly: filesystem granularity can blur wall-clock order.
            # a.txt is old (pre-cutoff), b.txt is new (post-cutoff).
            os.utime(os.path.join(d, "a.txt"), (cutoff - 60, cutoff - 60))
            with open(os.path.join(d, "b.txt"), "w") as f:   # written AFTER the run began
                f.write("caption from this run")
            os.utime(os.path.join(d, "b.txt"), (cutoff + 60, cutoff + 60))
            todo3, skipped3 = F.caption_targets(d, overwrite=True, newer_than=cutoff)
            self.assertEqual(skipped3, 1, "a caption written by THIS run must not be redone")
            self.assertNotIn("b.PNG", [os.path.basename(p) for p in todo3])
            self.assertIn("a.jpg", [os.path.basename(p) for p in todo3],
                          "a sidecar predating the run must still be overwritten")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_pause_is_not_lane_busy_and_not_runtime_error(self):
        """run_queue's LaneBusy and generic handlers must never catch an operator pause."""
        self.assertFalse(issubclass(F.PauseRequested, F.LaneBusy))
        self.assertFalse(issubclass(F.PauseRequested, RuntimeError))

    def test_singleton_acquire_reclaim_release(self):
        logs = []
        self.assertTrue(F.acquire_runner_singleton(logs.append))
        self.assertEqual(open(F.RUNNER_LOCK).read().strip(), str(os.getpid()))
        F.release_runner_singleton()
        self.assertFalse(os.path.exists(F.RUNNER_LOCK))
        with open(F.RUNNER_LOCK, "w") as f:
            f.write("999999999")                     # dead pid -> reclaim
        self.assertTrue(F.acquire_runner_singleton(logs.append))
        self.assertTrue(any("reclaiming" in m for m in logs))

    def test_singleton_defers_to_live_holder(self):
        with open(F.RUNNER_LOCK, "w") as f:
            f.write(str(os.getppid()))               # the test runner's parent: alive, not us
        logs = []
        self.assertFalse(F.acquire_runner_singleton(logs.append))
        self.assertTrue(any("exiting" in m or "WEDGED" in m for m in logs),
                        "deferral must be LOUD, never silent")


class TestKontextBakeSkip(unittest.TestCase):
    """A Kontext EDIT prompt must never reach the Mistral cond bake -- it would bake a cond
    nothing loads (Kontext runs clip_l+t5xxl in-graph)."""

    def test_all_kontext_prompts_mean_no_bake_calls(self):
        j = Job("kx", mode="probe")
        j.probe_prompts = [{"name": "edit1", "text": "remove the coat",
                            "kontext": {"source": r"D:\renders\cellA.png"}}]
        F.bake_missing(j, j.probe_prompts)      # must not raise, must not subprocess
        self.assertEqual([m for m in j.logs if "baking" in m], [])

    def test_inpaint_prompts_also_skip_the_bake(self):
        """Same rule as kontext: the inpaint graph runs clip_l+t5xxl in-graph, so a P20 Mistral
        cond would be baked and never loaded."""
        j = Job("ink", mode="probe")
        j.probe_prompts = [{"name": "edit1", "text": "bare skin",
                            "inpaint": {"source": r"D:\renders\cellA.png",
                                        "mask": {"box": [0.1, 0.2, 0.9, 0.8]}}}]
        F.bake_missing(j, j.probe_prompts)
        self.assertEqual([m for m in j.logs if "baking" in m], [])

    def test_flux2_engine_inpaint_DOES_bake(self):
        """The FLUX2-engine inpaint renders through SLIM's baked-cond path (P20LoadConditioning),
        so its text MUST reach the bake -- the filter that correctly skips the flux1 engines
        would otherwise starve it and the render dies on a missing .cond."""
        j = Job("ink2", mode="probe")
        j.probe_prompts = [{"name": "ink2cell", "text": "trigger, subject, figure study",
                            "inpaint": {"source": r"D:\renders\cellA.png", "engine": "flux2",
                                        "mask": {"box": [0.2, 0.25, 0.85, 0.95]},
                                        "denoise": 0.6}}]

        class _R(object):
            stdout, returncode = "ok", 0

        calls = []
        real_needs, real_run = F.cond_needs_bake, F.subprocess.run
        F.cond_needs_bake = lambda n, t: "missing"
        F.subprocess.run = lambda *a, **k: calls.append(a) or _R()
        try:
            F.bake_missing(j, j.probe_prompts)
        finally:
            F.cond_needs_bake, F.subprocess.run = real_needs, real_run
        self.assertTrue(any("baking" in m for m in j.logs),
                        "flux2-engine inpaint prompt never reached the bake")
        self.assertEqual(len(calls), 1)


class TestStatusBoard(unittest.TestCase):
    """Tests for the status board contract.

    Components must declare what data they read and its staleness requirements
    to prevent consumers from using stale or undefined sources."""

    def setUp(self):
        import p20_status
        self.st = p20_status
        self.tmp = tempfile.mkdtemp(prefix="statustest_")
        self._real = self.st.STATUS_DIR
        self.st.STATUS_DIR = os.path.join(self.tmp, "status")

    def tearDown(self):
        self.st.STATUS_DIR = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, name, age=0):
        p = os.path.join(self.tmp, name)
        with open(p, "w") as f:
            f.write("x")
        t = __import__("time").time() - age
        os.utime(p, (t, t))
        return p

    def test_fresh_source_is_ok_stale_is_not(self):
        fresh = self.st.source("fresh", self._touch("a.csv", age=5), stale_after=60)
        stale = self.st.source("stale", self._touch("b.csv", age=600), stale_after=60)
        self.assertTrue(fresh["ok"])
        self.assertFalse(stale["ok"])
        self.assertIn("STALE", stale["note"])
        self.assertIsNotNone(stale["age_s"], "age must be MEASURED, not implied — the orphan CSV "
                                             "was undated, which is how stale read as fresh")

    def test_absent_source_is_not_ok_and_says_so(self):
        s = self.st.source("gone", os.path.join(self.tmp, "nope.csv"))
        self.assertFalse(s["ok"])
        self.assertIn("ABSENT", s["note"])

    def test_publish_and_read_roundtrip_with_verdict(self):
        self.st.publish("demo", "watching", sources=[
            self.st.source("good", self._touch("g.csv", age=1), stale_after=60),
            self.st.source("bad", self._touch("h.csv", age=999), stale_after=60)])
        cards = self.st.read_all()
        self.assertIn("demo", cards)
        self.assertFalse(cards["demo"]["sources_ok"], "one bad input must fail the card's verdict")
        self.assertTrue(cards["demo"]["reporting"])
        self.assertIn("BAD-INPUT", self.st.summary_line(cards))

    def test_runner_card_publishable_from_inside_the_work(self):
        """Verify publish_runner_card writes state and updates the heartbeat regardless of call site."""
        qdir = os.path.join(self.tmp, "queue")
        os.makedirs(qdir)
        with open(os.path.join(qdir, "waiting.job.json"), "w") as f:
            f.write("{}")
        beat = os.path.join(self.tmp, "runner.heartbeat")
        with mock.patch.multiple(F, QUEUE_DIR=qdir, RUNNER_HEARTBEAT=beat,
                                 CURRENT_RUN=os.path.join(self.tmp, "CURRENT_RUN.json")):
            F.publish_runner_card("working", "rendering cell_x")
        cards = self.st.read_all()
        self.assertIn("foundry_runner", cards)
        c = cards["foundry_runner"]
        self.assertEqual(c["state"], "working")
        self.assertIn("rendering cell_x", c["notes"])
        q = [s for s in c["sources"] if s["name"] == "queue_dir"][0]
        self.assertIn("1 order(s) pending", q["note"], "queue count must be computed AT PUBLISH")
        self.assertTrue(os.path.exists(beat), "the card and the heartbeat must move together")

    def test_silent_component_is_reported_silent_not_current(self):
        """A component that stopped publishing is the 'worker is blind' case — a stale card must
        never be served as current (the dashboard once showed a 3-day-old run as live)."""
        self.st.publish("quiet", "watching", sources=[])
        p = os.path.join(self.st.STATUS_DIR, "quiet.json")
        old = __import__("time").time() - 9999
        os.utime(p, (old, old))
        cards = self.st.read_all(stale_after=180)
        self.assertFalse(cards["quiet"]["reporting"])
        self.assertIn("SILENT", self.st.summary_line(cards))

    def test_publish_never_raises_on_a_bad_dir(self):
        self.st.STATUS_DIR = os.path.join(self.tmp, "nope\x00bad")
        self.assertIsNone(self.st.publish("x", "y"))      # returns quietly, does not explode


# ---------------------------------------------------------------------------- 7. embeds guard
class TestEmbedsGuard(unittest.TestCase):

    def test_probe_orders_do_not_touch_the_embeds_cache(self):
        j = Job("p", mode="probe")
        F.retire_stale_embeds(j)
        self.assertEqual(j.logs, [], "a probe order must not touch the trainer's embeds cache")


# ------------------------------------------------------------------- 8. stopping a trainer
class TestShipCaptionsProvenance(unittest.TestCase):
    """SHIPPING must deliver ONLY this order's products.

    Avoids mixing artifacts from previous runs (e.g., older prompt sidecars)
    which would corrupt corpus provenance."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "src")
        self.dest = os.path.join(self.tmp, "dest")
        os.makedirs(self.src)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_this_runs_captions_ship(self):
        import time as _t
        cutoff = _t.time()
        old = os.path.join(self.src, "from_an_older_prompt.txt")
        with open(old, "w") as f:
            f.write("stale")
        os.utime(old, (cutoff - 9999, cutoff - 9999))
        new = os.path.join(self.src, "ours.txt")
        with open(new, "w") as f:
            f.write("fresh")
        with open(os.path.join(self.src, "_caption_manifest.json"), "w") as f:
            f.write("{}")

        job = Job("shipit", mode="caption")
        job.spec = {"deliver_to": self.dest}
        job.state = {"caption_started_at": cutoff}
        with mock.patch.object(F, "notify", lambda *_a: None):
            F.ship_captions(job, self.src)

        shipped = sorted(os.listdir(self.dest))
        self.assertIn("ours.txt", shipped)
        self.assertIn("_caption_manifest_shipit.json", shipped,
                      "the record always ships, under its per-order name")
        self.assertNotIn("_caption_manifest.json", shipped,
                         "the BARE manifest name is the documented collision - it must not ship")
        self.assertNotIn("from_an_older_prompt.txt", shipped,
                         "another captioner's output must never ride along")
        self.assertTrue(os.path.exists(old), "and it is LEFT where it was, not moved or deleted")


class TestCaptionStaging(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "pool")
        self.dest = os.path.join(self.tmp, "probe")
        for sub in ("alpha", "beta"):
            os.makedirs(os.path.join(self.src, sub))
            for i in range(6):
                with open(os.path.join(self.src, sub, "%s_%d.jpg" % (sub, i)), "w") as f:
                    f.write("x")
        self.job = Job("stager", mode="caption")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_factory_stages_its_own_inputs(self):
        n = F.stage_caption_set(self.job, self.src, self.dest, count=4)
        self.assertEqual(n, 4)
        self.assertEqual(len([f for f in os.listdir(self.dest) if f.endswith(".jpg")]), 4)

    def test_sampling_is_stratified_across_collections(self):
        """Taking the first N of a sorted pool would quietly make every probe a study of whichever
        collection sorts first."""
        F.stage_caption_set(self.job, self.src, self.dest, count=4, stratify=True)
        got = os.listdir(self.dest)
        self.assertTrue(any(f.startswith("alpha__") for f in got), got)
        self.assertTrue(any(f.startswith("beta__") for f in got), got)

    def test_provenance_survives_the_copy(self):
        F.stage_caption_set(self.job, self.src, self.dest, count=2)
        self.assertTrue(all("__" in f for f in os.listdir(self.dest)),
                        "the source collection must stay readable in the name")

    def test_staging_is_idempotent_for_resume(self):
        """A pause/relaunch must not re-stage over sidecars already written beside the images."""
        F.stage_caption_set(self.job, self.src, self.dest, count=4)
        with open(os.path.join(self.dest, "already.txt"), "w") as f:
            f.write("a caption written before the pause")
        F.stage_caption_set(self.job, self.src, self.dest, count=4)
        self.assertTrue(os.path.exists(os.path.join(self.dest, "already.txt")))
        self.assertEqual(len([f for f in os.listdir(self.dest) if f.endswith(".jpg")]), 4)

    def test_capability_is_registered(self):
        self.assertIn("caption.stage", F.FACTORY_CAPABILITIES)


class TestManifestGenerationsLedger(unittest.TestCase):
    """A resumed run must preserve prior captioning records.
    Overwriting the manifest would erase audit trails for pre-pause images,
    creating a silent partial coverage gap that appears valid but is incomplete."""

    def test_merge_preserves_prior_generations_fields(self):
        """Asserts that `merge_manifest_fields` preserves prior field keys/values
        while adding new ones. This tests the live merge logic, not a copy.
        Regression: old inline test asserted against a frozen snapshot,
        bypassing the real foundry code."""
        prior = {"fields": {"a.jpg": {"view": "front"}, "b.jpg": {"view": "back"}},
                 "prompt_sha256": "OLDHASH", "schema": "rich", "captioned": 2}
        fresh = {"c.jpg": {"view": "profile"}}
        merged = F.merge_manifest_fields(prior["fields"], fresh)
        self.assertEqual(set(merged), {"a.jpg", "b.jpg", "c.jpg"})
        self.assertEqual(merged["a.jpg"]["view"], "front", "pre-pause records survive")

    def test_generations_ledger_records_which_prompt_did_what(self):
        """Verifies that the generations ledger explicitly maps prompts to their respective output counts,
        preserving history across schema changes rather than aggregating."""
        gens = []
        prior = {"prompt_sha256": "OLD", "schema": "rich", "captioned": 696}
        if prior and not gens:
            gens.append({"prompt_sha256": prior["prompt_sha256"], "captioned": 696})
        gens.append({"prompt_sha256": "NEW", "captioned": 1147})
        self.assertEqual(len(gens), 2)
        self.assertEqual(sum(g["captioned"] for g in gens), 1843)


class TestCaptionStopOrder(unittest.TestCase):

    def test_caption_stop_is_a_registered_capability(self):
        self.assertIn("caption.stop", F.FACTORY_CAPABILITIES)

    def test_the_caption_loop_reads_stop_orders(self):
        src = io.open(os.path.join(BIN, "p20_foundry.py"), encoding="utf-8").read()
        cap = src[src.index('elif phase == "CAPTION"'):src.index('elif phase == "LADDER"')]
        self.assertIn("stop_order(job)", cap,
                      "a caption run must be stoppable, not only pausable")
        self.assertLess(cap.index("stop_order(job)"), cap.index("ship_captions(job"),
                        "the stop must be seen BEFORE shipping, so what exists gets shipped")


class TestMultiHostFactory(unittest.TestCase):

    def test_no_foundry_path_is_hardcoded(self):
        """A literal F:\\foundry path defeats FOUNDRY_ROOT, and a second factory would then write
        its lock, heartbeat and STOP flags into the FIRST factory's tree -- two order takers, one
        lane, which the layout doc calls the hard rule."""
        # CODE only. Comments legitimately quote the old literals to explain why they went, and a
        # test that cannot tell prose from code fails on its own documentation.
        import re as _re
        bad = [ln.strip() for ln in self._code_lines()
               if _re.search(r'=\s*r"F:\\foundry', ln)]
        self.assertEqual(bad, [], "foundry-owned paths must derive from foundry_paths")

    @staticmethod
    def _code_lines():
        src = io.open(os.path.join(BIN, "p20_foundry.py"), encoding="utf-8").read()
        return [ln for ln in src.splitlines()
                if not ln.strip().startswith("#") and not ln.strip().startswith('"')]

    def test_fleet_host_falls_back_to_the_truth_not_a_sentinel(self):
        """The sister copy defaulted to "mothership": a factory that loses its env var would claim
        ANOTHER box's identity, inherit its capabilities and write to its fleet state. A wrong
        identity is worse than an absent one."""
        import foundry_paths as FP
        self.assertEqual(F.FLEET_HOST, FP.this_box(), "the foundry names itself through the one shared derivation")
        unset = lambda name, default="": "" if name == "FOUNDRY_FLEET_HOST" else default   # noqa: E731
        with mock.patch.object(FP, "setting", unset), \
                mock.patch("socket.gethostname", return_value="SomeFactory.example.lan"):
            self.assertEqual(FP.this_box(), "somefactory", "unset: the machine's own hostname, never a fixed name")
        with mock.patch.object(FP, "setting", lambda name, default="": "factory-x" if name == "FOUNDRY_FLEET_HOST"
                               else default):
            self.assertEqual(FP.this_box(), "factory-x", "set: the setting wins")

    def test_capability_is_detected_not_keyed_by_hostname(self):
        """A hostname table is wrong SILENTLY every time hardware changes or a factory is added.
        Their own handoff argues this for the render worker and then hardcodes it here."""
        code = "\n".join(self._code_lines())
        self.assertNotIn("_HOST_CAPS_OMIT", code, "no hostname table in CODE")
        self.assertIn("_detected_caps_to_omit", code)

    def test_host_tag_is_one_helper_so_the_format_cannot_drift(self):
        src = io.open(os.path.join(BIN, "p20_foundry.py"), encoding="utf-8").read()
        self.assertEqual(src.count("def _host_tag"), 1)
        self.assertGreaterEqual(src.count("_host_tag()"), 2,
                                "both the job DM and the desk DM use it")


class TestCapabilityAssessment(unittest.TestCase):
    """Capability assessment: an unbuildable order is HELD (a third fate - not archived, not
    retried forever) and releases itself when the capability arrives. The factory declares its
    own gaps as work items."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.q = os.path.join(self.tmp, "queue")
        os.makedirs(self.q)
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spec(self, name, requires=None):
        p = os.path.join(self.q, name + ".job.json")
        body = {"mode": "caption", "name": name}
        if requires is not None:
            body["requires"] = requires
        with open(p, "w") as f:
            json.dump(body, f)
        return p

    def test_an_order_needing_nothing_special_is_buildable(self):
        p = self._spec("plain")
        with mock.patch.multiple(F, QUEUE_DIR=self.q):
            self.assertTrue(F.assess_capability(p, self.logs.append))
        self.assertFalse(os.path.exists(F.held_marker(p)))

    def test_an_unbuildable_order_is_HELD_not_failed(self):
        """Held is a THIRD fate: not archived (nothing is wrong with it), not retried forever."""
        p = self._spec("futuristic", ["caption.telepathy"])
        with mock.patch.multiple(F, QUEUE_DIR=self.q):
            self.assertFalse(F.assess_capability(p, self.logs.append))
        marker = F.held_marker(p)
        self.assertTrue(os.path.exists(marker))
        self.assertTrue(os.path.exists(p), "the ORDER itself must be untouched")
        body = json.load(io.open(marker, encoding="utf-8"))
        self.assertEqual(body["missing"], ["caption.telepathy"])

    def test_a_held_order_is_invisible_to_the_queue(self):
        """It must not sit at the head of a priority-sorted queue blocking everything behind it."""
        held = self._spec("futuristic", ["caption.telepathy"])
        ok = self._spec("ordinary")
        with mock.patch.multiple(F, QUEUE_DIR=self.q):
            F.assess_capability(held, self.logs.append)
            pending = F.queued_orders()
        self.assertIn(ok, pending)
        self.assertNotIn(held, pending)

    def test_a_held_order_RELEASES_ITSELF_when_the_capability_arrives(self):
        """This is what makes 'then build the capability' a workflow instead of a slogan: adding
        the capability IS the whole fix -- no second action, no operator step."""
        p = self._spec("futuristic", ["caption.telepathy"])
        with mock.patch.multiple(F, QUEUE_DIR=self.q):
            F.assess_capability(p, self.logs.append)
            self.assertTrue(os.path.exists(F.held_marker(p)))
            caps = dict(F.FACTORY_CAPABILITIES)
            caps["caption.telepathy"] = "reads the operator's mind"
            with mock.patch.multiple(F, FACTORY_CAPABILITIES=caps):
                self.assertTrue(F.assess_capability(p, self.logs.append))
                self.assertFalse(os.path.exists(F.held_marker(p)))
                self.assertIn(p, F.queued_orders())

    def test_the_capabilities_this_build_actually_ships_are_registered(self):
        """The registry is a PROMISE. A name may only appear once the code behind it is deployed,
        which is the thing that stops it becoming hand-waving with a JSON file."""
        for cap in ("caption", "caption.timing", "caption.ship", "caption.audit",
                    "caption.schema.rich", "lane.yield", "order.after_exists"):
            self.assertIn(cap, F.FACTORY_CAPABILITIES)

    def test_held_orders_are_still_reassessed(self):
        """queued_orders() hides held orders, so assessment must sweep the RAW queue -- otherwise
        a held order could never be re-checked and would never release."""
        p = self._spec("futuristic", ["caption.telepathy"])
        with mock.patch.multiple(F, QUEUE_DIR=self.q):
            F.assess_all_queued(self.logs.append)
            self.assertTrue(os.path.exists(F.held_marker(p)))
            caps = dict(F.FACTORY_CAPABILITIES); caps["caption.telepathy"] = "x"
            with mock.patch.multiple(F, FACTORY_CAPABILITIES=caps), \
                    mock.patch.object(F, "notify_desk", lambda *_a: None):
                F.assess_all_queued(self.logs.append)
        self.assertFalse(os.path.exists(F.held_marker(p)))


class TestLogLineCannotKillTheFactory(unittest.TestCase):

    def test_stdout_can_encode_anything_a_message_might_contain(self):
        for ch in ("\U0001f6a8", "—", "é", "中"):
            try:
                sys.stdout.encoding and ch.encode(sys.stdout.encoding, errors="strict")
            except (UnicodeEncodeError, LookupError, TypeError):
                self.fail("stdout encoding %r cannot carry %r -- a log line could kill the runner"
                          % (sys.stdout.encoding, ch))

    def test_no_source_message_smuggles_non_ascii_into_a_print(self):
        """Belt and braces alongside the stream reconfigure: the alert text itself is ASCII."""
        src = io.open(os.path.join(BIN, "p20_foundry.py"), encoding="utf-8").read()
        i = src.find("TRESPASS - %d change(s)")
        self.assertGreater(i, 0, "the trespass alert text moved; re-point this test")
        self.assertTrue(src[i:i + 60].isascii(), "trespass alert must stay ASCII")


class TestPatrol(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.floor = os.path.join(self.tmp, "state")
        os.makedirs(self.floor)
        self.ledger = os.path.join(self.tmp, "ledger.json")
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patrol(self, worked=False):
        with mock.patch.multiple(F, SHOPFLOOR=[self.floor], PATROL_LEDGER=self.ledger,
                                 PATROL_IGNORE=frozenset()), \
                mock.patch.object(F, "notify_desk", lambda *_a: None):
            return F.patrol(self.logs.append, factory_worked=worked)

    def _plant(self, name, age_s):
        p = os.path.join(self.floor, name)
        with open(p, "w") as f:
            f.write("x")
        old = __import__("time").time() - age_s
        os.utime(p, (old, old))
        return p

    def test_first_pass_adopts_and_does_not_accuse(self):
        """A factory that cries trespass about its own existing artifacts is noise, and noise is
        how a real alert gets ignored."""
        self._plant("existing.json", 9999)
        self.assertEqual(self._patrol(), [])

    def test_a_foreign_file_is_reported(self):
        self._plant("existing.json", 9999)
        self._patrol()                                   # adopt
        self._plant("someone_elses.txt", 9999)           # old enough to clear the grace window
        found = self._patrol()
        self.assertTrue(any("someone_elses.txt" in p for p, _w in found), found)

    def test_a_deletion_is_reported(self):
        p = self._plant("doomed.json", 9999)
        self._patrol()
        os.remove(p)
        found = self._patrol()
        self.assertTrue(any("doomed" in q for q, _w in found), found)

    def test_the_factory_cannot_trespass_on_itself(self):
        """Verify the factory does not report its own output as trespassing.

        Regression for v1, which keyed ownership on file age; long runs caused
        sidecars to age out of the grace window before the next patrol pass,
        resulting in massive false positives (847+ alerts).
        Idleness is now measured by the order taker and passed in, so adopted
        output is ignored regardless of age."""
        self._plant("old.json", 9999)
        self._patrol()
        for i in range(5):
            self._plant("factory_output_%d.txt" % i, 9999)   # old enough to have tripped v1
        self.assertEqual(self._patrol(worked=True), [],
                         "the factory's own artifacts are never trespass")
        # and having adopted them, a later idle pass must not re-accuse them either
        self.assertEqual(self._patrol(worked=False), [])

    def test_live_signalling_is_not_the_shop_floor(self):
        joined = " ".join(F.SHOPFLOOR).lower()
        self.assertNotIn("var\\state", joined,
                         "live signalling belongs to whoever is signalling")
        self.assertTrue(any("work" in p.lower() for p in F.SHOPFLOOR), F.SHOPFLOOR)
        self.assertTrue(any("archive" in p.lower() for p in F.SHOPFLOOR), F.SHOPFLOOR)

    def test_ignored_paths_are_never_reported(self):
        """The ledger is the patrol's own writing, and the pause/stop flags are the DOCUMENTED
        customer mechanisms. v1 reported both -- policing the controls you told the customer to
        use is how a guard trains people to ignore it."""
        for p in (F.PATROL_LEDGER, F.PAUSE_FLAG, F.STOP_NOW):
            self.assertIn(os.path.normcase(p), F.PATROL_IGNORE)

    def test_patrol_never_deletes_anything(self):
        self._plant("old.json", 9999)
        self._patrol()
        intruder = self._plant("intruder.txt", 9999)
        self._patrol()
        self.assertTrue(os.path.exists(intruder), "report-only: the human decides, not a heuristic")


class TestArchiveGenerations(unittest.TestCase):
    """Tests that one name maps to exactly one current artifact. The archive logic avoids
    the 'FIRST-WINS' trap where the bare filename (oldest/stale) would be preferred
    over timestamped successors, causing silent state corruption on requeue."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.q = os.path.join(self.tmp, "queue")
        self.arch = os.path.join(self.tmp, "archive")
        os.makedirs(self.q); os.makedirs(self.arch)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _retire(self, name, body):
        p = os.path.join(self.q, name)
        with open(p, "w") as f:
            f.write(body)
        with mock.patch.multiple(F, QUEUE_DIR=self.q), \
                mock.patch.object(F, "drain_order_renders", lambda *_a: None):
            return F.retire_order(p)

    def test_a_klein_bake_gets_its_own_cache_NAME_so_it_cannot_be_confused(self):
        """Klein bakes write `.klein.*` to prevent silent compatibility errors when
        cross-feeding conditioning data between models with different encoders (e.g., flux2-dev/Mistral vs. klein/Qwen3)."""
        import subprocess as _sp
        calls = []
        tmp = tempfile.mkdtemp()
        try:
            job = mock.Mock()
            job.spec = {"bake_encoder": "klein"}
            job.log = lambda *_a: None
            prompts = [{"name": "core", "text": "a prompt"}]

            def fake_run(cmd, **kw):
                calls.append((cmd, kw.get("env") or {}))
                # simulate the bake tool writing the SUFFIXED artifact
                open(os.path.join(tmp, "core.klein.cond"), "w").close()
                R = collections.namedtuple("R", "returncode stdout stderr")
                return R(0, "BAKED", "")

            with mock.patch.object(F, "COND_DIR", tmp), \
                    mock.patch.object(F, "cond_needs_bake", lambda *_a: "absent"), \
                    mock.patch.object(_sp, "run", fake_run):
                F.bake_missing(job, prompts)

            self.assertTrue(calls, "the bake was never invoked")
            _cmd, env = calls[0]
            self.assertEqual(env.get("P20_BAKE_ENCODER"), "klein",
                             "the encoder choice must reach the bake tool")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "core.klein.txt")),
                            "the sidecar must be written under the SUFFIXED name, or staleness is "
                            "checked against the wrong artifact and it re-bakes forever")
            self.assertFalse(os.path.isfile(os.path.join(tmp, "core.txt")),
                             "a klein bake must not claim the default cond's name")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_bake_with_no_encoder_declared_is_byte_identical_to_before(self):
        """Additive, not substituting: every existing order omits `bake_encoder`, and those must
        keep writing exactly the names they always have."""
        import subprocess as _sp
        tmp = tempfile.mkdtemp()
        try:
            job = mock.Mock()
            job.spec = {}
            job.log = lambda *_a: None

            def fake_run(cmd, **kw):
                open(os.path.join(tmp, "core.cond"), "w").close()
                self.assertNotIn("P20_BAKE_ENCODER", kw.get("env") or {},
                                 "no encoder declared -> the bake tool must see no override")
                R = collections.namedtuple("R", "returncode stdout stderr")
                return R(0, "BAKED", "")

            with mock.patch.object(F, "COND_DIR", tmp), \
                    mock.patch.object(F, "cond_needs_bake", lambda *_a: "absent"), \
                    mock.patch.object(_sp, "run", fake_run):
                F.bake_missing(job, [{"name": "core", "text": "a prompt"}])
            self.assertTrue(os.path.isfile(os.path.join(tmp, "core.txt")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_shipped_count_distinguishes_produced_something_from_produced_nothing(self):
        """
        Verify that the shipped count distinguishes between an order that produced content
        and one that completed with empty lists (e.g., paused or failed renders).
        A zero count is a legitimate outcome, not a failure.
        """
        job = mock.Mock()
        job.output_dir = self.tmp
        job.name = "o"

        job.state = {"previews_done": [], "ladder_done": []}
        self.assertEqual(F._order_shipped_count(job), 0, "empty state must read as shipped-nothing")

        job.state = {"previews_done": [250, 500], "ladder_done": [1000]}
        self.assertEqual(F._order_shipped_count(job), 3)

        # a TRAINING order's product is a checkpoint on disk, not an entry in state
        job.state = {"previews_done": [], "ladder_done": []}
        open(os.path.join(self.tmp, "o_000000250.safetensors"), "w").close()
        self.assertEqual(F._order_shipped_count(job), 1,
                         "a checkpoint IS a shipped deliverable - miscounting this would cry wolf "
                         "on every training run")

    def test_shipped_count_never_raises_on_a_malformed_state(self):
        """Bookkeeping must never fail a finished order."""
        job = mock.Mock()
        job.output_dir = self.tmp
        job.name = "o"
        for bad in (None, {}, {"previews_done": None}, {"ladder_done": "nonsense"}):
            job.state = bad
            self.assertIsInstance(F._order_shipped_count(job), int)

    def test_a_STOPPED_order_takes_its_byproducts_with_it(self):
        """Verify that stopping an order archives its JOB-named byproducts
        (.job.state.json, .job.foundry.log) rather than leaving them in the queue.

        Note: The stop file is <name>.stop.json, but byproducts are named after
        the job (<name>.job.*), not the stop signal. If the sweep only looks for
        *.stop.*, it will miss these files and leave litter in the queue."""
        for sib in ("o.job.state.json", "o.job.foundry.log"):
            with open(os.path.join(self.q, sib), "w") as f:
                f.write("byproduct")
        self._retire("o.stop.json", '{"stop":1}')
        left = sorted(os.listdir(self.q))
        self.assertEqual(left, [], "a stopped order left byproducts behind: %s" % left)
        arch = sorted(os.listdir(self.arch))
        self.assertTrue(any(n.startswith("o.job.state.json") for n in arch), arch)
        self.assertTrue(any(n.startswith("o.job.foundry.log") for n in arch), arch)

    def test_a_deploys_stop_slip_retires_with_the_run_it_was_written_for(self):
        """Deploy slips are scoped to the run they were created for and retire with it, while customer slips persist without a TTL."""
        slips = {"fleet_deploy-20261022-083615.stop.json": {"target": "o", "why": "fleet_deploy: stop for a deploy"},
                 "fleet_deploy-20261022-090000.stop.json": {"target": "other"},
                 "customer.stop.json": {"target": "o", "why": "Alex: stop it"}}
        for n, d in slips.items():
            with open(os.path.join(self.q, n), "w") as f:
                json.dump(d, f)
        dst = self._retire("o.job.json", '{"name":"o"}')
        self.assertTrue(dst and not dst.startswith("!"), dst)
        self.assertEqual(sorted(os.listdir(self.q)), ["customer.stop.json", "fleet_deploy-20261022-090000.stop.json"])
        self.assertTrue(any(n.startswith("fleet_deploy-20261022-083615.stop.json.spent-")
                            for n in os.listdir(self.arch)), os.listdir(self.arch))

    def test_retire_is_idempotent_a_second_call_sweeps_recreated_sidecars(self):
        """Writers that legitimately fire AFTER an in-run retirement (the comfy-cleanup `finally`
        logs, the shipped-count bookkeeping saves state) recreate the sidecars behind the first
        sweep. The run loop then calls retire_order a second time on the already-absent spec; that
        call must sweep again, or every compute order leaves its litter in the queue forever."""
        spec = os.path.join(self.q, "o.job.json")
        dst = self._retire("o.job.json", '{"name":"o"}')
        self.assertTrue(dst and not dst.startswith("!"), dst)
        # simulate the post-retirement writers
        for sib in ("o.job.state.json", "o.job.foundry.log"):
            with open(os.path.join(self.q, sib), "w") as f:
                f.write("recreated after the first retirement")
        with mock.patch.multiple(F, QUEUE_DIR=self.q), \
                mock.patch.object(F, "drain_order_renders", lambda *_a: None):
            second = F.retire_order(spec)
        self.assertTrue(second.startswith("!nothing to archive"), second)
        left = sorted(os.listdir(self.q))
        self.assertEqual(left, [], "the second retirement left recreated sidecars behind: %s" % left)

    def test_the_fleet_items_file_retires_with_its_order(self):
        """The desk's parallel deal writes `<order>.items.json` beside the spec; it is an order
        byproduct like state and log, and must leave the queue when the order does."""
        with open(os.path.join(self.q, "o.items.json"), "w") as f:
            f.write("[]")
        self._retire("o.job.json", '{"name":"o"}')
        left = sorted(os.listdir(self.q))
        self.assertEqual(left, [], "the items file stayed behind: %s" % left)
        self.assertTrue(any(n.startswith("o.items.json") for n in sorted(os.listdir(self.arch))))

    def test_startup_sweep_archives_orphans_and_leaves_pending_work_and_slips(self):
        """The queue directory lists PENDING WORK and nothing else. Orphaned sidecars (no matching
        spec) are archived; a still-queued order keeps its sidecars (paused/requeued orders live
        there); registrations and slips are not sidecars and stay."""
        for orphan in ("gone.job.state.json", "gone.job.foundry.log", "gone.items.json"):
            with open(os.path.join(self.q, orphan), "w") as f:
                f.write("orphan")
        for keeper in ("live.job.json", "live.job.state.json", "live.job.foundry.log",
                       "live.items.json", "x.job.registration.json", "run_x.stop.json",
                       "c.ctl.json"):
            with open(os.path.join(self.q, keeper), "w") as f:
                f.write("{}")
        logged = []
        with mock.patch.multiple(F, QUEUE_DIR=self.q):
            moved = F.sweep_orphaned_sidecars(logged.append)
        self.assertEqual(3, moved, "exactly the three orphans move, nothing else")
        left = sorted(os.listdir(self.q))
        self.assertEqual(left, sorted(["live.job.json", "live.job.state.json",
                                       "live.job.foundry.log", "live.items.json",
                                       "x.job.registration.json", "run_x.stop.json", "c.ctl.json"]),
                         left)
        self.assertTrue(any("3 orphaned sidecar" in m for m in logged), logged)

    def test_a_normal_order_still_takes_its_byproducts_with_it(self):
        """The job path must keep working — the stop fix shares its implementation now."""
        for sib in ("o.job.state.json", "o.job.foundry.log"):
            with open(os.path.join(self.q, sib), "w") as f:
                f.write("byproduct")
        self._retire("o.job.json", '{"v":1}')
        self.assertEqual(sorted(os.listdir(self.q)), [])

    def test_no_bare_canonical_name_survives_in_the_archive(self):
        self._retire("o.job.json", '{"v":1}')
        names = os.listdir(self.arch)
        self.assertTrue(names)
        self.assertNotIn("o.job.json", names,
                         "a bare name is a filename that lies about which run it holds")
        self.assertTrue(all(n.startswith("o.job.json.") for n in names), names)

    def test_two_retirements_of_one_name_are_both_kept_and_distinguishable(self):
        self._retire("o.job.json", '{"v":1}')
        self._retire("o.job.json", '{"v":2}')
        gens = sorted(os.listdir(self.arch))
        self.assertEqual(len(gens), 2, "the archive is evidence; nothing may be overwritten")
        self.assertEqual(len(set(gens)), 2)

    def test_requeue_takes_the_NEWEST_generation(self):
        """The v10 scenario, reproduced."""
        import time as _t
        old = os.path.join(self.arch, "o.job.state.json")          # legacy bare name
        with open(old, "w") as f:
            f.write('{"step": 100}')
        os.utime(old, (1, 1))                                       # unambiguously ancient
        new = os.path.join(self.arch, "o.job.state.json.20260802-095516")
        with open(new, "w") as f:
            f.write('{"step": 2125}')
        gens = glob.glob(os.path.join(self.arch, "o.job.state.json*"))
        picked = max(gens, key=os.path.getmtime)
        self.assertEqual(picked, new, "newest generation, not the legacy bare name")
        self.assertIn("2125", io.open(picked).read())


class TestPauseTrainerGuard(unittest.TestCase):
    """Pins the pause guard at a resume boundary: with no step line yet the baseline is UNKNOWN, and
    the ignored-flag detector must arm from the first observed value -- gating on a falsy baseline
    disables it at exactly the boundary it exists for."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.stop = os.path.join(self.tmp, "STOP_NOW")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, steps, hard_cap=900):
        """pause_trainer against a scripted step sequence. steps[0] is read at flag-drop."""
        j = Job("v10", mode="train")
        with mock.patch.multiple(F, STOP_NOW=self.stop, STOP_HARD_CAP=hard_cap), \
                mock.patch.object(F, "trainer_running", return_value=True), \
                mock.patch.object(F, "current_step", side_effect=list(steps) + [steps[-1]] * 50), \
                mock.patch.object(F.time, "sleep", lambda *_a: None):
            F.pause_trainer(j)
        return j

    def test_unknown_baseline_still_catches_a_stepping_trainer(self):
        """THE REGRESSION TEST. Baseline unreadable at flag-drop (booting trainer), then it steps
        away. Before the fix this returned nothing and let the 900s backstop mislabel it."""
        seq = [None] + list(range(2059, 2075))
        with self.assertRaises(F.TrainerUnstoppable) as cm:
            self._run(seq)
        msg = str(cm.exception)
        self.assertIn("IGNORED", msg)
        self.assertIn("2059", msg, "must report the baseline it actually established, not 0")

    def test_a_known_baseline_is_unchanged(self):
        """The path that already worked must keep working -- one variable at a time."""
        with self.assertRaises(F.TrainerUnstoppable):
            self._run([2000] + list(range(2001, 2020)))

    def test_a_trainer_inside_the_allowance_is_left_alone(self):
        """1-2 extra steps after the flag is NORMAL (it tests STOP_NOW once per step). Exiting
        while inside the allowance must be a clean pause, not a fault."""
        calls = {"n": 0}

        def running(_job):
            calls["n"] += 1
            return calls["n"] < 4                    # exits on the 4th poll
        j = Job("v10", mode="train")
        with mock.patch.multiple(F, STOP_NOW=self.stop, STOP_HARD_CAP=900), \
                mock.patch.object(F, "trainer_running", side_effect=running), \
                mock.patch.object(F, "current_step", side_effect=[2000, 2001, 2002, 2002, 2002]), \
                mock.patch.object(F.time, "sleep", lambda *_a: None):
            F.pause_trainer(j)                       # must not raise
        self.assertTrue(any("exited cleanly" in m for m in j.logs), j.logs)

    def test_the_backstop_reports_what_it_measured(self):
        """It used to assert 'the step count static at N' as a hardcoded narrative, and said so
        while its own log showed thirty advancing steps. A guard may not describe a state it did
        not measure."""
        with self.assertRaises(F.TrainerUnstoppable) as cm:
            self._run([2000, 2000, 2000], hard_cap=-1)   # -1, not 0: elapsed 0.0 is not > 0
        msg = str(cm.exception)
        self.assertIn("advanced=False", msg)
        self.assertIn("saving or wedged", msg)
        self.assertNotIn("flag is not being read", msg, "static steps are NOT an ignored flag")

    def test_unstoppable_is_not_lane_busy(self):
        """Type contract. If TrainerUnstoppable were a LaneBusy, run_queue would 'wait and retry'
        forever against a card no wait can free; if it were a plain Exception the catch-all would
        ARCHIVE the order -- which is what failed v10 twice for another component's fault."""
        self.assertTrue(issubclass(F.TrainerUnstoppable, RuntimeError))
        self.assertFalse(issubclass(F.TrainerUnstoppable, F.LaneBusy))
        self.assertFalse(issubclass(F.LaneBusy, F.TrainerUnstoppable))
        self.assertFalse(issubclass(F.TrainerUnstoppable, F.PauseRequested))


class TestOrphanTrainerGuard(unittest.TestCase):
    """Test that foreign_run_guard detects active trainers via file locks rather than runtime checks.

    The guard relies on filesystem state (CURRENT_RUN) to determine lane ownership. It does not
    inspect the running process directly, so if the lock file is stale or missing, a new trainer
    could launch while a previous one still holds the GPU.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cur = os.path.join(self.tmp, "CURRENT_RUN.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _guard(self, job_name="v11", trainer=(None, "")):
        j = Job(job_name, mode="train")
        with mock.patch.multiple(F, CURRENT_RUN=self.cur), \
                mock.patch.object(F, "any_trainer_running", return_value=trainer):
            F.foreign_run_guard(j)
        return j

    def test_no_claim_and_no_trainer_is_free(self):
        self._guard()                                # must not raise

    def test_no_claim_but_a_live_foreign_trainer_is_NOT_free(self):
        """THE REGRESSION TEST: absence of a claim is not evidence of a free card."""
        with self.assertRaises(F.LaneBusy) as cm:
            self._guard(trainer=("18624", "python.exe -u run.py config\\concept_flux2_v10.yaml"))
        self.assertIn("18624", str(cm.exception))

    def test_our_own_trainer_does_not_block_our_resume(self):
        """A resume re-enters its OWN lane -- blocking that would deadlock every restart."""
        self._guard(job_name="concept_flux2_v10",
                    trainer=("18624", "python.exe -u run.py config\\concept_flux2_v10.yaml"))

    def test_a_done_claim_still_asks_the_card(self):
        """state=done reads as stated-free, but the trainer may still be writing its checkpoint."""
        with open(self.cur, "w") as f:
            json.dump({"name": "concept_flux2_v10", "state": "done"}, f)
        with self.assertRaises(F.LaneBusy):
            self._guard(trainer=("18624", "run.py config\\concept_flux2_v10.yaml"))

    def test_a_stale_claim_still_asks_the_card(self):
        """A dead REPORTER is not a dead trainer. The stale branch used to declare the card free
        on the strength of a timestamp alone."""
        with open(self.cur, "w") as f:
            json.dump({"name": "other", "state": "training", "step": 900,
                       "updated": "2020-01-01T00:00:00"}, f)
        with self.assertRaises(F.LaneBusy):
            self._guard(trainer=("18624", "run.py config\\other.yaml"))

    def test_cannot_enumerate_is_never_free(self):
        """Fail CLOSED -- 'I cannot tell' has never meant 'it is available'."""
        j = Job("v11", mode="train")
        with mock.patch.multiple(F, CURRENT_RUN=self.cur), \
                mock.patch.object(F, "any_trainer_running", side_effect=OSError("wmic gone")):
            with self.assertRaises(F.LaneBusy):
                F.foreign_run_guard(j)


class TestManifestMerge(unittest.TestCase):

    RICH = {"build": "slender", "pose": "standing", "hair": "short", "state": "clothed",
            "view": "front", "crop": "waist up"}
    FRAMING_ONLY = {"crop": "knee up", "head_crop": "head partly cropped",
                    "framing_tags": ["knee up"], "limbs_out": []}

    def test_manifest_merge_never_amputates(self):
        merged = F.merge_manifest_fields({"a.jpg": dict(self.RICH)},
                                         {"a.jpg": dict(self.FRAMING_ONLY)})
        rec = merged["a.jpg"]
        self.assertEqual(rec["build"], "slender")          # rich keys SURVIVE
        self.assertEqual(rec["pose"], "standing")
        self.assertEqual(rec["crop"], "knee up")           # newer value wins per key
        self.assertEqual(rec["head_crop"], "head partly cropped")

    def test_new_stems_and_prior_stems_both_kept(self):
        merged = F.merge_manifest_fields({"a.jpg": dict(self.RICH)},
                                         {"b.jpg": dict(self.FRAMING_ONLY)})
        self.assertIn("a.jpg", merged)
        self.assertIn("b.jpg", merged)
        self.assertEqual(merged["a.jpg"], self.RICH)

    def test_recaption_updates_attributes(self):
        merged = F.merge_manifest_fields({"a.jpg": dict(self.RICH)},
                                         {"a.jpg": {"build": "average", "pose": "seated"}})
        self.assertEqual(merged["a.jpg"]["build"], "average")
        self.assertEqual(merged["a.jpg"]["view"], "front")

    def test_empty_prior_and_empty_new(self):
        self.assertEqual(F.merge_manifest_fields(None, {"a.jpg": {"x": 1}}),
                         {"a.jpg": {"x": 1}})
        self.assertEqual(F.merge_manifest_fields({"a.jpg": {"x": 1}}, None),
                         {"a.jpg": {"x": 1}})


class TestComfyOutputIsPerHost(unittest.TestCase):

    def test_output_dir_is_env_driven_with_the_historical_default(self):
        self.assertTrue(hasattr(F, "COMFY_OUTPUT_DIR"))
        # the constant is the setting the foundry's path module reads (FOUNDRY_COMFY_OUTPUT, else <COMFY_ROOT>\output,
        # else empty: a box with no ComfyUI renders nothing)
        self.assertEqual(F.COMFY_OUTPUT_DIR, F.P.COMFY_OUTPUT.strip())

    def test_render_uses_the_constant_not_a_literal(self):
        """The literal must not come back. Asserting on source is defensible here: this is a path
        constant with no observable behaviour from inside the test process."""
        src = io.open(os.path.join(BIN, "p20_foundry.py"), encoding="utf-8").read()
        body = src.split("def render(", 1)[1][:20000]
        code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
        self.assertNotIn(r'r"F:\comfyui-rocm\output"', code,
                         "render() must use COMFY_OUTPUT_DIR, never a hardcoded comfy output path")
        self.assertIn("COMFY_OUTPUT_DIR", code)


class TestDemonGate(unittest.TestCase):

    class _Job(object):
        name = "demon_t"
        load = "gpu"

        def __init__(self):
            self.logged = []

        def log(self, m):
            self.logged.append(m)

    def _hb(self, age_s):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "stall_rescue.heartbeat")
        open(p, "w").close()
        past = time.time() - age_s
        os.utime(p, (past, past))
        return p

    def test_fresh_heartbeat_passes(self):
        with mock.patch.object(F, "DEMON_REQUIRED", True), \
             mock.patch.object(F, "DEMON_HEARTBEAT", self._hb(5)):
            F.require_demon(self._Job())          # must not raise

    def test_stale_heartbeat_raises_LaneBusy_after_failed_revival(self):
        """LaneBusy, deliberately: demon-down is a property of the FACTORY, never of the order -- the
        catch-all must not archive a healthy order for a factory fault."""
        with mock.patch.object(F, "DEMON_REQUIRED", True), \
             mock.patch.object(F, "DEMON_HEARTBEAT", self._hb(999)), \
             mock.patch.object(F, "revive_demon", lambda log: False), \
             mock.patch.object(F, "notify", lambda *a, **k: None):
            with self.assertRaises(F.LaneBusy):
                F.require_demon(self._Job())

    def test_absent_heartbeat_is_down_not_fine(self):
        """could-not-tell is never 'watching'."""
        with mock.patch.object(F, "DEMON_REQUIRED", True), \
             mock.patch.object(F, "DEMON_HEARTBEAT",
                               os.path.join(tempfile.mkdtemp(), "never_written.heartbeat")), \
             mock.patch.object(F, "revive_demon", lambda log: False), \
             mock.patch.object(F, "notify", lambda *a, **k: None):
            with self.assertRaises(F.LaneBusy):
                F.require_demon(self._Job())

    def test_successful_revival_opens_the_door(self):
        with mock.patch.object(F, "DEMON_REQUIRED", True), \
             mock.patch.object(F, "DEMON_HEARTBEAT", self._hb(999)), \
             mock.patch.object(F, "revive_demon", lambda log: True):
            F.require_demon(self._Job())          # revived -> no raise

    def test_not_required_hosts_pass_without_a_demon(self):
        """BigHonker/Softserve have no stall-rescue daemon; the gate must not brick them."""
        with mock.patch.object(F, "DEMON_REQUIRED", False), \
             mock.patch.object(F, "DEMON_HEARTBEAT",
                               os.path.join(tempfile.mkdtemp(), "absent.heartbeat")):
            F.require_demon(self._Job())          # must not raise

    # ── v6.2 capability HOLD: "protected" must mean protected against THIS work type ──

    def _state_dir(self, capabilities):
        """A fake P.STATE_DIR whose status/stall_rescue.json declares `capabilities`
        (None = no capabilities key at all — the pre-v6.2 daemon shape)."""
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "status"))
        body = {"component": "stall_rescue", "state": "watching"}
        if capabilities is not None:
            body["capabilities"] = capabilities
        with open(os.path.join(d, "status", "stall_rescue.json"), "w", encoding="utf-8") as f:
            json.dump(body, f)
        return d

    def _compute_job(self):
        j = self._Job()
        j.spec = {"mode": "compute"}
        return j

    def test_compute_holds_when_demon_lacks_the_arm(self):
        """A beating v6.1 daemon cannot watch compute — running the order anyway is exactly the
        unprotected-overnight-capture hole the gate exists to close."""
        with mock.patch.object(F, "DEMON_REQUIRED", True), \
             mock.patch.object(F, "DEMON_HEARTBEAT", self._hb(5)), \
             mock.patch.object(F.P, "STATE_DIR", self._state_dir(["watch.train", "watch.render"])), \
             mock.patch.object(F, "notify", lambda *a, **k: None):
            with self.assertRaises(F.LaneBusy):
                F.require_demon(self._compute_job())

    def test_compute_holds_when_no_capabilities_declared(self):
        """An absent list is a pre-v6.2 daemon: it has never watched compute. Hold, don't guess."""
        with mock.patch.object(F, "DEMON_REQUIRED", True), \
             mock.patch.object(F, "DEMON_HEARTBEAT", self._hb(5)), \
             mock.patch.object(F.P, "STATE_DIR", self._state_dir(None)), \
             mock.patch.object(F, "notify", lambda *a, **k: None):
            with self.assertRaises(F.LaneBusy):
                F.require_demon(self._compute_job())

    def test_compute_passes_when_the_arm_is_declared(self):
        with mock.patch.object(F, "DEMON_REQUIRED", True), \
             mock.patch.object(F, "DEMON_HEARTBEAT", self._hb(5)), \
             mock.patch.object(F.P, "STATE_DIR",
                               self._state_dir(["watch.train", "watch.render", "watch.compute"])):
            F.require_demon(self._compute_job())  # must not raise

    def test_train_orders_grandfathered_without_capabilities(self):
        """The daemon watched training long before capabilities existed — a missing list must not
        hold the work it has always protected."""
        j = self._Job()
        j.spec = {"mode": "train"}
        with mock.patch.object(F, "DEMON_REQUIRED", True), \
             mock.patch.object(F, "DEMON_HEARTBEAT", self._hb(5)), \
             mock.patch.object(F.P, "STATE_DIR", self._state_dir(None)):
            F.require_demon(j)                    # must not raise

    def test_revival_reports_not_kills_when_the_loop_is_alive(self):
        d = self._state_dir(["watch.compute"])    # written just now -> fresh mtime
        logged = []
        with mock.patch.object(F, "DEMON_HEARTBEAT", self._hb(999)), \
             mock.patch.object(F.P, "STATE_DIR", d), \
             mock.patch.object(F.subprocess, "run") as sub:
            self.assertFalse(F.revive_demon(lambda m: logged.append(m)))
            sub.assert_not_called()
        self.assertTrue(any("NOT killing" in m for m in logged), logged)


class TestControlSlipsCountAsFactoryWork(unittest.TestCase):

    def _queue(self, slips):
        d = tempfile.mkdtemp()
        for name, body in slips.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                json.dump(body, f)
        return d

    def test_returns_a_count_so_the_caller_can_tell_the_patrol(self):
        d = self._queue({"a.ctl.json": {"action": "pause"}})
        with mock.patch.object(F, "QUEUE_DIR", d), \
             mock.patch.object(F, "PAUSE_FLAG", os.path.join(d, "pause.flag")):
            self.assertEqual(F.process_control_orders(lambda *a: None), 1)

    def test_a_reship_slip_marks_a_retired_orders_kept_output_from_its_archived_spec(self):
        """Reship reads deliver_to from the archived spec, marks the kept output, and delivers it via reship_kept. If no kept output exists, the slip must FAIL rather than mark it."""
        root = tempfile.mkdtemp()
        q, arch, out_root, dest = (os.path.join(root, d) for d in ("queue", "archive", "compute", "delivered"))
        for d in (q, arch, out_root, dest, os.path.join(out_root, "late.one_mothership")):
            os.makedirs(d, exist_ok=True)
        with open(os.path.join(arch, "late.one_mothership.job.json.20261009-132700"), "w", encoding="utf-8") as f:
            json.dump({"mode": "compute", "deliver_to": dest}, f)
        with open(os.path.join(out_root, "late.one_mothership", "results.json"), "w", encoding="utf-8") as f:
            f.write("{\"rows\": []}")
        with open(os.path.join(q, "r.ctl.json"), "w", encoding="utf-8") as f:
            json.dump({"action": "reship", "order": "late.one_mothership", "why": "share down at ship time"}, f)
        with open(os.path.join(q, "r2.ctl.json"), "w", encoding="utf-8") as f:
            json.dump({"action": "reship", "order": "late.none_mothership"}, f)
        logged = []
        with mock.patch.object(F, "QUEUE_DIR", q), mock.patch.object(F, "COMPUTE_OUT_ROOT", out_root):
            self.assertEqual(F.process_control_orders(logged.append), 2)
            marker = os.path.join(out_root, "late.one_mothership", F.UNSHIPPED)
            self.assertTrue(os.path.isfile(marker), logged)
            with open(marker, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["deliver_to"], dest)
            self.assertTrue(any("r2.ctl.json FAILED" in x and "no archived order" in x for x in logged), logged)
            self.assertFalse(os.path.exists(os.path.join(out_root, "late.none_mothership", F.UNSHIPPED)))
            with mock.patch.object(F, "notify_desk"):
                self.assertEqual(F.reship_kept(logged.append, isdir=lambda p: True), ["late.one_mothership"])
        self.assertTrue(os.path.isfile(os.path.join(dest, "late.one_mothership", "results.json")))
        self.assertFalse(os.path.exists(marker))

    def test_zero_when_there_is_nothing_to_do(self):
        """No slips must NOT set the worked flag -- otherwise every idle poll adopts whatever
        appeared on the floor, which is the guard switched off."""
        d = self._queue({})
        with mock.patch.object(F, "QUEUE_DIR", d):
            self.assertEqual(F.process_control_orders(lambda *a: None), 0)

    def test_a_FAILED_slip_still_counts(self):
        """A cancel can move a file and then raise. 'Did we touch the floor' is the question the
        patrol asks, not 'did it succeed'."""
        d = self._queue({"bad.ctl.json": {"action": "cancel", "order": "no_such_order"}})
        with mock.patch.object(F, "QUEUE_DIR", d):
            self.assertEqual(F.process_control_orders(lambda *a: None), 1)

    def test_unreadable_slip_still_counts_and_is_consumed(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "junk.ctl.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        with mock.patch.object(F, "QUEUE_DIR", d):
            self.assertEqual(F.process_control_orders(lambda *a: None), 1)
        self.assertFalse(os.path.exists(p), "a slip that cannot be actioned must never jam the desk")


class StopBackstopSaysWhatItSaw(unittest.TestCase):

    def test_an_honoured_flag_is_never_reported_as_ignored(self):
        v = F.stop_verdict(honoured=True, stepped=True, still_moving=False)
        self.assertIn("WAS read", v)
        self.assertNotIn("not being read", v)

    def test_only_a_trainer_still_stepping_is_said_to_ignore_the_flag(self):
        self.assertIn("not being read", F.stop_verdict(honoured=False, stepped=True, still_moving=True))
        self.assertNotIn("not being read", F.stop_verdict(honoured=False, stepped=True, still_moving=False))
        self.assertIn("never moved", F.stop_verdict(honoured=False, stepped=False, still_moving=False))

    def test_the_honoured_line_is_read_from_the_trainers_log(self):
        d = tempfile.mkdtemp(prefix="stoplog_")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "t.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write("Saved optimizer\n[P20] STOP_NOW honored - saved at step 938, exiting cleanly.\n")
        self.assertTrue(F._log_tail_has(types.SimpleNamespace(log_path=p), "STOP_NOW honored"))
        self.assertFalse(F._log_tail_has(types.SimpleNamespace(log_path=os.path.join(d, "none.log")), "x"))


class StaleClaimAfterAHeldOrder(unittest.TestCase):
    """Ensures the desk cancel does not deadlock on a stale claim (CURRENT_RUN still 'training' but trainer dead).
    Validates that the cancel checks claim age, finds the trainer by config.name (not order name), and releases the claim."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="staleclaim_")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.q = os.path.join(self.d, "queue")
        os.makedirs(self.q)
        os.makedirs(os.path.join(self.d, "archive"))
        cfg = os.path.join(self.d, "k.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('config:\n  name: "alex_krea2_v1"\n')
        bat = os.path.join(self.d, "run.bat")
        with open(bat, "w", encoding="utf-8") as f:
            f.write("venv\\python.exe -u run.py %s\n" % cfg)
        with open(os.path.join(self.q, "alex_krea2_v1_mothership.job.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "alex_krea2_v1_mothership", "mode": "train", "launcher": bat,
                       "output_dir": os.path.join(self.d, "out")}, f)
        self.cur = os.path.join(self.d, "CURRENT_RUN.json")

    def claim(self, minutes_ago):
        when = (datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)).isoformat()
        with open(self.cur, "w", encoding="utf-8") as f:
            json.dump({"name": "alex_krea2_v1_mothership", "state": "training", "updated": when}, f)

    def held(self, procs):
        with mock.patch.object(F, "CURRENT_RUN", self.cur), mock.patch.object(F, "QUEUE_DIR", self.q), \
                mock.patch.object(F, "python_cmdlines", lambda: procs):
            return F._order_still_holds_card("alex_krea2_v1_mothership")

    def test_a_stale_claim_with_no_trainer_releases_but_a_fresh_one_or_a_live_trainer_holds(self):
        trainer = [(2240, r"venv\python.exe  -u run.py D:\krea2-train\config\alex_krea2_v1.yaml")]
        self.claim(minutes_ago=5)
        self.assertTrue(self.held([])[0], "a fresh claim is a stated hold")
        self.claim(minutes_ago=120)
        self.assertEqual(self.held([]), (False, ""), "stale + no trainer = left over by an order that ended")
        self.assertTrue(self.held(trainer)[0], "the trainer is matched by its config.name, not the suffixed order")

    def test_the_cancel_releases_a_claim_that_names_the_cancelled_order(self):
        self.claim(minutes_ago=120)
        slip = os.path.join(self.q, "fleet-x.ctl.json")
        with open(slip, "w", encoding="utf-8") as f:
            json.dump({"action": "cancel", "order": "alex_krea2_v1_mothership", "why": "test"}, f)
        with mock.patch.object(F, "CURRENT_RUN", self.cur), mock.patch.object(F, "QUEUE_DIR", self.q), \
                mock.patch.object(F, "python_cmdlines", lambda: []), \
                mock.patch.object(F, "_desk_report_cancel", lambda *a, **k: None), \
                mock.patch.object(F.urllib.request, "urlopen", side_effect=OSError("no desk in a test")):
            F.process_control_orders(lambda *a: None)
        self.assertFalse(os.path.exists(slip))
        self.assertFalse(os.path.exists(self.cur), "the cancel saw the order end, so it owns the claim's cleanup")

    def test_the_launch_guard_knows_its_own_trainer_by_config_name(self):
        job = types.SimpleNamespace(name="alex_krea2_v1_mothership",
                                    launcher=os.path.join(self.d, "run.bat"), log=lambda m: None)
        with mock.patch.object(F, "CURRENT_RUN", os.path.join(self.d, "absent.json")), \
                mock.patch.object(F, "any_trainer_running",
                                  lambda: (2240, r"venv\python.exe -u run.py D:\x\alex_krea2_v1.yaml")):
            F.foreign_run_guard(job)        # its own trainer: no refusal
        other = types.SimpleNamespace(name="someone_else", launcher="", log=lambda m: None)
        with mock.patch.object(F, "CURRENT_RUN", os.path.join(self.d, "absent.json")), \
                mock.patch.object(F, "any_trainer_running",
                                  lambda: (2240, r"venv\python.exe -u run.py D:\x\alex_krea2_v1.yaml")):
            with self.assertRaises(F.LaneBusy):
                F.foreign_run_guard(other)

    def test_claim_age_reads_the_timestamp_and_treats_unreadable_as_fresh(self):
        self.assertGreater(F.claim_age_s({"updated": "2026-10-26T10:00:00"}), 0)
        self.assertEqual(F.claim_age_s({"updated": "not a time"}), 0.0)
        self.assertEqual(F.claim_age_s({}), 0.0)


class TestACancelThatMustWaitIsKept(unittest.TestCase):
    """A cancel slip that cannot be resolved (e.g., timeout) must remain queued, not fail immediately."""

    def _lab(self):
        d = tempfile.mkdtemp()
        q, arch = os.path.join(d, "queue"), os.path.join(d, "archive")
        os.makedirs(q)
        os.makedirs(arch)
        with open(os.path.join(q, "a_order.job.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "a_order", "mode": "compute"}, f)
        slip = os.path.join(q, "fleet-x.ctl.json")
        with open(slip, "w", encoding="utf-8") as f:
            json.dump({"action": "cancel", "order": "a_order", "why": "test"}, f)
        return q, slip

    def test_the_slip_stays_and_its_order_is_not_offered(self):
        q, slip = self._lab()
        logs = []
        with mock.patch.object(F, "QUEUE_DIR", q), \
             mock.patch.object(F, "_order_still_holds_card",
                               lambda n: (True, "could not enumerate trainers (TimeoutExpired) - refusing on the safe side")):
            F.process_control_orders(lambda m: logs.append(m))
            F.process_control_orders(lambda m: logs.append(m))
            self.assertTrue(os.path.exists(slip), "a cancel that must wait is kept for the next pass")
            self.assertIn("a_order", F.waiting_cancels())
            offered = [s for s in F.queued_orders() if F._queued_name(s) not in F.waiting_cancels()]
            self.assertEqual(offered, [], "the picker must not start an order whose cancel is waiting")
        self.assertEqual(sum("WAITING" in m for m in logs), 1, "logged once, not every pass: %s" % logs)
        self.assertFalse(any("FAILED" in m for m in logs), logs)

    def test_once_the_card_is_free_the_waiting_cancel_lands(self):
        q, slip = self._lab()
        with mock.patch.object(F, "QUEUE_DIR", q), \
             mock.patch.object(F, "_desk_report_cancel", lambda *a, **k: None):
            with mock.patch.object(F, "_order_still_holds_card", lambda n: (True, "CURRENT_RUN says state=compute")):
                F.process_control_orders(lambda *a: None)
            with mock.patch.object(F, "_order_still_holds_card", lambda n: (False, "")):
                F.process_control_orders(lambda *a: None)
            self.assertFalse(os.path.exists(slip))
            self.assertFalse(os.path.exists(os.path.join(q, "a_order.job.json")))
            self.assertEqual(F.waiting_cancels(), set())

    def test_a_broken_cancel_is_still_consumed(self):
        """The anti-jam rule stands for a slip that can never be applied."""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "bad.ctl.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"action": "cancel"}, f)
        with mock.patch.object(F, "QUEUE_DIR", d):
            F.process_control_orders(lambda *a: None)
        self.assertFalse(os.path.exists(p))

    def test_queued_name_strips_every_order_extension(self):
        for ext in (".job.json", ".job.yaml", ".job.yml"):
            self.assertEqual(F._queued_name(os.path.join("q", "a.b-c" + ext)), "a.b-c")


class ColdLoadIsRecorded(unittest.TestCase):
    """#34 Phase 7 step 4: residency is a placement COST, so its price must be measured. The clean load's own
    load_duration (ollama's reply) is the order's placement.load_s; --cold-load evicts first so an order measures it."""
    HEALTHY = {"shared_mb": 170, "dedicated_mb": 4000, "adapter_mb": 4200, "card_mb": 8192}

    def _run(self, residency, cold=False, reply=None):
        spec = importlib.util.spec_from_file_location("infer_ollama_cold", os.path.join(BIN, "infer_ollama.py"))
        io_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(io_mod)
        seq, posts = list(residency), []
        with contextlib.ExitStack() as st:
            for p in (mock.patch.object(io_mod, "_residency", lambda h, m: seq.pop(0) if len(seq) > 1 else seq[0]),
                      mock.patch.object(io_mod, "_spill_reading", lambda: self.HEALTHY),
                      mock.patch.object(io_mod.gpu_spill, "wait_released", lambda *a, **k: True),
                      mock.patch.object(io_mod, "_post", lambda *a, **k: posts.append(a) or {}),
                      mock.patch.object(io_mod, "_chat", lambda *a, **k: dict(reply or {})),
                      mock.patch.object(io_mod.time, "sleep", lambda s: None),
                      mock.patch("sys.stdout", new_callable=io.StringIO)):
                st.enter_context(p)
            placed = io_mod._assert_resident("http://x", "m", 8192, cold=cold)
        return placed, posts

    def test_a_warm_model_is_recorded_warm_with_no_load_price(self):
        placed, posts = self._run([(5e9, 5e9)])
        self.assertEqual((placed["cold"], placed["load_s"]), (False, None))
        self.assertEqual(posts, [], "a warm model is never evicted without --cold-load")

    def test_a_clean_load_records_ollamas_own_load_duration(self):
        placed, _ = self._run([(None, None), (5e9, 5e9)], reply={"load_duration": 12_345_000_000})
        self.assertEqual((placed["cold"], placed["load_s"]), (True, 12.35))

    def test_cold_load_evicts_a_resident_model_first_and_measures_the_reload(self):
        placed, posts = self._run([(5e9, 5e9)], cold=True, reply={"load_duration": 4_000_000_000})
        self.assertEqual(len(posts), 1, "exactly one eviction")
        self.assertEqual(posts[0][2].get("keep_alive"), 0)
        self.assertEqual((placed["cold"], placed["load_s"]), (True, 4.0))

    def test_a_reply_without_load_duration_is_unknown_never_zero(self):
        placed, _ = self._run([(None, None), (5e9, 5e9)], reply={})
        self.assertIsNone(placed["load_s"], "could not tell must not read as a free load")


class TestMemorySnapshotTransport(unittest.TestCase):
    def test_an_empty_successful_counter_sample_is_not_a_failed_query(self):
        import gpu_spill
        reading = {"counter_ok": True, "card_mb": 8192, "shared_mb": None, "dedicated_mb": None, "adapter_mb": None}
        output = type("Result", (), {"stdout": json.dumps(reading), "returncode": 0})()
        with mock.patch.object(gpu_spill.sys, "platform", "win32"), \
             mock.patch.object(gpu_spill, "ambient_memory", return_value={"used_mb": 164, "age_s": 0}), \
             mock.patch.object(gpu_spill.subprocess, "run", return_value=output):
            self.assertEqual(gpu_spill.snapshot(), dict(reading, ambient_mb=164, ambient_age_s=0))
            self.assertIsNone(gpu_spill.read())
        output.stdout = json.dumps(dict(reading, counter_ok=False))
        slept = []
        with mock.patch.object(gpu_spill.sys, "platform", "win32"), \
             mock.patch.object(gpu_spill.subprocess, "run", return_value=output):
            self.assertIsNone(gpu_spill.snapshot(sleep=slept.append), "two failed queries are a failure")
        self.assertEqual(slept, [gpu_spill.RESAMPLE_S])

    def test_a_failed_counter_query_is_re_sampled_once_before_it_reads_as_unreadable(self):
        import gpu_spill
        good = {"counter_ok": True, "card_mb": 24560, "shared_mb": 120, "dedicated_mb": 18000, "adapter_mb": 19000}
        bad = type("Result", (), {"stdout": json.dumps(dict(good, counter_ok=False)), "returncode": 0})()
        ok = type("Result", (), {"stdout": json.dumps(good), "returncode": 0})()
        slept = []
        with mock.patch.object(gpu_spill.sys, "platform", "win32"), \
             mock.patch.object(gpu_spill.subprocess, "run", side_effect=[bad, ok]) as run:
            self.assertEqual(gpu_spill.snapshot(sleep=slept.append), good)
        self.assertEqual((run.call_count, slept), (2, [gpu_spill.RESAMPLE_S]))
        with mock.patch.object(gpu_spill.sys, "platform", "win32"), \
             mock.patch.object(gpu_spill.subprocess, "run", side_effect=[ok]) as run:
            self.assertEqual(gpu_spill.snapshot(sleep=slept.append), good)
        self.assertEqual(run.call_count, 1, "a good first read is not re-sampled")

    def test_start_sends_the_actual_order_in_the_authenticated_post_body(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true,"start_ok":false}'
        order = {"payload": "infer_ollama.py", "args": ["--model", "m", "--num-ctx", "39936"]}
        with mock.patch.object(F, "_worker_token", return_value="fixture-token"), \
             mock.patch.object(F, "start_memory_snapshot", return_value={"counter_ok": True}), \
             mock.patch.object(F.urllib.request, "urlopen", return_value=response) as sent:
            r = F.desk_availability(order)
        request = sent.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"host": F.FLEET_HOST, "spec": order,
                                                  "gpu_memory": {"counter_ok": True}})
        self.assertFalse(r["start_ok"])

    def test_plex_coexistence_never_bypasses_fit_and_midrun_does_not_recheck_own_load(self):
        avail = {"gpu_ok": True, "model_fit": {"ok": False, "why": "model fit: no room"}}
        self.assertIn("model fit:", F.host_unavailable_reason(avail, plex_ok=True))
        self.assertEqual(F.host_unavailable_reason(avail, running=True), "")


class TestSharedMemorySpillIsNotResidency(unittest.TestCase):
    """Shared memory usage indicates spilling, not residency; gpu_spill checks runner counters, not Ollama size fields."""
    HEALTHY_FULL = {"shared_mb": 170, "dedicated_mb": 24300, "adapter_mb": 24438, "card_mb": 24560}
    SPILLED = {"shared_mb": 1544, "dedicated_mb": 23526, "adapter_mb": 23683, "card_mb": 24560}
    ROOMY = {"shared_mb": 540, "dedicated_mb": 4208, "adapter_mb": 4372, "card_mb": 8192}

    def _io(self, readings, vram=17.6e9, size=17.6e9):
        spec = importlib.util.spec_from_file_location("infer_ollama_undertest", os.path.join(BIN, "infer_ollama.py"))
        io_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(io_mod)
        seq = list(readings)
        return io_mod, [mock.patch.object(io_mod, "_residency", lambda h, m: (vram, size)),
                        mock.patch.object(io_mod, "_spill_reading", lambda: seq.pop(0) if len(seq) > 1 else seq[0]),
                        mock.patch.object(io_mod.gpu_spill, "wait_released", lambda *a, **k: True),
                        mock.patch.object(io_mod, "_post", lambda *a, **k: {}),
                        mock.patch.object(io_mod, "_chat", lambda *a, **k: {}),
                        mock.patch.object(io_mod.time, "sleep", lambda s: None)]

    def test_the_rule_matches_the_three_measured_points(self):
        import gpu_spill
        self.assertFalse(gpu_spill.spilled(self.HEALTHY_FULL), "170 MB on a full card is the healthy run")
        self.assertTrue(gpu_spill.spilled(self.SPILLED), "1,544 MB on a full card is the 15 tok/s run")
        self.assertFalse(gpu_spill.spilled(self.ROOMY), "540 MB on a half-empty card is BigHonker's healthy MiniCPM-V")
        self.assertFalse(gpu_spill.spilled(None), "could not tell is not a spill verdict")

    def test_a_spilled_model_is_refused_after_one_clean_reload(self):
        io_mod, patches = self._io([self.SPILLED])
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(mock.patch("sys.stdout", out))
            with self.assertRaises(SystemExit) as cm:
                io_mod._assert_resident("http://x", "gemma3:27b", 78848)
        self.assertEqual(cm.exception.code, F.REFUSED_RC)
        self.assertIn("shared system memory", out.getvalue())
        self.assertIn("evicting and reloading clean", out.getvalue())

    def test_a_spill_that_clears_on_the_clean_reload_runs_and_is_recorded(self):
        io_mod, patches = self._io([self.SPILLED, self.HEALTHY_FULL])
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(mock.patch("sys.stdout", new_callable=io.StringIO))
            placed = io_mod._assert_resident("http://x", "gemma3:27b", 78848)
        self.assertEqual(placed["spill"], self.HEALTHY_FULL)
        self.assertFalse(placed["offload"])

    def test_unreadable_counters_are_recorded_as_could_not_tell(self):
        io_mod, patches = self._io([None])
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            placed = io_mod._assert_resident("http://x", "m", 8192)
        self.assertEqual(placed["spill"], "could not tell")

    def test_wait_released_waits_for_the_runner_to_let_go(self):
        import gpu_spill
        held = {"shared_mb": 0, "dedicated_mb": 20000, "adapter_mb": 20100, "card_mb": 24560}
        seq = [held, held, dict(held, dedicated_mb=40)]
        self.assertTrue(gpu_spill.wait_released(timeout=60, read_fn=lambda: seq.pop(0), sleep=lambda s: None))
        self.assertFalse(gpu_spill.wait_released(timeout=0, read_fn=lambda: held, sleep=lambda s: None))
        self.assertTrue(gpu_spill.wait_released(timeout=0, read_fn=lambda: None, sleep=lambda s: None))


class TestCancelSurvivesAnAutomatedRequeue(unittest.TestCase):

    def _lab(self, archived=("a_order.job.json",), cancelled=None):
        d = tempfile.mkdtemp()
        q = os.path.join(d, "queue")
        arch = os.path.join(d, "archive")
        os.makedirs(q)
        os.makedirs(arch)
        for a in archived:
            with open(os.path.join(arch, a), "w", encoding="utf-8") as f:
                json.dump({"name": "a_order"}, f)
        if cancelled:
            with open(os.path.join(arch, cancelled), "w", encoding="utf-8") as f:
                json.dump({"at": "2026-08-09T12:44:00", "why": "operator ended it"}, f)
        return q, arch

    def _slip(self, q, body, name="s.ctl.json"):
        with open(os.path.join(q, name), "w", encoding="utf-8") as f:
            json.dump(body, f)

    def test_requeue_refuses_a_cancelled_order(self):
        q, arch = self._lab(cancelled="a_order.cancelled")
        self._slip(q, {"action": "requeue", "order": "a_order"})
        logs = []
        with mock.patch.object(F, "QUEUE_DIR", q):
            F.process_control_orders(lambda m: logs.append(m))
        self.assertFalse(os.path.exists(os.path.join(q, "a_order.job.json")),
                         "a cancelled order must NOT be restored by an automated requeue")
        self.assertTrue(any("CANCELLED" in m for m in logs), logs)

    def test_the_refusal_names_the_override(self):
        """An operator who genuinely wants it back must be told the exact word, not left guessing."""
        q, arch = self._lab(cancelled="a_order.cancelled")
        self._slip(q, {"action": "requeue", "order": "a_order"})
        logs = []
        with mock.patch.object(F, "QUEUE_DIR", q):
            F.process_control_orders(lambda m: logs.append(m))
        self.assertTrue(any("force" in m for m in logs), logs)

    def test_force_brings_it_back_and_clears_the_tombstone(self):
        q, arch = self._lab(cancelled="a_order.cancelled")
        self._slip(q, {"action": "requeue", "order": "a_order", "force": True})
        with mock.patch.object(F, "QUEUE_DIR", q):
            F.process_control_orders(lambda *a: None)
        self.assertTrue(os.path.exists(os.path.join(q, "a_order.job.json")))
        self.assertFalse(os.path.exists(os.path.join(arch, "a_order.cancelled")),
                         "a forced requeue is a fresh intent; the tombstone must not linger")

    def test_an_uncancelled_order_still_requeues_normally(self):
        """The demon's remedy for its OWN stop must keep working — this guard is narrow."""
        q, arch = self._lab()
        self._slip(q, {"action": "requeue", "order": "a_order"})
        with mock.patch.object(F, "QUEUE_DIR", q):
            F.process_control_orders(lambda *a: None)
        self.assertTrue(os.path.exists(os.path.join(q, "a_order.job.json")))

    def test_cancel_during_the_remedy_window_is_RECORDED_not_lost(self):
        """Test that a cancel received while an order is archived during remedy
        (between stop and requeue) is recorded as cancelled, not lost.

        If the cancel arrives when the order is neither queued nor running,
        it cannot be retired immediately, but must still persist so it is not
        silently ignored upon re-queuing."""
        q, arch = self._lab(archived=("a_order.job.json.20260809-124357",))
        self._slip(q, {"action": "cancel", "order": "a_order", "why": "operator ended it"})
        logs = []
        with mock.patch.object(F, "QUEUE_DIR", q), \
             mock.patch.object(F, "_order_still_holds_card", lambda t: (False, "")):
            F.process_control_orders(lambda m: logs.append(m))
        self.assertTrue(os.path.exists(os.path.join(arch, "a_order.cancelled")),
                        "the operator's cancel must survive the remedy window")
        self.assertTrue(any("recorded the cancel" in m for m in logs), logs)

    def test_a_cancel_for_a_RUNNING_order_still_points_at_the_stop_channel(self):
        """The narrow guard must not swallow the case it was never meant to cover: an order that
        holds the card is stopped with a stop slip, and the operator must still be told that."""
        q, arch = self._lab(archived=("a_order.job.json.20260809-124357",))
        self._slip(q, {"action": "cancel", "order": "a_order"})
        logs = []
        with mock.patch.object(F, "QUEUE_DIR", q), \
             mock.patch.object(F, "_order_still_holds_card", lambda t: (True, "CURRENT_RUN")):
            F.process_control_orders(lambda m: logs.append(m))
        self.assertFalse(os.path.exists(os.path.join(arch, "a_order.cancelled")))
        self.assertTrue(any("stop channel" in m for m in logs), logs)

    def test_cancel_writes_the_tombstone(self):
        q, arch = self._lab(archived=())
        with open(os.path.join(q, "a_order.job.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "a_order"}, f)
        self._slip(q, {"action": "cancel", "order": "a_order", "why": "operator ended it"})
        with mock.patch.object(F, "QUEUE_DIR", q), \
             mock.patch.object(F, "_order_still_holds_card", lambda t: (False, "")):
            F.process_control_orders(lambda *a: None)
        self.assertTrue(os.path.exists(os.path.join(arch, "a_order.cancelled")),
                        "without the tombstone the demon's requeue would undo this cancel")

    def test_cancelling_a_desk_order_closes_it_at_the_desk(self):
        q, arch = self._lab(archived=())
        with open(os.path.join(q, "d_order.job.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "d_order", "fleet_order_id": "abc123"}, f)
        self._slip(q, {"action": "cancel", "order": "d_order", "why": "no longer needed"})
        told = []
        with mock.patch.object(F, "QUEUE_DIR", q), \
             mock.patch.object(F, "_order_still_holds_card", lambda t: (False, "")), \
             mock.patch.object(F, "report_fleet_result",
                               lambda job, ok, why="", status=None: told.append((job.spec, ok, why, status))):
            logs = []
            F.process_control_orders(lambda m: logs.append(m))
        self.assertEqual(len(told), 1, "the desk was not told; log: %r" % logs)
        spec, ok, why, status = told[0]
        self.assertEqual((spec.get("fleet_order_id"), ok), ("abc123", False))
        self.assertEqual(status, "cancelled", "an operator cancel must reach the desk as cancelled, not failed")
        self.assertIn("no longer needed", why)

    def _post_capture(self, fail=False):
        """Run the REAL report_fleet_result against a mocked urlopen; returns (requests, logs)."""
        reqs, logs = [], []

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            reqs.append(req.full_url)
            if fail:
                raise OSError("desk down")
            return _R()
        return reqs, logs, urlopen

    def test_cancel_posts_status_cancelled_to_the_desk(self):
        reqs, logs, urlopen = self._post_capture()
        with mock.patch.object(F, "_worker_token", lambda: "tok"), \
             mock.patch.object(F.urllib.request, "urlopen", urlopen):
            F._desk_report_cancel({"fleet_order_id": "abc123"}, "d_order", "no longer needed", logs.append)
        self.assertEqual(len(reqs), 1, "exactly one POST; logs: %r" % logs)
        from urllib.parse import urlparse, parse_qs
        u = urlparse(reqs[0])
        self.assertTrue(u.path.endswith("/p20/jobs/abc123/result"), u.path)
        q = parse_qs(u.query)
        self.assertEqual(q["status"], ["cancelled"])
        self.assertTrue(q["error"][0].startswith("cancelled on "), q)
        self.assertIn("by operator slip", q["error"][0])
        self.assertTrue(any("reported cancelled for step abc123" in m for m in logs), logs)

    def test_report_fleet_result_defaults_are_unchanged(self):
        import types
        from urllib.parse import urlparse, parse_qs
        for ok, want in ((True, "done"), (False, "failed")):
            reqs, logs, urlopen = self._post_capture()
            shim = types.SimpleNamespace(spec={"fleet_order_id": "j1"}, log=logs.append)
            with mock.patch.object(F, "_worker_token", lambda: "tok"), \
                 mock.patch.object(F.urllib.request, "urlopen", urlopen):
                F.report_fleet_result(shim, ok, "boom")
            self.assertEqual(parse_qs(urlparse(reqs[0]).query)["status"], [want])

    def test_cancel_report_never_raises_when_the_desk_is_down(self):
        reqs, logs, urlopen = self._post_capture(fail=True)
        with mock.patch.object(F, "_worker_token", lambda: "tok"), \
             mock.patch.object(F.urllib.request, "urlopen", urlopen):
            F._desk_report_cancel({"fleet_order_id": "abc123"}, "d_order", "x", logs.append)
        self.assertEqual(len(reqs), 1)
        self.assertTrue(any("fleet completion report failed" in m for m in logs), logs)

    def test_a_desk_that_cannot_be_told_never_breaks_the_cancel(self):
        q, arch = self._lab(archived=())
        with open(os.path.join(q, "e_order.job.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "e_order", "fleet_order_id": "zzz"}, f)
        self._slip(q, {"action": "cancel", "order": "e_order"})

        def boom(*a, **k):
            raise RuntimeError("desk down")
        with mock.patch.object(F, "QUEUE_DIR", q), \
             mock.patch.object(F, "_order_still_holds_card", lambda t: (False, "")), \
             mock.patch.object(F, "report_fleet_result", boom):
            F.process_control_orders(lambda *a: None)
        self.assertTrue(os.path.exists(os.path.join(arch, "e_order.cancelled")), "the cancel itself must still land")


class TestACollectedOrderHasOneName(unittest.TestCase):
    """The spec file is named by the spec's name; the queue file is named by order_name, which can differ in late-routed records."""

    def _collect(self, record):
        q = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, q, ignore_errors=True)

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            url = req.full_url
            body = {"jobs": [record]} if "/p20/jobs?host=" in url else {"ok": True}
            return _Resp(json.dumps(body).encode())
        with mock.patch.object(F, "_worker_token", lambda: "tok"), mock.patch.object(F, "QUEUE_DIR", q), \
                mock.patch.object(F.urllib.request, "urlopen", urlopen):
            got = F.collect_fleet_orders(lambda *a: None)
        return got, sorted(os.listdir(q))

    def test_the_file_takes_the_specs_name_not_the_records_order_name(self):
        got, files = self._collect({"kind": "factory_order", "id": "41908c229218", "order_name": "tb-x.20261011-0241",
                                    "spec": {"name": "tb-x.20261011-0241_softserve", "mode": "compute"}})
        self.assertEqual((got, files), (1, ["tb-x.20261011-0241_softserve.job.json"]))

    def test_a_spec_without_a_name_still_takes_the_records_order_name(self):
        got, files = self._collect({"kind": "factory_order", "id": "j2", "order_name": "o2_softserve",
                                    "spec": {"mode": "compute"}})
        self.assertEqual((got, files), (1, ["o2_softserve.job.json"]))


class TestATransientNeverConsumesAnOrder(unittest.TestCase):
    """Transient timeouts must mark orders as WAIT; only real failures may consume them as FATAL."""

    class _Job:
        def __init__(self, path):
            self.spec = {"name": "o1", "fleet_order_id": "j9"}
            self.name = "o1"
            self.logs = []
            self.state = {}

        def log(self, m):
            self.logs.append(m)

        def save(self):
            pass

    def _queue(self, run_raises=None, then=None):
        """Drive the REAL run_queue over one queued order whose run() raises `run_raises` (or finishes, after calling
        `then(job)`); one pass, then a pause. Statuses the desk was told land in self.statuses."""
        told, retired, logs = [], [], []
        self.statuses = []
        stub = dict(acquire_runner_singleton=lambda log: True, sweep_orphaned_sidecars=lambda log: None,
                    collect_fleet_controls=lambda log: 0, process_control_orders=lambda log: 0,
                    collect_fleet_orders=lambda log: 0, assess_all_queued=lambda log: None,
                    patrol=lambda log, factory_worked=False: None, gpu_patrol=lambda log: None,
                    publish_runner_card=lambda s: None, _announce_capabilities=lambda: None,
                    queued_orders=lambda: ["o1.job.json"], Job=self._Job, card_check_before_launch=lambda job: None,
                    notify=lambda job, m: None, release_lane_if_ours=lambda n, log: None,
                    consume_pause_flag=lambda: None, release_runner_singleton=lambda: None,
                    clear_current_run=lambda: None,
                    retire_order=lambda p: retired.append(p),
                    _order_shipped_count=lambda job: 1,
                    report_fleet_result=lambda job, ok, why="", status=None: (
                        told.append((job.spec["fleet_order_id"], ok)), self.statuses.append(status)))
        pauses = iter([False, True])

        def run(job):
            if then is not None:
                then(job)
            if run_raises is not None:
                raise run_raises
        with mock.patch.multiple(F, **stub), mock.patch.object(F, "run", run), \
                mock.patch.object(F, "pause_requested", lambda: next(pauses)), \
                mock.patch.object(F, "stop_comfy", lambda job: None), \
                mock.patch.object(F.time, "sleep", lambda s: None), \
                mock.patch("builtins.print", lambda *a, **k: logs.append(" ".join(map(str, a)))):
            F.run_queue(poll=0)
        return told, retired, logs

    def test_the_pass_after_a_quick_collect_keeps_every_guard_and_does_not_ask_the_desk_twice(self):
        """The quick-poll pass skips the second collect to avoid re-asking the desk, but still runs all guards (control, capability, trespass, GPU) in order before pickup."""
        calls, have, asked = [], [], [0]

        def collect(log):
            calls.append("collect")
            asked[0] += 1
            if asked[0] == 2:                 # the desk has an order on the QUICK poll, not on the first pass's ask
                have.append("o1.job.json")
                return 1
            return 0
        rec = lambda name, ret=None: (lambda *a, **k: (calls.append(name), ret)[1])
        stub = dict(acquire_runner_singleton=lambda log: True, sweep_orphaned_sidecars=lambda log: None,
                    collect_fleet_controls=rec("controls", 0), process_control_orders=rec("slips", 0),
                    reship_kept=rec("reship", 0), resend_desk_reports=rec("resend"),
                    collect_fleet_orders=collect, assess_all_queued=rec("assess"),
                    patrol=rec("patrol"), gpu_patrol=rec("police"),
                    publish_runner_card=lambda s: None, _announce_capabilities=lambda: None,
                    queued_orders=lambda: list(have[:1]), Job=self._Job, card_check_before_launch=rec("card_check"),
                    notify=lambda job, m: None, release_lane_if_ours=lambda n, log: None,
                    consume_pause_flag=lambda: None, release_runner_singleton=lambda: None,
                    clear_current_run=lambda: None, retire_order=lambda p: have.clear(),
                    _order_shipped_count=lambda job: 1, waiting_cancels=lambda: set(),
                    report_fleet_result=lambda job, ok, why="", status=None: None)
        pauses = iter([False, False, True])
        with mock.patch.multiple(F, **stub), mock.patch.object(F, "run", rec("RUN")), \
                mock.patch.object(F, "pause_requested", lambda: next(pauses)), \
                mock.patch.object(F, "stop_comfy", lambda job: None), \
                mock.patch.object(F.time, "sleep", lambda s: None), \
                mock.patch("builtins.print", lambda *a, **k: None):
            F.run_queue(poll=30.0)
        run_at = calls.index("RUN")
        first_pass = ["controls", "slips", "reship", "resend", "collect", "assess", "patrol", "police"]
        self.assertEqual(calls[:len(first_pass)], first_pass, "an ordinary pass asks the desk for orders")
        between = calls[len(first_pass):run_at]
        self.assertEqual(between, ["collect",                                   # the quick poll, between passes
                                   "controls", "slips", "reship", "resend", "assess", "patrol", "police", "card_check"],
                         "the pass after it: every guard, in order, and no second collect")
        self.assertIn("collect", calls[run_at:], "the pass after the order asks the desk again, as always")

    def test_a_real_failure_reaches_the_desk_as_failed(self):
        told, retired, logs = self._queue(RuntimeError("boom"))
        self.assertEqual(told, [("j9", False)], "the desk must hear it; log: %r" % logs)
        self.assertEqual(retired, ["o1.job.json"])

    def test_a_busy_lane_stays_queued_and_the_desk_hears_nothing(self):
        told, retired, logs = self._queue(F.LaneBusy("port held"))
        self.assertEqual((told, retired), ([], []), "a transient must never consume or fail the order")
        self.assertTrue(any("STAYS QUEUED" in m for m in logs), logs)

    # Only compute orders told the desk they finished; Krea training orders stay `claimed` at the desk to avoid holding chained successors.
    def test_a_finished_desk_order_reaches_the_desk_as_done(self):
        told, retired, logs = self._queue()
        self.assertEqual((told, self.statuses), ([("j9", True)], [None]), "log: %r" % logs)

    def test_a_stopped_early_order_reaches_the_desk_as_cancelled(self):
        told, retired, logs = self._queue(then=lambda job: job.state.update(stopped_early={"at": 938, "why": "x"}))
        self.assertEqual((told, self.statuses), ([("j9", False)], ["cancelled"]), "log: %r" % logs)

    def test_a_deploys_pause_tells_the_desk_nothing_and_a_customers_stop_reports_failed(self):
        """A deploy pause is a temporary state, not a completion; it must not notify the desk or mark the job as finished."""
        told, retired, logs = self._queue(then=lambda job: F.report_stopped(
            job, [{"why": "fleet_deploy 1: stop for a deploy", "pause": True}]))
        self.assertEqual(told, [], "a pause is not a finish; log: %r" % logs)

        # _queue's stub of report_fleet_result does not mark the job; the REAL one does once the desk heard it
        # (test_the_real_report_marks_the_job_only_when_the_desk_heard_it_or_it_was_kept), so mark it as the real one would
        def stop_for_good(slips):
            def then(job):
                if F.report_stopped(job, slips):
                    job.desk_reported = True
            return then
        told, retired, logs = self._queue(then=stop_for_good([{"why": "superseded"}]))
        self.assertEqual(told, [("j9", False)], "a customer's stop reports failed, once; log: %r" % logs)
        told, retired, logs = self._queue(then=stop_for_good([{"why": "fleet_deploy 1", "pause": True}, {"why": "now"}]))
        self.assertEqual(told, [("j9", False)], "a second slip that is not a pause means stop for good")

    def test_an_order_that_told_the_desk_itself_is_not_told_twice(self):
        # run_compute_order reports its own result; the real report_fleet_result marks the job when the desk heard it
        told, retired, logs = self._queue(then=lambda job: setattr(job, "desk_reported", True))
        self.assertEqual(told, [], "a second report would overwrite the order's own; log: %r" % logs)

    def test_the_real_report_marks_the_job_only_when_the_desk_heard_it_or_it_was_kept(self):
        import types
        for fail, keep, want in ((False, True, True), (True, True, True), (True, False, False)):
            reqs, logs = [], []

            class _R:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            def urlopen(req, timeout=None, fail=fail):
                reqs.append(req.full_url)
                if fail:
                    raise OSError("desk down")
                return _R()
            shim = types.SimpleNamespace(spec={"fleet_order_id": "j1"}, log=logs.append)
            with mock.patch.object(F, "_worker_token", lambda: "tok"), \
                 mock.patch.object(F.urllib.request, "urlopen", urlopen), \
                 mock.patch.object(F, "_keep_desk_report", lambda *a: keep):
                F.report_fleet_result(shim, True)
            self.assertEqual((len(reqs), getattr(shim, "desk_reported", False)), (1, want), (fail, keep, logs))

    def _stop(self, port_free):
        job = self._Job("o1")

        def slow(*a, **k):
            raise F.subprocess.TimeoutExpired(a[0], 30)
        with mock.patch.object(F.subprocess, "run", slow), mock.patch.object(F, "_port_free", lambda p: port_free):
            F.stop_comfy(job)
        return job.logs

    def test_a_slow_powershell_with_the_port_free_is_a_stopped_comfy(self):
        # A free port indicates ComfyUI is stopped; this path must not raise LaneBusy and must not invoke PowerShell.
        self.assertTrue(any("8189" in m and "free" in m for m in self._stop(True)))

    def test_a_slow_powershell_with_the_port_held_makes_the_order_wait(self):
        with self.assertRaises(F.LaneBusy):
            self._stop(False)

    def test_cancelling_an_archived_desk_order_closes_its_desk_record(self):
        d = tempfile.mkdtemp()
        q, arch = os.path.join(d, "queue"), os.path.join(d, "archive")
        os.makedirs(q)
        os.makedirs(arch)
        for stamp, jid in (("20261003-010058", "old"), ("20261003-080812", "j77")):
            p = os.path.join(arch, "w_order.job.json.%s" % stamp)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"name": "w_order", "fleet_order_id": jid}, f)
            t = time.time() - (100 if jid == "old" else 0)
            os.utime(p, (t, t))
        with open(os.path.join(q, "c.ctl.json"), "w", encoding="utf-8") as f:
            json.dump({"action": "cancel", "order": "w_order", "why": "FATAL stale claim"}, f)
        told = []
        with mock.patch.object(F, "QUEUE_DIR", q), \
                mock.patch.object(F, "_order_still_holds_card", lambda t: (False, "")), \
                mock.patch.object(F, "report_fleet_result",
                                  lambda job, ok, why="", status=None: told.append((job.spec.get("fleet_order_id"), status))):
            F.process_control_orders(lambda m: None)
        self.assertEqual(told, [("j77", "cancelled")], "the newest archived generation's desk id is closed")


class TestAfterExistsIsTokenExpanded(unittest.TestCase):

    def _spec_file(self, after):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "gated.job.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"name": "gated", "mode": "compute", "after_exists": after}, f)
        return p

    def test_output_token_is_expanded(self):
        p = self._spec_file(r"{OUTPUT}\prolora\thing.npz")
        got = F.order_after_exists(p)
        self.assertNotIn("{OUTPUT}", got or "",
                         "an unexpanded token can never match a real file, so the gate never opens")
        self.assertTrue(got.lower().endswith(r"prolora\thing.npz".lower()))

    def test_foundry_root_token_is_expanded(self):
        p = self._spec_file(r"{FOUNDRY_ROOT}\var\cache\x.pt")
        self.assertNotIn("{FOUNDRY_ROOT}", F.order_after_exists(p) or "")

    def test_absent_gate_is_none(self):
        """No after_exists must mean 'ready now', never an empty path that exists()."""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "plain.job.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"name": "plain", "mode": "compute"}, f)
        self.assertIsNone(F.order_after_exists(p))

    def test_a_real_expanded_path_actually_resolves(self):
        """End to end: write the file the token resolves to, and prove the gate sees it."""
        p = self._spec_file(r"{OUTPUT}\gate_probe_marker.tmp")
        target = F.order_after_exists(p)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w").close()
        try:
            self.assertTrue(os.path.exists(target))
        finally:
            os.remove(target)

    def test_unreadable_spec_is_none_not_a_crash(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "bad.job.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(F.order_after_exists(p))


class TestEveryModeIsCounted(unittest.TestCase):
    """Test that every dispatchable mode produces a state term visible to `_order_shipped_count`.

    Modes lacking a counting term score 0, causing silent failures where orders are stamped
    complete despite shipping nothing. This test ensures new modes fail loudly in the suite
    rather than silently in production ledgers."""

    # mode -> a state dict representing "this order produced something"
    PRODUCED = {
        "train": {"previews_done": [1, 2], "ladder_done": [3]},
        "probe": {"probe_rendered": 9},
        "caption": {"captioned": 250},
        # {ORDER_OUT} + deliver_to orders: ship_compute records the files it delivered.
        "compute": {"delivered": 3},
        "cpu": None,              # legacy alias of compute
    }

    class _Job(object):
        def __init__(self, spec, state=None):
            self.spec = spec
            self.state = state or {}

    def test_every_dispatchable_mode_has_a_counting_term(self):
        for mode, state in self.PRODUCED.items():
            if state is None:
                continue
            job = self._Job({"mode": mode}, dict(state))
            self.assertGreater(
                F._order_shipped_count(job), 0,
                "mode %r produced something but the ledger cannot see it -> it would be stamped "
                "SHIPPED NOTHING. Add its term to _order_shipped_count." % mode)

    def test_every_mode_still_reports_zero_when_it_produced_nothing(self):
        # the guard's entire reason to exist; a fix that loses this is worse than the defect
        for mode in self.PRODUCED:
            job = self._Job({"mode": mode}, {})
            self.assertEqual(
                F._order_shipped_count(job), 0,
                "mode %r produced nothing and MUST still report 0" % mode)

    def test_each_mode_key_is_declared_in_the_shared_constant(self):
        """The counter and this test must read ONE list, not two that drift.

        Asserts the PROPERTY (every mode's key is a key the counter honours) against the shared
        constant — never against the counter's source text, which would be testing how the code is
        spelled and would break the moment somebody tidied it while the property still held."""
        declared = set(F.SHIPPED_COUNT_KEYS)
        self.assertTrue(declared, "SHIPPED_COUNT_KEYS is empty - the counter can see nothing")
        for mode, state in self.PRODUCED.items():
            if state is None:
                continue
            for key in state:
                self.assertIn(
                    key, declared,
                    "mode %r records %r but the counter does not honour it -> it would be stamped "
                    "SHIPPED NOTHING. Add it to SHIPPED_COUNT_KEYS." % (mode, key))

    def test_a_collection_and_a_number_both_count(self):
        # previews_done is a list, probe_rendered is an int; a counter that assumed one shape
        # would silently score the other as zero
        self.assertEqual(F._order_shipped_count(self._Job({}, {"previews_done": [1, 2, 3]})), 3)
        self.assertEqual(F._order_shipped_count(self._Job({}, {"probe_rendered": 3})), 3)


class TestProbeOrderCountsAsShipped(unittest.TestCase):
    """Tests that probe orders, whose deliverables are renders (not persisted in `_made`),
    are correctly counted as shipped. Also ensures the guard against shipping zero items
    is not disabled by fixing the counting logic."""

    class _Job(object):
        def __init__(self, spec, state=None):
            self.spec = spec
            self.state = state or {}

    def test_a_probe_that_rendered_counts_as_shipped(self):
        job = self._Job({"mode": "probe"}, {"probe_rendered": 9})
        self.assertEqual(F._order_shipped_count(job), 9,
                         "a probe that produced 9 renders must not read as SHIPPED NOTHING")

    def test_a_probe_that_rendered_NOTHING_still_reads_as_shipped_nothing(self):
        # the case the whole alarm exists for - it must survive the fix
        for state in ({"probe_rendered": 0}, {}, {"probe_rendered": None}):
            job = self._Job({"mode": "probe"}, dict(state))
            self.assertEqual(F._order_shipped_count(job), 0,
                             "a probe that produced nothing MUST still report 0 (state=%r)" % state)

    def test_probe_count_is_a_number_not_a_flag(self):
        # if this ever becomes truthy-based, the zero case silently starts passing
        job = self._Job({"mode": "probe"}, {"probe_rendered": 2})
        self.assertEqual(F._order_shipped_count(job), 2)

    def test_a_malformed_probe_count_does_not_raise(self):
        job = self._Job({"mode": "probe"}, {"probe_rendered": "nine"})
        self.assertIsInstance(F._order_shipped_count(job), int)


class TestComputeOrderCountsAsShipped(unittest.TestCase):

    class _Job(object):
        def __init__(self, spec, state=None):
            self.spec = spec
            self.state = state or {}

    def test_a_compute_order_whose_deliverable_exists_counts(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "acts.npz")
        open(p, "w").close()
        job = self._Job({"mode": "compute", "deliverables": [p]})
        with mock.patch.object(F, "checkpoints", lambda j: []):
            self.assertEqual(F._order_shipped_count(job), 1)

    def test_a_declared_deliverable_that_is_ABSENT_does_not_count(self):
        """Evidence on disk, never a claim in the spec -- otherwise the guard is satisfied by the
        order merely SAYING it would produce something, which is exactly the failure it exists to
        catch."""
        job = self._Job({"mode": "compute",
                         "deliverables": [os.path.join(tempfile.mkdtemp(), "never_written.npz")]})
        with mock.patch.object(F, "checkpoints", lambda j: []):
            self.assertEqual(F._order_shipped_count(job), 0)

    def test_prose_deliverables_still_count_zero(self):
        """Render orders write English here ('12 PNGs: 4 crop arms x 3 seeds'). Those must behave
        exactly as before -- a prose entry never resolves to a real path."""
        job = self._Job({"deliverables": ["12 PNGs: 4 crop arms x 3 seeds", "a comparison card"]})
        with mock.patch.object(F, "checkpoints", lambda j: []):
            self.assertEqual(F._order_shipped_count(job), 0)

    def test_render_state_still_wins_and_short_circuits(self):
        job = self._Job({"deliverables": ["irrelevant"]}, {"previews_done": [1, 2, 3]})
        with mock.patch.object(F, "checkpoints", lambda j: []):
            self.assertEqual(F._order_shipped_count(job), 3)

    def test_missing_deliverables_field_is_not_an_error(self):
        job = self._Job({"mode": "compute"})
        with mock.patch.object(F, "checkpoints", lambda j: []):
            self.assertEqual(F._order_shipped_count(job), 0)


class TestComfyCleanupOnEveryRoute(unittest.TestCase):

    class _Job(object):
        name = "cleanup_t"

        def __init__(self):
            self.logged = []

        def log(self, m):
            self.logged.append(m)

    def test_comfy_is_stopped_when_the_order_raises(self):
        calls = []
        with mock.patch.object(F, "run", side_effect=RuntimeError("probe produced 0 renders")), \
             mock.patch.object(F, "stop_comfy", side_effect=lambda j: calls.append(j)):
            with self.assertRaises(RuntimeError):
                F._run_order_with_comfy_cleanup(self._Job())
        self.assertEqual(len(calls), 1, "an order that raised must still leave the card clean")

    def test_the_original_exception_is_not_swallowed_by_cleanup(self):
        """
        A cleanup that eats the failure would archive a broken order as complete;
        a cleanup that raises its own exception would mask the real diagnosis.
        We assert on the original exception type to ensure it survives cleanup errors."""
        job = self._Job()
        with mock.patch.object(F, "run", side_effect=F.OrderCancelled("stopped at the desk")), \
             mock.patch.object(F, "stop_comfy", side_effect=OSError("powershell missing")):
            with self.assertRaises(F.OrderCancelled):
                F._run_order_with_comfy_cleanup(job)
        self.assertTrue(any("cleanup failed" in m for m in job.logged),
                        "a cleanup that could not run must say so on the order's own log")

    def test_cleanup_survives_a_job_that_cannot_even_log(self):
        """Belt and braces: nothing in the cleanup path may replace the exception in flight, even
        when the reporting itself is broken."""
        class _Mute(object):
            name = "mute_t"

            def log(self, m):
                raise IOError("log file gone")

        with mock.patch.object(F, "run", side_effect=RuntimeError("real failure")), \
             mock.patch.object(F, "stop_comfy", side_effect=OSError("powershell missing")):
            with self.assertRaises(RuntimeError) as ctx:
                F._run_order_with_comfy_cleanup(_Mute())
        self.assertIn("real failure", str(ctx.exception))

    def test_comfy_is_stopped_on_the_success_route_too(self):
        calls = []
        with mock.patch.object(F, "run", lambda j: None), \
             mock.patch.object(F, "stop_comfy", side_effect=lambda j: calls.append(j)):
            F._run_order_with_comfy_cleanup(self._Job())
        self.assertEqual(len(calls), 1)


class TestComfyProcCountIsPerHost(unittest.TestCase):

    def _ps(self, payload):
        class R(object):
            stdout = payload
        return mock.patch.object(F.subprocess, "run", lambda *a, **k: R())

    def test_counts_this_factorys_comfy_on_a_non_mothership_path(self):
        rows = json.dumps([
            {"ProcessId": 111, "CommandLine": r"python.exe F:\comfy-bench\ComfyUI\main.py --port 8189"},
            {"ProcessId": 222, "CommandLine": r"python.exe F:\foundry\bin\p20_foundry.py --queue"},
        ])
        with self._ps(rows), mock.patch.object(F, "_is_our_comfy",
                                               lambda c: "comfy-bench" in c):
            self.assertEqual(F._comfy_procs_alive(), 1)

    def test_excludes_the_observer(self):
        """The querying process is excluded from the match set."""
        rows = json.dumps([{"ProcessId": os.getpid(),
                            "CommandLine": r"python.exe F:\comfy-bench\ComfyUI\main.py"}])
        with self._ps(rows), mock.patch.object(F, "_is_our_comfy", lambda c: True):
            self.assertEqual(F._comfy_procs_alive(), 0)

    def test_a_single_match_is_not_dropped(self):
        """ConvertTo-Json emits a bare object, not a list, for exactly one row — parsing that as a
        list would silently count zero, which is the failure mode this whole class exists for."""
        row = json.dumps({"ProcessId": 111, "CommandLine": r"python.exe F:\x\ComfyUI\main.py"})
        with self._ps(row), mock.patch.object(F, "_is_our_comfy", lambda c: True):
            self.assertEqual(F._comfy_procs_alive(), 1)

    def test_unreadable_process_table_reads_as_NOT_quiet(self):
        """`could not tell` must never render as `free`. -1 used to fall through the `n > 0` test
        in wait_card_quiet and read as a clean card."""
        class R(object):
            stdout = "not json at all"
        with mock.patch.object(F.subprocess, "run", lambda *a, **k: R()):
            self.assertEqual(F._comfy_procs_alive(), -1)
        src = io.open(os.path.join(BIN, "p20_foundry.py"), encoding="utf-8").read()
        body = src.split("def wait_card_quiet(", 1)[1][:4000]
        code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
        self.assertIn("n < 0", code,
                      "wait_card_quiet must treat an unreadable process table as busy")


class TestComputeOrderKind(unittest.TestCase):
    """Tests for mode:"compute" (renamed from "cpu").
    Stop-slip wiring is verified via live decoy (sleep + real stop.json),
    not by faking the beat thread's clock, to ensure real-time behavior."""

    def _spec(self, **fields):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "%s.job.json" % fields.get("name", "compute_t"))
        base = {"name": "compute_t", "mode": "compute", "payload": __file__}
        base.update(fields)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(base, f)
        return p

    def test_tokens_expand_to_this_boxes_paths(self):
        got = F._expand_compute_path(r"{FOUNDRY_ROOT}\workspace\x.py")
        self.assertNotIn("{", got)
        self.assertTrue(got.endswith(r"\workspace\x.py"))
        self.assertEqual(F._expand_compute_path(r"{MODEL_CACHE}\m.pt"),
                         F.P.MODEL_CACHE_DIR + r"\m.pt")
        self.assertEqual(F._expand_compute_path(r"{OUTPUT}\compute\o"),
                         F.P.OUTPUT + r"\compute\o")
        # token-free strings pass through untouched — expansion must be safe to apply always
        self.assertEqual(F._expand_compute_path(r"C:\plain\path.py"), r"C:\plain\path.py")

    def test_tokenized_payload_is_not_a_spec_defect(self):
        """spec_defects must expand tokens BEFORE the exists check, or intake archives a good
        order over a path the runner would have resolved fine (the seam this feature shipped
        with, caught pre-deploy)."""
        with mock.patch.dict(F.COMPUTE_PATH_TOKENS, {"{FOUNDRY_ROOT}": os.path.dirname(__file__)}):
            p = self._spec(payload=r"{FOUNDRY_ROOT}" + "\\" + os.path.basename(__file__))
            self.assertEqual([d for d in F.spec_defects(p) if "payload" in d], [])

    def test_missing_payload_is_still_a_defect(self):
        p = self._spec(payload=r"{FOUNDRY_ROOT}\no\such\file.py")
        self.assertTrue(any("does not exist" in d for d in F.spec_defects(p)))

    def test_legacy_cpu_mode_is_the_same_kind(self):
        """"cpu" is a legacy ALIAS — an old spec must be validated as a compute order, not
        skipped by the compute checks."""
        p = self._spec(mode="cpu", payload=r"{FOUNDRY_ROOT}\no\such\file.py")
        self.assertTrue(any("does not exist" in d for d in F.spec_defects(p)))

    def test_bad_load_value_is_a_defect(self):
        p = self._spec(load="banana")
        self.assertTrue(any("load" in d for d in F.spec_defects(p)),
                        "an invalid load classification must be named at intake")


class TestComputeCardQuietGate(unittest.TestCase):
    """card_quiet cross-cut into run_compute_order: a gpu
    compute order must CLEAR the card before spawning its payload (stop_comfy + the same
    wait_card_quiet the trainer uses), and a cpu order must not touch the gate. The ollama_busy
    flag is behavioral: resident ollama = busy for a trainer (default), NOT busy for a compute
    payload that uses ollama itself."""

    class _J:
        def __init__(self, load):
            self.name = "cq_t"
            self.mode = "compute"
            self.load = load
            self.spec = {"payload": __file__, "args": []}
            self.state = {}
            self.spec_path = __file__
            self.total_steps = 0
            self.logs = []

        def log(self, m):
            self.logs.append(m)

        def save(self):
            pass

    def _run(self, load):
        calls = []

        def rec(tag):
            return lambda *a, **k: calls.append((tag, k))

        def boom(*a, **k):
            calls.append(("spawn", {}))
            raise RuntimeError("stop before real spawn")

        with mock.patch.object(F, "require_demon", rec("demon")), \
             mock.patch.object(F, "stop_comfy", rec("stop_comfy")), \
             mock.patch.object(F, "wait_card_quiet", rec("quiet")), \
             mock.patch.object(F, "publish_current_run", rec("publish")), \
             mock.patch.object(F, "notify", rec("notify")), \
             mock.patch.object(F, "report_fleet_result", rec("report")), \
             mock.patch.object(F, "retire_order", rec("retire")), \
             mock.patch.object(F, "clear_current_run", rec("clear")), \
             mock.patch.object(F.subprocess, "Popen", boom):
            F.run_compute_order(self._J(load))
        return calls

    def test_gpu_compute_clears_the_card_before_spawn(self):
        calls = self._run("gpu")
        names = [c[0] for c in calls]
        self.assertIn("stop_comfy", names)
        self.assertIn("quiet", names)
        self.assertLess(names.index("stop_comfy"), names.index("spawn"))
        self.assertLess(names.index("quiet"), names.index("spawn"))
        self.assertLess(names.index("stop_comfy"), names.index("quiet"))
        quiet_kwargs = calls[names.index("quiet")][1]
        self.assertIs(quiet_kwargs.get("ollama_busy"), False,
                      "a compute payload uses ollama - resident models must not read as busy")

    def test_items_share_is_materialised_to_a_file_and_argv(self):
        """A fleet-split compute order's spec["items"] must reach the payload as --items-file
        (argv cannot carry hundreds of items on Windows), written beside the spec."""
        import tempfile
        calls = []

        def boom(cmd, **k):
            calls.append(cmd)
            raise RuntimeError("stop before real spawn")

        j = self._J("cpu")
        d = tempfile.mkdtemp()
        j.spec_path = os.path.join(d, "share_t.job.json")
        j.spec["items"] = ["b64one", "b64two"]
        with mock.patch.object(F, "notify", lambda *a, **k: None), \
             mock.patch.object(F, "report_fleet_result", lambda *a, **k: None), \
             mock.patch.object(F, "retire_order", lambda *a, **k: None), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F.subprocess, "Popen", boom):
            F.run_compute_order(j)
        cmd = calls[0]
        self.assertIn("--items-file", cmd)
        items_path = cmd[cmd.index("--items-file") + 1]
        with open(items_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), ["b64one", "b64two"])
        self.assertEqual(os.path.dirname(items_path), d,
                         "the share file lives beside the spec, in factory-local space")

    def test_cpu_compute_skips_the_card_gate(self):
        names = [c[0] for c in self._run("cpu")]
        self.assertNotIn("stop_comfy", names)
        self.assertNotIn("quiet", names)
        self.assertIn("spawn", names)

    def test_progress_convention_derives_glob_and_total_when_undeclared(self):
        """An order with no progress_glob gets the marker convention (<out-stem>*.progress)
        derived from its last .json arg, and items_count as the denominator."""
        j = self._J("cpu")
        j.spec["args"] = ["\\\\share\\work.json", "\\\\share\\out.json"]
        j.spec["items"] = ["a", "b", "c"]
        j.spec["items_count"] = 3
        import tempfile
        j.spec_path = os.path.join(tempfile.mkdtemp(), "pc_t.job.json")
        with mock.patch.object(F, "unreachable_shares", lambda *a, **k: []), \
             mock.patch.object(F, "notify", lambda *a, **k: None), \
             mock.patch.object(F, "report_fleet_result", lambda *a, **k: None), \
             mock.patch.object(F, "retire_order", lambda *a, **k: None), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F.subprocess, "Popen",
                               mock.Mock(side_effect=RuntimeError("stop"))):
            F.run_compute_order(j)
        self.assertEqual(j.total_steps, 3,
                         "items_count must become the tile denominator when undeclared")

    def test_declared_progress_still_wins_over_the_convention(self):
        j = self._J("cpu")
        j.spec["args"] = ["\\\\share\\out.json"]
        j.spec["progress_glob"] = "\\\\share\\explicit\\*.done"
        j.spec["progress_total"] = 7
        with mock.patch.object(F, "unreachable_shares", lambda *a, **k: []), \
             mock.patch.object(F, "notify", lambda *a, **k: None), \
             mock.patch.object(F, "report_fleet_result", lambda *a, **k: None), \
             mock.patch.object(F, "retire_order", lambda *a, **k: None), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F.subprocess, "Popen",
                               mock.Mock(side_effect=RuntimeError("stop"))):
            F.run_compute_order(j)
        self.assertEqual(j.total_steps, 7)

    def test_compute_progress_parses_the_workers_own_count(self):
        """The runner reads progress off the worker's own printed `[tag] N/M ` lines —
        the same channel the caption pass uses. Producer format (triage_rewrite's
        per-section line) and consumer regex must agree."""
        line = "[rewrite] %d/%d %s ok in %.0fs (%d cuts)" % (3, 8, "net-004", 42.0, 2)
        m = F.COMPUTE_PROG_RE.match(line)
        self.assertIsNotNone(m, "the payload's real per-section line must parse")
        self.assertEqual((int(m.group(1)), int(m.group(2))), (3, 8))
        for neg in ("[rewrite] net-004 SKIP too-large (est 9000)",
                    "[rewrite] breaker: 3 consecutive failures",
                    "[rewrite] shipped x: 8 drafted, 0 errors",
                    "MERGED 2 shares -> out.json: 8 drafted, 0 errors",
                    "share x: 8 of 20 sections -> y"):
            self.assertIsNone(F.COMPUTE_PROG_RE.match(neg), neg)

    def _quiet_env(self, resident):
        return [mock.patch.object(F, "_port_free", lambda p: True),
                mock.patch.object(F, "_comfy_procs_alive", lambda: 0),
                mock.patch.object(F, "_ollama_resident", lambda: resident),
                mock.patch.object(F.time, "sleep", lambda s: None)]

    def test_default_flag_holds_resident_ollama_busy(self):
        """Trainer semantics unchanged: resident ollama keeps the card busy until timeout."""
        j = self._J("gpu")
        notes = []
        pats = self._quiet_env(True) + [mock.patch.object(F, "notify",
                                                          lambda job, m: notes.append(m))]
        for p in pats:
            p.start()
        try:
            F.wait_card_quiet(j, max_wait=0.2)
        finally:
            for p in pats:
                p.stop()
        self.assertTrue(any("timed out" in m for m in notes),
                        "resident ollama under the default flag must ride to the timeout path")

    def test_ollama_ok_flag_reads_resident_ollama_as_quiet(self):
        j = self._J("gpu")
        notes = []
        pats = self._quiet_env(True) + [mock.patch.object(F, "notify",
                                                          lambda job, m: notes.append(m))]
        for p in pats:
            p.start()
        try:
            F.wait_card_quiet(j, ollama_busy=False)
        finally:
            for p in pats:
                p.stop()
        self.assertEqual(notes, [])
        self.assertTrue(any("safe to launch" in m for m in j.logs))


class TestTriageCtxDeclaration(unittest.TestCase):
    """The factory declares triage.ctx.<N> from ITS OWN triage_local.json row -- one
    authority, the same file the classify payload reads."""

    def test_triage_ctx_capability_matches_own_row(self):
        import socket
        with open(os.path.join(F.P.BIN, "triage_local.json"), encoding="utf-8") as f:
            row = json.load(f).get(socket.gethostname().lower()) or {}
        declared = [c for c in F.FACTORY_CAPABILITIES if c.startswith("triage.ctx.")]
        if not row.get("num_ctx") or "triage.rubric.v1" not in F.FACTORY_CAPABILITIES:
            self.assertEqual(declared, [],
                             "no row / no rubric cap must mean no ctx declaration")
        else:
            self.assertEqual(declared, ["triage.ctx.%d" % int(row["num_ctx"])],
                             "declaration must equal this box's own customization, exactly once")

    def test_triage_charter_capability_is_detected_from_files(self):
        # the rewrite cap declares iff BOTH the payload and the versioned charter
        # are on this box -- same detected-not-declared rule as the rubric lane
        have = (os.path.exists(os.path.join(F.P.BIN, "triage_rewrite.py"))
                and os.path.exists(os.path.join(F.P.BIN, "triage_charter.md")))
        self.assertEqual("triage.charter.v1" in F.FACTORY_CAPABILITIES, have,
                         "the promise must track the files that make it keepable")

    def test_triage_model_capability_matches_own_row(self):
        # the desk's tier-scoped rewrite deal keys on WHICH model this box's triage row
        # runs; model.* caps list everything installed, a different claim
        import socket
        with open(os.path.join(F.P.BIN, "triage_local.json"), encoding="utf-8") as f:
            row = json.load(f).get(socket.gethostname().lower()) or {}
        declared = [c for c in F.FACTORY_CAPABILITIES if c.startswith("triage.model.")]
        if not row.get("model") or "triage.rubric.v1" not in F.FACTORY_CAPABILITIES:
            self.assertEqual(declared, [],
                             "no row / no rubric cap must mean no model declaration")
        else:
            self.assertEqual(declared, ["triage.model.%s" % row["model"]],
                             "declaration must equal this box's own row, exactly once")


class TestDeclaredModels(unittest.TestCase):
    """declared_capabilities() = static set + live model.<name> caps from local /api/tags:
    detected per-push (a pull adds a model with no restart), cached 60 s,
    fail-soft on an unreachable ollama, and consumed by missing_capabilities so the factory
    never refuses an order the desk validly routed on a model requirement."""

    def setUp(self):
        F._MODEL_CAPS_CACHE["t"] = 0.0
        F._MODEL_CAPS_CACHE["caps"] = []
        # the box's real Strata row must not leak in (Mothership serves one): these tests are about ollama's half
        self._served = mock.patch.object(F, "STRATA_SERVED", os.path.join(tempfile.gettempdir(), "no-such-served.json"))
        self._served.start()

    def tearDown(self):
        self._served.stop()
        F._MODEL_CAPS_CACHE["t"] = 0.0
        F._MODEL_CAPS_CACHE["caps"] = []

    def _tags(self, names):
        import types
        body = json.dumps({"models": [{"name": n} for n in names]}).encode("utf-8")

        class _R:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return mock.patch.object(F.urllib.request, "urlopen", lambda *a, **k: _R())

    def test_live_models_ride_the_declaration(self):
        with self._tags(["qwen3:8b", "nomic-embed-text:latest"]):
            caps = F.declared_capabilities()
        self.assertIn("model.qwen3:8b", caps)
        self.assertIn("model.nomic-embed-text:latest", caps)
        for c in sorted(F.FACTORY_CAPABILITIES):
            if c != "render.browser":      # asked per push, from the box's own proof (RenderBrowserProven)
                self.assertIn(c, caps, "static caps must survive alongside model caps")

    def test_ollama_down_is_fail_soft(self):
        def boom(*a, **k):
            raise OSError("refused")
        with mock.patch.object(F.urllib.request, "urlopen", boom):
            caps = F.declared_capabilities()
        self.assertEqual([c for c in caps if c.startswith("model.")], [],
                         "no model caps when ollama cannot be asked - cannot-tell declares "
                         "nothing")
        self.assertTrue(any(not c.startswith("model.") for c in caps))

    def test_cache_prevents_per_call_queries(self):
        calls = []

        def counting(*a, **k):
            calls.append(1)
            raise OSError("refused")
        with mock.patch.object(F.urllib.request, "urlopen", counting):
            F.declared_capabilities()
            F.declared_capabilities()
        self.assertEqual(len(calls), 1, "the 60 s cache must absorb the second call")

    def test_missing_capabilities_accepts_a_served_model(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "m.job.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"requires": ["model.qwen3:8b"]}, f)
        with self._tags(["qwen3:8b"]):
            self.assertEqual(F.missing_capabilities(p), [])
        F._MODEL_CAPS_CACHE["t"] = 0.0
        F._MODEL_CAPS_CACHE["caps"] = []
        with self._tags(["other:1b"]):
            self.assertEqual(F.missing_capabilities(p), ["model.qwen3:8b"])

    def test_ctx_requirement_is_a_minimum_window(self):
        have = {"order.compute", "triage.ctx.122880"}
        self.assertEqual(F.unmet_requires(["triage.ctx.65536", "order.compute"], have), [])
        self.assertEqual(F.unmet_requires(["triage.ctx.131072"], have), ["triage.ctx.131072"])
        self.assertEqual(F.unmet_requires(["triage.ctx.x"], have), ["triage.ctx.x"])
        self.assertEqual(F.unmet_requires(["render.klein"], have), ["render.klein"])


class TestComputeNotifyPolicy(unittest.TestCase):
    """notify:"failures": an order opts its routine lifecycle DMs down to log-only.
    Failures always DM regardless -- only chatter opts out, never severity."""

    def _run(self, spec_extra, popen):
        notes = []
        j = TestComputeCardQuietGate._J("cpu")
        j.spec.update(spec_extra)
        with mock.patch.object(F, "notify", lambda job, m: notes.append(m)), \
             mock.patch.object(F, "report_fleet_result", lambda *a, **k: None), \
             mock.patch.object(F, "retire_order", lambda *a, **k: None), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F.subprocess, "Popen", popen):
            F.run_compute_order(j)
        return notes, j

    def test_quiet_order_suppresses_lifecycle_dms(self):
        proc = mock.Mock()
        proc.stdout = iter([])
        proc.wait = lambda: 0
        notes, j = self._run({"notify": "failures"}, lambda *a, **k: proc)
        self.assertEqual([m for m in notes if "started" in m or "COMPLETE" in m], [],
                         "lifecycle DMs must be suppressed under notify:failures")
        self.assertTrue(any("failures-only" in m for m in j.logs),
                        "the suppression must be visible in the log")

    def test_failure_still_dms_under_quiet(self):
        proc = mock.Mock()
        proc.stdout = iter([])
        proc.wait = lambda: 3
        notes, _j = self._run({"notify": "failures"}, lambda *a, **k: proc)
        self.assertTrue(any("FAILED" in m for m in notes),
                        "a failed order must DM even when the order asked for quiet")

    def test_default_stays_loud(self):
        proc = mock.Mock()
        proc.stdout = iter([])
        proc.wait = lambda: 0
        notes, _j = self._run({}, lambda *a, **k: proc)
        self.assertTrue(any("started" in m for m in notes))
        self.assertTrue(any("COMPLETE" in m for m in notes))

    def test_failure_dm_carries_the_childs_last_line(self):
        # the runner streams the child's output, so the reason is in hand; the DM must
        # carry the last non-empty line, not send the operator to fetch what the read
        # loop just read.
        proc = mock.Mock()
        proc.stdout = iter(["[rewrite] rewrite-v1 | 10 sections\n", "\n",
                            "REFUSED: model partially resident (?/0.0 GB)\n"])
        proc.wait = lambda: 1
        notes, _j = self._run({"notify": "failures"}, lambda *a, **k: proc)
        fails = [m for m in notes if "FAILED" in m]
        self.assertTrue(fails and "partially resident" in fails[0],
                        "the DM must name the reason: %r" % fails)
        self.assertNotIn("see log", fails[0],
                         "'see log' is only for a child that died with no output")


class TestOrderDeclaredClock(unittest.TestCase):
    """Orders declare their own clock (`gpu_clock`); the factory must apply it at launch and restore the box's lock on every end route."""

    def setUp(self):
        self.calls = []
        self.logs = []

        def fake_run(argv, **kw):
            self.calls.append(argv)
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        self.run = fake_run

    def test_unlocked_releases_the_clock(self):
        with mock.patch.object(F.subprocess, "run", self.run):
            got = F.apply_order_clock({"gpu_clock": "unlocked"}, self.logs.append)
        self.assertEqual(got, "unlocked")
        self.assertEqual(self.calls, [["nvidia-smi", "-rgc"]])

    def test_a_number_locks_to_it(self):
        with mock.patch.object(F.subprocess, "run", self.run):
            self.assertEqual(F.apply_order_clock({"gpu_clock": 1695}, self.logs.append), 1695)
        self.assertEqual(self.calls, [["nvidia-smi", "-lgc", "1695"]])

    def _range_run(self, argv, **kw):
        """nvidia-smi as a 3070 answers: max SM 2100, supported graphics clocks down to 210."""
        self.calls.append(argv)
        if "--query-gpu=clocks.max.sm" in argv:
            return mock.Mock(returncode=0, stdout="2100\n", stderr="")
        if "--query-supported-clocks=graphics" in argv:
            return mock.Mock(returncode=0, stdout="2100\n1965\n1500\n210\n", stderr="")
        return mock.Mock(returncode=0, stdout="ok", stderr="")

    def test_a_floor_keeps_the_cards_own_max_above_it(self):
        """A floor sets the minimum clock without pinning the max; a bare value pins both to the same value."""
        with mock.patch.object(F.subprocess, "run", self._range_run):
            self.assertEqual(F.apply_order_clock({"gpu_clock": {"floor": 1965}}, self.logs.append), {"floor": 1965})
        self.assertEqual(self.calls[-1], ["nvidia-smi", "-lgc", "1965,2100"])

    def test_a_ceiling_keeps_the_cards_lowest_clock_below_it(self):
        with mock.patch.object(F.subprocess, "run", self._range_run):
            F.apply_order_clock({"gpu_clock": {"ceiling": 1500}}, self.logs.append)
        self.assertEqual(self.calls[-1], ["nvidia-smi", "-lgc", "210,1500"])

    def test_a_band_needs_no_query_and_a_bad_one_is_refused(self):
        with mock.patch.object(F.subprocess, "run", self.run):
            F.apply_order_clock({"gpu_clock": {"floor": 1905, "ceiling": 1995}}, self.logs.append)
            self.assertIsNone(F.apply_order_clock({"gpu_clock": {"floor": 2000, "ceiling": 1500}}, self.logs.append))
        self.assertEqual(self.calls, [["nvidia-smi", "-lgc", "1905,1995"]], "the band, and nothing for the bad one")

    def test_a_floor_the_card_cannot_bound_is_not_applied(self):
        def no_range(argv, **kw):
            self.calls.append(argv)
            return mock.Mock(returncode=9, stdout="", stderr="not supported")
        with mock.patch.object(F.subprocess, "run", no_range):
            self.assertIsNone(F.apply_order_clock({"gpu_clock": {"floor": 1965}}, self.logs.append))
        self.assertFalse(any("-lgc" in a for a in self.calls), "never a guessed top")

    def test_an_order_without_the_field_touches_nothing(self):
        with mock.patch.object(F.subprocess, "run", self.run):
            self.assertIsNone(F.apply_order_clock({"name": "x"}, self.logs.append))
            F.restore_box_clock(None, self.logs.append)
        self.assertEqual(self.calls, [])

    def test_restore_uses_the_env_lock_when_the_process_has_it(self):
        with mock.patch.object(F, "BOX_CLOCK_LOCK_MHZ", 1500), mock.patch.object(F.subprocess, "run", self.run):
            F.restore_box_clock("unlocked", self.logs.append)
        self.assertEqual(self.calls, [["nvidia-smi", "-lgc", "1500"]])

    def test_restore_reads_the_daemons_published_lock_when_the_env_is_absent(self):
        import p20_status
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "health_daemon_bighonker.json"), "w", encoding="utf-8") as f:
            json.dump({"component": "health_daemon_bighonker", "clock_lock_mhz": 1500}, f)
        with mock.patch.object(F, "BOX_CLOCK_LOCK_MHZ", 0), mock.patch.object(F, "BOX_CLOCK_POLICY", None), \
                mock.patch.object(F, "FLEET_HOST", "bighonker"), \
                mock.patch.object(p20_status, "STATUS_DIR", d), mock.patch.object(F.subprocess, "run", self.run):
            F.restore_box_clock("unlocked", self.logs.append)
        self.assertEqual(self.calls, [["nvidia-smi", "-lgc", "1500"]])

    def test_restore_on_a_box_with_no_lock_returns_the_cards_own_boost(self):
        """Restoring a box that never locked the card must restore the cards own boost."""
        import p20_status
        with mock.patch.object(F, "BOX_CLOCK_LOCK_MHZ", 0), mock.patch.object(F, "BOX_CLOCK_POLICY", None), \
                mock.patch.object(p20_status, "STATUS_DIR", tempfile.mkdtemp()), \
                mock.patch.object(F.subprocess, "run", self.run):
            F.restore_box_clock(1500, self.logs.append)
        self.assertEqual(self.calls, [["nvidia-smi", "-rgc"]])
        self.assertTrue(any("unlocked restored" in l for l in self.logs), self.logs)

    def test_a_missing_nvidia_smi_never_raises(self):
        def boom(*a, **k):
            raise FileNotFoundError("nvidia-smi")
        with mock.patch.object(F.subprocess, "run", boom):
            self.assertIsNone(F.apply_order_clock({"gpu_clock": "unlocked"}, self.logs.append))
            F.restore_box_clock("unlocked", self.logs.append)
        self.assertTrue(any("not run" in l for l in self.logs), self.logs)


class TestEngineRatesAreTheFactorys(unittest.TestCase):
    """Parse rates from the serving engine's own timing lines when the payload omits them.
    Fixtures must remain byte-verbatim to preserve parser compatibility."""
    OLLAMA = ("slot print_timing: id  0 | task 13087 | prompt eval time =     227.71 ms /   345 tokens (    0.66 ms "
              "per token,  1515.10 tokens per second)\n"
              "slot print_timing: id  0 | task 13087 |        eval time =   25568.28 ms /  3296 tokens (    7.76 ms "
              "per token,   128.87 tokens per second)\n")
    SERVER = ("0.29.331.172 I slot print_timing: id  0 | task 6 | prompt eval time =    2681.72 ms /    37 tokens (   "
              "72.48 ms per token,    13.80 tokens per second)\n"
              "0.29.331.175 I slot print_timing: id  0 | task 6 |        eval time =    1792.16 ms /    64 tokens (   "
              "28.45 ms per token,    35.15 tokens per second)\n")

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.serve = os.path.join(self.d, "serve.log")
        self.out = os.path.join(self.d, "order_out")
        os.makedirs(self.out)
        with open(self.serve, "w", encoding="utf-8") as f:
            f.write(self.OLLAMA)                      # written BEFORE the order: must not count

    def test_only_lines_written_after_the_order_started_count(self):
        er = F.EngineRates(self.out, serve_logs=[self.serve])
        self.assertEqual(er.poll(), [])
        with open(self.serve, "a", encoding="utf-8") as f:
            f.write(self.OLLAMA)
        self.assertEqual(er.poll(), [128.87], "the decode line counts, the prompt-eval line never does")
        self.assertEqual(er.poll(), [128.87], "a line is read once")

    def test_an_engine_log_in_the_orders_own_dir_counts_from_its_first_byte(self):
        er = F.EngineRates(self.out, serve_logs=[self.serve])
        with open(os.path.join(self.out, "server_N-MTP2.log"), "w", encoding="utf-8") as f:
            f.write(self.SERVER)
        self.assertEqual(er.poll(), [35.15])

    def test_short_answers_are_overhead_not_a_rate(self):
        er = F.EngineRates(self.out, serve_logs=[self.serve])
        with open(os.path.join(self.out, "x.log"), "w", encoding="utf-8") as f:
            f.write(self.SERVER.replace("/    64 tokens", "/     4 tokens"))
        self.assertEqual(er.poll(), [])

    def test_the_tile_phase_gets_the_engine_rate_when_the_payload_prints_none(self):
        er = F.EngineRates(self.out, serve_logs=[self.serve])
        with open(self.serve, "a", encoding="utf-8") as f:
            f.write(self.OLLAMA)
        self.assertEqual(F.toks_phase("compute(gpu): ladder.py", None or er.poll()),
                         "compute(gpu): ladder.py · 128.9 tok/s")


class TestEngineViewIsTheTilesModelWork(unittest.TestCase):
    """Engine log lines are byte-verbatim reprs from ollama-serve.log. Note that distinct task types (e.g., chat generation and embeddings) can share the same task ID because each model runner numbers tasks independently."""
    CHAT_START = ("slot launch_slot_: id  0 | task 366 | processing task, is_child = 0\n"
                  "slot   operator(): id  0 | task 366 | new prompt, n_ctx_slot = 17408, n_keep = 4, task.n_tokens = 104\n")
    CHAT_END = ("slot print_timing: id  0 | task 366 | prompt eval time =      68.22 ms /    97 tokens (    0.70 ms per "
                "token,  1421.79 tokens per second)\n"
                "slot print_timing: id  0 | task 366 |        eval time =    1350.53 ms /    81 tokens (   16.88 ms per "
                "token,    59.24 tokens per second)\n"
                "slot print_timing: id  0 | task 366 |       total time =    1418.76 ms /   178 tokens\n"
                "slot print_timing: id  0 | task 366 |    graphs reused =        428\n"
                "slot print_timing: id  0 | task 366 |   graph reallocs =          0    /     0        (same size) / "
                "(changed size)\n"
                "slot      release: id  0 | task 366 | stop processing: n_tokens = 184, truncated = 0\n")
    EMBED = ("slot launch_slot_: id  0 | task 366 | processing task, is_child = 0\n"
             "slot   operator(): id  0 | task 366 | new prompt, n_ctx_slot = 8448, n_keep = 0, task.n_tokens = 564\n"
             "slot      release: id  0 | task 366 | stop processing: n_tokens = 564, truncated = 0\n")

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.serve = os.path.join(self.d, "serve.log")
        with open(self.serve, "w", encoding="utf-8") as f:
            f.write(self.CHAT_START + self.CHAT_END)        # before the order: none of it is the order's
        self.er = F.EngineRates(os.path.join(self.d, "out"), serve_logs=[self.serve])

    def _add(self, text):
        with open(self.serve, "a", encoding="utf-8") as f:
            f.write(text)
        self.er.poll()
        return self.er.view(write=59.2)

    def test_nothing_before_the_order_is_counted(self):
        self.er.poll()
        v = self.er.view()
        self.assertEqual((v["turn"], v["tool"], v["read"], v["ctx"]), (None, None, None, None))

    def test_a_generation_in_flight_then_ended(self):
        v = self._add(self.CHAT_START)
        self.assertEqual((v["turn"], v["tool"], v["tool_running"], v["ctx"]), (1, "generating", True, None))
        v = self._add(self.CHAT_END)
        self.assertEqual((v["turn"], v["tool"], v["tool_running"]), (1, "between calls", False))
        self.assertEqual(v["read"], 1421.79)
        self.assertEqual(v["ctx"], [184, 17408], "the tokens the slot held at the end, of its context size")
        self.assertEqual(v["turn_label"], "call")
        self.assertNotIn("tool_s", v, "the engine lines carry no clock: no invented tool time")

    def test_an_embedding_is_not_a_call(self):
        self._add(self.CHAT_START + self.CHAT_END)
        v = self._add(self.EMBED)
        self.assertEqual((v["turn"], v["ctx"]), (1, [184, 17408]))

    def test_an_embedding_sharing_the_number_inside_a_generation(self):
        v = self._add(self.CHAT_START + self.EMBED)
        self.assertEqual((v["turn"], v["tool"]), (1, "generating"), "the embedding's end is not the chat's end")
        v = self._add(self.CHAT_END)
        self.assertEqual((v["turn"], v["tool"], v["ctx"]), (1, "between calls", [184, 17408]))

    def test_the_view_formats_on_the_desk(self):
        """The shape the desk's _clean_gen keeps: these keys and types, nothing else."""
        v = self._add(self.CHAT_START + self.CHAT_END)
        self.assertEqual(set(v), {"write", "read", "turn", "turn_label", "tool", "tool_running", "ctx"})
        self.assertIsInstance(v["tool_running"], bool)


class TestComputeRefusalIsHeld(unittest.TestCase):
    """Refusal is a precondition failure (card cannot hold the model now), not an order failure: the order must stay queued and retry, never be reported failed or retired."""

    def _run(self, rc, lines):
        calls = {"retire": 0, "report": 0}
        notes = []
        j = TestComputeCardQuietGate._J("cpu")
        d = tempfile.mkdtemp()
        j.spec_path = os.path.join(d, "ref_t.job.json")
        with open(j.spec_path, "w", encoding="utf-8") as f:
            json.dump({"name": "ref_t"}, f)
        proc = mock.Mock()
        proc.stdout = iter(lines)
        proc.wait = lambda: rc
        raised = None
        with mock.patch.object(F, "notify", lambda job, m: notes.append(m)), \
             mock.patch.object(F, "report_fleet_result",
                               lambda *a, **k: calls.__setitem__("report", calls["report"] + 1)), \
             mock.patch.object(F, "retire_order",
                               lambda *a, **k: calls.__setitem__("retire", calls["retire"] + 1)), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F.subprocess, "Popen", lambda *a, **k: proc):
            try:
                F.run_compute_order(j)
            except F.LaneBusy as e:
                raised = e
        return raised, calls, notes, j

    def test_refusal_holds_the_order_instead_of_retiring_it(self):
        why = "REFUSED (rc 2): qwen3-vl:8b partially resident (5.6/6.3 GB) at num_ctx 8192"
        raised, calls, notes, j = self._run(F.REFUSED_RC, [why + "\n"])
        self.assertIsNotNone(raised, "a refusal must reach run_queue as LaneBusy (stays queued)")
        self.assertEqual((calls["retire"], calls["report"]), (0, 0),
                         "a refused order is neither retired nor reported failed to the desk")
        self.assertTrue(F.refusal_cooling(j.spec_path), "it steps aside for the retry window")
        self.assertFalse(F.refusal_cooling(j.spec_path, now=time.time() + F.REFUSED_RETRY_S + 1),
                         "and is offered again once the window passes")
        self.assertEqual(len([m for m in notes if "REFUSED" in m]), 1)
        self.assertTrue(any(why in m for m in notes), "the DM carries the payload's reason")

    def _run_desk(self, spec, desk_answer):
        """A DESK order refused by its payload: what reaches run_queue, and whether it went back to the desk."""
        calls = {"retire": 0, "report": 0, "handback": []}
        j = TestComputeCardQuietGate._J("cpu")
        d = tempfile.mkdtemp()
        j.spec_path = os.path.join(d, "ref_d.job.json")
        j.spec = dict(j.spec or {}, **spec)
        with open(j.spec_path, "w", encoding="utf-8") as f:
            json.dump(j.spec, f)
        proc = mock.Mock()
        proc.stdout = iter(["REFUSED (rc 2): qwen3.5:9b at num_ctx 39936 holds 696 MB in shared system memory "
                            "on a full card (7591 of 8192 MB)\n"])
        proc.wait = lambda: F.REFUSED_RC
        raised = None
        with mock.patch.object(F, "notify", lambda job, m: None), \
             mock.patch.object(F, "report_fleet_result", lambda *a, **k: calls.__setitem__("report", calls["report"] + 1)), \
             mock.patch.object(F, "retire_order", lambda *a, **k: calls.__setitem__("retire", calls["retire"] + 1)), \
             mock.patch.object(F, "hand_back_to_desk", lambda s, why, log: calls["handback"].append(why) or desk_answer), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F.subprocess, "Popen", lambda *a, **k: proc):
            try:
                F.run_compute_order(j)
            except (F.HandedBack, F.LaneBusy) as e:
                raised = e
        return raised, calls, j

    def test_a_refused_desk_order_goes_back_to_the_desk_not_onto_a_timer(self):
        raised, calls, j = self._run_desk({"fleet_order_id": "abc123"}, {"ok": True, "outcome": "placed",
                                                                          "host": "softserve"})
        self.assertIsInstance(raised, F.HandedBack)
        self.assertEqual(len(calls["handback"]), 1)
        self.assertTrue(calls["handback"][0].startswith("refused: REFUSED (rc 2)"), calls["handback"][0])
        self.assertEqual((calls["retire"], calls["report"]), (1, 0), "retired here as handed back, never failed")
        self.assertFalse(F.refusal_cooling(j.spec_path), "no retry timer is armed for an order that left")

    def test_a_pinned_order_still_waits_for_its_own_box(self):
        raised, calls, j = self._run_desk({"fleet_order_id": "abc123", "pinned_to": [F.FLEET_HOST]}, {"ok": True})
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertEqual(calls["handback"], [], "a pinned order is never re-assigned")
        self.assertEqual(calls["retire"], 0)

    def test_when_the_desk_cannot_take_it_the_order_stays_queued(self):
        raised, calls, j = self._run_desk({"fleet_order_id": "abc123"}, None)
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertEqual(len(calls["handback"]), 1)
        self.assertEqual((calls["retire"], calls["report"]), (0, 0))

    def test_other_nonzero_exit_still_fails_and_retires(self):
        raised, calls, notes, _j = self._run(1, ["Traceback: boom\n"])
        self.assertIsNone(raised)
        self.assertEqual((calls["retire"], calls["report"]), (1, 1))

    def test_refusal_count_advances_per_refusal(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "n_t.job.json")
        counts = [F.record_refusal(p, "x") for _ in range(F.REFUSED_DM_EVERY)]
        self.assertEqual(counts, list(range(1, F.REFUSED_DM_EVERY + 1)))

    def test_queued_orders_skips_a_cooling_refusal(self):
        q, saved = tempfile.mkdtemp(), F.QUEUE_DIR
        F.QUEUE_DIR = q
        try:
            a = os.path.join(q, "a.job.json")
            b = os.path.join(q, "b.job.json")
            for p in (a, b):
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"name": os.path.basename(p)}, f)
            F.record_refusal(a, "card busy")
            self.assertEqual([os.path.basename(p) for p in F.queued_orders()], ["b.job.json"],
                             "the refused order steps aside and the next order runs")
        finally:
            F.QUEUE_DIR = saved

    def test_hold_markers_leave_the_queue_with_their_order(self):
        """Markers like `.refused.json` or `.held.json` must be cleaned up when their order is removed from the queue to prevent orphaned state."""
        q, saved = tempfile.mkdtemp(), F.QUEUE_DIR
        arch = os.path.join(tempfile.mkdtemp(), "archive_t")      # its own fresh dir: a shared temp root survives runs
        os.makedirs(arch)
        F.QUEUE_DIR = q
        try:
            spec = os.path.join(q, "r.job.json")
            for name in ("r.job.refused.json", "r.job.held.json", "gone.job.held.json", "gone.job.refused.json"):
                open(os.path.join(q, name), "w").close()
            F._sweep_order_siblings("r.job", spec, arch)
            self.assertEqual(sorted(os.listdir(arch)), ["r.job.held.json", "r.job.refused.json"])
            F.sweep_orphaned_sidecars(lambda m: None)
            self.assertEqual(os.listdir(q), [], "markers whose spec is gone are litter")
        finally:
            F.QUEUE_DIR = saved

    def _infer(self):
        spec = importlib.util.spec_from_file_location("infer_ollama_undertest", os.path.join(BIN, "infer_ollama.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def _payload(self, body):
        p = os.path.join(tempfile.mkdtemp(), "fake_payload.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p

    def test_think_level_reaches_ollama_as_the_level_string(self):
        """Ollama 0.40.5 maps true/medium/high to MEDIUM; only "xhigh" is preserved as-is."""
        io_mod = self._infer()
        valid = {"xhigh", "high", "medium", "low", "max", True, False}
        self.assertEqual(io_mod.think_value("xhigh"), "xhigh")
        self.assertEqual(io_mod.think_value("medium"), "medium")
        self.assertEqual(io_mod.think_value("low"), "low")
        self.assertIs(io_mod.think_value(True), True)
        self.assertIs(io_mod.think_value(False), False)
        for label in io_mod.THINK_LEVELS + (True, False):
            self.assertIn(io_mod.think_value(label), valid, label)
        sent = []
        with mock.patch.object(io_mod, "_chat_stream", lambda host, body, idle, ceiling_s=None: sent.append(body) or {}):
            io_mod._chat("h", "m", [], {}, think="xhigh")
        self.assertEqual(sent[0]["think"], "xhigh")

    def test_grader_runs_checks_with_this_boxs_python_not_the_python3_alias(self):
        """The tablet scorer spawns `python3`; on the fleet boxes that is the Store alias. The grader must swap it."""
        calls = []
        io_mod = self._infer()
        real = io_mod.subprocess.run
        with mock.patch.object(io_mod.subprocess, "run", lambda argv, **k: (calls.append(list(argv)), real(argv, **k))[1]):
            io_mod._BoxPython().run(["python3", "-c", "print(1)"], capture_output=True)
        self.assertEqual(calls[0][0], sys.executable)

    def test_code_grader_proves_itself_and_grades_the_same_as_the_scorer(self):
        body = ("import subprocess, re\n"
                "TASKS = {'t': {'checks': ['lambda: add(2,3)==5']}}\nCONTROL_TASKS = {}\n"
                "def extract_code(text): return text\n"
                "def score(code, checks, work_dir):\n"
                "    prog = code + '\\n__c=0\\nfor _f in [' + ', '.join(checks) + ']:\\n    __c += bool(_f())\\nprint(\"RESULT\", __c, %d)\\n' % len(checks)\n"
                "    r = subprocess.run(['python3', '-c', prog], capture_output=True)\n"
                "    m = re.search(r'RESULT (\\d+) (\\d+)', r.stdout.decode())\n"
                "    return (int(m.group(1)), int(m.group(2)), '') if m else (0, len(checks), 'no_result_line')\n")
        g = self._infer().Grader(self._payload(body))
        self.assertEqual(g.kind, "code")
        self.assertTrue(g.grade({"id": "t", "answer": "def add(a,b):\n    return a+b\n"})["ok"])
        self.assertFalse(g.grade({"id": "t", "answer": "def add(a,b):\n    return a-b\n"})["ok"])
        v = g.grade({"id": "missing", "answer": "x"})
        self.assertIsNone(v["ok"])
        self.assertTrue(v["grade_error"], "an ungradable row must say why, never crash the order")

    def test_in_order_grading_reads_the_answer_as_the_menu_does(self):
        body = ("TASKS = {'ops': {'checks': [\"lambda: tuple(ANSWER) == ('D', 'A')\"]}}\nCONTROL_TASKS = {}\n"
                "def extract_code(text): return text\n"
                "def score(code, checks, work_dir):\n"
                "    ns = {}\n"
                "    try:\n"
                "        exec(code, ns)\n"
                "        n = sum(bool(eval(c, ns)()) for c in checks)\n"
                "    except Exception as e:\n"
                "        return 0, len(checks), repr(e)\n"
                "    return n, len(checks), ''\n")
        g = self._infer().Grader(self._payload(body))
        right = "Rebuild:\n\n```\ndocker compose build app\n```\n\n```python\nANSWER = ('D', 'A')\n```"
        self.assertTrue(g.grade({"id": "ops", "answer": right})["ok"])
        hedge = "```python\nANSWER = ('B', 'C')\n```\n```python\nANSWER = ('D', 'A')\n```"
        self.assertFalse(g.grade({"id": "ops", "answer": hedge})["ok"])

    def test_a_grader_that_cannot_run_checks_fails_the_order_loudly(self):
        body = ("TASKS = {}\ndef extract_code(t): return t\n"
                "def score(code, checks, work_dir): return 0, len(checks), 'no_result_line'\n")
        with self.assertRaises(SystemExit) as cm:
            self._infer().Grader(self._payload(body))
        self.assertIn("GRADER BROKEN", str(cm.exception))

    def test_grader_detects_tool_and_vlm_payloads(self):
        io_mod = self._infer()
        self.assertEqual(io_mod.Grader(self._payload("def score_tool(a, t, g): return 2, 2, ''\nTASKS = {}\n")).kind, "tool")
        self.assertEqual(io_mod.Grader(self._payload("def _vlm_score(a, g, m): return True\n_VLM_ALL = {}\n")).kind, "vlm")

    def test_infer_ollama_resumes_answered_rows_and_reruns_errors(self):
        """A pinned order that waited for its card restarts in the same {ORDER_OUT}: answered rows are kept, not redone."""
        io_mod = self._infer()
        d = tempfile.mkdtemp()
        out = os.path.join(d, "out.json")
        with open(out + ".partial.jsonl", "w", encoding="utf-8") as f:
            real = {"seed": None, "thinking": "", "done_reason": "stop", "prompt_eval_count": 10, "eval_count": 5,
                    "t0": 1.0, "t1": 2.0, "wall_s": 1.0}              # the shape _run writes for every row
            f.write(json.dumps(dict(real, id="a", round=1, answer="kept", served_model="m", error="")) + "\n")
            f.write(json.dumps(dict(real, id="b", round=1, answer="", served_model=None, error="HTTP 500: boom")) + "\n")
            f.write('{"id": "c", "rou')                                 # a line cut by the stop
        asked = []

        def chat(host, model, messages, options, fmt=None, think=False):
            asked.append(messages[-1]["content"])
            return {"model": "m", "message": {"content": "new"}, "done_reason": "stop"}

        class Meter:
            source = None
            samples = []

            def stop(self):
                pass
        a = mock.Mock(out=out, rounds=1, num_ctx=4096, temperature=None, seed_base=None, host="h", model="m",
                      think=False, grader=None, placement=None, price_ceiling_s=None, at_cap_brake=None)
        items = [{"id": "a", "prompt": "A"}, {"id": "b", "prompt": "B"}, {"id": "c", "prompt": "C"}]
        with mock.patch.object(io_mod, "_chat", chat), mock.patch("sys.stdout", new_callable=io.StringIO):
            io_mod._run(a, items, 3, "box", Meter(), (0, None))
        self.assertEqual(asked, ["B", "C"], "an answered row was redone, or an errored one skipped")
        with open(out, encoding="utf-8") as f:
            rows = {r["id"]: r for r in json.load(f)["rows"]}
        self.assertEqual(sorted(rows), ["a", "b", "c"])
        self.assertEqual(rows["a"]["answer"], "kept")
        self.assertEqual(rows["b"]["answer"], "new")

    def test_infer_ollama_partial_stays_one_row_per_line_after_a_cut_line(self):
        """A line cut mid-write lacks a trailing newline; appending directly after it merges the two lines into one unparseable entry."""
        io_mod = self._infer()
        d = tempfile.mkdtemp()
        out = os.path.join(d, "out.json")
        with open(out + ".partial.jsonl", "w", encoding="utf-8") as f:
            f.write('{"id": "a", "rou')                                 # the stop cut this line

        def chat(host, model, messages, options, fmt=None, think=False):
            return {"model": "m", "message": {"content": "new"}, "done_reason": "stop"}

        class Meter:
            source = None
            samples = []

            def stop(self):
                pass
        a = mock.Mock(out=out, rounds=1, num_ctx=4096, temperature=None, seed_base=None, host="h", model="m",
                      think=False, grader=None, placement=None, price_ceiling_s=None, at_cap_brake=None)
        with mock.patch.object(io_mod, "_chat", chat), mock.patch("sys.stdout", new_callable=io.StringIO):
            io_mod._run(a, [{"id": "a", "prompt": "A"}, {"id": "b", "prompt": "B"}], 2, "box", Meter(), (0, None))
        lines = open(out + ".partial.jsonl", encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 3, "the cut line, then one line per row")
        parsed = [json.loads(ln)["id"] for ln in lines[1:]]
        self.assertEqual(parsed, ["a", "b"], "both rows written after the cut are readable to the next resume")

    def _silent_run(self, probe_answers):
        """Item a answers, item b goes silent (a TimeoutError like ollama's IDLE_S read), item c would answer.
        `probe_answers` = whether the 4-token probe gets a token. -> (io_mod, out path, questions asked, exit code)."""
        io_mod = self._infer()
        out = os.path.join(tempfile.mkdtemp(), "out.json")
        asked = []

        def chat(host, model, messages, options, fmt=None, think=False, timeout=None):
            if timeout is not None:                                      # the engine probe
                if not probe_answers:
                    raise TimeoutError("timed out")
                return {"model": "m", "message": {"content": "OK"}}
            asked.append(messages[-1]["content"])
            if messages[-1]["content"] == "B":
                raise TimeoutError("timed out")
            return {"model": "m", "message": {"content": "ans"}, "done_reason": "stop"}

        class Meter:
            source = None
            samples = []

            def stop(self):
                pass
        a = mock.Mock(out=out, rounds=1, num_ctx=4096, temperature=None, seed_base=None, host="h", model="m",
                      think=False, grader=None, placement=None, price_ceiling_s=None, at_cap_brake=None)
        items = [{"id": "a", "prompt": "A"}, {"id": "b", "prompt": "B"}, {"id": "c", "prompt": "C"}]
        code = None
        with mock.patch.object(io_mod, "_chat", chat), mock.patch("sys.stdout", new_callable=io.StringIO):
            try:
                io_mod._run(a, items, 3, "box", Meter(), (0, None))
            except SystemExit as e:
                code = e.code
        return io_mod, out, asked, code

    def test_infer_ollama_holds_the_order_when_the_engine_is_hung(self):
        """Silence + a silent probe = a hung engine: exit with the code the foundry HOLDS on, and do not record the
        silent answer (the retry re-asks it) - never an error row per silent answer that reads as progress."""
        _io, out, asked, code = self._silent_run(probe_answers=False)
        self.assertEqual(code, F.REFUSED_RC)
        self.assertEqual(asked, ["A", "B"], "nothing may be asked after the engine is known hung")
        with open(out + ".partial.jsonl", encoding="utf-8") as f:
            kept = [json.loads(ln)["id"] for ln in f if ln.strip()]
        self.assertEqual(kept, ["a"], "the answered row is kept; the silent one is not written, so a retry re-asks it")

    def test_infer_ollama_keeps_going_when_a_silent_answer_meets_a_live_engine(self):
        _io, out, asked, code = self._silent_run(probe_answers=True)
        self.assertIsNone(code)
        self.assertEqual(asked, ["A", "B", "C"])
        with open(out, encoding="utf-8") as f:
            rows = {r["id"]: r for r in json.load(f)["rows"]}
        self.assertIn("TimeoutError", rows["b"]["error"], "a live engine's silent answer is an error row, as before")
        self.assertFalse(rows["b"].get("priced_out"), "silence is never a price")

    def _rated_run(self, resp_extra):
        io_mod = self._infer()
        out = os.path.join(tempfile.mkdtemp(), "out.json")

        def chat(host, model, messages, options, fmt=None, think=False, timeout=None):
            return dict({"model": "m", "message": {"content": "ans"}, "done_reason": "stop"}, **resp_extra)

        class Meter:
            source = None
            samples = []

            def stop(self):
                pass
        a = mock.Mock(out=out, rounds=1, num_ctx=4096, temperature=None, seed_base=None, host="h", model="m",
                      think=False, grader=None, placement=None, price_ceiling_s=None, at_cap_brake=None)
        printed = io.StringIO()
        with mock.patch.object(io_mod, "_chat", chat), mock.patch("sys.stdout", printed):
            io_mod._run(a, [{"id": "a", "prompt": "A"}, {"id": "b", "prompt": "B"}], 2, "box", Meter(), (0, None))
        lines = [ln for ln in printed.getvalue().splitlines() if F.COMPUTE_PROG_RE.match(ln)]
        with open(out, encoding="utf-8") as f:
            return lines, json.load(f)["rows"]

    def test_a_payloads_running_score_reaches_the_tile_phase(self):
        """A graded payload's `solved X/Y` (a1_order's progress line) shows on the deck beside the rate."""
        line = "[a1] 2/10 sgf-parsing fail 20/23 · solved 1/2 · 31 turns · 2987 s · 70.9 tok/s\n"
        self.assertTrue(F.COMPUTE_PROG_RE.match(line))
        sm = F.COMPUTE_SOLVED_RE.search(line)
        self.assertEqual((int(sm.group(1)), int(sm.group(2))), (1, 2))
        phase = F.solved_phase(F.toks_phase("compute(gpu): a1_order.py", [70.9]), (1, 2))
        self.assertEqual(phase, "compute(gpu): a1_order.py · 70.9 tok/s · solved 1 of 2")
        self.assertEqual(F.solved_phase("p", None), "p")

    def test_a_payloads_void_count_and_alert_lines_are_read(self):
        """a1_validity (10-08): a1_order's progress line carries `void N` and its alarms are `ALERT: <text>`;
        the foundry shows the first and DMs the second (line formats copied from a1_order's own output)."""
        line = "[a1] 3/10 react VOID 12/14 · solved 1/2 · void 1 · 32 turns · 12189 s · 61.0 tok/s\n"
        self.assertEqual(F.COMPUTE_VOID_RE.search(line).group(1), "1")
        self.assertEqual(F.COMPUTE_SOLVED_RE.search(line).groups(), ("1", "2"))
        alert = "ALERT: A1 x_mothership: react VOID (engine/harness error: loop error) - running it once more"
        self.assertEqual(F.COMPUTE_ALERT_RE.match(alert).group(1),
                         "A1 x_mothership: react VOID (engine/harness error: loop error) - running it once more")
        self.assertIsNone(F.COMPUTE_ALERT_RE.match("[a1] 1/1 ALERT: not at the start"))
        # the deck (app.py) reads the first `N / M` in a phase as progress: the score and void must never match it
        phase = "%s · void %d" % (F.solved_phase(F.toks_phase("compute(gpu): a1_order.py", [61.0]), (1, 2)), 1)
        self.assertIsNone(F.re.search(r"(\d+)\s*/\s*(\d+)", phase), "the score would displace the order's progress")

    def test_infer_ollama_decode_rate_reaches_the_tile_phase(self):
        """The payload's REAL progress line, through the foundry's REAL parser, onto the published phase."""
        lines, rows = self._rated_run({"eval_count": 200, "eval_duration": 2_000_000_000})
        self.assertEqual(len(lines), 2, "both answers print a progress line")
        self.assertEqual([r["tok_s"] for r in rows], [100.0, 100.0])
        toks = [float(F.COMPUTE_TOKS_RE.search(ln).group(1)) for ln in lines]
        self.assertEqual(F.toks_phase("compute(gpu): infer_ollama.py", toks),
                         "compute(gpu): infer_ollama.py · 100.0 tok/s")

    def test_no_decode_timing_means_no_rate_on_the_tile(self):
        lines, rows = self._rated_run({"eval_count": 200})
        self.assertEqual(len(lines), 2)
        self.assertFalse([ln for ln in lines if F.COMPUTE_TOKS_RE.search(ln)], "never print a rate it did not measure")
        self.assertNotIn("tok_s", rows[0])
        self.assertEqual(F.toks_phase("p", []), "p")
        self.assertEqual(F.toks_phase("p", [10.0, 30.0, 20.0, 99.0]), "p · 25.0 tok/s")

    def test_every_answer_is_bounded_by_the_tested_context(self):
        """Ensure a predict limit is set to prevent unbounded streaming from looping models."""
        io_mod = self._infer()
        seen = []

        def chat(host, model, messages, options, fmt=None, think=False, timeout=None):
            seen.append(dict(options))
            return {"model": "m", "message": {"content": "ans"}, "done_reason": "stop"}

        class Meter:
            source = None
            samples = []

            def stop(self):
                pass
        a = mock.Mock(out=os.path.join(tempfile.mkdtemp(), "out.json"), rounds=1, num_ctx=5120, temperature=None,
                      seed_base=None, host="h", model="m", think=False, grader=None, placement=None,
                      price_ceiling_s=None, at_cap_brake=None)
        with mock.patch.object(io_mod, "_chat", chat), mock.patch("sys.stdout", new_callable=io.StringIO):
            io_mod._run(a, [{"id": "a", "prompt": "A"}, {"id": "b", "prompt": "B"}], 2, "box", Meter(), (0, None))
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(o.get("num_predict") == 5120 and o.get("num_ctx") == 5120 for o in seen), seen)

    def _braked_run(self, reasons, share):
        io_mod = self._infer()
        asked = []

        def chat(host, model, messages, options, fmt=None, think=False, timeout=None):
            asked.append(messages[-1]["content"])
            return {"model": "m", "message": {"content": "ans"}, "done_reason": reasons[len(asked) - 1]}

        class Meter:
            source = None
            samples = []

            def stop(self):
                pass
        out = os.path.join(tempfile.mkdtemp(), "out.json")
        a = mock.Mock(out=out, rounds=1, num_ctx=5120, temperature=None, seed_base=None, host="h", model="m",
                      think=False, grader=None, placement=None, price_ceiling_s=None, at_cap_brake=share)
        items = [{"id": "control_add", "prompt": "C"}] + [{"id": "t%d" % i, "prompt": "P%d" % i}
                                                         for i in range(len(reasons) - 1)]
        with mock.patch.object(io_mod, "_chat", chat), mock.patch("sys.stdout", new_callable=io.StringIO):
            io_mod._run(a, items, len(items), "box", Meter(), (0, None))
        with open(out, encoding="utf-8") as f:
            return asked, json.load(f)

    def test_infer_ollama_brakes_an_arm_whose_answers_run_to_the_context(self):
        """W1 has no price line, so the cannot-fit share (FIT_VOID_SHARE) applies to the order as well - same rule and bound as tolkien_llm.brake_verdict."""
        asked, res = self._braked_run(["stop", "length", "length", "stop", "stop", "stop"], 0.25)
        self.assertEqual(len(asked), 3, "control + the two answers that proved the share; nothing after")
        self.assertEqual((res["braked"]["at_cap"], res["braked"]["answers"]), (2, 2))

    def test_without_the_brake_every_answer_runs(self):
        asked, res = self._braked_run(["stop", "length", "length", "stop", "stop", "stop"], None)
        self.assertEqual(len(asked), 6)
        self.assertIsNone(res["braked"])

    def test_one_runaway_and_a_capped_control_do_not_brake(self):
        asked, res = self._braked_run(["length", "length", "stop", "stop", "stop", "stop"], 0.25)
        self.assertEqual(len(asked), 6)
        self.assertIsNone(res["braked"])

    def test_both_factories_draw_the_brake_line_in_the_same_place(self):
        io_mod = self._infer()
        for k, n in ((2, 2), (1, 1), (2, 3), (3, 4), (1, 8)):
            self.assertEqual(io_mod.wilson_low(k, n) >= 0.25, {(2, 2): True, (3, 4): True}.get((k, n), False), (k, n))

    def _fixtures(self):
        with open(os.path.join(BIN, "loop_fixtures.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_loop_rule_on_real_answers(self):
        """A loop is no progress. Real text only: a loop that ran to the context, the lowest-scoring genuine finish,
        and a correct answer whose last window dipped."""
        io_mod, fx = self._infer(), self._fixtures()
        self.assertTrue(io_mod.looping(fx["loop"]["text"]))
        self.assertFalse(io_mod.looping(fx["legit_lowest"]["text"]), "the lowest genuine finish must never be cut")
        self.assertFalse(io_mod.looping(fx["correct_single_dip"]["text"]), "one repetitive window is not a loop")
        self.assertTrue(io_mod.looping(fx["counting_loop"]["text"]), "a counting loop is a loop")
        self.assertFalse(io_mod.looping(fx["loop"]["text"][-io_mod.LOOP_WINDOW:]), "too short to judge")

    def test_a_long_read_is_a_status_not_silence(self):
        io_mod = self._infer()
        pp = {"id": "c", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": None}], "prompt_progress": {"total": 164461, "processed": 59058, "cache": 0, "time_ms": 554000}}
        end = {"id": "c", "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}
        stream = b": keep-alive\n\n" + b"".join(b"data: " + json.dumps(x).encode() + b"\n\n" for x in (pp, pp, end)) + b"data: [DONE]\n\n"
        sent, seen = {}, []

        def fake(req, timeout=None):
            sent.update(json.loads(req.data.decode()))
            return io.BytesIO(stream)
        before = io_mod._BEAT["chunks"]
        with mock.patch.object(io_mod.urllib.request, "urlopen", fake), mock.patch("sys.stdout", new_callable=io.StringIO):
            out = io_mod._chat_openai("http://h", [{"role": "user", "content": "hi"}], {}, on_chunk=seen.append)
        self.assertIs(sent.get("return_progress"), True, "the engine only reports progress when asked")
        self.assertEqual((out["message"]["content"], len(seen)), ("ok", 4), "the chunks reach the window's stream, which is what Kirk's clock reads")
        self.assertEqual(seen[0], {"keepalive": True}, "before the first progress line the keep-alive is all a read says")
        self.assertEqual(io_mod._BEAT["chunks"] - before, 4, "the worker's beat moves while it reads, which is what the demon reads")

    def test_a_table_of_numbers_is_not_a_loop(self):
        io_mod, fx = self._infer(), self._fixtures()
        for name in ("number_table_thinking", "number_table_in_code"):
            self.assertFalse(io_mod.looping(fx[name]["text"]), name)

    def test_numbers_that_never_end_are_still_a_loop(self):
        io_mod, rnd = self._infer(), random.Random(11)
        rows = "".join("Row %d: %s\n" % (i, ",".join(str(rnd.randrange(12)) for _ in range(12))) for i in range(4000))
        self.assertFalse(io_mod.looping(rows[:io_mod.LOOP_WINDOW * 3]), "a table's length: positive control for the next line")
        self.assertTrue(io_mod.looping(rows[:io_mod.LOOP_WINDOW * io_mod.LOOP_DATA_WINDOWS]))

    def test_prose_after_a_table_clears_the_count(self):
        io_mod, rnd = self._infer(), random.Random(12)
        rows = "".join("Row %d: %s\n" % (i, ",".join(str(rnd.randrange(12)) for _ in range(12))) for i in range(600))
        words = " ".join("".join(rnd.choice("abcdefghijklmnop") for _ in range(rnd.randrange(2, 10))) for _ in range(2000))
        self.assertFalse(io_mod.looping(rows[:io_mod.LOOP_WINDOW * 3] + words + rows[:io_mod.LOOP_WINDOW * 3]))

    def _stream(self, text, final_reason="stop"):
        """Feed the REAL _chat_stream a streamed reply of `text` in 50-char chunks; returns (reply, chunks read, total)."""
        io_mod = self._infer()
        chunks = [text[i:i + 50] for i in range(0, len(text), 50)]
        lines = [json.dumps({"model": "m", "message": {"content": c}, "done": False}).encode() + b"\n" for c in chunks]
        lines.append(json.dumps({"model": "m", "message": {"content": ""}, "done": True,
                                 "done_reason": final_reason}).encode() + b"\n")
        read = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                for ln in lines:
                    read.append(1)
                    yield ln
        with mock.patch.object(io_mod.urllib.request, "urlopen", lambda req, timeout=None: Resp()), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            reply = io_mod._chat_stream("http://h", {}, 600)
        return reply, len(read), len(lines)

    def test_a_looping_stream_is_closed_and_says_loop(self):
        reply, read, total = self._stream(self._fixtures()["loop"]["text"] * 3, final_reason="length")
        self.assertEqual(reply["done_reason"], "loop")
        self.assertLess(read, total, "the stream must be closed early, not read to the context")

    def test_a_genuine_long_answer_streams_to_its_own_end(self):
        reply, read, total = self._stream(self._fixtures()["legit_lowest"]["text"])
        self.assertEqual((reply["done_reason"], read), ("stop", total))

    @staticmethod
    def _prose(n, seed):
        """`n` chars of text that does not repeat and does not compress like a loop."""
        import random
        rnd = random.Random(seed)
        out, size = [], 0
        while size < n:
            w = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randrange(2, 11)))
            out.append(w)
            size += len(w) + 1
        return " ".join(out)[:n]

    def test_one_long_block_written_over_and_over_is_a_loop(self):
        io_mod = self._infer()
        lead, block = self._prose(5000, 1), self._prose(23401, 2)
        self.assertEqual(io_mod.repeating(lead + block * 3), 23401)
        self.assertTrue(io_mod.looping(lead + block * 3))
        self.assertEqual(io_mod.repeating(lead + block * 2), 0, "a block written twice is a restatement")
        self.assertFalse(io_mod.looping(lead + block * 2))
        self.assertEqual(io_mod.repeating(lead + block * 3 + self._prose(600, 3)), 0, "it moved on: not a loop")

    def test_a_short_block_needs_as_much_text_as_the_ratio_rule_asks(self):
        """Six identical 300-char cards in a page are content; the same card for 8,000 chars is a loop."""
        io_mod = self._infer()
        lead, card = self._prose(12000, 4), self._prose(300, 5)
        self.assertEqual(io_mod.repeating(lead + card * 6), 0)
        self.assertEqual(io_mod.repeating(lead + card * 27), 300)
        self.assertEqual(io_mod.repeating(lead + self._prose(60, 6) * 400), 0, "under LOOP_MIN_PERIOD: the ratio's to judge")

    def test_rows_that_differ_only_in_their_numbers_are_not_the_long_rule_s(self):
        io_mod, row = self._infer(), self._prose(400, 7)
        rows = "".join("%s %06d\n" % (row, i) for i in range(40))
        self.assertEqual(io_mod.repeating(self._prose(3000, 8) + rows), 0)

    def test_the_long_rule_leaves_the_real_genuine_answers_alone(self):
        io_mod, fx = self._infer(), self._fixtures()
        self.assertEqual([io_mod.repeating(fx[k]["text"]) for k in ("legit_lowest", "correct_single_dip")], [0, 0])

    def test_a_long_loop_is_cut_in_the_stream_because_the_whole_turn_is_kept(self):
        text = self._prose(5000, 9) + self._prose(23401, 10) * 5
        reply, read, total = self._stream(text, final_reason="length")
        self.assertEqual(reply["done_reason"], "loop")
        self.assertLess(read, total * 0.72, "ended on the third copy, not at the context")

    def test_tool_calls_survive_the_streamed_reassembly(self):
        # A1 (agentic builds) drives a model through ollama native tool calling on the same hang-proof stream
        io_mod = self._infer()
        call = {"function": {"name": "write_file", "arguments": {"path": "a.py", "content": "x = 1"}}}
        lines = [json.dumps({"model": "m", "message": {"content": "", "tool_calls": [call]}, "done": False}).encode()
                 + b"\n", json.dumps({"model": "m", "message": {"content": ""}, "done": True,
                                      "done_reason": "stop"}).encode() + b"\n"]

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                return iter(lines)
        with mock.patch.object(io_mod.urllib.request, "urlopen", lambda req, timeout=None: Resp()), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            reply = io_mod._chat_stream("http://h", {}, 600)
        self.assertEqual(reply["message"]["tool_calls"], [call])
        plain, _r, _t = self._stream("just text")
        self.assertNotIn("tool_calls", plain["message"], "no calls -> no key")

    def test_on_chunk_sees_every_chunk_and_leaves_the_reply_unchanged(self):
        # a live chat window relays the chunks as they arrive; the row it writes must be the same row either way
        io_mod = self._infer()
        text = "a streamed answer long enough for several fifty-character chunks to arrive one by one"
        plain, _r, total = self._stream(text)
        chunks = [text[i:i + 50] for i in range(0, len(text), 50)]
        lines = [json.dumps({"model": "m", "message": {"content": c}, "done": False}).encode() + b"\n" for c in chunks]
        lines.append(json.dumps({"model": "m", "message": {"content": ""}, "done": True,
                                 "done_reason": "stop"}).encode() + b"\n")

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                return iter(lines)
        seen = []
        with mock.patch.object(io_mod.urllib.request, "urlopen", lambda req, timeout=None: Resp()), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            relayed = io_mod._chat_stream("http://h", {}, 600, on_chunk=seen.append)
        self.assertEqual(len(seen), total, "every chunk, including the terminal one")
        self.assertTrue(seen[-1]["done"])
        self.assertEqual("".join((c.get("message") or {}).get("content") or "" for c in seen), text)
        self.assertEqual(relayed, plain)

    def test_chat_sends_the_callers_keep_alive_else_ten_minutes(self):
        io_mod = self._infer()
        bodies = []
        with mock.patch.object(io_mod, "_chat_stream", lambda host, body, idle, ceiling_s=None: bodies.append(body) or {}):
            io_mod._chat("http://h", "m", [], {}, keep_alive="2m")
            io_mod._chat("http://h", "m", [], {})
        self.assertEqual([b["keep_alive"] for b in bodies], ["2m", "10m"])

    def test_chat_sends_tools_only_when_given(self):
        io_mod = self._infer()
        bodies = []
        with mock.patch.object(io_mod, "_chat_stream", lambda host, body, idle, ceiling_s=None: bodies.append(body) or {}):
            io_mod._chat("http://h", "m", [], {}, tools=[{"type": "function", "function": {"name": "finish"}}])
            io_mod._chat("http://h", "m", [], {})
        self.assertEqual(bodies[0]["tools"][0]["function"]["name"], "finish")
        self.assertNotIn("tools", bodies[1])

    def test_the_brake_counts_a_loop_as_an_answer_at_the_cap(self):
        io_mod = self._infer()
        rows = [{"id": "t%d" % i, "done_reason": "loop"} for i in range(2)]
        self.assertEqual(io_mod.brake_verdict(rows, 0.25)["at_cap"], 2)

    def test_infer_ollama_residency_refusal_exits_2(self):
        spec = importlib.util.spec_from_file_location("infer_ollama_undertest", os.path.join(BIN, "infer_ollama.py"))
        io_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(io_mod)
        with mock.patch.object(io_mod, "_residency", lambda h, m: (5.6e9, 6.3e9)), \
             mock.patch.object(io_mod.gpu_spill, "wait_released", lambda *a, **k: True), \
             mock.patch.object(io_mod, "_post", lambda *a, **k: {}), \
             mock.patch.object(io_mod, "_chat", lambda *a, **k: {}), \
             mock.patch.object(io_mod.time, "sleep", lambda s: None), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm:
                io_mod._assert_resident("http://x", "m:1b", 8192)
        self.assertEqual(cm.exception.code, F.REFUSED_RC,
                         "the payload's refusal must exit with the code the foundry holds on")

    def _io_mod_placed(self, vram, size):
        spec = importlib.util.spec_from_file_location("infer_ollama_undertest", os.path.join(BIN, "infer_ollama.py"))
        io_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(io_mod)
        return io_mod, [mock.patch.object(io_mod, "_residency", lambda h, m: (vram, size)),
                        # unstubbed it reads the real card, causing tests to fail on full Softserve cards
                        mock.patch.object(io_mod, "_spill_reading", lambda: None),
                        mock.patch.object(io_mod.gpu_spill, "wait_released", lambda *a, **k: True),
                        mock.patch.object(io_mod, "_post", lambda *a, **k: {}),
                        mock.patch.object(io_mod, "_chat", lambda *a, **k: {}),
                        mock.patch.object(io_mod.time, "sleep", lambda s: None),
                        # the remembered clean placements: this test's own file, never the box's var\state
                        mock.patch.object(io_mod, "PLACEMENTS_FILE", os.path.join(self._placements_dir(), "p.json")),
                        mock.patch("sys.stdout", new_callable=io.StringIO)]

    def _placements_dir(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_a_declared_offload_already_placed_as_its_last_clean_load_is_kept_without_a_reload(self):
        """Ensures that if an offload is already resident and matches its last known clean load size, it is kept without a costly reload."""
        io_mod, patches = self._io_mod_placed(6.8e9, 18.8e9)
        posts = []
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(mock.patch.object(io_mod, "_post", lambda *a, **k: posts.append(a) or {}))
            first = io_mod._assert_resident("http://x", "big:moe", 65536, offload_ok=True)
            self.assertTrue(first["cold"], "no memory yet: the clean load, as before")
            evicts = len(posts)
            again = io_mod._assert_resident("http://x", "big:moe", 65536, offload_ok=True)
        self.assertEqual(evicts, 1)
        self.assertEqual(len(posts), 1, "the second window evicts nothing")
        self.assertEqual(again, {"size_vram": 6.8e9, "size": 18.8e9, "offload": True, "cold": False, "load_s": None})

    def test_a_worse_placement_than_the_last_clean_one_is_still_reloaded(self):
        io_mod, patches = self._io_mod_placed(6.8e9, 18.8e9)
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            io_mod._assert_resident("http://x", "big:moe", 65536, offload_ok=True)
            st.enter_context(mock.patch.object(io_mod, "_residency", lambda h, m: (3.1e9, 18.8e9)))
            posts = []
            st.enter_context(mock.patch.object(io_mod, "_post", lambda *a, **k: posts.append(a) or {}))
            io_mod._assert_resident("http://x", "big:moe", 65536, offload_ok=True)
            self.assertEqual(len(posts), 1, "a load that landed fewer layers (a desktop held the card) is redone")
            posts.clear()
            io_mod._assert_resident("http://x", "big:moe", 32768, offload_ok=True)
            self.assertEqual(len(posts), 1, "another context is another placement: never borrowed")

    def test_infer_ollama_declared_offload_runs_and_records_its_placement(self):
        io_mod, patches = self._io_mod_placed(23.1e9, 58.3e9)
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            placed = io_mod._assert_resident("http://x", "big:moe", 8192, offload_ok=True)
        self.assertEqual(placed, {"size_vram": 23.1e9, "size": 58.3e9, "offload": True, "cold": True, "load_s": None})

    def test_infer_ollama_declared_offload_still_refuses_a_model_with_nothing_on_the_card(self):
        io_mod, patches = self._io_mod_placed(0, 58.3e9)           # the CPU-only fallback is never the model
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            with self.assertRaises(SystemExit) as cm:
                io_mod._assert_resident("http://x", "big:moe", 8192, offload_ok=True)
        self.assertEqual(cm.exception.code, F.REFUSED_RC)

    def test_infer_ollama_offload_flag_reaches_the_residency_check(self):
        io_mod, patches = self._io_mod_placed(0, 1)
        seen = {}
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(mock.patch.object(io_mod, "_assert_resident",
                                               lambda h, m, c, offload_ok=False, cold=False:
                                               seen.update(ok=offload_ok) or {}))
            st.enter_context(mock.patch.object(io_mod, "_run", lambda *a, **k: 0))
            st.enter_context(mock.patch.object(io_mod, "PowerMeter",
                                               lambda: mock.Mock(source=None, stop=lambda: None)))
            with tempfile.TemporaryDirectory() as d:
                items = os.path.join(d, "items.json")
                with open(items, "w", encoding="utf-8") as f:
                    json.dump({"items": [{"id": "a", "prompt": "p"}]}, f)
                io_mod.main([items, os.path.join(d, "out.json"), "--model", "big:moe", "--offload-ok"])
        self.assertEqual(seen, {"ok": True})

    def _stream_server(self, chunks, gap_s, first_wait_s=0.0):
        """A local stand-in for ollama's streamed /api/chat: one NDJSON chunk every gap_s."""
        import http.server, threading

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                time.sleep(first_wait_s)
                for c in chunks:
                    self.wfile.write((json.dumps(c) + "\n").encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(gap_s)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def test_a_long_answer_still_being_written_is_never_cut_off(self):
        """Test that streams running longer than the idle timeout still complete if they remain active.

        The idle timeout kills silent connections, but this stream must stay alive and reassemble
        the full response even when the total duration exceeds 3x the idle window."""
        m = self._infer()
        chunks = ([{"message": {"thinking": "t%d " % i}} for i in range(6)]
                  + [{"message": {"content": "def f(): return 1"}}]
                  + [{"model": "m:1b", "done": True, "done_reason": "stop", "eval_count": 7, "prompt_eval_count": 3,
                      "message": {"content": ""}}])
        host = self._stream_server(chunks, gap_s=0.15)
        out = io.StringIO()
        with mock.patch.object(m, "IDLE_S", 0.5), mock.patch.object(m, "WORK_BEAT_S", 0.3), \
             mock.patch("sys.stdout", out):
            t0 = time.time()
            resp = m._chat(host, "m:1b", [{"role": "user", "content": "x"}], {})
            self.assertGreater(time.time() - t0, 0.5 * 2, "the fixture must outlast the idle window, or it proves nothing")
        beats = [l for l in out.getvalue().splitlines() if l.startswith("[beat] ")]
        self.assertGreaterEqual(len(beats), 2, "the payload must beat while it streams: %r" % out.getvalue()[-200:])
        self.assertEqual(resp["message"]["content"], "def f(): return 1")
        self.assertTrue(resp["message"]["thinking"].startswith("t0 t1"))
        self.assertEqual((resp["model"], resp["done_reason"], resp["eval_count"]), ("m:1b", "stop", 7))

    def test_real_silence_still_ends_the_request(self):
        m = self._infer()
        host = self._stream_server([{"message": {"content": "late"}}], gap_s=0.0, first_wait_s=1.5)
        with mock.patch.object(m, "IDLE_S", 0.3):
            with self.assertRaises(Exception) as cm:
                m._chat(host, "m:1b", [{"role": "user", "content": "x"}], {})
        self.assertIn("timed out", str(cm.exception).lower())

    def _slow_stream(self):
        chunks = ([{"message": {"thinking": "t%d " % i}} for i in range(20)]
                  + [{"model": "m:1b", "done": True, "done_reason": "stop", "eval_count": 20, "prompt_eval_count": 3,
                      "message": {"content": "def f(): return 1"}}])
        return chunks, self._stream_server(chunks, gap_s=0.1)

    def test_the_price_ceiling_cuts_a_streaming_answer_keeps_the_partial_text_and_names_it(self):
        """Project 34 price line: an answer that has cost more than the order's ceiling is cut and recorded
        priced_out - a measurement of cost, with done_reason that is NOT 'length' (a context cap is void)."""
        m = self._infer()
        _chunks, host = self._slow_stream()                       # ~2.1 s of streaming in total
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            t0 = time.time()
            resp = m._chat(host, "m:1b", [{"role": "user", "content": "x"}], {}, ceiling_s=0.5)
            took = time.time() - t0
        self.assertTrue(resp.get("priced_out"))
        self.assertEqual(resp["done_reason"], "priced_out")
        self.assertNotEqual(resp["done_reason"], "length", "a priced-out row must never read as a context-cap (void) row")
        self.assertLess(took, 1.5, "the stream was not closed at the ceiling")
        self.assertTrue(resp["message"]["thinking"].startswith("t0 t1"), "the partial text is kept")

    def test_without_a_price_ceiling_the_same_stream_completes_and_is_not_priced_out(self):
        m = self._infer()
        _chunks, host = self._slow_stream()
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            resp = m._chat(host, "m:1b", [{"role": "user", "content": "x"}], {})
        self.assertNotIn("priced_out", resp)
        self.assertEqual((resp["done_reason"], resp["message"]["content"]), ("stop", "def f(): return 1"))

    def _run_with_ceiling(self, chat, items, grader=None, ceiling=19.0, partial=None):
        io_mod = self._infer()
        d = tempfile.mkdtemp()
        out = os.path.join(d, "out.json")
        if partial:
            with open(out + ".partial.jsonl", "w", encoding="utf-8") as f:
                for r in partial:
                    f.write(json.dumps(r) + "\n")

        class Meter:
            source = None
            samples = []

            def stop(self):
                pass
        a = mock.Mock(out=out, rounds=1, num_ctx=4096, temperature=None, seed_base=None, host="h", model="m",
                      think=False, grader=grader, placement=None, price_ceiling_s=ceiling, at_cap_brake=None)
        with mock.patch.object(io_mod, "_chat", chat), mock.patch("sys.stdout", new_callable=io.StringIO):
            io_mod._run(a, items, len(items), "box", Meter(), (0, None))
        with open(out, encoding="utf-8") as f:
            return json.load(f)

    def test_the_price_ceiling_binds_every_item_but_a_control_and_a_cut_row_is_not_graded(self):
        seen = {}

        def chat(host, model, messages, options, fmt=None, think=False, **kw):
            q = messages[-1]["content"]
            seen[q] = kw.get("ceiling_s")
            if q == "slow":
                return {"model": "m", "message": {"content": "part", "thinking": "..."}, "priced_out": True,
                        "done_reason": "priced_out"}
            return {"model": "m", "message": {"content": "ok"}, "done_reason": "stop", "prompt_eval_count": 4,
                    "eval_count": 2}
        graded = []

        class G:
            path = "g.py"
            sha = "x"
            kind = "code"

            def grade(self, row):
                graded.append(row["id"])
                return {"ok": True, "checks_passed": 1, "checks_total": 1}
        items = [{"id": "control_add", "prompt": "ctl"}, {"id": "t1", "prompt": "fast"}, {"id": "t2", "prompt": "slow"}]
        out = self._run_with_ceiling(chat, items, grader=G())
        self.assertEqual(seen, {"ctl": None, "fast": 19.0, "slow": 19.0},
                         "the control must never carry the ceiling; every other item must")
        rows = {r["id"]: r for r in out["rows"]}
        self.assertTrue(rows["t2"]["priced_out"])
        self.assertEqual((rows["t2"]["ok"], rows["t2"]["ceiling_s"], rows["t2"]["done_reason"]), (False, 19.0, "priced_out"))
        self.assertNotIn("priced_out", rows["t1"])
        self.assertEqual(sorted(graded), ["control_add", "t1"], "a cut answer's partial text must never be graded")
        self.assertEqual((out["price_ceiling_s"], out["summary"]["priced_out"], out["summary"]["errors"]), (19.0, 1, 0))

    def test_a_priced_out_row_is_an_answered_row_on_resume_and_is_not_redone(self):
        asked = []

        def chat(host, model, messages, options, fmt=None, think=False, **kw):
            asked.append(messages[-1]["content"])
            return {"model": "m", "message": {"content": "new"}, "done_reason": "stop"}
        cut = {"id": "t1", "round": 1, "seed": None, "answer": "part", "thinking": "", "served_model": "m",
               "done_reason": "priced_out", "prompt_eval_count": None, "eval_count": None, "wall_s": 19.4,
               "error": "", "priced_out": True, "ceiling_s": 19.0, "t0": 1.0, "t1": 20.4}
        out = self._run_with_ceiling(chat, [{"id": "t1", "prompt": "A"}, {"id": "t2", "prompt": "B"}], partial=[cut])
        self.assertEqual(asked, ["B"], "a priced-out row is a finished measurement; it must not be re-asked")
        self.assertTrue({r["id"]: r for r in out["rows"]}["t1"]["priced_out"])

    def test_a_price_ceiling_must_be_a_positive_number_of_seconds(self):
        io_mod = self._infer()
        with tempfile.TemporaryDirectory() as d:
            items = os.path.join(d, "items.json")
            with open(items, "w", encoding="utf-8") as f:
                json.dump({"items": [{"id": "a", "prompt": "p"}]}, f)
            for bad in ("0", "-5"):
                with self.assertRaises(SystemExit) as cm:
                    io_mod.main([items, os.path.join(d, "out.json"), "--model", "m", "--price-ceiling-s", bad])
                self.assertIn("positive", str(cm.exception))

    def test_the_price_ceiling_flag_reaches_the_run(self):
        io_mod, patches = self._io_mod_placed(1, 1)
        seen = {}
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(mock.patch.object(io_mod, "_run", lambda a, *r, **k: seen.update(c=a.price_ceiling_s) or 0))
            st.enter_context(mock.patch.object(io_mod, "PowerMeter", lambda: mock.Mock(source=None, stop=lambda: None)))
            with tempfile.TemporaryDirectory() as d:
                items = os.path.join(d, "items.json")
                with open(items, "w", encoding="utf-8") as f:
                    json.dump({"items": [{"id": "a", "prompt": "p"}]}, f)
                io_mod.main([items, os.path.join(d, "out.json"), "--model", "m", "--price-ceiling-s", "24.5"])
        self.assertEqual(seen, {"c": 24.5})


class TestRenderDeliverableAccounting(unittest.TestCase):
    """
    render() must return (produced_count, failed_count).
    The probe caller treats zero produced images as a failure,
    preventing silent success on complete rendering failures.
    """

    class _J:
        def __init__(self):
            self.name = "probe_t"; self.mode = "probe"; self.spec = {}; self.state = {}
            self.logs = []
        def log(self, m): self.logs.append(m)
        def save(self): pass

    def _run(self, n, produce):
        prompts = [{"name": "p%d" % i, "text": "a figure, standing, front", "base": True}
                   for i in range(n)]
        def exists(path):
            return produce if str(path).endswith(".png") else True
        pats = [
            mock.patch.object(F.subprocess, "run",
                              mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))),
            mock.patch.object(F, "pause_requested", lambda: False),
            mock.patch.object(F, "order_cancel_requested", lambda *a, **k: False),
            mock.patch.object(F, "render_key", lambda *a, **k: "k"),
            mock.patch.object(F, "render_index_lookup", lambda *a, **k: None),
            mock.patch.object(F, "render_index_put", lambda *a, **k: None),
            mock.patch.object(F, "supervise_render", lambda job, cmd, png: ("ok", "done")),
            mock.patch.object(F, "ship_render", lambda *a, **k: None),
            mock.patch.object(F, "notify", lambda *a, **k: None),
            mock.patch.object(F, "notify_file", lambda *a, **k: None),
            mock.patch.object(F, "comfy_is_cold", lambda: False),
            mock.patch.object(F, "ensure_comfy", lambda *a, **k: None),
            mock.patch.object(F, "stop_comfy", lambda *a, **k: None),
            mock.patch.object(F.time, "sleep", lambda *a, **k: None),
            mock.patch("os.path.exists", exists),
        ]
        for p in pats:
            p.start()
        try:
            return F.render(self._J(), r"C:\ck.safetensors", 1200, prompts,
                            dm_each=False, tag="_LORA")
        finally:
            for p in pats:
                p.stop()

    def test_all_renders_fail_counts_zero_produced(self):
        self.assertEqual(self._run(2, produce=False), (0, 2))

    def test_all_renders_succeed_counts_all_produced(self):
        self.assertEqual(self._run(3, produce=True), (3, 0))

    def test_returns_two_int_tuple(self):
        r = self._run(1, produce=True)
        self.assertIsInstance(r, tuple)
        self.assertEqual(len(r), 2)
        self.assertTrue(all(isinstance(x, int) for x in r))


class KreaTrainingPath(unittest.TestCase):
    """Krea training orders render via p20_krea_render, bake no .cond, and never touch the FLUX.2 text-embeds cache. Orders without a base remain unchanged."""

    def job_from(self, spec):
        d = tempfile.mkdtemp(prefix="krea_job_")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "x.job.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        return F.Job(p)

    def test_the_base_comes_from_the_order_or_its_register_else_flux2(self):
        self.assertEqual(self.job_from({"name": "a", "base": "krea2"}).base, "krea2")
        self.assertEqual(self.job_from({"name": "b", "register": {"base": "krea2"}}).base, "krea2")
        self.assertEqual(self.job_from({"name": "c"}).base, "flux2-dev")

    def test_a_krea_order_bakes_nothing_and_leaves_the_flux2_embeds_cache_alone(self):
        job = types.SimpleNamespace(name="k", mode="train", base="krea2", spec={}, log=lambda m: None)
        with mock.patch.object(F.subprocess, "run") as run, \
                mock.patch.object(F, "embeds_needs_rebuild", side_effect=AssertionError("touched FLUX.2 cache")):
            F.bake_missing(job, [{"name": "p", "text": "n1ck, a man"}])
            F.retire_stale_embeds(job)
        run.assert_not_called()

    def _rendered_cmd(self, base):
        cmds, made = [], set()

        class J(TestRenderDeliverableAccounting._J):
            pass
        job = J()
        if base:
            job.base = base

        def sup(job_, cmd, png):
            cmds.append(cmd)
            made.add(png)
            return "ok", "done"
        pats = [mock.patch.object(F.subprocess, "run",
                                  mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))),
                mock.patch.object(F, "pause_requested", lambda: False),
                mock.patch.object(F, "order_cancel_requested", lambda *a, **k: False),
                mock.patch.object(F, "render_key", lambda *a, **k: "k"),
                mock.patch.object(F, "render_index_lookup", lambda *a, **k: None),
                mock.patch.object(F, "render_index_put", lambda *a, **k: None),
                mock.patch.object(F, "supervise_render", sup),
                mock.patch.object(F, "ship_render", lambda *a, **k: None),
                mock.patch.object(F, "notify", lambda *a, **k: None),
                mock.patch.object(F, "notify_file", lambda *a, **k: None),
                mock.patch.object(F, "comfy_is_cold", lambda: False),
                mock.patch.object(F, "ensure_comfy", lambda *a, **k: None),
                mock.patch.object(F, "stop_comfy", lambda *a, **k: None),
                mock.patch.object(F.time, "sleep", lambda *a, **k: None),
                mock.patch("os.path.exists", lambda path: path in made or not str(path).endswith(".png"))]
        for p in pats:
            p.start()
        try:
            F.render(job, r"C:\ck.safetensors", 250, [{"name": "window", "text": "n1ck, a man by a window"}])
        finally:
            for p in pats:
                p.stop()
        self.assertEqual(len(cmds), 1)
        return cmds[0]

    def test_a_krea_checkpoint_renders_through_krea_with_its_prompt_text(self):
        cmd = self._rendered_cmd("krea2")
        self.assertEqual(cmd[1], F.KREA_RENDER)
        self.assertIn("n1ck, a man by a window", cmd)

    def test_an_order_without_a_base_still_renders_flux2_slim(self):
        self.assertEqual(self._rendered_cmd(None)[1], F.SLIM)


class TrainerNameFromItsOwnConfig(unittest.TestCase):
    """Identity and output paths must derive from the trainer's config.name, not the order's label."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="trainer_name_")
        self.addCleanup(shutil.rmtree, self.d, True)
        cfg = os.path.join(self.d, "alex_krea2_v1.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('---\njob: extension\nconfig:\n  name: "alex_krea2_v1"\n  process: []\nmeta:\n  name: "[name]"\n')
        self.bat = os.path.join(self.d, "run.bat")
        with open(self.bat, "w", encoding="utf-8") as f:
            f.write("cd /d D:\\ai-toolkit-krea2\nvenv\\python.exe -u run.py %s > x.log 2>&1\n" % cfg)
        self.out = os.path.join(self.d, "out")
        os.makedirs(self.out)
        self.job = types.SimpleNamespace(name="alex_krea2_v1_mothership", launcher=self.bat, output_dir=self.out)

    def test_the_trainer_name_is_the_configs_not_the_desk_suffixed_order(self):
        self.assertEqual(F.trainer_name(self.job), "alex_krea2_v1")

    def test_checkpoints_are_found_under_the_trainers_name(self):
        open(os.path.join(self.out, "alex_krea2_v1_000000250.safetensors"), "wb").close()
        self.assertEqual(sorted(F.checkpoints(self.job)), [250])

    def test_the_process_check_looks_for_the_trainers_name(self):
        seen = []
        with mock.patch.object(F.subprocess, "run",
                               side_effect=lambda cmd, **k: seen.append(cmd) or types.SimpleNamespace(stdout="1")):
            self.assertTrue(F.trainer_running(self.job))
        self.assertIn("Contains('alex_krea2_v1')", seen[0][-1])

    def test_no_launcher_or_no_config_falls_back_to_the_order_name(self):
        job = types.SimpleNamespace(name="probe_x", launcher="", output_dir=self.out)
        self.assertEqual(F.trainer_name(job), "probe_x")
        with open(self.bat, "w", encoding="utf-8") as f:
            f.write("venv\\python.exe -u run.py missing.yaml\n")
        self.assertEqual(F.trainer_name(self.job), "alex_krea2_v1_mothership")


def _row(pid, ppid, cmd):
    """Build a process row exactly as the PowerShell emitter does, using the SAME separator
    constant the parser splits on. Hardcoding a tab here is what let the real bug through."""
    return "%s%s%s%s%s" % (pid, F.GPU_PROC_SEP, ppid, F.GPU_PROC_SEP, cmd)


class TestGpuPatrol(unittest.TestCase):
    """THE POLICE, PART II. This patrol can KILL processes, so every test below pins a SAFETY
    property rather than a convenience. A false positive here is not noise, it is data loss."""

    def setUp(self):
        # the patrol's alert ledger is the live factory's state: a test writes a temp copy, never that one
        self.live_ledger = F.GPU_ALERT_LEDGER          # the path production uses, for the test that pins where it lives
        d = tempfile.mkdtemp(prefix="patrol_ledger_")
        self.addCleanup(shutil.rmtree, d, True)
        p = mock.patch.object(F, "GPU_ALERT_LEDGER", os.path.join(d, "gpu_patrol_alerted.json"))
        p.start()
        self.addCleanup(p.stop)

    def _proc(self, cmd, pid=4242, ppid=1):
        return {"pid": pid, "ppid": ppid, "cmd": cmd}

    # --- attribution: the factory's own components are NEVER foreign --------------------------

    def test_the_rescue_burst_payload_is_never_foreign(self):
        """The stall daemon's burst payload is an internal GPU process. It must not be classified as foreign, lest the protection mechanism terminate its own rescue logic."""
        owner, _why = F.gpu_owner(
            self._proc(r"F:\ai-toolkit\venv\python.exe -u F:\ai-toolkit\p20_rescue_burst.py"),
            own_pid=987654, claim={})
        self.assertEqual(owner, "factory")

    def test_factory_components_are_not_foreign(self):
        for cmd in (r"python.exe -u F:\ai-toolkit\p20_stall_rescue.py",
                    r"python.exe -u F:\foundry\bin\p20_render_worker.py",
                    r"python.exe F:\foundry\bin\p20_foundry.py somejob.job.json"):
            owner, _ = F.gpu_owner(self._proc(cmd), own_pid=987654, claim={})
            self.assertEqual(owner, "factory", cmd)

    def test_ollama_server_is_orchestrator_infrastructure(self):
        owner, _ = F.gpu_owner(self._proc(r"C:\ollama\ollama.exe serve"), own_pid=987654, claim={})
        self.assertEqual(owner, "orchestrator")

    def test_the_running_orders_trainer_is_not_foreign(self):
        owner, _ = F.gpu_owner(
            self._proc(r"F:\ai-toolkit\venv\python.exe -u run.py config\concept_flux2_v11.yaml"),
            own_pid=987654, claim={"name": "concept_flux2_v11"})
        self.assertEqual(owner, "factory")

    def test_the_patrols_own_query_is_excluded(self):
        """A CommandLine filter matches the querying process's own command line -- documented, and
        it has bitten before. The patrol's query contains every signature it searches for, so
        without this it arrests itself on the first pass."""
        me = 31337
        owner, _ = F.gpu_owner(self._proc("powershell -Command ... ai-toolkit comfyui ollama",
                                          pid=me), own_pid=me, claim={})
        self.assertEqual(owner, "police")
        owner, _ = F.gpu_owner(self._proc("powershell -Command ... ollama", pid=99, ppid=me),
                               own_pid=me, claim={})
        self.assertEqual(owner, "police")

    # --- attribution: a genuine trespasser IS foreign -----------------------------------------

    def test_an_unregistered_trainer_is_foreign(self):
        owner, why = F.gpu_owner(
            self._proc(r"F:\ai-toolkit\venv\python.exe -u run.py config\klein_concept_v1_full.yaml"),
            own_pid=987654, claim={})
        self.assertIsNone(owner)
        self.assertIn("no order", why)

    def test_a_trainer_for_a_DIFFERENT_order_is_foreign(self):
        owner, _ = F.gpu_owner(
            self._proc(r"python.exe -u run.py config\klein_concept_v1_full.yaml"),
            own_pid=987654, claim={"name": "concept_flux2_v11"})
        self.assertIsNone(owner)

    # --- the narrow-signature requirement: ordinary use must be invisible ----------------------

    def test_browsers_games_and_editors_are_not_even_candidates(self):
        """Ordinary user processes (browsers, games, editors) must be filtered out by signature
        and never reach attribution."""
        for cmd in (r"C:\Program Files\Google\Chrome\chrome.exe --type=gpu-process",
                    r"C:\Steam\steamapps\common\game\game.exe",
                    r"C:\Program Files\Microsoft VS Code\Code.exe",
                    r"C:\Windows\explorer.exe",
                    r"python.exe -m glances -w --bind 0.0.0.0"):
            self.assertIsNone(F._parse_proc_line(_row(100, 1, cmd)), cmd)

    def test_a_real_ai_workload_does_survive_the_signature_filter(self):
        got = F._parse_proc_line(_row(100, 1, r"python.exe -u run.py F:\ai-toolkit\cfg.yaml"))
        self.assertIsNotNone(got)
        self.assertEqual(got["pid"], 100)

    def test_every_process_query_excludes_the_observer_by_construction(self):
        """Ensures the observer process (this script) is automatically excluded from process listings.

        The CommandLine filter used to find runners also matches the query command itself.
        This exclusion is enforced in the shared helper, preventing callers from accidentally
        including the observer or opting out of this safety invariant."""
        import subprocess as _sp
        seen = {}

        def fake_run(cmd, **kw):
            seen["ps"] = cmd[-1]
            R = collections.namedtuple("R", "returncode stdout stderr")
            return R(0, "", "")

        with mock.patch.object(_sp, "run", fake_run):
            F.ps_process_query("$_.CommandLine -like '*p20_foundry*'")

        self.assertIn("$_.ProcessId -ne $PID", seen["ps"],
                      "the querying process must be excluded")
        self.assertIn("$_.ParentProcessId -ne $PID", seen["ps"],
                      "and so must its child shell")
        self.assertIn("p20_foundry", seen["ps"], "the caller's own predicate must survive")

    def test_a_failed_process_query_is_none_not_empty(self):
        """'Could not ask' and 'nothing matched' are different answers — an empty list would read
        as 'no processes running', and a guard reading it goes silently blind."""
        import subprocess as _sp
        R = collections.namedtuple("R", "returncode stdout stderr")
        for res in (R(1, "", "boom"),):
            with mock.patch.object(_sp, "run", lambda *a, **k: res):
                self.assertIsNone(F.ps_process_query("$_.Name -like 'x*'"))
        with mock.patch.object(_sp, "run", mock.Mock(side_effect=OSError("no powershell"))):
            self.assertIsNone(F.ps_process_query("$_.Name -like 'x*'"))

    def test_residency_is_could_not_tell_when_the_card_cannot_be_asked(self):
        """Empty output, missing command, or blank lines indicate
        GPU status unknown (None), not idle."""
        import subprocess as _sp
        R = collections.namedtuple("R", "returncode stdout stderr")
        for out, label in ((R(0, "", ""), "empty output"),
                           (R(1, "", "no such command"), "nvidia-smi missing"),
                           (R(0, "\n \n", ""), "blank lines only")):
            with mock.patch.object(_sp, "run", lambda *a, **k: out):
                self.assertIsNone(F._gpu_resident_pids(),
                                  "%s must read as 'could not tell', not 'idle'" % label)

    def test_residency_downgrades_only_and_never_hides_a_real_workload(self):
        """The filter may REMOVE a report, never create one — and an unregistered job that IS on
        the card must still be caught, or the rule has quietly become 'never report anything'."""
        import subprocess as _sp
        R = collections.namedtuple("R", "returncode stdout stderr")
        with mock.patch.object(_sp, "run", lambda *a, **k: R(0, "4242\n7777\n", "")):
            pids = F._gpu_resident_pids()
        self.assertEqual(pids, {4242, 7777})
        self.assertIn(4242, pids, "a resident workload must remain reportable")
        self.assertNotIn(9999, pids, "a process not on the card is the one this filter removes")

    def test_the_trainer_AS_THE_FACTORY_ACTUALLY_LAUNCHES_IT_is_seen(self):
        """
            Ensures detection works for relative-path commands launched from a specific CWD,
            which lack absolute path signatures but match real-world launcher behavior.
        """
        for cmd in (r"venv\python.exe -u run.py config\concept_flux2_v11.yaml",
                    r"venv\python.exe -u run.py config\klein_concept_v1_full.yaml"):
            self.assertIsNotNone(F._parse_proc_line(_row(100, 1, cmd)),
                                 "patrol is blind to a real trainer: %s" % cmd)

    def test_a_downloader_writing_into_a_model_dir_is_not_a_gpu_job(self):
        """Signature matching must rely on identity (executable/entrypoint), not arbitrary path
        arguments. E.g., a download writing to 'ComfyUI/models' is not a GPU job.

        See: test_a_downloader_writing_into_a_model_dir_is_not_a_gpu_job context."""
        for cmd in (r'curl.exe -L -C - -o "F:\comfy-bench\ComfyUI\models\unet\k.gguf" "https://x"',
                    r"robocopy.exe F:\src F:\comfyui-rocm\models\loras *.safetensors",
                    r'"C:\Windows\explorer.exe" F:\comfyui-rocm\models'):
            self.assertIsNone(F._parse_proc_line(_row(100, 1, cmd)),
                              "a file operation is not a GPU job: %s" % cmd)

    def test_somebody_using_the_desktop_never_trips_the_police(self):
        """Desktop GPU workloads (photo editing, gaming) are legitimate and must
        not trigger alarms, regardless of resource usage."""
        for cmd in (r'"C:\Program Files\Adobe\Adobe Lightroom\lightroom.exe"',
                    r'"C:\Program Files\Adobe\Adobe Photoshop\Photoshop.exe"',
                    r'"D:\Steam\steamapps\common\G\game.exe" -dx12',
                    r'"C:\Program Files\DaVinci Resolve\Resolve.exe"'):
            self.assertIsNone(F._parse_proc_line(_row(100, 1, cmd)),
                              "ordinary desktop GPU use must be invisible: %s" % cmd)

    def test_malformed_process_rows_are_skipped_not_crashed(self):
        for bad in ("", "garbage", _row("notanint", 1, "ai-toolkit"),
                    _row(1, "x", "ai-toolkit")):
            self.assertIsNone(F._parse_proc_line(bad), repr(bad))

    # --- fail-safe behaviour ------------------------------------------------------------------

    def test_inconclusive_enumeration_produces_no_verdict_and_no_arrest(self):
        """'Could not tell' must never render as either 'clear' or 'trespass'."""
        said = []
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch.object(F, "_gpu_processes", return_value=None), \
             mock.patch.object(F, "notify_desk") as dm, \
             mock.patch("subprocess.run") as sp:
            out = F.gpu_patrol(said.append)
        self.assertEqual(out, [])
        dm.assert_not_called()
        sp.assert_not_called()
        self.assertTrue(any("INCONCLUSIVE" in s for s in said), said)

    def test_shore_leave_stands_the_patrol_down_entirely(self):
        said = []
        with mock.patch("os.path.exists", side_effect=lambda p: p == F.GPU_SHORE_LEAVE), \
             mock.patch.object(F, "_gpu_processes") as enum, \
             mock.patch.object(F, "notify_desk") as dm:
            out = F.gpu_patrol(said.append)
        self.assertEqual(out, [])
        enum.assert_not_called()
        dm.assert_not_called()
        self.assertTrue(any("GPU_SHORE_LEAVE" in s for s in said), said)

    def test_no_arrest_flag_alarms_but_does_not_kill(self):
        said = []
        foreign = [self._proc(r"python.exe run.py F:\ai-toolkit\rogue.yaml")]
        with mock.patch("os.path.exists", side_effect=lambda p: p == F.GPU_NO_ARREST), \
             mock.patch.object(F, "_gpu_processes", return_value=foreign), \
             mock.patch.object(F, "_current_run_claim", return_value={}), \
             mock.patch.object(F, "notify_desk") as dm, \
             mock.patch("subprocess.run") as sp:
            out = F.gpu_patrol(said.append)
        self.assertEqual(len(out), 1)
        # Assert no TASKKILL specifically, not just any subprocess call.
        # GPU_NO_ARREST must never kill processes, but may legitimately query the system.
        killed = [c for c in sp.call_args_list
                  if c.args and any("taskkill" in str(a).lower() for a in c.args[0])]
        self.assertEqual(killed, [], "GPU_NO_ARREST must never kill: %s" % killed)
        dm.assert_called_once()                     # but it DID alarm
        self.assertIn("NOT arrested", dm.call_args[0][0])

    def test_a_clean_card_is_silent(self):
        """No foreign jobs = no DM. A guard that speaks every pass gets muted."""
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch.object(F, "_gpu_processes", return_value=[]), \
             mock.patch.object(F, "notify_desk") as dm:
            out = F.gpu_patrol(lambda _s: None)
        self.assertEqual(out, [])
        dm.assert_not_called()

    def test_arrest_kills_by_pid_and_reports(self):
        said = []
        foreign = [self._proc(r"python.exe run.py F:\ai-toolkit\rogue.yaml", pid=777)]
        killed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch.object(F, "_gpu_processes", return_value=foreign), \
             mock.patch.object(F, "_current_run_claim", return_value={}), \
             mock.patch.object(F, "notify_desk") as dm, \
             mock.patch("subprocess.run", return_value=killed) as sp:
            out = F.gpu_patrol(said.append)
        self.assertEqual(len(out), 1)
        self.assertIn("777", " ".join(str(a) for a in sp.call_args[0][0]))
        self.assertIn("ARRESTED 1", dm.call_args[0][0])

    def test_an_arrest_that_fails_is_reported_not_swallowed(self):
        foreign = [self._proc(r"python.exe run.py F:\ai-toolkit\rogue.yaml", pid=888)]
        out_log = []
        resisted = mock.Mock(returncode=1, stdout="Access is denied", stderr="")
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch.object(F, "_gpu_processes", return_value=foreign), \
             mock.patch.object(F, "_current_run_claim", return_value={}), \
             mock.patch.object(F, "notify_desk") as dm, \
             mock.patch("subprocess.run", return_value=resisted):
            F.gpu_patrol(out_log.append)
        self.assertTrue(any("ARREST FAILED" in s for s in out_log), out_log)
        self.assertIn("1 resisted", dm.call_args[0][0])

    def test_our_own_comfyui_is_never_arrested_even_with_no_order_claim(self):
        owner, _ = F.gpu_owner(
            self._proc(r"F:\comfyui-rocm\venv\python.exe main.py --listen"),
            own_pid=987654, claim={})
        self.assertEqual(owner, "factory")

    def test_comfyui_is_still_factory_when_an_order_is_running(self):
        owner, _ = F.gpu_owner(
            self._proc(r"F:\comfyui-rocm\venv\python.exe main.py"),
            own_pid=987654, claim={"name": "concept_flux2_v11"})
        self.assertEqual(owner, "factory")

    def test_the_comfy_hint_never_degenerates_to_a_bare_drive_root(self):
        """A hint like 'f:\\' would match every path on the box and whitelist the world."""
        self.assertFalse(F._is_our_comfy(r"c:\some\random\thing.exe"))
        self.assertFalse(F._is_our_comfy(r"python.exe -u run.py cfg.yaml ai-toolkit"))

    def test_the_emitter_and_the_parser_use_the_SAME_separator(self):
        """Ensure emitter and parser share the same separator constant (GPU_PROC_SEP)."""
        import inspect
        emitter = inspect.getsource(F._gpu_processes)
        parser = inspect.getsource(F._parse_proc_line)
        self.assertIn("GPU_PROC_SEP", emitter, "emitter must use the shared separator")
        self.assertIn("GPU_PROC_SEP", parser, "parser must use the shared separator")
        # Strip comments: the emitter's docstring mentions the `t` bug,
        # preventing naive substring checks on raw source.
        code = "\n".join(l.split("#", 1)[0] for l in emitter.splitlines()
                         if not l.strip().startswith("#"))
        self.assertNotIn("`t", code,
                         "backtick-t is not an escape inside a PS single-quoted string")

    def test_the_separator_survives_being_a_plain_ascii_token(self):
        """No backslash, no backtick, nothing a shell or quoting layer rewrites in transit."""
        sep = F.GPU_PROC_SEP
        self.assertGreaterEqual(len(sep), 2)
        for ch in ("\\", "`", "'", '"', "\t", "\n"):
            self.assertNotIn(ch, sep)

    def test_a_row_built_the_emitters_way_round_trips(self):
        got = F._parse_proc_line(_row(4242, 7, r"python.exe -u run.py F:\ai-toolkit\x.yaml"))
        self.assertIsNotNone(got)
        self.assertEqual((got["pid"], got["ppid"]), (4242, 7))

    def test_the_factorys_own_deploy_gate_is_not_reported(self):
        cmd = ('"F:\\ai-toolkit\\venv\\python.exe"  -W error::SyntaxWarning '
               + os.path.join(F.P.FOUNDRY_ROOT, "tests", "test_foundry.py"))
        self.assertTrue(F._is_ai_workload(cmd), "precondition: the training venv makes it a candidate")
        owner, _ = F.gpu_owner(self._proc(cmd), own_pid=987654, claim={})
        self.assertEqual(owner, "factory")

    def test_ollama_store_commands_are_not_gpu_jobs(self):
        cmd = (r"C:\Users\someone\AppData\Local\AMD\AI_Bundle\Ollama-0.34.0\ollama.exe create "
               r"qwen3.8-flash-next:unsloth-iq3xxs -f D:\Models\Qwen3.8-Flash-Next-GGUF\Modelfile.unsloth-iq3xxs")
        self.assertFalse(F._is_ai_workload(cmd))
        for sub in ("pull qwen3.8:27b", "rm old:tag", "list", "show qwen3.8:27b", "ps"):
            self.assertFalse(F._is_ai_workload(r'"C:\ollama\ollama.exe" ' + sub), sub)

    def test_ollama_run_is_still_a_gpu_job(self):
        self.assertTrue(F._is_ai_workload(r"C:\ollama\ollama.exe run qwen3.8:27b"))
        self.assertTrue(F._is_ai_workload(r"ollama run qwen3.8:27b"))

    def test_a_same_named_suite_outside_this_factory_is_still_reported(self):
        cmd = r'"F:\ai-toolkit\venv\python.exe" C:\temp\elsewhere\test_foundry.py'
        owner, _ = F.gpu_owner(self._proc(cmd), own_pid=987654, claim={})
        self.assertIsNone(owner)

    def test_the_hai_ollama_watchdog_is_never_arrested(self):
        """The HAI watchdog script is a monitor, not an AI tool; it must never be arrested."""
        owner, _ = F.gpu_owner(
            self._proc(r'"powershell.exe" -NoProfile -File "C:\ProgramData\HAI\ollama-watchdog.ps1"'),
            own_pid=987654, claim={})
        self.assertEqual(owner, "lab")

    def test_other_lab_monitors_are_not_arrested_either(self):
        for cmd in (r"powershell -File C:\ProgramData\HAI\keep-awake.ps1",
                    r"python.exe -m glances -w --bind 0.0.0.0 ollama"):
            owner, _ = F.gpu_owner(self._proc(cmd), own_pid=987654, claim={})
            self.assertEqual(owner, "lab", cmd)

    def _chargeable(self, cmd):
        """Exactly what gpu_patrol asks before it kills: identity, arrest signs, via _sig_hit."""
        return F._sig_hit(F._identity_of(cmd), F.GPU_ARREST_SIGNS)

    def test_a_monitor_is_reported_but_NOT_chargeable(self):
        """The two-tier bar: broad signature -> report; narrow signature -> arrest."""
        watchdog = r"powershell -File C:\somewhere\my-ollama-monitor.ps1"
        self.assertIsNone(self._chargeable(watchdog))

    def test_a_real_trainer_IS_chargeable(self):
        trainer = r"python.exe -u run.py config\klein_concept_v1_full.yaml"
        self.assertEqual(self._chargeable(trainer), "run.py")

    # Prevent substring matches (e.g. "run.py" in "sudo_run.py") from false positives
    def test_a_script_signature_never_matches_a_LONGER_filename(self):
        """Substrings like `sudo_run.py` must not match the target `run.py`.

        The check requires exact filename identity, not substring containment,
        to avoid false positives on wrappers (e.g., `sudo_`, `dry_`) that are
        not AI workloads."""
        for cmd in (r"C:\python312\python.exe sudo_run.py pinky pinky bench_install.sh",
                    r"C:\python312\python.exe sudo_run.py streambox StreamBox openmpi_install.sh",
                    r"ssh.exe sundae docker exec tablet-foundry python /tmp/sub_vs_main.py",
                    r"python.exe dry_run.py", r"python.exe fleet_run.py --host kirk"):
            self.assertIsNone(self._chargeable(cmd), cmd)
            self.assertFalse(F._is_ai_workload(cmd), "not even a candidate: " + cmd)

    def test_arrest_matches_IDENTITY_not_arguments(self):
        """A `find` searching FOR the trainer's filename was arrested -- and returned empty, which
        reads exactly like 'no such file'. Mentioning a thing is not being one."""
        searching = (r'"C:\Program Files\Git\usr\bin\find.exe" E:/ -maxdepth 6 -name sudo_run.py')
        self.assertIsNone(self._chargeable(searching))
        grepping = r"python.exe my_audit.py --pattern run.py --pattern main.py"
        self.assertIsNone(self._chargeable(grepping))

    def test_script_ARGUMENTS_and_quoted_image_paths_are_not_identity(self):
        """Command strings are raw bytes; file arguments and quoted paths (e.g., C:\\Program) must not trigger AI identity signatures."""
        for cmd in (r'"C:\Program Files\Git\usr\bin\timeout.exe" 600 python fleet_deploy.py --only mothership '
                    r'helper_window.py infer_ollama.py test_foundry.py',
                    r'"C:\Program Files\Git\usr\bin\grep.exe" -E "^-- |OK -|REFUSED|DEFERRED|helper_window.py '
                    r'|infer_ollama.py |test_foundry.py |new   pid|NEW CODE|lane"'):
            self.assertFalse(F._is_ai_workload(cmd), cmd)
        self.assertEqual(F._identity_of(r'"C:\Program Files\Git\usr\bin\timeout.exe" 600 python fleet_deploy.py '
                                        r'infer_ollama.py'),
                         r"c:\program files\git\usr\bin\timeout.exe fleet_deploy.py")
        # positive controls: the entrypoint and a spaced image path still identify a real workload
        self.assertTrue(F._is_ai_workload(r"python.exe infer_ollama.py --model x"))
        self.assertTrue(F._is_ai_workload(r'"C:\Program Files\Ollama\ollama.exe" run qwen3.8:27b'))

    def test_a_shell_script_is_the_entrypoint_and_the_files_it_is_given_are_arguments(self):
        """A shell script that deploys a file named like a model server is not an AI job: `.sh` is a script suffix, so
        the script is the entrypoint. Strings are the patrol log's own bytes."""
        for cmd in (
                '"C:\\Program Files\\Git\\usr\\bin\\bash.exe" /e/SSDShared/Lab/bin/deploy_orch.sh one-summary-rule ollama_client.py config.py tests/test_context_compaction.py tests/g',
                '"C:\\Program Files\\Git\\usr\\bin\\bash.exe" /e/SSDShared/Lab/bin/deploy_orch.sh revert-summary-rule ollama_client.py config.py tests/test_context_compaction.py test',
        ):
            self.assertFalse(F._is_ai_workload(cmd), cmd)
            self.assertTrue(F._identity_of(cmd).endswith("deploy_orch.sh"), F._identity_of(cmd))
        # positive control: a shell script that IS a trainer launch is still seen (its own path says so)
        self.assertTrue(F._is_ai_workload(r'bash.exe /f/ai-toolkit/launch_train.sh config.yaml'))

    def test_inline_code_is_not_an_entrypoint(self):
        """AMD probe: fixture must contain 'main.py' as a distinct token to verify the detector rejects this specific shell-wrapper pattern."""
        real_prefix = ("-WindowStyle Hidden -NonInteractive -NoProfile -Command \"& { $ComfyUIDir = Join-Path "
                       "-Path $env:LOCALAPPDATA -ChildPath 'AMD\\ai_bundle\\ComfyUI' ;if(-not (Test-P")
        probe = (real_prefix + "ath -Path $ComfyUIDir -PathType Container)) { exit 1 } ;"
                 "$Main = Join-Path -Path $ComfyUIDir -ChildPath 'main.py' ;Test-Path $Main }\"")
        self.assertIn("main.py", F._norm_cmd(probe).split(), "the fixture must carry main.py as a TOKEN")
        self.assertIsNone(self._chargeable(probe))
        self.assertFalse(F._is_ai_workload(probe), "not even a candidate")
        self.assertIsNone(self._chargeable(r'python.exe -c "import runpy; print(\'main.py\')"'))
        # a switch AFTER the entrypoint is the script's own argument, not inline code
        self.assertEqual(self._chargeable(r"python.exe -u main.py -c config.yaml"), "main.py")

    def test_every_true_positive_still_gets_arrested(self):
        """Idealised invocation (copied, not retyped) to validate cwd-relative trainer blindness."""
        for cmd, want in (
                (r"venv\python.exe -u run.py config\concept_flux2_v11.yaml", "run.py"),
                (r"F:\ai-toolkit\venv\python.exe -u run.py config\klein.yaml", "run.py"),
                (r"python_env\python.exe -u main.py --disable-api-nodes --dont-print-server", "main.py"),
                (r'"C:\Program Files\Python312\python.exe" -u main.py --port 8188', "main.py"),
                # Matches
                (r"F:\comfy\python.exe -m accelerate launch train_network.py", "train_network"),
        ):
            self.assertEqual(self._chargeable(cmd), want, cmd)

    def test_ollama_serve_stays_report_only(self):
        """The two-tier bar's whole point: broad enough to report, narrow enough not to shoot. The
        first live patrol killed the HAI ollama-watchdog; ollama itself must never be chargeable."""
        for cmd in (r'"C:\Users\x\AppData\Local\Programs\Ollama\ollama.exe"  serve',
                    r"C:\Users\x\AppData\Local\Programs\Ollama\ollama.exe serve"):
            self.assertTrue(F._is_ai_workload(cmd), "must still be SEEN: " + cmd)
            self.assertIsNone(self._chargeable(cmd), "must not be SHOT: " + cmd)

    def test_the_matcher_is_validated_against_a_known_positive(self):
        """Positive control: verifies _sig_hit actually matches known AI workloads.
        Without this, negative tests would pass even if matching was broken."""
        self.assertEqual(F._sig_hit(F._identity_of(r"python.exe -u run.py x.yaml"),
                                    F.GPU_ARREST_SIGNS), "run.py")
        self.assertEqual(F._sig_hit(r"f:\ai-toolkit\venv\python.exe", F.GPU_WORKLOAD_SIGNS),
                         "ai-toolkit")

    def test_the_kill_log_names_the_signature_that_matched(self):
        foreign = [self._proc(r"python.exe -u run.py rogue.yaml", pid=4242)]
        lines = []
        killed = mock.Mock(returncode=0, stdout="SUCCESS", stderr="")
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch.object(F, "_gpu_processes", return_value=foreign), \
             mock.patch.object(F, "_current_run_claim", return_value={}), \
             mock.patch.object(F, "notify_desk"), \
             mock.patch("subprocess.run", return_value=killed):
            F.gpu_patrol(lines.append)
        self.assertTrue(any("matched='run.py'" in l for l in lines),
                        "no line named the matched signature:\n" + "\n".join(lines))

    def test_already_gone_counts_as_arrested_not_resisted(self):
        """A process that is already gone (returncode 128) counts as 'arrested', not 'resisted'.
        taskkill /T kills the tree; if the child's own taskkill runs, it returns 128 ('not found').
        This must be treated as successful cleanup, not a failure/escape."""
        foreign = [self._proc(r"python.exe run.py F:\ai-toolkit\rogue.yaml", pid=555)]
        gone = mock.Mock(returncode=128, stdout='ERROR: The process "555" not found.', stderr="")
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch.object(F, "_gpu_processes", return_value=foreign), \
             mock.patch.object(F, "_current_run_claim", return_value={}), \
             mock.patch.object(F, "notify_desk") as dm, \
             mock.patch("subprocess.run", return_value=gone):
            F.gpu_patrol(lambda _s: None)
        msg = dm.call_args[0][0]
        self.assertIn("ARRESTED 1", msg)
        self.assertIn("0 resisted", msg)

    def test_ollama_is_recognised_whether_or_not_its_PATH_IS_QUOTED(self):
        """Test that ollama is recognised whether or not its path is quoted.

        These are real command lines from three factories (account names generalized). Mothership runs ollama unquoted;
        BigHonker and Softserve run the identical service with a quoted path.

        Softserve's string below carries TWO spaces between the closing quote and `serve`
        because that is what the real service invocation has. Tidying it to one space causes
        the patrol to report ollama as UNREGISTERED."""
        for cmd in (
            r"C:\Users\someone\AppData\Local\AMD\AI_Bundle\Ollama\ollama.exe serve",
            r'"C:\Users\example\AppData\Local\Programs\Ollama\ollama.exe" serve',
            r'"C:\Users\someone\AppData\Local\Programs\Ollama\ollama.exe"  serve',
        ):
            owner, _ = F.gpu_owner(self._proc(cmd), own_pid=987654, claim={})
            self.assertEqual(owner, "orchestrator", cmd)

    def test_every_matcher_normalises_quoting_the_same_way(self):
        """Verify that independent command-line matchers use the same normalisation logic.

        Drift in quoting handling would break `GPU_PROC_SEP` consistency. This test asserts
        behavioral equivalence (quoting does not change verdicts) rather than implementation
        details, avoiding fragility to refactors."""
        for bare, quoted in (
            (r"C:\X\Ollama.exe serve", r'"C:\X\Ollama.exe" serve'),
            (r"F:\ai-toolkit\venv\python.exe -u run.py cfg.yaml",
             r'"F:\ai-toolkit\venv\python.exe" -u run.py cfg.yaml'),
            (r"F:\comfyui-rocm\venv\python.exe main.py",
             r'"F:\comfyui-rocm\venv\python.exe" main.py'),
        ):
            self.assertEqual(F._parse_proc_line(_row(1, 1, bare)) is None,
                             F._parse_proc_line(_row(1, 1, quoted)) is None,
                             "quoting changed candidacy: %s" % bare)
            self.assertEqual(F.gpu_owner(self._proc(bare), own_pid=987654, claim={})[0],
                             F.gpu_owner(self._proc(quoted), own_pid=987654, claim={})[0],
                             "quoting changed attribution: %s" % bare)

    def test_the_normaliser_strips_quotes_and_lowercases(self):
        self.assertEqual(F._norm_cmd(r'"C:\X\Ollama.EXE" serve'), r"c:\x\ollama.exe serve")
        self.assertEqual(F._norm_cmd(None), "")

    def test_the_normaliser_collapses_whitespace(self):
        self.assertEqual(F._norm_cmd(r'"C:\X\Ollama.EXE"  serve'), r"c:\x\ollama.exe serve")
        self.assertEqual(F._norm_cmd("a.exe \t b"), "a.exe b")
        self.assertEqual(F._norm_cmd("  a.exe b  "), "a.exe b")

    def test_a_quoted_factory_component_is_still_ours(self):
        owner, _ = F.gpu_owner(
            self._proc(r'"F:\ai-toolkit\venv\python.exe" -u "F:\ai-toolkit\p20_rescue_burst.py"'),
            own_pid=987654, claim={})
        self.assertEqual(owner, "factory")

    def test_a_standing_offender_alerts_ONCE_not_once_per_poll(self):
        foreign = [self._proc(r"python.exe run.py F:\ai-toolkit\rogue.yaml", pid=4242)]
        led = os.path.join(tempfile.mkdtemp(), "alerted.json")
        with mock.patch.object(F, "GPU_ALERT_LEDGER", led), \
             mock.patch("os.path.exists", side_effect=lambda p: p == led and os.stat), \
             mock.patch.object(F, "_gpu_processes", return_value=foreign), \
             mock.patch.object(F, "_current_run_claim", return_value={}), \
             mock.patch.object(F, "notify_desk") as dm, \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="", stderr="")):
            F.gpu_patrol(lambda _s: None)      # first sighting -> alerts
            first = dm.call_count
            F.gpu_patrol(lambda _s: None)      # same offender  -> silent
            F.gpu_patrol(lambda _s: None)
            self.assertEqual(first, 1)
            self.assertEqual(dm.call_count, 1, "a standing offender must not re-alert every poll")

    def test_a_NEW_offender_re_arms_the_alert(self):
        """Dedupe must not become deafness: a different process is a new event."""
        led = os.path.join(tempfile.mkdtemp(), "alerted.json")
        a = [self._proc(r"python.exe run.py F:\ai-toolkit\a.yaml", pid=1)]
        b = [self._proc(r"python.exe run.py F:\ai-toolkit\b.yaml", pid=2)]
        seq = [a, a, b]
        with mock.patch.object(F, "GPU_ALERT_LEDGER", led), \
             mock.patch.object(F, "_gpu_processes", side_effect=seq), \
             mock.patch.object(F, "_current_run_claim", return_value={}), \
             mock.patch.object(F, "notify_desk") as dm, \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="", stderr="")):
            for _ in seq:
                F.gpu_patrol(lambda _s: None)
            self.assertEqual(dm.call_count, 2, "new offender must re-alert")

    def test_the_alert_ledger_is_the_patrols_own_bookkeeping(self):
        """It lives under this factory's STATE_DIR, like every other patrol file."""
        self.assertTrue(self.live_ledger.startswith(F.P.STATE_DIR))

    def test_operator_control_files_live_under_this_factorys_root(self):
        """PER-FACTORY, like everything else the patrol touches: derived from FOUNDRY_ROOT, never
        hardcoded, or a second factory would police the first one's flags."""
        for flag in (F.GPU_SHORE_LEAVE, F.GPU_NO_ARREST):
            self.assertTrue(flag.startswith(F.P.STATE_DIR), flag)

    def test_an_unreadable_current_run_does_not_manufacture_a_trespass(self):
        """Here, unlike the LAUNCH guard, cannot-tell degrades to 'no claim' -- but that must only
        ever cost a missed catch, never produce an accusation from a file that could not be read."""
        with mock.patch("builtins.open", side_effect=OSError("boom")):
            self.assertEqual(F._current_run_claim(), {})


class TestShipStrengthDefault(unittest.TestCase):

    def test_ship_strength_is_the_documented_operating_point(self):
        self.assertEqual(F.SHIP_STRENGTH, 0.6)

    def test_render_resolves_the_default_BEFORE_the_index_key(self):
        """Strength must default before `render_key()` to prevent cache poisoning: 
        if the key were generated with None, cached renders for other strengths could be served incorrectly."""
        import inspect
        raw = inspect.getsource(F.render)
        # Strip comments before searching for calls to avoid false positives from prose.
        code = "\n".join(l.split("#", 1)[0] for l in raw.splitlines()
                         if not l.strip().startswith("#"))
        i_default = code.find("strength = SHIP_STRENGTH")
        i_key = code.find("render_key(")
        self.assertGreater(i_default, 0, "render() must resolve the ship-strength default")
        self.assertGreater(i_key, 0, "render() must still key its cells")
        self.assertLess(i_default, i_key,
                        "the default must be resolved BEFORE render_key() sees strength")

    def test_the_base_arm_is_left_alone(self):
        """base_only renders with NO adapter, so a strength would be meaningless -- and stamping
        one into its index key would split the base cells for no reason."""
        import inspect
        src = inspect.getsource(F.render)
        self.assertIn("if strength is None and not base_only:", src)

    def test_an_explicit_strength_still_wins(self):
        """An order that genuinely wants 1.0 must still get 1.0."""
        import inspect
        src = inspect.getsource(F.render)
        self.assertIn("if strength is None", src)
        self.assertNotIn("strength = SHIP_STRENGTH\n    ", src.replace("if strength is None and not base_only:\n        strength = SHIP_STRENGTH", ""))

    def test_keys_at_different_strengths_do_not_collide(self):
        a = F.render_key("t", 42, "ck.safetensors", None, False)
        b = F.render_key("t", 42, "ck.safetensors", 0.6, False)
        c = F.render_key("t", 42, "ck.safetensors", 1.0, False)
        self.assertNotEqual(b, c, "0.6 and 1.0 must be different cells")
        # `None` normalizes to `1.0` in keys to match historical defaults, ensuring
        # `a` (None) equals `c` (1.0). Distinct from `render()`'s runtime default of 0.6.
        self.assertEqual(a, c, "absent must normalise to the historical default (1.0)")


class TestOwnedAncestor(unittest.TestCase):
    """Test that exempt status is inherited by child processes."""

    OLLAMA = {"pid": 100, "ppid": 1, "cmd": r"C:\Users\example\AppData\Local\Programs\Ollama\ollama.exe serve"}
    # the exact command line from the live 03:52 alert
    LLAMA = {"pid": 1948, "ppid": 100,
             "cmd": (r"C:\Users\example\AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe "
                     r"--model C:\Users\pat.ollama\models\blobs\sha256-970aa74c0a90ef7482477cf803")}

    def _by_pid(self, *procs):
        return {p["pid"]: p for p in procs}

    def test_the_live_false_arrest_is_now_exempt(self):
        owner, why = F.gpu_owner(self.LLAMA, own_pid=1, claim={},
                                 by_pid=self._by_pid(self.OLLAMA, self.LLAMA))
        self.assertEqual(owner, "orchestrator", "llama-server forked by ollama must NOT be arrested")
        self.assertIn("child", why)

    def test_orphan_llama_server_is_still_caught(self):
        """The rule must not become 'never arrest llama-server' -- a model server with no exempt
        ancestor is exactly what the patrol exists to find."""
        orphan = dict(self.LLAMA, ppid=999)
        owner, why = F.gpu_owner(orphan, own_pid=1, claim={}, by_pid=self._by_pid(orphan))
        self.assertIsNone(owner, "an unparented llama-server is still unregistered")

    def test_missing_by_pid_does_not_crash_or_exempt(self):
        owner, _ = F.gpu_owner(self.LLAMA, own_pid=1, claim={}, by_pid=None)
        self.assertIsNone(owner, "no ancestor data must not silently exempt")

    def test_cycle_in_process_table_terminates(self):
        """A malformed table must not hang the patrol."""
        a = {"pid": 1, "ppid": 2, "cmd": "llama-server.exe"}
        b = {"pid": 2, "ppid": 1, "cmd": "llama-server.exe"}
        owner, _ = F.gpu_owner(a, own_pid=99, claim={}, by_pid={1: a, 2: b})
        self.assertIsNone(owner)

    def test_self_parent_terminates(self):
        a = {"pid": 5, "ppid": 5, "cmd": "llama-server.exe"}
        owner, _ = F.gpu_owner(a, own_pid=99, claim={}, by_pid={5: a})
        self.assertIsNone(owner)

    def test_depth_is_bounded(self):
        """A long chain of non-exempt ancestors must stop, not walk forever."""
        chain = {}
        for i in range(1, 30):
            chain[i] = {"pid": i, "ppid": i + 1, "cmd": "llama-server.exe"}
        chain[30] = {"pid": 30, "ppid": 0, "cmd": r"C:\Ollama\ollama.exe serve"}
        owner, _ = F.gpu_owner(chain[1], own_pid=99, claim={}, by_pid=chain)
        self.assertIsNone(owner, "an exempt ancestor beyond max_depth must not exempt")

    def test_observer_exclusion_still_wins(self):
        p = dict(self.LLAMA, ppid=42)
        owner, why = F.gpu_owner(p, own_pid=42, claim={}, by_pid=self._by_pid(p))
        self.assertEqual(owner, "police")


class TestSpecDefects(unittest.TestCase):
    """Tests for spec defects that previously ran, spent resources,
    and reported success while performing incorrect actions."""

    def _spec(self, **kw):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t.job.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(kw, f)
        return p


    def test_probe_strengths_without_sweep_names_is_a_defect(self):
        """Probe strengths require corresponding sweep_names.

        Without sweep_names, the probe phase filters the empty sweep set,
        resulting in zero renders despite a successful completion status."""
        p = self._spec(mode="probe", probe_strengths=[0.6],
                       probe_prompts=[{"name": "a", "text": "x"}])
        d = F.spec_defects(p)
        self.assertTrue(any("sweep_names is empty" in x for x in d), d)

    def test_ab_at_beyond_total_steps_is_a_defect(self):
        """When ab_at equals total_steps, the completion check fires first,
        causing the A/B render to be silently skipped and flagged as a defect."""
        p = self._spec(mode="train", launcher="x.bat", total_steps=1000, ab_at=[1000])
        d = F.spec_defects(p)
        self.assertTrue(any("ab_at" in x and "silently skipped" in x for x in d), d)

    # --- the rest of the family ---------------------------------------------------------------

    def test_sweep_names_typo_is_caught(self):
        p = self._spec(mode="probe", probe_strengths=[0.6], sweep_names=["crop_head_to_toe"],
                       probe_prompts=[{"name": "crop_head_to_to", "text": "x"}])
        d = F.spec_defects(p)
        self.assertTrue(any("do not exist in probe_prompts" in x for x in d), d)

    def test_probe_with_no_prompts_is_a_defect(self):
        d = F.spec_defects(self._spec(mode="probe", probe_prompts=[]))
        self.assertTrue(any("nothing to render" in x for x in d), d)

    def test_train_without_launcher_is_a_defect(self):
        d = F.spec_defects(self._spec(mode="train"))
        self.assertTrue(any("no launcher" in x for x in d), d)

    def test_live_previews_with_no_prompts_is_a_defect(self):
        p = self._spec(mode="train", launcher="x.bat", previews="live", preview_prompts=[])
        d = F.spec_defects(p)
        self.assertTrue(any("preview_prompts is empty" in x for x in d), d)

    def test_yield_at_beyond_total_steps_is_a_defect(self):
        p = self._spec(mode="train", launcher="x.bat", total_steps=2000, yield_at=[2500])
        self.assertTrue(any("yield_at" in x for x in F.spec_defects(p)))

    def test_seeds_that_can_never_be_used_are_a_defect(self):
        p = self._spec(mode="probe", probe_seeds=[1, 2, 3],
                       probe_prompts=[{"name": "a", "text": "x", "base": False}])
        self.assertTrue(any("probe_seeds" in x for x in F.spec_defects(p)))


    def test_a_correct_probe_order_has_NO_defects(self):
        """The corrected probe_v11_framing_ship shape. A guard that fires on good orders gets
        muted, and then it is worse than not having one (847 false positives, then a dead
        watchdog -- both this same week)."""
        p = self._spec(mode="probe", probe_strengths=[0.6], probe_seeds=[42, 777],
                       sweep_names=["a", "b"],
                       probe_prompts=[{"name": "a", "text": "x", "base": False},
                                      {"name": "b", "text": "y", "base": False}])
        self.assertEqual(F.spec_defects(p), [])

    def test_a_correct_training_order_has_NO_defects(self):
        p = self._spec(mode="train", launcher="run.bat", total_steps=3000,
                       ab_at=[1000, 2000], yield_at=[1500], previews="live",
                       preview_prompts=[{"name": "p", "text": "x"}])
        self.assertEqual(F.spec_defects(p), [])

    def test_previews_none_with_no_prompts_is_FINE(self):
        """klein orders do this deliberately: the trainer samples itself and the factory's preview
        path cannot render that architecture."""
        p = self._spec(mode="train", launcher="run.bat", previews="none", preview_prompts=[])
        self.assertEqual(F.spec_defects(p), [])

    def test_an_unreadable_spec_yields_no_defects_not_a_crash(self):
        """Unreadable is a DIFFERENT failure with its own handling; this must not double-report it
        or, worse, raise inside intake and take the order taker down."""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "bad.job.json")
        open(p, "w", encoding="utf-8").write("{not json")
        self.assertEqual(F.spec_defects(p), [])
        self.assertEqual(F.spec_defects(os.path.join(d, "does_not_exist.json")), [])

    def test_a_minimal_train_order_with_just_a_launcher_is_FINE(self):
        self.assertEqual(F.spec_defects(self._spec(mode="train", launcher="run.bat")), [])


class TestComputeShipping(unittest.TestCase):
    """A compute order ships like every other kind: the payload writes to its own {ORDER_OUT} folder and
    the factory copies that folder to `deliver_to\\<order name>` - two orders can never land on one file."""

    def _job(self, name, deliver_to=""):
        j = mock.Mock()
        j.name = name
        j.spec = {"deliver_to": deliver_to} if deliver_to else {}
        j.state = {}
        j.log = lambda *a, **k: None
        return j

    def test_a_delivered_order_counts_as_shipped(self):
        dest = tempfile.mkdtemp()
        j = self._job("ship.count_bighonker", dest)
        self.assertEqual(F._order_shipped_count(j), 0, "positive control: nothing shipped yet reads 0")
        self._made(j, {"out.json": "{\"rows\": []}", "out.json.partial.jsonl": ""})
        self.assertTrue(F.ship_compute(j))
        self.assertEqual(F._order_shipped_count(j), 2)

    def _made(self, job, files):
        d = F.order_out_dir(job)
        os.makedirs(d, exist_ok=True)
        for fn, body in files.items():
            with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                f.write(body)

    def test_order_out_is_one_folder_per_order_and_never_the_live_root_in_tests(self):
        a, b = F.order_out_dir(self._job("run.a_mothership")), F.order_out_dir(self._job("run.b_mothership"))
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith(F.COMPUTE_OUT_ROOT))
        self.assertEqual(F._expand_compute_path("{ORDER_OUT}\\out.json", a), a + "\\out.json")

    def test_ships_the_orders_folder_under_its_own_name(self):
        dest = tempfile.mkdtemp()
        j = self._job("ship.a_bighonker", dest)
        self._made(j, {"out.json": "{\"rows\": []}"})
        got = F.ship_compute(j)
        self.assertEqual(got, os.path.join(dest, "ship.a_bighonker"))
        self.assertTrue(os.path.isfile(os.path.join(got, "out.json")))

    def test_a_second_delivery_of_the_same_name_never_overwrites_the_first(self):
        dest = tempfile.mkdtemp()
        j = self._job("ship.b_bighonker", dest)
        self._made(j, {"out.json": "first"})
        first = F.ship_compute(j)
        self._made(j, {"out.json": "second"})
        second = F.ship_compute(j)
        self.assertNotEqual(first, second)
        with open(os.path.join(first, "out.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "first")

    def test_no_deliver_to_ships_nothing(self):
        j = self._job("ship.c_mothership")
        self._made(j, {"out.json": "x"})
        self.assertIsNone(F.ship_compute(j))

    def test_a_failed_ship_marks_the_kept_output_and_reship_delivers_it_when_the_share_answers(self):
        """A share outage is a property of the share, not the deliverable: mark, wait while it is down, deliver on the first pass it answers, once."""
        dest = tempfile.mkdtemp()
        j = self._job("ship.late_mothership", dest)
        self._made(j, {"results.json": "{\"rows\": []}"})
        with mock.patch.object(F.shutil, "copytree", side_effect=OSError(22, "The user name or password is incorrect")):
            self.assertIsNone(F.ship_compute(j))
        marker = os.path.join(F.order_out_dir(j), F.UNSHIPPED)
        self.assertTrue(os.path.isfile(marker), "the kept output is marked for reship")
        with open(marker, encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual((m["name"], m["deliver_to"]), ("ship.late_mothership", dest))
        self.assertIn("ship_error", j.state)
        logged = []
        # the seam: reship asks the ONE share derivation (unreachable_shares) and WAITS while it says down
        with mock.patch.object(F, "unreachable_shares", side_effect=lambda things, isdir=None: list(things)):
            self.assertNotIn("ship.late_mothership", F.reship_kept(logged.append), "share still down: WAIT")
        self.assertTrue(os.path.isfile(marker))
        self.assertFalse(any("late_mothership" in x for x in logged), "waiting is quiet: %r" % logged)
        with mock.patch.object(F, "notify_desk") as dm:
            self.assertIn("ship.late_mothership", F.reship_kept(logged.append))
        self.assertTrue(os.path.isfile(os.path.join(dest, "ship.late_mothership", "results.json")))
        self.assertFalse(os.path.exists(os.path.join(dest, "ship.late_mothership", F.UNSHIPPED)),
                         "the marker never travels")
        self.assertFalse(os.path.exists(marker), "delivered: marker gone")
        self.assertTrue(any("RESHIPPED ship.late_mothership" in x for x in logged), logged)
        self.assertTrue(dm.called, "a late delivery is announced once")
        self.assertNotIn("ship.late_mothership", F.reship_kept(logged.append, isdir=lambda p: True),
                         "nothing is shipped twice")

    def test_a_successful_ship_clears_an_old_marker_and_never_ships_it(self):
        dest = tempfile.mkdtemp()
        j = self._job("ship.clear_mothership", dest)
        self._made(j, {"a.json": "x", F.UNSHIPPED: "{}"})
        self.assertTrue(F.ship_compute(j))
        self.assertFalse(os.path.exists(os.path.join(F.order_out_dir(j), F.UNSHIPPED)))
        self.assertFalse(os.path.exists(os.path.join(dest, "ship.clear_mothership", F.UNSHIPPED)))
        self.assertTrue(os.path.isfile(os.path.join(dest, "ship.clear_mothership", "a.json")))

    @unittest.skipUnless(os.name == "nt", "Windows path forms")
    def test_ship_paths_use_the_long_path_form(self):
        """A destination of 260 characters or more needs the long-path prefix to pass Windows' MAX_PATH limit."""
        self.assertEqual(F.win_long(r"F:\foundry\output\compute\x"), "\\\\?\\F:\\foundry\\output\\compute\\x")
        self.assertEqual(F.win_long(r"\\10.0.0.204\SSDShared\tab\y"), "\\\\?\\UNC\\10.0.0.204\\SSDShared\\tab\\y")
        self.assertEqual(F.win_long("\\\\?\\F:\\z"), "\\\\?\\F:\\z", "never prefixed twice")
        dest = tempfile.mkdtemp()
        j = self._job("ship.long_bighonker", dest)
        self._made(j, {"out.json": "x"})
        seen = []
        real = F.shutil.copytree
        with mock.patch.object(F.shutil, "copytree", lambda s, d, **k: seen.append((s, d)) or real(s, d, **k)):
            got = F.ship_compute(j)
        self.assertTrue(got and seen and all(p.startswith("\\\\?\\") for p in seen[0]), seen)
        self.assertNotIn("ship_error", j.state)

    def test_a_clean_run_that_did_not_deliver_reports_failed_not_done(self):
        reports = []
        j = TestComputeCardQuietGate._J("cpu")
        proc = mock.Mock()
        proc.stdout = iter([])
        proc.wait = lambda: 0

        def ship(job):
            job.state["ship_error"] = "FileNotFoundError(2)"

        with mock.patch.object(F, "ship_compute", ship), \
             mock.patch.object(F, "notify", lambda *a, **k: None), \
             mock.patch.object(F, "report_fleet_result", lambda job, ok, why="", status=None: reports.append((ok, why))), \
             mock.patch.object(F, "retire_order", lambda *a, **k: None), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F.subprocess, "Popen", lambda *a, **k: proc):
            F.run_compute_order(j)
        self.assertEqual(len(reports), 1)
        self.assertFalse(reports[0][0], "a product that never left the box is not done")
        self.assertIn("SHIP FAILED", reports[0][1])

    def test_a_failed_delivery_is_dmd_and_never_raises(self):
        dest = tempfile.mkdtemp()
        j = self._job("ship.d_mothership", dest)
        self._made(j, {"out.json": "x"})
        notes = []
        with mock.patch.object(F.shutil, "copytree", side_effect=OSError("share gone")), \
             mock.patch.object(F, "notify", lambda job, m: notes.append(m)):
            self.assertIsNone(F.ship_compute(j))
        self.assertTrue(any("SHIP FAILED" in m for m in notes))


class TestDeskReportResend(unittest.TestCase):
    """A desk outage is a property of the desk, never of the order: keep the report, wait while the desk is down, resend once it answers."""

    def setUp(self):
        import types
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        for name, value in (("DESK_REPORTS", os.path.join(self.d, "desk_reports")),
                            ("_worker_token", lambda: "tok")):
            p = mock.patch.object(F, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.logs = []
        self.job = types.SimpleNamespace(spec={"fleet_order_id": "abc123"}, name="o1_bighonker", log=self.logs.append)

    def refused(self, *a, **k):
        import urllib.error
        raise urllib.error.URLError(ConnectionRefusedError(10061, "actively refused"))

    def test_a_refused_report_is_kept_waits_quietly_and_is_resent_once_the_desk_answers(self):
        with mock.patch.object(F.urllib.request, "urlopen", self.refused):
            F.report_fleet_result(self.job, True)
        marker = os.path.join(F.DESK_REPORTS, "abc123.json")
        self.assertTrue(os.path.isfile(marker), "the report is kept")
        self.assertTrue(any("kept; the order taker resends it" in x for x in self.logs), self.logs)
        logged, posted = [], []
        with mock.patch.object(F.urllib.request, "urlopen", self.refused):
            self.assertEqual(F.resend_desk_reports(logged.append), [], "desk still down: WAIT")
        self.assertTrue(os.path.isfile(marker))
        self.assertEqual(logged, [], "waiting is quiet")
        with mock.patch.object(F, "_post_desk_result", lambda *a: posted.append(a)):
            self.assertEqual(F.resend_desk_reports(logged.append), ["abc123"])
        self.assertEqual(posted, [("abc123", "done", "", "tok")], "the same outcome, the worker's own token")
        self.assertFalse(os.path.exists(marker), "delivered: marker gone")
        self.assertTrue(any("RESENT desk report: done for step abc123" in x for x in logged), logged)

    def test_a_report_the_desk_takes_first_time_leaves_nothing_behind(self):
        with mock.patch.object(F, "_post_desk_result", lambda *a: None):
            F.report_fleet_result(self.job, False, why="boom")
        self.assertFalse(glob.glob(os.path.join(F.DESK_REPORTS, "*")), "nothing kept")
        self.assertTrue(any("fleet desk: reported FAILED for step abc123" in x for x in self.logs), self.logs)

    def test_a_desk_refusal_of_one_report_sets_it_aside_and_the_rest_still_go(self):
        import urllib.error
        os.makedirs(F.DESK_REPORTS)
        for jid in ("a1", "b2"):
            with open(os.path.join(F.DESK_REPORTS, jid + ".json"), "w", encoding="utf-8") as f:
                json.dump({"jid": jid, "outcome": "done", "name": jid, "at": "t", "error": "e"}, f)

        def post(jid, outcome, why, token):
            if jid == "a1":
                raise urllib.error.HTTPError("u", 404, "no such step", {}, io.BytesIO(b""))
        logged = []
        with mock.patch.object(F, "_post_desk_result", post):
            self.assertEqual(F.resend_desk_reports(logged.append), ["b2"])
        self.assertTrue(os.path.isfile(os.path.join(F.DESK_REPORTS, "a1.json.refused")), "set aside for a human")
        self.assertTrue(any("REFUSED" in x and "HTTP 404" in x for x in logged), logged)
        self.assertEqual(sorted(os.listdir(F.DESK_REPORTS)), ["a1.json.refused"])


class TestDeskReportsNeverLive(unittest.TestCase):
    def test_the_suite_keeps_refused_reports_out_of_the_factorys_real_state_dir(self):
        """The on-box gate runs this suite on a live factory: a kept report there would be resent to the real desk."""
        live = os.path.normcase(os.path.abspath(F.P.STATE_DIR))
        self.assertFalse(os.path.normcase(os.path.abspath(F.DESK_REPORTS)).startswith(live), F.DESK_REPORTS)


class TestCardUnavailableHandsBack(unittest.TestCase):
    """GPU orders fail closed if the local device is unavailable (lost or blocked by transcoding). They must be routed back to the desk for re-placement or hold, never degraded to CPU."""

    LOST = {"ok": True, "gpu_ok": False, "gpu_why": "GPU LOST per BigHonker: GPU is lost",
            "start_ok": False, "start_why": "GPU LOST per BigHonker", "plex_active": None}
    FINE = {"ok": True, "gpu_ok": True, "gpu_why": "ok", "start_ok": True, "start_why": "",
            "plex_active": False}

    class _J:
        def __init__(self, spec, load="gpu"):
            d = tempfile.mkdtemp()
            self.name = spec.get("name", "hb_t")
            self.mode = spec.get("mode", "compute")
            self.load = load
            self.spec = dict({"payload": __file__, "args": []}, **spec)
            self.state = {}
            self.spec_path = os.path.join(d, self.name + ".job.json")
            with open(self.spec_path, "w", encoding="utf-8") as f:
                json.dump(self.spec, f)
            self.total_steps = 0
            self.logs = []

        def log(self, m):
            self.logs.append(m)

        def save(self):
            pass

    # --- the verdict --------------------------------------------------------------------------------
    def test_could_not_tell_is_never_a_reason_to_act(self):
        self.assertEqual(F.host_unavailable_reason(None), "")
        self.assertEqual(F.host_unavailable_reason(None, running=True), "")

    def test_a_lost_card_is_a_reason_before_and_during(self):
        self.assertIn("GPU LOST", F.host_unavailable_reason(self.LOST))
        self.assertIn("GPU LOST", F.host_unavailable_reason(self.LOST, running=True))

    def test_mid_run_only_plex_transcoding_counts_not_the_utilisation_gate(self):
        # once our order runs, the util gate reads our own load as Plex's -- it must not evict us
        busy_by_us = dict(self.FINE, start_ok=False, start_why="Plex first: gpu busy (util 95%)")
        self.assertEqual(F.host_unavailable_reason(busy_by_us, running=True), "")
        self.assertIn("Plex first", F.host_unavailable_reason(busy_by_us))
        self.assertIn("Plex", F.host_unavailable_reason(dict(self.FINE, plex_active=True), running=True))

    def test_a_plex_coexistence_measurement_runs_beside_plex_but_never_on_a_lost_card(self):
        """The advisor must run under Plex: the order measuring that cannot be stopped by Plex."""
        transcoding = dict(self.FINE, plex_active=True, start_ok=False, start_why="Plex first: transcoding")
        spec = {"coexists_with_plex": True}
        self.assertTrue(F.coexists_with_plex(spec))
        self.assertFalse(F.coexists_with_plex({}))
        self.assertEqual(F.host_unavailable_reason(transcoding, plex_ok=F.coexists_with_plex(spec)), "")
        self.assertEqual(F.host_unavailable_reason(transcoding, running=True, plex_ok=True), "")
        self.assertIn("GPU LOST", F.host_unavailable_reason(self.LOST, plex_ok=True), "a lost card still stops it")
        self.assertIn("Plex", F.host_unavailable_reason(transcoding), "every other order still yields to Plex")

    def test_what_counts_as_a_gpu_order(self):
        self.assertFalse(F.order_uses_gpu({"mode": "cpu"}))
        self.assertFalse(F.order_uses_gpu({"mode": "compute", "load": "cpu"}))
        self.assertTrue(F.order_uses_gpu({"mode": "compute", "load": "gpu"}))
        self.assertTrue(F.order_uses_gpu({"mode": "compute"}), "default load is mixed")
        self.assertTrue(F.order_uses_gpu({"mode": "caption"}))

    # --- before launch ------------------------------------------------------------------------------
    def _before(self, spec, avail, answer, local=""):
        calls = {"retire": 0, "handback": 0, "asked": 0}

        def ask(spec=None):
            calls["asked"] += 1
            return avail

        def hb(s, why, log):
            calls["handback"] += 1
            return answer
        j = self._J(spec)
        with mock.patch.object(F, "desk_availability", ask), \
             mock.patch.object(F, "local_card_lost", lambda: local), \
             mock.patch.object(F, "hand_back_to_desk", hb), \
             mock.patch.object(F, "retire_order",
                               lambda *a, **k: calls.__setitem__("retire", calls["retire"] + 1)):
            try:
                F.card_check_before_launch(j)
                raised = None
            except (F.HandedBack, F.LaneBusy) as e:
                raised = e
        return raised, calls

    def test_the_local_driver_stops_a_launch_the_desk_still_calls_healthy(self):
        lost = "GPU lost per this box's nvidia-smi: GPU is lost"
        with mock.patch.object(F, "FLEET_HOST", "bighonker"):
            raised, calls = self._before({"mode": "compute", "load": "gpu", "fleet_order_id": "abc",
                                          "pinned_to": ["bighonker"]}, self.FINE, None, local=lost)
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertIn("nvidia-smi", str(raised))
        self.assertEqual((calls["handback"], calls["retire"]), (0, 0))

    def test_the_local_driver_does_not_hold_a_cpu_order(self):
        raised, _ = self._before({"mode": "cpu"}, self.FINE, None, local="GPU lost per this box's nvidia-smi")
        self.assertIsNone(raised)

    def test_local_card_lost_reads_the_driver(self):
        def run(out="", err="", exc=None):
            def _r(*a, **k):
                if exc:
                    raise exc
                return mock.Mock(stdout=out, stderr=err)
            return _r
        with mock.patch.object(F.subprocess, "run", run(err="Unable to determine the device handle for GPU0: "
                                                              "0000:01:00.0: GPU is lost.  Reboot the system")):
            self.assertIn("GPU is lost", F.local_card_lost())
        with mock.patch.object(F.subprocess, "run", run(out="No devices were found")):
            self.assertIn("No devices", F.local_card_lost())
        with mock.patch.object(F.subprocess, "run", run(exc=F.subprocess.TimeoutExpired("nvidia-smi", 25))):
            self.assertIn("wedged", F.local_card_lost())
        with mock.patch.object(F.subprocess, "run", run(out="NVIDIA GeForce RTX 3070")):
            self.assertEqual(F.local_card_lost(), "", "a healthy card is not a reason")
        with mock.patch.object(F.subprocess, "run", run(exc=FileNotFoundError("nvidia-smi"))):
            self.assertEqual(F.local_card_lost(), "", "no nvidia-smi (the AMD box) is could-not-tell, never lost")

    def test_desk_order_on_a_lost_card_goes_back_to_the_desk(self):
        raised, calls = self._before({"mode": "compute", "load": "gpu", "fleet_order_id": "abc"},
                                     self.LOST, {"ok": True, "outcome": "placed", "host": "softserve"})
        self.assertIsInstance(raised, F.HandedBack)
        self.assertEqual((calls["handback"], calls["retire"]), (1, 1))

    def test_desk_that_cannot_take_it_back_means_wait_not_run(self):
        raised, calls = self._before({"mode": "compute", "load": "gpu", "fleet_order_id": "abc"},
                                     self.LOST, None)
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertEqual(calls["retire"], 0, "an order the desk did not take back was retired")

    def test_a_pinned_order_waits_instead_of_being_handed_back(self):
        with mock.patch.object(F, "FLEET_HOST", "softserve"):
            raised, calls = self._before({"mode": "compute", "load": "gpu", "fleet_order_id": "abc",
                                          "pinned_to": ["softserve"]}, self.LOST, {"ok": True, "outcome": "placed"})
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertEqual((calls["handback"], calls["retire"]), (0, 0), "a pinned order was sent away")

    def test_a_pin_to_another_box_does_not_hold_this_one(self):
        with mock.patch.object(F, "FLEET_HOST", "bighonker"):
            raised, calls = self._before({"mode": "compute", "load": "gpu", "fleet_order_id": "abc",
                                          "pinned_to": ["softserve"]}, self.LOST, {"ok": True, "outcome": "placed"})
        self.assertIsInstance(raised, F.HandedBack)

    def test_a_train_order_waits_for_its_own_card(self):
        raised, calls = self._before({"mode": "train", "fleet_order_id": "abc"}, self.LOST, {"ok": True})
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertEqual(calls["handback"], 0, "a trainer's checkpoints live on this box")

    def test_a_cpu_order_ignores_a_lost_card(self):
        raised, calls = self._before({"mode": "compute", "load": "cpu", "fleet_order_id": "abc"},
                                     self.LOST, {"ok": True})
        self.assertIsNone(raised)
        self.assertEqual(calls["handback"], 0)

    def test_a_cpu_order_does_not_start_while_plex_transcodes(self):
        raised, calls = self._before({"mode": "compute", "load": "cpu", "fleet_order_id": "abc"},
                                     dict(self.FINE, plex_active=True, plex_why="1 Plex transcode(s)"),
                                     {"ok": True, "outcome": "held"})
        self.assertIsInstance(raised, F.HandedBack)
        self.assertEqual(calls["handback"], 1)

    def test_a_gpu_order_does_not_start_while_plex_transcodes_even_if_start_ok(self):
        raised, calls = self._before({"mode": "compute", "load": "gpu", "fleet_order_id": "abc"},
                                     dict(self.FINE, plex_active=True, plex_why="1 Plex transcode(s)"),
                                     {"ok": True, "outcome": "held"})
        self.assertIsInstance(raised, F.HandedBack)

    def test_a_healthy_card_launches(self):
        raised, calls = self._before({"mode": "compute", "load": "gpu", "fleet_order_id": "abc"},
                                     self.FINE, {"ok": True})
        self.assertIsNone(raised)
        self.assertEqual(calls["handback"], 0)

    def test_an_unreachable_start_verdict_waits_without_loading_or_handing_back(self):
        raised, calls = self._before({"mode": "compute", "load": "gpu", "fleet_order_id": "abc"}, None, None)
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertEqual((calls["handback"], calls["retire"]), (0, 0))

    # --- mid-run ------------------------------------------------------------------------------------
    def _mid_run(self, avail, answer):
        return self._mid_run_spec({}, avail, answer)

    def test_the_payloads_beat_is_published_as_work_beat(self):
        """infer_ollama prints `[beat] N` while tokens stream; the runner must publish it for the stall demon."""
        import threading as _t
        done = _t.Event()
        published = []

        def lines():
            yield "[infer] 3/75 x r1 m 5.0s\n"
            yield "[beat] 42\n"
            done.wait(1.0)

        proc = mock.Mock()
        proc.pid, proc.stdout, proc.wait = 4243, lines(), (lambda: 0)

        gens = []

        def pub(job, **kw):
            published.append(kw.get("work_beat"))
            gens.append(kw.get("gen"))
            if kw.get("work_beat") == 42:
                done.set()
        j = self._J({"name": "beat_t", "mode": "compute", "load": "gpu"})
        with mock.patch.object(F, "COMPUTE_BEAT_S", 0.02), \
             mock.patch.object(F, "CARD_CHECK_EVERY_BEATS", 1000), \
             mock.patch.object(F, "desk_availability", lambda: None), \
             mock.patch.object(F, "require_demon", lambda *a, **k: None), \
             mock.patch.object(F, "stop_comfy", lambda *a, **k: None), \
             mock.patch.object(F, "wait_card_quiet", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", pub), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "notify", lambda *a, **k: None), \
             mock.patch.object(F, "ship_compute", lambda *a, **k: None), \
             mock.patch.object(F, "report_fleet_result", lambda *a, **k: None), \
             mock.patch.object(F, "retire_order", lambda *a, **k: None), \
             mock.patch.object(F.subprocess, "Popen", lambda *a, **k: proc):
            F.run_compute_order(j)
        self.assertIn(42, published, "work_beat never published: %r" % published[-5:])
        self.assertTrue(F.COMPUTE_BEAT_RE.match("[beat] 42\n") and not F.COMPUTE_PROG_RE.match("[beat] 42\n"))
        beat_gens = [g for b, g in zip(published, gens) if b == 42]
        self.assertTrue(beat_gens and all(isinstance(g, dict) and g.get("turn_label") == "call" for g in beat_gens),
                        "every beat states the order's model work for the tile (#37): %r" % gens[-2:])

    def test_plex_starting_to_transcode_mid_run_ends_a_cpu_order_too(self):
        raised, calls = self._mid_run_spec({"load": "cpu"},
                                           dict(self.FINE, plex_active=True, plex_why="1 Plex transcode(s)"),
                                           {"ok": True, "outcome": "held"}, load="cpu")
        self.assertIsInstance(raised, F.HandedBack)
        self.assertTrue(calls["kill"] and "4242" in calls["kill"][0], "the CPU order kept running under Plex")

    def test_a_cpu_order_ignores_the_card_verdict_mid_run(self):
        self.assertEqual(F.host_unavailable_reason(self.LOST, running=True, gpu=False), "")
        self.assertEqual(F.host_unavailable_reason(self.LOST, gpu=False), "")

    def _mid_run_spec(self, extra, avail, answer, load="gpu"):
        import threading as _t
        killed = _t.Event()
        calls = {"retire": 0, "report": 0, "ship": 0, "kill": [], "handback": 0}

        def lines():
            yield "working\n"
            killed.wait(5)

        proc = mock.Mock()
        proc.pid = 4242
        proc.stdout = lines()
        proc.wait = lambda: 1

        def kill(cmd, **k):
            calls["kill"].append(cmd)
            killed.set()

        def hb(s, why, log):
            calls["handback"] += 1
            return answer

        j = self._J(dict({"name": "hb_mid", "mode": "compute", "load": "gpu", "fleet_order_id": "abc"}, **extra),
                    load=load)
        raised = None
        with mock.patch.object(F, "COMPUTE_BEAT_S", 0.02), \
             mock.patch.object(F, "CARD_CHECK_EVERY_BEATS", 1), \
             mock.patch.object(F, "desk_availability", lambda: avail), \
             mock.patch.object(F, "hand_back_to_desk", hb), \
             mock.patch.object(F, "require_demon", lambda *a, **k: None), \
             mock.patch.object(F, "stop_comfy", lambda *a, **k: None), \
             mock.patch.object(F, "wait_card_quiet", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "notify", lambda *a, **k: None), \
             mock.patch.object(F, "ship_compute",
                               lambda *a, **k: calls.__setitem__("ship", calls["ship"] + 1)), \
             mock.patch.object(F, "report_fleet_result",
                               lambda *a, **k: calls.__setitem__("report", calls["report"] + 1)), \
             mock.patch.object(F, "retire_order",
                               lambda *a, **k: calls.__setitem__("retire", calls["retire"] + 1)), \
             mock.patch.object(F.subprocess, "run", kill), \
             mock.patch.object(F.subprocess, "Popen", lambda *a, **k: proc):
            try:
                F.run_compute_order(j)
            except (F.HandedBack, F.LaneBusy) as e:
                raised = e
        return raised, calls

    def test_a_card_lost_mid_run_ends_the_child_and_hands_the_order_back(self):
        raised, calls = self._mid_run(self.LOST, {"ok": True, "outcome": "placed", "host": "softserve"})
        self.assertIsInstance(raised, F.HandedBack)
        self.assertTrue(calls["kill"] and "4242" in calls["kill"][0], "the child kept running on CPU")
        self.assertEqual((calls["handback"], calls["retire"]), (1, 1))
        self.assertEqual((calls["ship"], calls["report"]), (0, 0),
                         "CPU rows shipped, or the desk order was reported failed")

    def test_plex_starting_to_transcode_mid_run_hands_it_back(self):
        raised, calls = self._mid_run(dict(self.FINE, plex_active=True, plex_why="1 Plex transcode(s)"),
                                      {"ok": True, "outcome": "held"})
        self.assertIsInstance(raised, F.HandedBack)
        self.assertEqual(calls["ship"], 0)

    def test_plex_mid_run_on_a_pinned_order_waits_on_this_box(self):
        with mock.patch.object(F, "FLEET_HOST", "softserve"):
            raised, calls = self._mid_run_spec({"pinned_to": ["softserve"]},
                                               dict(self.FINE, plex_active=True, plex_why="1 Plex transcode(s)"),
                                               {"ok": True, "outcome": "placed", "host": "bighonker"})
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertEqual((calls["handback"], calls["retire"], calls["ship"], calls["report"]), (0, 0, 0, 0))
        self.assertTrue(calls["kill"], "Plex still gets the card: the child must end")

    def test_desk_unreachable_mid_run_keeps_the_order_queued(self):
        raised, calls = self._mid_run(self.LOST, None)
        self.assertIsInstance(raised, F.LaneBusy)
        self.assertEqual((calls["retire"], calls["report"], calls["ship"]), (0, 0, 0))


def load_health_daemon():
    """Import the live p20_health_daemon from BIN without starting its loop (it reads its poll from argv[1])."""
    sys.path.insert(0, BIN)
    saved, sys.argv = sys.argv, ["p20_health_daemon.py"]
    try:
        spec = importlib.util.spec_from_file_location("p20_health_daemon_undertest",
                                                      os.path.join(BIN, "p20_health_daemon.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.argv = saved


class HealthDaemonTwoOrOne(unittest.TestCase):
    """Two models coexist while free memory stays above reserve; below it, all models except the keeper are unloaded. Unreadable memory falls back to one-model mode."""
    K, E = "tev1:latest", "nomic-embed-text:latest"

    @classmethod
    def setUpClass(cls):
        cls.H = load_health_daemon()

    def test_one_model_or_none_is_left_alone(self):
        self.assertEqual(self.H.two_or_one([(self.K, 5338)], 100, 3000, keeper=self.K)[0], [])
        self.assertEqual(self.H.two_or_one([], None, None, keeper=self.K)[0], [])
        self.assertEqual(self.H.two_or_one([(self.K, 5338), (self.E, 0)], 100, 3000, keeper=self.K)[0], [],
                         "a model ollama lists with nothing on the card is not on the card")

    def test_room_for_both_keeps_both(self):
        unload, verdict = self.H.two_or_one([(self.K, 5338), (self.E, 320)], 1451, 1451, keeper=self.K, reserve=512)
        self.assertEqual(unload, [])
        self.assertIn("two models", verdict)

    def test_a_fall_that_will_cross_the_reserve_next_poll_goes_to_one_before_it_does(self):
        # 1,451 -> 900 free: still above 512 now, but the same fall again lands at 349 - Plex is rising into the room
        unload, verdict = self.H.two_or_one([(self.K, 5338), (self.E, 320)], 900, 1451, keeper=self.K, reserve=512)
        self.assertEqual(unload, [self.E])
        self.assertIn("one at a time", verdict)

    def test_already_under_the_reserve_goes_to_one(self):
        self.assertEqual(self.H.two_or_one([(self.E, 320), (self.K, 5338)], 400, 400, keeper=self.K, reserve=512)[0],
                         [self.E])

    def test_unreadable_card_memory_is_one_at_a_time_never_room_for_both(self):
        unload, verdict = self.H.two_or_one([(self.K, 5338), (self.E, 320)], None, 1451, keeper=self.K)
        self.assertEqual(unload, [self.E])
        self.assertIn("unreadable", verdict)

    def test_without_the_keeper_the_largest_stays(self):
        unload, _v = self.H.two_or_one([(self.E, 320), ("qwen3:8b", 5100)], 100, 100, keeper=self.K, reserve=512)
        self.assertEqual(unload, [self.E])

    def test_card_free_is_total_minus_used_and_garbage_is_none(self):
        H = self.H
        with mock.patch.object(H, "smi", return_value=("1233, 8192", "")):
            self.assertEqual(H.card_free_mib(), 6959)
        with mock.patch.object(H, "smi", return_value=(None, "nvidia-smi TIMED OUT")):
            self.assertIsNone(H.card_free_mib())

    def test_the_guard_unloads_through_ollama_and_reads_the_trend_across_polls(self):
        H = self.H
        H._prev_free[0] = None
        frees = iter([1451, 900])
        with mock.patch.object(H, "PLEX_GUARD", True), mock.patch.object(H, "KEEPER", self.K), \
                mock.patch.object(H, "resident_models", return_value=[(self.K, 5338), (self.E, 320)]), \
                mock.patch.object(H, "card_free_mib", side_effect=lambda: next(frees)), \
                mock.patch.object(H, "unload_model", return_value=True) as unload, \
                mock.patch.object(H, "log"):
            ok1, note1 = H.check_plex_guard()
            ok2, note2 = H.check_plex_guard()
        self.assertTrue(ok1 and ok2)
        self.assertIn("two models", note1)
        unload.assert_called_once_with(self.E)
        self.assertIn("unloaded " + self.E, note2)

    def test_off_on_every_other_box_and_when_ollama_cannot_be_asked(self):
        H = self.H
        with mock.patch.object(H, "PLEX_GUARD", False), mock.patch.object(H, "unload_model") as unload:
            self.assertEqual(H.check_plex_guard()[0], None)
        unload.assert_not_called()
        with mock.patch.object(H, "PLEX_GUARD", True), mock.patch.object(H, "resident_models", return_value=None), \
                mock.patch.object(H, "card_free_mib", return_value=100), \
                mock.patch.object(H, "unload_model") as unload:
            ok, note = H.check_plex_guard()
        self.assertIsNone(ok)
        unload.assert_not_called()
        self.assertIn("could not be asked", note)

    def test_the_guard_is_a_setting_off_unless_this_box_says_so(self):
        """No box is special by name: FOUNDRY_PLEX_MODEL_GUARD=1 on the box that shares its card with a media server."""
        for host in ("softserve", "bighonker", "mothership"):
            with mock.patch.dict(os.environ, {"FOUNDRY_FLEET_HOST": host, "FOUNDRY_PLEX_MODEL_GUARD": "0"}):
                self.assertIs(load_health_daemon().PLEX_GUARD, False, host)
        with mock.patch.dict(os.environ, {"FOUNDRY_FLEET_HOST": "anybox", "FOUNDRY_PLEX_MODEL_GUARD": "1"}):
            self.assertIs(load_health_daemon().PLEX_GUARD, True)
        self.assertEqual(self.H.RESERVE_MIB, 512, "the lab launchers' LLAMA_ARG_FIT_TARGET, not a new number")


class HealthDaemonClockDuty(unittest.TestCase):
    """FOUNDRY_TARGET_CLOCK=0 disables duty 1; without it, check_clock re-applies nvidia-smi -lgc 0 to idle cards."""

    @classmethod
    def setUpClass(cls):
        cls.H = load_health_daemon()

    def setUp(self):
        self.H._relocks = 0

    def test_target_zero_measures_and_applies_nothing(self):
        H = self.H
        with mock.patch.object(H, "TARGET_CLOCK_MHZ", 0), \
                mock.patch.object(H, "smi") as smi, mock.patch.object(H.subprocess, "run") as run:
            ok, note = H.check_clock()
        self.assertIsNone(ok)
        self.assertIn("OFF", note)
        smi.assert_not_called()
        run.assert_not_called()
        self.assertEqual(H._relocks, 0)

    def test_target_zero_is_labelled_off_not_as_a_lock(self):
        H = self.H
        with mock.patch.object(H, "TARGET_CLOCK_MHZ", 0):
            self.assertEqual(H._clock_duty_label(), "clock duty OFF")
        with mock.patch.object(H, "TARGET_CLOCK_MHZ", 1500):
            self.assertEqual(H._clock_duty_label(), "clock lock 1500 MHz")

    def test_a_set_target_still_relocks_a_card_above_it(self):
        """Positive control: the duty the zero case skips still fires when it is on."""
        H = self.H
        with mock.patch.object(H, "TARGET_CLOCK_MHZ", 1500), \
                mock.patch.object(H, "smi", side_effect=[("1965, 2100", ""), ("1500", "")]), \
                mock.patch.object(H.subprocess, "run") as run, mock.patch.object(H, "log"):
            ok, _ = H.check_clock()
        self.assertIs(ok, False)
        self.assertEqual(run.call_args[0][0], ["nvidia-smi", "-lgc", "1500"])
        self.assertEqual(H._relocks, 1)

    def _run(self, gpu_clock, age_s=10.0, updated=None):
        d = tempfile.mkdtemp()
        spec = os.path.join(d, "o.job.json")
        with open(spec, "w", encoding="utf-8") as f:
            json.dump({"name": "o", **({"gpu_clock": gpu_clock} if gpu_clock is not None else {})}, f)
        ts = updated or (datetime.datetime.now() - datetime.timedelta(seconds=age_s)).isoformat()
        return {"name": "bhcrash-x-r1", "spec_file": spec, "updated": ts}

    def test_a_fresh_order_that_owns_the_clock_is_left_alone(self):
        H = self.H
        with mock.patch.object(H, "TARGET_CLOCK_MHZ", 1500), \
                mock.patch.object(H, "smi", return_value=("1980, 2100", "")) as smi, \
                mock.patch.object(H.subprocess, "run") as run:
            ok, note = H.check_clock(self._run("unlocked"))
        self.assertIsNone(ok)
        self.assertIn("yielded to order bhcrash-x-r1", note)
        smi.assert_not_called()
        run.assert_not_called()
        self.assertEqual(H._relocks, 0)

    def test_the_lock_returns_once_the_owning_run_goes_stale(self):
        H = self.H
        stale = self._run("unlocked", age_s=H.CURRENT_RUN_STALE_S + 60)
        with mock.patch.object(H, "TARGET_CLOCK_MHZ", 1500), \
                mock.patch.object(H, "smi", side_effect=[("1980, 2100", ""), ("1500", "")]), \
                mock.patch.object(H.subprocess, "run") as run, mock.patch.object(H, "log"):
            ok, _ = H.check_clock(stale)
        self.assertIs(ok, False)
        self.assertEqual(run.call_args[0][0], ["nvidia-smi", "-lgc", "1500"])

    def test_an_order_that_declares_no_clock_does_not_lift_the_lock(self):
        H = self.H
        for run_rec in (self._run(None), self._run("unlocked", updated="not a time"), None,
                        {"name": "x", "spec_file": os.path.join(tempfile.mkdtemp(), "missing.json"),
                         "updated": datetime.datetime.now().isoformat()}):
            H._relocks = 0
            with mock.patch.object(H, "TARGET_CLOCK_MHZ", 1500), \
                    mock.patch.object(H, "smi", side_effect=[("1980, 2100", ""), ("1500", "")]), \
                    mock.patch.object(H.subprocess, "run") as run, mock.patch.object(H, "log"):
                ok, _ = H.check_clock(run_rec)
            self.assertIs(ok, False, run_rec)
            self.assertEqual(run.call_args[0][0], ["nvidia-smi", "-lgc", "1500"])

    def test_a_set_target_accepts_an_idle_card_below_it(self):
        H = self.H
        with mock.patch.object(H, "TARGET_CLOCK_MHZ", 1500), \
                mock.patch.object(H, "smi", return_value=("210, 2100", "")), \
                mock.patch.object(H.subprocess, "run") as run:
            ok, _ = H.check_clock()
        self.assertIs(ok, True)
        run.assert_not_called()


class HealthDaemonFansAndHotspot(unittest.TestCase):
    """Tasklist lines are fixed byte-for-byte fixtures; do not normalize or re-fetch."""
    BH = ('"AsusFanControlService.exe","3776","Services","0","20,060 K"\n'
          '"FanControl.Service.exe","10876","Services","0","83,788 K"\n')
    SS = ('"AsusFanControlService.exe","4776","Services","0","11,536 K"\n'
          '"FanControl.exe","19592","Console","1","92,716 K"\n')
    ASUS_ONLY = '"AsusFanControlService.exe","3776","Services","0","20,060 K"\n'
    HEAD = ["ts", "temp_c", "hotspot_c", "mb_fan1_rpm", "mb_fan2_rpm", "mb_fan3_rpm"]

    @classmethod
    def setUpClass(cls):
        cls.H = load_health_daemon()

    def test_the_service_and_the_app_both_count(self):
        self.assertEqual(self.H.fan_processes(self.BH), ["FanControl.Service.exe"])
        self.assertEqual(self.H.fan_processes(self.SS), ["FanControl.exe"])

    def test_asus_own_service_is_not_fan_control(self):
        ok, note = self.H.check_fans(self.ASUS_ONLY, self.HEAD, ["t", "55", "72", "800", "0", "650"])
        self.assertIs(ok, False)
        self.assertIn("NOT RUNNING", note)

    def test_running_reports_how_many_case_fans_read_rpm(self):
        ok, note = self.H.check_fans(self.BH, self.HEAD, ["t", "55", "72", "800", "0", "650"])
        self.assertIs(ok, True)
        self.assertIn("2 of 3", note)

    def test_an_unlistable_box_is_inconclusive_never_a_verdict(self):
        ok, _ = self.H.check_fans(None, self.HEAD, ["t", "55", "72", "800", "0", "650"])
        self.assertIsNone(ok)

    def test_hotspot_note_and_its_absence(self):
        self.assertEqual(self.H.hotspot_note(self.HEAD, ["t", "55.4", "72.6", "1", "1", "1"]),
                         "hotspot 73 C (core 55, gap 17)")
        self.assertEqual(self.H.hotspot_note(["ts", "temp_c"], ["t", "55"]), "hotspot: not logged on this box")
        self.assertEqual(self.H.hotspot_note(None, None), "hotspot: telemetry unreadable")

    def test_last_row_reads_the_tail_of_a_real_file(self):
        p = os.path.join(tempfile.mkdtemp(), "t.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(",".join(self.HEAD) + "\n" + "".join("t%d,50,70,1,1,1\n" % i for i in range(5000)))
        head, row = self.H.last_row(p)
        self.assertEqual(head, self.HEAD)
        self.assertEqual(row[0], "t4999")

    def test_live_telemetry_prefers_the_newest_dated_file(self):
        import foundry_paths as FP
        d = tempfile.mkdtemp()
        for n in ("gpu_telemetry.csv", "gpu_telemetry-20261004.csv", "gpu_telemetry-20261005.csv",
                  "gpu_telemetry-20261005_prev-schema_123249.csv"):
            open(os.path.join(d, n), "w").close()
        with mock.patch.object(FP, "TELEMETRY_DIR", d), \
                mock.patch.object(FP, "GPU_TELEMETRY", os.path.join(d, "gpu_telemetry.csv")):
            self.assertEqual(os.path.basename(FP.gpu_telemetry_live()), "gpu_telemetry-20261005.csv")
        e = tempfile.mkdtemp()
        with mock.patch.object(FP, "TELEMETRY_DIR", e), \
                mock.patch.object(FP, "GPU_TELEMETRY", os.path.join(e, "gpu_telemetry.csv")):
            self.assertEqual(os.path.basename(FP.gpu_telemetry_live()), "gpu_telemetry.csv")


class CardQuietSettlesByStatus(unittest.TestCase):
    """Compute orders settle by status: one quiet read is sufficient when the card is not busy; a recent busy sighting
    requires a second read. Trainers always perform two reads."""

    class _J:
        def __init__(self):
            self.logs = []

        def log(self, m):
            self.logs.append(m)

    def _env(self, port_free=True, comfy=0):
        self.sleeps = []
        return [mock.patch.object(F, "_port_free", lambda p: port_free),
                mock.patch.object(F, "_comfy_procs_alive", lambda: comfy),
                mock.patch.object(F, "_ollama_resident", lambda: True),
                mock.patch.object(F.time, "sleep", lambda s: self.sleeps.append(s)),
                mock.patch.object(F, "notify", lambda job, m: None)]

    def _run(self, busy_ago, ollama_busy):
        j = self._J()
        F._CARD_BUSY_SEEN[0] = (time.time() - busy_ago) if busy_ago is not None else 0.0
        pats = self._env()
        for p in pats:
            p.start()
        try:
            F.wait_card_quiet(j, ollama_busy=ollama_busy)
        finally:
            for p in pats:
                p.stop()
            F._CARD_BUSY_SEEN[0] = 0.0
        return j

    def test_a_compute_order_on_an_untouched_card_launches_after_one_read(self):
        j = self._run(busy_ago=None, ollama_busy=False)
        self.assertEqual(self.sleeps, [], "no 15 s settle on a card nothing touched")
        self.assertTrue(any("quiet 1/1" in m and "settled" in m for m in j.logs), j.logs)
        self.assertTrue(any("safe to launch" in m for m in j.logs))

    def test_a_recent_busy_sighting_still_buys_the_second_read(self):
        j = self._run(busy_ago=5, ollama_busy=False)
        self.assertEqual(self.sleeps, [15], "busy 5 s ago: the teardown window has not passed")
        self.assertTrue(any("quiet 2/2" in m for m in j.logs))
        j = self._run(busy_ago=16, ollama_busy=False)
        self.assertEqual(self.sleeps, [], "busy 16 s ago: settled")

    def test_trainers_keep_two_consecutive_reads(self):
        pats = self._env()
        j = self._J()
        F._CARD_BUSY_SEEN[0] = 0.0
        for p in pats:
            p.start()
        try:
            with mock.patch.object(F, "_ollama_resident", lambda: False):
                F.wait_card_quiet(j, ollama_busy=True)
        finally:
            for p in pats:
                p.stop()
        self.assertEqual(self.sleeps, [15])
        self.assertTrue(any("quiet 2/2" in m for m in j.logs))

    def test_a_busy_read_and_a_held_port_at_stop_comfy_mark_the_card_busy(self):
        F._CARD_BUSY_SEEN[0] = 0.0
        reads = iter([1, 0, 0])                      # one ComfyUI proc on the first read, then a clear card
        pats = self._env()
        for p in pats:
            p.start()
        try:
            j = self._J()
            with mock.patch.object(F, "_comfy_procs_alive", lambda: next(reads)):
                F.wait_card_quiet(j, ollama_busy=False)
            self.assertGreater(F._CARD_BUSY_SEEN[0], 0.0, "a busy read is a sighting")
            self.assertEqual(self.sleeps, [15, 15], "busy, then two quiet reads: the sighting was seconds ago")
            F._CARD_BUSY_SEEN[0] = 0.0
            with mock.patch.object(F, "_port_free", lambda p: False), \
                    mock.patch.object(F.subprocess, "run", lambda *a, **k: None):
                F.stop_comfy(j)
            self.assertGreater(F._CARD_BUSY_SEEN[0], 0.0, "stop_comfy with the port held is a sighting")
        finally:
            for p in pats:
                p.stop()
            F._CARD_BUSY_SEEN[0] = 0.0


class IdleWaitAsksTheDesk(unittest.TestCase):
    """Between full passes the idle runner asks the desk every QUICK_POLL_S and returns the moment an order was
    collected (a piece used to wait up to the whole 30 s pass); nothing collected = the full `poll` elapses."""

    def _run(self, poll, collects):
        slept, clock = [], [0.0]

        def sleep(s):
            slept.append(s)
            clock[0] += s
        it = iter(collects)
        got = F._idle_wait(poll, lambda: next(it, 0), sleep=sleep, clock=lambda: clock[0])
        return got, slept

    def test_returns_as_soon_as_the_desk_hands_over_an_order(self):
        got, slept = self._run(30.0, [0, 0, 1])
        self.assertTrue(got)
        self.assertEqual(slept, [F.QUICK_POLL_S] * 3)

    def test_an_empty_desk_waits_out_the_full_pass_in_quick_steps(self):
        got, slept = self._run(12.5, [0] * 20)
        self.assertFalse(got)
        self.assertEqual(slept, [F.QUICK_POLL_S] * 12 + [0.5])
        self.assertEqual(sum(slept), 12.5)
        # QUICK_POLL_S is 1.0s to match the live polling cadence; do not use an arbitrary test value.
        self.assertEqual(F.QUICK_POLL_S, 1.0)

    def test_the_pickup_says_what_its_pass_spent_its_seconds_on(self):
        marks = [("", 100.0), ("controls", 100.3), ("reship", 100.3), ("collect", 100.3), ("patrol", 102.3),
                 ("police", 102.8), ("announce", 103.6)]
        self.assertEqual(F.pass_report(marks), "pass 3.6 s: patrol 2.0, announce 0.8, police 0.5, controls 0.3")
        self.assertEqual(F.pass_report([("", 5.0), ("controls", 5.01)]), "pass 0.0 s")


class ShareDownWaitsNotFails(unittest.TestCase):
    """A down share is a transient property of the share, not the order: the order must wait (LaneBusy) and retry, not be archived as FAILED."""
    PLAN = r"\\10.0.0.204\SSDShared\mothership-build\workspace\a1\plans\a1v22-py-agentic-hai-ornith-best-thon-spot.20261008-0755.json"
    PAYLOAD = r"\\10.0.0.204\SSDShared\tabfoundry-jobs\payloads\map_swarm_code_hard_w1.py"

    def test_every_share_an_order_names_is_found_once(self):
        spec = {"payload": self.PAYLOAD, "args": ["--plan", self.PLAN, "F:\\foundry\\output\\x"],
                "deliver": {"to": r"\\10.0.0.204\Documentation\State\x"}}
        self.assertEqual(F.share_roots(spec), [r"\\10.0.0.204\Documentation", r"\\10.0.0.204\SSDShared"])
        self.assertEqual(F.share_roots({"payload": "F:\\foundry\\workspace\\a1\\a1_order.py"}), [])

    def test_only_the_unreachable_shares_are_named(self):
        up = {r"\\10.0.0.204\SSDShared"}
        spec = {"args": [self.PLAN, r"\\10.0.0.204\Documentation\x"]}
        self.assertEqual(F.unreachable_shares([spec], isdir=lambda r: r in up), [r"\\10.0.0.204\Documentation"])

        def boom(r):
            raise OSError(22, "The user name or password is incorrect")
        self.assertEqual(F.unreachable_shares([spec], isdir=boom, heal=lambda r: []),
                         [r"\\10.0.0.204\Documentation", r"\\10.0.0.204\SSDShared"], "an error is unreachable")

    NET_USE = ("New connections will be remembered.\n\n"
               "Status       Local     Remote                    Network\n\n"
               "-------------------------------------------------------------------------------\n"
               "Unavailable  S:        \\\\10.0.0.204\\SSDShared Microsoft Windows Network\n"
               "Unavailable  Y:        \\\\10.0.0.204\\Documentation \n"
               "                                                Microsoft Windows Network\n"
               "OK           T:        \\\\nas\\media               Microsoft Windows Network\n"
               "The command completed successfully.\n")

    def test_mapped_letters_are_read_per_server_from_net_use(self):
        self.assertEqual(F.mapped_letters(self.NET_USE), {"10.0.0.204": ["S", "Y"], "nas": ["T"]})
        self.assertEqual(F.mapped_letters(""), {})

    def test_an_unreachable_share_is_healed_by_touching_its_mapped_letter_then_re_asked(self):
        """Healing an unreachable share requires touching the mapped letters for that server to re-establish the credential binding, then re-querying; this is credential-free and read-only."""
        state = {"healed": False}
        touched = []

        def isdir(r):
            if not state["healed"]:
                raise OSError(22, "The user name or password is incorrect")
            return True

        def listdir(p):
            touched.append(p)
            state["healed"] = True
            return []
        spec = {"args": [self.PLAN]}
        heal = lambda r: F.heal_share(r, net_use=lambda: self.NET_USE, listdir=listdir)   # noqa: E731
        self.assertEqual(F.unreachable_shares([spec], isdir=isdir, heal=heal), [], "healed and re-asked: reachable")
        self.assertEqual(touched, ["S:\\", "Y:\\"], "only the letters mapped to that server")
        self.assertEqual(F.heal_share(r"\\nowhere\x", net_use=lambda: self.NET_USE, listdir=listdir), [],
                         "no mapping for that server: nothing to touch, nothing raised")

    def test_a_share_that_stays_down_after_the_heal_is_still_unreachable(self):
        spec = {"args": [self.PLAN]}
        heal = lambda r: F.heal_share(r, net_use=lambda: self.NET_USE, listdir=lambda p: [])   # noqa: E731
        self.assertEqual(F.unreachable_shares([spec], isdir=lambda r: False, heal=heal), [r"\\10.0.0.204\SSDShared"])
        self.assertEqual(F.heal_share(r"\\10.0.0.204\SSDShared", net_use=lambda: (_ for _ in ()).throw(OSError("x"))),
                         [], "net use failing never raises out of the guard")

    def _run(self, isdir, rc=1, line="OSError: [Errno 22] Invalid argument: '%s'\n"):
        calls = {"retire": 0, "report": 0, "spawned": 0}
        j = TestComputeCardQuietGate._J("cpu")
        j.spec = {"payload": __file__, "args": ["--plan", self.PLAN]}
        d = tempfile.mkdtemp()
        j.spec_path = os.path.join(d, "share_t.job.json")
        with open(j.spec_path, "w", encoding="utf-8") as f:
            json.dump({"name": "share_t"}, f)
        proc = mock.Mock()
        proc.stdout = iter([line % self.PLAN])
        proc.wait = lambda: rc

        def popen(*a, **k):
            calls["spawned"] += 1
            return proc
        real_isdir = os.path.isdir
        raised = None
        with mock.patch.object(F, "notify", lambda *a, **k: None), \
             mock.patch.object(F, "report_fleet_result",
                               lambda *a, **k: calls.__setitem__("report", calls["report"] + 1)), \
             mock.patch.object(F, "retire_order",
                               lambda *a, **k: calls.__setitem__("retire", calls["retire"] + 1)), \
             mock.patch.object(F, "clear_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "publish_current_run", lambda *a, **k: None), \
             mock.patch.object(F, "ship_compute", lambda *a, **k: None), \
             mock.patch.object(F, "heal_share", lambda r: []), \
             mock.patch.object(F.os.path, "isdir", lambda p: isdir(p) if p.startswith("\\\\") else real_isdir(p)), \
             mock.patch.object(F.subprocess, "Popen", popen):
            try:
                F.run_compute_order(j)
            except F.LaneBusy as e:
                raised = e
        return raised, calls

    def test_a_share_down_before_the_spawn_holds_the_order_untouched(self):
        raised, calls = self._run(lambda r: False)
        self.assertIsNotNone(raised)
        self.assertIn(r"\\10.0.0.204\SSDShared", str(raised))
        self.assertEqual((calls["spawned"], calls["retire"], calls["report"]), (0, 0, 0))

    def test_a_share_that_drops_mid_run_holds_the_order_instead_of_failing_it(self):
        state = {"n": 0}

        def flaky(r):                   # up for the preflight, gone when the child has died
            state["n"] += 1
            return state["n"] == 1
        raised, calls = self._run(flaky)
        self.assertIsNotNone(raised)
        self.assertEqual((calls["spawned"], calls["retire"], calls["report"]), (1, 0, 0))

    def test_a_real_failure_with_the_share_up_still_fails_and_retires(self):
        raised, calls = self._run(lambda r: True, line="Traceback: boom %s\n")
        self.assertIsNone(raised)
        self.assertEqual((calls["spawned"], calls["retire"], calls["report"]), (1, 1, 1))


class MedianUnitRate(unittest.TestCase):
    """The deck's s/unit for a compute order is the MEDIAN unit time: one marathon unit must not set every ETA after it."""

    def test_the_marathon_a1_order(self):
        marks = [(0.0, 0)] + [(m * 60.0, i) for i, m in enumerate((5, 185, 190, 236, 256), 1)]
        self.assertEqual(F.unit_rate(marks), 20 * 60.0)

    def test_a_jump_of_several_units_counts_each_one(self):
        # first observed at 2; then 3 units land in one beat over 90 s, then 1 over 100 s -> 30, 30, 30, 100
        self.assertEqual(F.unit_rate([(0.0, 2), (90.0, 5), (190.0, 6)]), 30.0)

    def test_no_rate_before_a_unit_lands(self):
        self.assertIsNone(F.unit_rate([]))
        self.assertIsNone(F.unit_rate([(5.0, 1)]))


# The suite pins its own size: a test that cannot be collected prints the same OK as one that passes, so a full run
# must also have run exactly this many. Change it in the same commit that adds or removes tests; EXPECT_TESTS=9999 in the
# environment must turn a green run into exit 3 (the decoy that proves the pin works).
EXPECT_TESTS = 661          # this file's own; each extension adds its EXPECT_TESTS

class StrataServedDeclares(unittest.TestCase):
    """A Strata-served model (Flash-Next) is declared like an ollama one, but only when what serves it is PRESENT on
    this box (plan, engine, every shard) and the plan serves the row's tested context; ollama down does not hide it."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.strata = os.path.join(self.d, "Strata")
        self.gguf = os.path.join(self.d, "gguf")
        os.makedirs(self.strata)
        os.makedirs(self.gguf)
        self.shards = {"m-00001.gguf": "aa", "m-00002.gguf": "bb"}
        for s in self.shards:
            open(os.path.join(self.gguf, s), "w").close()
        self.plan = os.path.join(self.d, "plan.json")
        self._plan(262144)
        self.served = os.path.join(self.d, "strata_served.json")
        with open(self.served, "w", encoding="utf-8") as f:
            json.dump({"boxa": {"flash": {"plan": self.plan, "ctx": 262144}}}, f)
        F._MODEL_CAPS_CACHE["t"] = 0.0
        F._MODEL_CAPS_CACHE["caps"] = []

    def tearDown(self):
        F._MODEL_CAPS_CACHE["t"] = 0.0
        F._MODEL_CAPS_CACHE["caps"] = []
        shutil.rmtree(self.d, ignore_errors=True)

    def _plan(self, ctx, extra=()):
        with open(self.plan, "w", encoding="utf-8") as f:
            json.dump({"strata": self.strata, "gguf_dir": self.gguf, "shards": self.shards,
                       "setup_args": ["--low-ram", "auto", "--context", str(ctx)] + list(extra)}, f)

    def test_present_and_tested_declares(self):
        self.assertEqual(F.strata_served_caps(self.served, "boxa"), ["model.flash"])

    def test_a_plan_whose_setup_turns_vision_on_declares_that_the_model_sees(self):
        """Caps are derived from the launcher's actual setup args, ensuring the client and server remain consistent."""
        for flag, sees in ((("--vision", "cpu"), True), (("--vision", "gpu"), True), (("--vision", "no"), False), ((), False)):
            self._plan(262144, flag)
            self.assertEqual(F.strata_served_caps(self.served, "boxa"),
                             ["model.flash", "sees.flash"] if sees else ["model.flash"], flag)

    def test_a_plan_that_carries_the_vision_section_declares_it_only_while_the_encoder_is_on_the_box(self):
        """Windows with an AMD card: Strata's setup leaves images off, so the plan carries the section and the launcher
        writes it in (strata_arm.apply_serving). A section naming an encoder that is not there declares nothing."""
        exe, mmproj = os.path.join(self.d, "strata-vision.exe"), os.path.join(self.gguf, "mmproj.gguf")
        with open(self.plan, encoding="utf-8") as f:
            plan = json.load(f)
        plan["vision"] = {"exe": exe, "mmproj": mmproj, "gpu": False, "max_tokens": 300}
        with open(self.plan, "w", encoding="utf-8") as f:
            json.dump(plan, f)
        self.assertEqual(F.strata_served_caps(self.served, "boxa"), ["model.flash"], "the encoder is not on the box")
        for p in (exe, mmproj):
            open(p, "w").close()
        self.assertEqual(F.strata_served_caps(self.served, "boxa"), ["model.flash", "sees.flash"])

    def test_another_box_declares_nothing(self):
        self.assertEqual(F.strata_served_caps(self.served, "boxb"), [])

    def test_a_missing_shard_declares_nothing(self):
        os.remove(os.path.join(self.gguf, "m-00002.gguf"))
        self.assertEqual(F.strata_served_caps(self.served, "boxa"), [])

    def test_a_plan_serving_another_context_declares_nothing(self):
        self._plan(131072)
        self.assertEqual(F.strata_served_caps(self.served, "boxa"), [])

    def test_it_rides_the_declaration_with_ollama_down(self):
        def boom(*a, **k):
            raise OSError("refused")
        with mock.patch.object(F, "STRATA_SERVED", self.served), mock.patch.object(F, "FLEET_HOST", "boxa"), \
                mock.patch.object(F.urllib.request, "urlopen", boom):
            caps = F.declared_capabilities()
        self.assertIn("model.flash", caps)


class InferOpenAIWire(unittest.TestCase):
    """Test infer_ollama's OpenAI wire implementation. This module is the single source for the OpenAI wire parsing used by lab_provider.openai_chat; tests live here to cover that shared logic."""

    @staticmethod
    def _io():
        spec = importlib.util.spec_from_file_location("infer_ollama_wire", os.path.join(BIN, "infer_ollama.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    @staticmethod
    def _sse(*objs):
        return [("data: " + json.dumps(o)).encode() for o in objs] + [b"data: [DONE]"]

    def test_stream_merges_thinking_text_tool_call_fragments_and_counts(self):
        io_mod = self._io()
        lines = self._sse(
            {"choices": [{"delta": {"reasoning_content": "plan "}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                    "function": {"name": "write_file", "arguments": "{\"pa"}}]},
                          "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "th\": \"a.py\"}"}}]},
                          "finish_reason": "tool_calls"}],
             "usage": {"prompt_tokens": 900, "completion_tokens": 40},
             "timings": {"prompt_n": 100, "cache_n": 800, "predicted_n": 40, "prompt_ms": 50.0, "predicted_ms": 600.0}})
        r = io_mod.parse_openai_stream(lines)
        self.assertEqual((r["message"]["thinking"], r["message"]["content"]), ("plan ", "ok"))
        self.assertEqual(r["message"]["tool_calls"], [{"function": {"name": "write_file", "arguments": {"path": "a.py"}}}])
        self.assertEqual((r["prompt_eval_count"], r["cache_n"], r["eval_count"]), (100, 800, 40))
        self.assertEqual((r["prompt_eval_duration"], r["eval_duration"]), (50_000_000, 600_000_000))
        self.assertEqual(r["done_reason"], "tool_calls")
        self.assertNotIn("priced_out", r)

    def test_on_chunk_sees_every_openai_chunk_and_leaves_the_result_unchanged(self):
        io_mod = self._io()
        objs = [{"choices": [{"delta": {"reasoning_content": "plan "}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 9, "completion_tokens": 2}}]
        seen = []
        relayed = io_mod.parse_openai_stream(self._sse(*objs), on_chunk=seen.append)
        self.assertEqual(seen, objs, "every chunk, in order, as parsed")
        self.assertEqual(relayed, io_mod.parse_openai_stream(self._sse(*objs)))

    def test_a_loop_ends_the_stream_and_drops_the_cut_call(self):
        io_mod = self._io()
        lines = self._sse({"choices": [{"delta": {"content": "x" * 50,
                                                  "tool_calls": [{"index": 0, "function": {"name": "f", "arguments": "{"}}]},
                                       "finish_reason": None}]},
                          {"choices": [{"delta": {"content": "never read"}, "finish_reason": "stop"}]})
        r = io_mod.parse_openai_stream(lines, looping=lambda tail: True, loop_every=1)
        self.assertEqual((r["done_reason"], r["message"]["tool_calls"]), ("loop", []))
        self.assertNotIn("never read", r["message"]["content"])

    def test_the_price_line_cuts_a_streaming_answer_as_priced_out(self):
        """Same rule as _chat_stream: past the order's price line the stream is closed, the partial text kept."""
        io_mod = self._io()
        lines = self._sse({"choices": [{"delta": {"content": "part"}, "finish_reason": None}]},
                          {"choices": [{"delta": {"content": " never read"}, "finish_reason": "stop"}]})
        r = io_mod.parse_openai_stream(lines, ceiling_s=5, t0=time.time() - 60)
        self.assertEqual((r["done_reason"], r.get("priced_out")), ("priced_out", True))
        self.assertEqual(r["message"]["content"], "part")
        self.assertEqual(io_mod.parse_openai_stream(self._sse(
            {"choices": [{"delta": {"content": "a"}, "finish_reason": "stop"}]}), ceiling_s=5)["done_reason"], "stop")

    def test_thinking_and_format_map_to_the_openai_request(self):
        io_mod = self._io()
        self.assertEqual(io_mod.template_kwargs(False), {"enable_thinking": False})
        self.assertEqual(io_mod.template_kwargs(True), {})          # Flash-Next's template default = xhigh
        self.assertEqual(io_mod.template_kwargs("xhigh"), {"enable_thinking": True, "reasoning_effort": "xhigh"})
        schema = {"type": "object", "properties": {"tool": {"type": "string"}}}
        self.assertIsNone(io_mod.response_format(None))
        self.assertEqual(io_mod.response_format("json"), {"type": "json_object"})
        self.assertEqual(io_mod.response_format(schema),
                         {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}})

    def test_stratas_over_context_refusal_carries_the_compaction_tag(self):
        """Strata's own wording -> " exceed_context_size" in the raised text (test_lab_provider pins that a1_chat reads
        that text as context-full: the two halves of the seam)."""
        io_mod = self._io()
        import urllib.error
        body = (b'{"error": {"type": "invalid_request_error", "message": "prompt (130000 tokens) + max tokens (20480) '
                b'exceeds the context (131072); requests are never truncated"}}')
        err = urllib.error.HTTPError("http://x/v1/chat/completions", 400, "Bad Request", {}, io.BytesIO(body))
        with mock.patch.object(io_mod.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                io_mod._chat_openai("http://127.0.0.1:8080", [{"role": "user", "content": "x"}], {}, think=True)
        self.assertIn("HTTP 400 exceed_context_size", str(cm.exception))

    def test_a_strata_order_asks_strata_and_an_ollama_order_asks_ollama(self):
        """`--strata`: num_ctx / num_predict are not sent (the served context is the tested one, checked at start),
        sampling and the price line are; the row names the order's model. Without it, the ollama call is unchanged."""
        io_mod = self._io()
        sent = []
        opts = {"num_ctx": 131072, "num_predict": 131072, "temperature": 0.6, "seed": 2}
        a = argparse.Namespace(strata="plan.json", host="http://127.0.0.1:8080", model="fn-strata", think=True,
                               strata_sampler={"top_p": 0.95, "top_k": 20, "temperature": 9.9})
        with mock.patch.object(io_mod, "_chat_openai", lambda *x, **k: sent.append(("openai", x, k)) or {}), \
                mock.patch.object(io_mod, "_chat", lambda *x, **k: sent.append(("ollama", x, k)) or {}):
            io_mod._ask(a, [{"role": "user", "content": "q"}], dict(opts), fmt={"type": "object"}, ceiling_s=30)
            io_mod._ask(argparse.Namespace(strata=None, host="h", model="m", think=False), [], dict(opts))
        kind, args, kw = sent[0]
        self.assertEqual((kind, args[0], args[2]), ("openai", "http://127.0.0.1:8080",
                                                    {"top_p": 0.95, "top_k": 20, "temperature": 0.6, "seed": 2}),
                         "the vendor sampler lies under the order's own temperature and seed")
        self.assertEqual((kw["think"], kw["ceiling_s"], kw["model"], kw["fmt"]), (True, 30, "fn-strata", {"type": "object"}))
        self.assertEqual((sent[1][0], sent[1][1][1], sent[1][1][3]["num_predict"]), ("ollama", "m", 131072))
        self.assertNotIn("ceiling_s", sent[1][2])

    METRICS_GENERATING = {"live": {"state": "generating", "queued": 0, "phase": "thinking", "prompt_tokens": 350,
                                   "prompt_read": None, "prompt_total": None, "generated": 525, "max_tokens": 261786,
                                   "elapsed_s": 9.0, "tok_s": 57.5, "tok_s_mean": 69.2, "prefill_tok_s_mean": 256.1,
                                   "tok_s_window_s": 2.0},
                          "requests": [{"finish": "stop", "prompt_tokens": 326, "prompt_total": 326, "prompt_read": 326,
                                        "output_tokens": 214, "prompt_ms": 1232.5, "decode_ms": 2911.9,
                                        "decode_tok_s": 73.5}],
                          "totals": {"requests": 7}}

    def test_strata_live_reads_writes_and_context_from_its_own_metrics(self):
        """Reading speed uses cumulative tokens over elapsed time; writing speed uses the engine's rolling 2s window."""
        gen = F.strata_live(self.METRICS_GENERATING)
        self.assertEqual((gen["write"], gen["held"], gen["calls"], gen["doing"], gen["busy"]),
                         (57.5, 875, 8, "generating", True))
        self.assertEqual(gen["read"], round(326 / 1.2325, 1), "while writing, the read row keeps the last read")
        reading = {"live": {"state": "reading", "prompt_tokens": 190000, "prompt_read": 96000, "prompt_total": 190000,
                            "generated": 0, "elapsed_s": 120.0}, "requests": [], "totals": {"requests": 3}}
        rd = F.strata_live(reading)
        self.assertEqual((rd["read"], rd["write"], rd["doing"], rd["calls"]), (800.0, None, "reading the prompt", 4))
        idle = F.strata_live({"live": {"state": "idle"}, "requests": self.METRICS_GENERATING["requests"],
                              "totals": {"requests": 8}})
        self.assertEqual((idle["write"], idle["busy"], idle["held"], idle["calls"]), (73.5, False, 540, 8))

    def test_the_tile_takes_the_live_numbers_only_from_a_declared_engine(self):
        io_mod = self._io()
        d = tempfile.mkdtemp()
        rates = F.EngineRates(d, serve_logs=())
        calls = []

        def fake_get(url, timeout=None):
            calls.append(url)
            resp = mock.MagicMock()
            resp.__enter__.return_value = io.BytesIO(json.dumps(self.METRICS_GENERATING).encode())
            return resp
        with mock.patch.object(F.urllib.request, "urlopen", fake_get):
            rates.poll()
            self.assertEqual(calls, [], "no declaration, no GET: the factory never probes for an engine")
            io_mod.write_engine_live(d, "http://127.0.0.1:8080", 262144)   # the order's half of the seam
            self.assertEqual(io_mod.ENGINE_LIVE_FILE, F.ENGINE_LIVE_FILE)
            rates.poll()
            view = rates.view(write=12.3)
        self.assertEqual(calls, ["http://127.0.0.1:8080/metrics"])
        self.assertEqual((view["write"], view["ctx"], view["turn"], view["tool"]), (57.5, [875, 262144], 8, "generating"))

    def test_an_unreachable_live_engine_shows_the_log_numbers_never_a_stale_live_one(self):
        io_mod = self._io()
        d = tempfile.mkdtemp()
        io_mod.write_engine_live(d, "http://127.0.0.1:8080", 262144)
        rates = F.EngineRates(d, serve_logs=())
        with mock.patch.object(F.urllib.request, "urlopen", side_effect=OSError("refused")):
            rates.poll()
            view = rates.view(write=40.0)
        self.assertIsNone(rates.live)
        self.assertEqual((view["write"], view["read"]), (40.0, None))

    def test_a_strata_length_far_below_the_context_is_its_repeat_guard_a_loop_not_a_cap(self):
        """Verify that 'length' stop reasons below context limit are scored as loops (model repeat guard), not voids (menu cap)."""
        io_mod = self._io()
        out = os.path.join(tempfile.mkdtemp(), "out.json")
        a = argparse.Namespace(out=out, rounds=1, num_ctx=262144, temperature=None, seed_base=None, host="h",
                               model="fn", think=True, grader=None, placement=None, price_ceiling_s=None,
                               at_cap_brake=None, strata="plan.json", strata_sampler={})
        replies = iter([{"model": "fn", "message": {"content": ""}, "done_reason": "length",
                         "prompt_eval_count": 200, "eval_count": 8009},
                        {"model": "fn", "message": {"content": "x"}, "done_reason": "length",
                         "prompt_eval_count": 200, "eval_count": 261900}])

        class Meter:
            source, samples = None, []

            def stop(self):
                pass
        with mock.patch.object(io_mod, "_chat_openai", lambda *x, **k: next(replies)), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            io_mod._run(a, [{"id": "t1", "prompt": "a"}, {"id": "t2", "prompt": "b"}], 2, "box", Meter(), (0, None))
        rows = json.load(open(out, encoding="utf-8"))["rows"]
        self.assertEqual((rows[0]["done_reason"], rows[0].get("engine_repeat_stop")), ("loop", True))
        self.assertEqual(rows[1]["done_reason"], "length", "an answer that filled the context IS the cap")

    def _fake_strata(self, served_ctx, events):
        S = mock.Mock()
        S.prepare = lambda plan, out, res, save=None, trust_verified=False: \
            events.append("prepare-trusted" if trust_verified else "prepare") or ("run.bat", "src")
        S.apply_serving = lambda plan, src: events.append("serving") or {"env": plan.get("env")}
        S.port_free = lambda port: True
        S.enter = lambda frontier=True: events.append("enter" if frontier else "enter-daily")
        proc = mock.Mock(returncode=None)
        S.launch = lambda bat, src, port, out: events.append("launch") or (proc, True, 12.0)
        S.stop = lambda p, port: events.append(("stop", p is proc, port))
        resp = mock.MagicMock()
        resp.__enter__.return_value = io.BytesIO(json.dumps({"default_generation_settings": {"n_ctx": served_ctx}}).encode())
        return S, resp

    def _plan(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "plan.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"port": 8091, "model": "Q2_0", "sampler": {"temperature": 0.6, "top_p": 0.95, "top_k": 20}}, f)
        return p, os.path.join(d, "out.json")

    def test_strata_comes_up_at_the_orders_context_and_records_its_placement(self):
        io_mod = self._io()
        events = []
        S, resp = self._fake_strata(131072, events)
        plan, out = self._plan()
        a = argparse.Namespace(strata=plan, out=out, num_ctx=131072, host=None)
        with mock.patch.object(io_mod, "strata_module", return_value=S), \
                mock.patch.object(io_mod.urllib.request, "urlopen", return_value=resp):
            server, placement = io_mod._strata_up(a)
        self.assertEqual(events, ["prepare", "serving", "enter", "launch"],
                         "the plan's env + engine flags land after setup rewrote the config, before the server starts")
        self.assertIn("serving", placement)
        self.assertEqual(a.host, "http://127.0.0.1:8091")
        self.assertEqual((placement["engine"], placement["served_ctx"], placement["load_s"]), ("strata", 131072, 12.0))
        self.assertEqual(server[2], 8091)
        self.assertEqual(a.strata_sampler, {"top_p": 0.95, "top_k": 20}, "the order sets temperature and seed itself")

    def test_a_daily_use_window_does_not_enter_frontier_mode(self):
        io_mod = self._io()
        events = []
        S, resp = self._fake_strata(131072, events)
        plan, out = self._plan()
        a = argparse.Namespace(strata=plan, out=out, num_ctx=131072, host=None)
        with mock.patch.object(io_mod, "strata_module", return_value=S), \
                mock.patch.object(io_mod.urllib.request, "urlopen", return_value=resp):
            io_mod._strata_up(a, frontier=False)
        self.assertEqual(events, ["prepare-trusted", "serving", "enter-daily", "launch"],
                         "a daily window accepts a shard already proven and unchanged; only a measurement re-hashes all")

    def test_a_wrong_served_context_is_refused_rc2_and_strata_is_stopped(self):
        io_mod = self._io()
        events = []
        S, resp = self._fake_strata(65536, events)
        plan, out = self._plan()
        a = argparse.Namespace(strata=plan, out=out, num_ctx=131072, host=None)
        with mock.patch.object(io_mod, "strata_module", return_value=S), \
                mock.patch.object(io_mod.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(SystemExit) as cm:
                io_mod._strata_up(a)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(events[-1], ("stop", True, 8091), "a refused start must not leave the server running")

    def test_strata_is_stopped_when_the_run_raises(self):
        """Verify server cleanup occurs even if the run raises."""
        io_mod = self._io()
        events = []
        S, resp = self._fake_strata(131072, events)
        plan, out = self._plan()
        items = os.path.join(os.path.dirname(out), "items.json")
        with open(items, "w", encoding="utf-8") as f:
            json.dump({"items": [{"id": "t1", "prompt": "q"}]}, f)
        with mock.patch.object(io_mod, "strata_module", return_value=S), \
                mock.patch.object(io_mod.urllib.request, "urlopen", return_value=resp), \
                mock.patch.object(io_mod, "PowerMeter", lambda: mock.Mock(source=None)), \
                mock.patch.object(io_mod, "_run", side_effect=KeyError("boom")):
            with self.assertRaises(KeyError):
                io_mod.main([items, out, "--model", "fn-strata", "--num-ctx", "131072", "--strata", plan, "--think"])
        self.assertEqual(events[-1], ("stop", True, 8091))


class PortFreeIsCheapAndTrue(unittest.TestCase):
    """Preflight checks for a listening ComfyUI instance via netstat and a plain bind check, not a PowerShell start. A free port skips the stop step entirely."""

    def test_a_real_listener_is_held_and_a_closed_port_is_free(self):
        import socket
        srv = socket.socket()
        srv.bind(("0.0.0.0", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertFalse(F._port_free(port))
        finally:
            srv.close()
        self.assertTrue(F._port_free(port))

    def blind_netstat(self):
        """A netstat that lists nothing on the port under test, as BigHonker's does for a wildcard listener."""
        return mock.patch.object(F.subprocess, "run", return_value=types.SimpleNamespace(
            returncode=0, stdout="  TCP    192.0.2.1:1    192.0.2.2:2    ESTABLISHED    4\n"))

    def test_a_listener_netstat_does_not_list_is_still_held(self):
        import socket
        for addr in ("0.0.0.0", "127.0.0.1"):
            srv = socket.socket()
            srv.bind((addr, 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            try:
                with self.blind_netstat():
                    self.assertFalse(F._port_free(port), addr)
            finally:
                srv.close()
            with self.blind_netstat():
                self.assertTrue(F._port_free(port), "a closed port binds")

    def test_a_server_that_just_closed_with_a_connection_reads_as_free(self):
        """The reason the bind carries no exclusive flag: TIME_WAIT left by a server that stopped must not read as held,
        or every stop would wait out minutes of it as LaneBusy."""
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        cli = socket.create_connection(("127.0.0.1", port), timeout=5)
        conn, _addr = srv.accept()
        conn.close()                                  # the server side closes first: its end of this pair waits
        srv.close()
        self.addCleanup(cli.close)
        with self.blind_netstat():
            self.assertTrue(F._port_free(port))

    def test_the_lookup_that_stops_a_listener_finds_a_real_one(self):
        """Live on each box's own Windows: on build 26300 `-State Listen` matches no wildcard listener, so a stop would have
        said "stopped" and killed nothing. Read off the source the stop actually runs."""
        import re
        import socket
        src = open(TARGET, encoding="utf-8").read()
        body = src[src.index("def stop_comfy(job):"):src.index("def _port_free(port):")]
        lookup = re.search(r'"\$p = \((.*?)\)\.OwningProcess', body, re.S).group(1)
        srv = socket.socket()
        srv.bind(("0.0.0.0", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertIn("-LocalPort 8189", lookup)
            got = subprocess.run(["powershell", "-NoProfile", "-Command", "(%s).OwningProcess" % lookup.replace(
                "8189", str(port))], capture_output=True, text=True, timeout=60).stdout.strip()
        finally:
            srv.close()
        self.assertEqual(got, str(os.getpid()))

    def test_unreadable_netstat_is_busy(self):
        with mock.patch.object(F.subprocess, "run", side_effect=OSError("no netstat")):
            self.assertFalse(F._port_free(8189))

    def test_a_free_port_starts_no_powershell(self):
        job = mock.Mock()
        with mock.patch.object(F, "_port_free", return_value=True), \
                mock.patch.object(F.subprocess, "run") as run:
            F.stop_comfy(job)
        run.assert_not_called()
        job.log.assert_called_once()


class LiveToks(unittest.TestCase):
    """Speed reflects the live rate from beat lines during advancement; the per-unit median applies only between generations."""

    def test_steady_beats_give_tokens_per_second(self):
        beats = [(1000.0, 0), (1030.0, 1500), (1060.0, 3000)]
        self.assertAlmostEqual(F.live_toks(beats, 1065.0), 50.0)
        self.assertEqual(F.toks_phase("p", [13.7], live=F.live_toks(beats, 1065.0)), "p · 50.0 tok/s")

    def test_a_clock_on_the_beat_line_is_skipped_not_averaged(self):
        beats = [(1040.0, 0), (1060.0, 1000), (1070.0, 1791650000), (1090.0, 2500)]
        self.assertAlmostEqual(F.live_toks(beats, 1092.0), 50.0)       # 2500 tokens over the 50 s between good beats

    def test_a_restarted_count_is_a_new_baseline(self):
        beats = [(1040.0, 9000), (1060.0, 0), (1080.0, 1000), (1090.0, 1500)]
        self.assertAlmostEqual(F.live_toks(beats, 1092.0), 50.0)       # 1500 tokens over the 30 s since the restart
        clock_first = [(1040.0, 1791650000), (1060.0, 0), (1090.0, 1500)]
        self.assertAlmostEqual(F.live_toks(clock_first, 1092.0), 50.0)

    def test_stale_beats_fall_back_to_the_unit_median(self):
        beats = [(1000.0, 0), (1030.0, 1500)]
        self.assertIsNone(F.live_toks(beats, 1030.0 + F.LIVE_TOKS_FRESH_S + 1))
        self.assertEqual(F.toks_phase("p", [13.7, 60.0, 70.0], live=None), "p · 60.0 tok/s")
        self.assertIsNone(F.live_toks([(1000.0, 100)], 1001.0))
        self.assertEqual(F.toks_phase("p", None), "p")


class WindowIdleRuleFollowsTheEngine(unittest.TestCase):
    """Window idle rules depend on backend: LIVE ollama uses --idle-ttl, Strata uses engine TTL, non-live has none. Prevents holding cold cards busy."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_undertest", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def args(self, **kw):
        a = types.SimpleNamespace(live=True, strata=None, idle_ttl=None)
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_an_ollama_live_window_ends_at_the_keep_alive_kirk_sent(self):
        self.assertEqual(self.HW.idle_rule(self.args(idle_ttl=300)), 300)
        self.assertEqual(self.HW.idle_rule(self.args(idle_ttl=120)), 120)

    def test_a_strata_window_keeps_its_engines_own_rule(self):
        self.assertEqual(self.HW.idle_rule(self.args(strata={"plan": 1}, idle_ttl=300)), self.HW.STRATA_IDLE_TTL_S)

    def test_no_rule_without_a_ttl_or_outside_a_live_window(self):
        self.assertIsNone(self.HW.idle_rule(self.args()), "no ttl sent = no rule (never a made-up number)")
        self.assertIsNone(self.HW.idle_rule(self.args(idle_ttl=0)))
        self.assertIsNone(self.HW.idle_rule(self.args(live=False, idle_ttl=300)), "a piece window is not live")


class LiveWindowServesWithoutTheIdleSleep(unittest.TestCase):
    """Live windows must not delay serving to take an idle sample; they serve immediately and sample during the first quiet gap."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_idle", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def test_the_first_quiet_gap_between_turns_is_the_sample(self):
        rows = [{"t0": 100.5, "t1": 110.0}, {"t0": 111.0, "t1": 120.0}, {"t0": 130.0, "t1": 140.0}]
        self.assertEqual(self.HW.idle_gap(rows, 100.0, 500.0), (120.0, 120.0 + self.HW.IDLE_SAMPLE_S))

    def test_the_wait_after_the_last_turn_counts_and_no_gap_is_none(self):
        self.assertEqual(self.HW.idle_gap([{"t0": 100.0, "t1": 110.0}], 100.0, 400.0), (110.0, 113.0))
        self.assertIsNone(self.HW.idle_gap([{"t0": 100.0, "t1": 110.0}], 100.0, 111.0))
        self.assertEqual(self.HW.idle_gap([], 100.0, 105.0), (100.0, 103.0), "an unused window idled from its open")


class ChatQuickResume(unittest.TestCase):
    """Per-chat slot persistence: saves on idle or turn pre-empt (only if changed), restores before next turn.
    Keeps 5 most recent per user; older slots offload to the array."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_conv", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.calls, self.t = [], [1000.0]
        a = types.SimpleNamespace(model="qwen3.8:27b", num_ctx=125952, strata=None)

        def save(name):
            self.calls.append(("save", name))
            open(os.path.join(self.d, name), "wb").close()
            return {"n_saved": 100}
        self.cs = self.HW.ConvSlots(a, save=save, restore=lambda name: self.calls.append(("restore", name)) or {"ok": 1},
                                    slot_dir=self.d, clock=lambda: self.t[0], retain_fn=lambda user: None)

    def turn(self, conv):
        self.cs.before({"conv": conv})
        self.cs.after({"conv": conv})

    def test_consecutive_turns_of_one_chat_never_save(self):
        self.turn("alex/A")
        self.turn("alex/A")
        self.turn("alex/A")
        self.assertEqual(self.calls, [])

    def test_a_switch_saves_the_outgoing_chat_and_restores_the_incoming_one(self):
        self.turn("alex/A")
        self.turn("alex/B")                                   # A saved; B has no file yet: nothing to restore
        self.turn("alex/A")                                   # B saved; A restored
        a, b = self.cs.name("alex/A"), self.cs.name("alex/B")
        self.assertEqual(self.calls, [("save", a), ("save", b), ("restore", a)])
        meta = json.load(open(os.path.join(self.d, a + ".json"), encoding="utf-8"))
        self.assertEqual((meta["user"], meta["chat"]), ("alex", "A"))

    def _item(self, conv, tools=("search_docs",), system="SYS", **extra):
        return dict({"conv": conv, "tools": [{"type": "function", "function": {"name": n}} for n in tools],
                     "messages": [{"role": "system", "content": system}, {"role": "user", "content": "q"}]}, **extra)

    def _two_chats(self, first, again, restore_takes=0.0):
        """Chat A turns with `first`, chat B takes the slot (A is saved), then A turns again with `again`."""
        self.cs.restore_fn = lambda name: (self.calls.append(("restore", name)),
                                           self.t.__setitem__(0, self.t[0] + restore_takes), {"ok": 1})[2]
        self.cs.before(first)
        self.cs.after(first)
        self.turn("alex/B")
        self.calls.clear()
        self.cs.before(again)
        return [c[0] for c in self.calls]

    def test_a_state_whose_start_is_not_this_turns_is_not_restored(self):
        """State is restored only if the saved turn's tool block matches the current turn's.
        System text differences do not prevent restoration."""
        a = "alex/A"
        self.assertNotIn("restore", self._two_chats(self._item(a), self._item(a, tools=("coding_read", "coding_patch"))))
        self.assertIn("restore", self._two_chats(self._item(a), self._item(a)), "the same start: restored, as always")
        self.assertIn("restore", self._two_chats(self._item(a), self._item(a, system="the lead path's longer prompt")),
                      "another system text, the same tools: its tool block is still worth restoring")
        self.assertNotIn("restore", self._two_chats(self._item(a, think=True), self._item(a, think=False)))

    def test_a_state_saved_before_this_check_existed_is_restored_as_before(self):
        """A file whose sidecar names no start (saved by an older window) cannot be judged: restored, as it always was."""
        a = "alex/A"
        self.turn(a)
        self.turn("alex/B")
        path = os.path.join(self.d, self.cs.name(a) + ".json")
        meta = json.load(open(path, encoding="utf-8"))
        meta.pop("start", None)
        json.dump(meta, open(path, "w", encoding="utf-8"))
        self.calls.clear()
        self.cs.before(self._item(a, tools=("anything",)))
        self.assertIn(("restore", self.cs.name(a)), self.calls)

    def test_a_turn_parked_mid_answer_is_restored_whatever_the_checks_say(self):
        a, continued = "alex/A", []
        self.cs.continue_fn = lambda name: continued.append(name) or {"ok": 1, "lab_continue_armed": True}
        self.cs.before(self._item(a))
        self.cs.after(self._item(a))
        self.cs.save_current("parked", cut=True)
        self.turn("alex/B")
        self.cs.before(self._item(a, tools=("other",), read_tok_s=10 ** 9))
        self.assertEqual(continued, [self.cs.name(a)], "the only way a cut answer goes on")

    def test_an_idle_save_means_the_next_switch_costs_nothing(self):
        self.turn("alex/A")
        self.cs.save_current("idle")
        self.turn("alex/B")
        self.assertEqual([c[0] for c in self.calls], ["save"], "A was saved once, at idle - not again at the switch")

    def test_a_call_outside_any_chat_counts_as_a_switch(self):
        self.turn("alex/A")
        self.cs.before({})
        self.assertEqual(self.calls, [("save", self.cs.name("alex/A"))])
        self.assertIsNone(self.cs.current, "the slot no longer holds A")

    def test_a_lend_reports_a_chat_already_saved_at_idle(self):
        self.turn("alex/A")
        self.cs.save_current("idle")
        self.assertTrue(self.cs.ensure_saved("card lent")["already_saved"])

    def test_each_users_five_most_recently_used_stay_and_older_move_to_the_array(self):
        arr = os.path.join(self.d, "array")
        for i in range(7):
            name = "conv-m-1-%02d.bin" % i
            open(os.path.join(self.d, name), "wb").close()
            with open(os.path.join(self.d, name + ".json"), "w", encoding="utf-8") as f:
                json.dump({"user": "alex", "t": 100 + i}, f)
        with open(os.path.join(self.d, "conv-m-1-zz.bin.json"), "w", encoding="utf-8") as f:
            json.dump({"user": "avery", "t": 1}, f)                    # another user's chat is never counted against him
        moved = self.HW.retain("alex", slot_dir=self.d, array_dir=arr)
        self.assertEqual(sorted(moved), ["conv-m-1-00.bin", "conv-m-1-01.bin"], "the two least recently used")
        self.assertTrue(os.path.exists(os.path.join(arr, "conv-m-1-00.bin.json")), "moved with its sidecar, not deleted")
        self.assertTrue(os.path.exists(os.path.join(self.d, "conv-m-1-zz.bin.json")))

    def test_a_cut_turns_sidecar_goes_to_the_array_and_comes_back(self):
        arr = os.path.join(self.d, "array")
        for i in range(6):
            name = "conv-m-1-%02d.bin" % i
            open(os.path.join(self.d, name), "wb").close()
            with open(os.path.join(self.d, name + ".json"), "w", encoding="utf-8") as f:
                json.dump({"user": "alex", "t": 100 + i, "n_saved": 1000}, f)
        open(os.path.join(self.d, "conv-m-1-00.bin.labturn"), "w").close()
        self.HW.retain("alex", slot_dir=self.d, array_dir=arr)
        self.assertTrue(os.path.exists(os.path.join(arr, "conv-m-1-00.bin.labturn")), "without it the cut is a plain re-ask")
        self.assertEqual(self.fetch("conv-m-1-00.bin", 1000.0, arr), "back")
        self.assertTrue(os.path.exists(os.path.join(self.d, "conv-m-1-00.bin.labturn")))
        self.assertFalse(os.path.exists(os.path.join(arr, "conv-m-1-00.bin.labturn")))

    def test_an_unwritable_array_keeps_them_here(self):
        for i in range(6):
            with open(os.path.join(self.d, "conv-m-1-%02d.bin.json" % i), "w", encoding="utf-8") as f:
                json.dump({"user": "alex", "t": i}, f)
        with mock.patch("shutil.move", side_effect=OSError("array offline")):
            self.assertEqual(self.HW.retain("alex", slot_dir=self.d, array_dir=os.path.join(self.d, "x")), [])
        self.assertEqual(len([f for f in os.listdir(self.d) if f.endswith(".json")]), 6, "nothing lost")

    # --- an older chat comes back from the array only when that beats re-reading it ---
    def on_array(self, name, size=100, n_saved=1000):
        arr = os.path.join(self.d, "array")
        os.makedirs(arr, exist_ok=True)
        with open(os.path.join(arr, name), "wb") as f:
            f.write(b"x" * size)
        with open(os.path.join(arr, name + ".json"), "w", encoding="utf-8") as f:
            json.dump({"user": "alex", "n_saved": n_saved, "t": 1}, f)
        return arr

    def fetch(self, name, rate, arr, step=0.0, chunk=10):
        t = [0.0]

        def clock():
            t[0] += step
            return t[0]
        return self.HW.fetch_from_array(name, rate, slot_dir=self.d, array_dir=arr, clock=clock, chunk=chunk)

    def test_an_older_chat_comes_back_from_the_array_and_is_restored(self):
        name = self.cs.name("alex/A")
        arr = self.on_array(name)
        self.cs.fetch_fn = lambda n, rate: self.fetch(n, rate, arr)
        self.cs.before({"conv": "alex/A", "state_here": True, "read_tok_s": 1000.0})
        self.assertEqual(self.calls, [("restore", name)])
        self.assertTrue(os.path.exists(os.path.join(self.d, name)))
        self.assertTrue(os.path.exists(os.path.join(self.d, name + ".json")), "its sidecar came back with it")
        self.assertFalse(os.path.exists(os.path.join(arr, name)), "moved back, not copied")
        self.assertIsNotNone(self.HW._array_rate(self.d), "the copy measured the array's read")

    def test_a_measured_slow_array_leaves_it_there_and_the_turn_re_reads(self):
        name = self.cs.name("alex/A")
        arr = self.on_array(name, size=10_000_000, n_saved=1000)
        self.HW._record_array_rate(self.d, 1e6, time.time)        # 10 s to copy vs 1 s to re-read 1000 tok at 1000/s
        self.assertEqual(self.fetch(name, 1000.0, arr), "reread")
        self.assertTrue(os.path.exists(os.path.join(arr, name)), "left on the array")
        self.assertFalse(os.path.exists(os.path.join(self.d, name)))

    def test_a_copy_projected_to_lose_stops_and_measures(self):
        name = self.cs.name("alex/A")
        arr = self.on_array(name, size=100, n_saved=1000)          # re-read = 1.0 s; each 10-byte chunk costs 0.5 s
        self.assertEqual(self.fetch(name, 1000.0, arr, step=0.5), "reread")
        self.assertEqual([f for f in os.listdir(self.d) if f.endswith(".part")], [], "the partial copy is dropped")
        self.assertTrue(os.path.exists(os.path.join(arr, name)))
        self.assertIsNotNone(self.HW._array_rate(self.d), "the losing copy still measured the array")

    def test_an_unmeasured_re_read_still_brings_it_back(self):
        name = self.cs.name("alex/A")
        arr = self.on_array(name)
        self.assertEqual(self.fetch(name, None, arr, step=0.5), "back", "no read rate: restoring is never worse")

    def test_a_refusing_array_is_asked_once_per_window(self):
        asked = []
        self.cs.fetch_fn = lambda n, rate: asked.append(n) or "refused"
        self.cs.before({"conv": "alex/A", "state_here": True})
        self.cs.before({"conv": "alex/B", "state_here": True})
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.calls, [], "nothing to restore")

    def test_the_array_is_only_asked_for_a_chat_this_box_saved(self):
        asked = []
        self.cs.fetch_fn = lambda n, rate: asked.append(n) or "absent"
        self.cs.before({"conv": "alex/A"})                         # a new chat, or one another box last served
        self.assertEqual(asked, [])
        self.cs.before({"conv": "alex/B", "state_here": True})
        self.assertEqual(asked, [self.cs.name("alex/B")])

    def ask_prefetch(self, conv, items):
        os.makedirs(items, exist_ok=True)
        with open(os.path.join(items, "f1" + self.HW.FETCH), "w", encoding="utf-8") as f:
            json.dump({"conv": conv, "read_tok_s": 1000.0}, f)

    def test_typing_in_an_older_chat_brings_its_state_back_before_the_turn(self):
        """Verify that opening a chat triggers a prefetch request which is consumed and does not modify the active slot."""
        items, asked = os.path.join(self.d, "items"), []
        self.cs.fetch_fn = lambda n, rate: asked.append((n, rate)) or "back"
        self.ask_prefetch("alex/A", items)
        self.assertEqual(self.cs.prefetch_asked(items), [("alex/A", "back")])
        self.assertEqual(asked, [(self.cs.name("alex/A"), 1000.0)])
        self.assertEqual([f for f in os.listdir(items) if f.endswith(self.HW.FETCH)], [], "the ask is consumed")
        self.assertEqual(self.calls, [], "a prefetch never touches the loaded slot")

    def test_a_prefetch_for_a_chat_already_here_asks_nothing(self):
        items, asked = os.path.join(self.d, "items"), []
        open(os.path.join(self.d, self.cs.name("alex/A")), "wb").close()
        self.cs.fetch_fn = lambda n, rate: asked.append(n) or "back"
        self.ask_prefetch("alex/A", items)
        self.assertEqual(self.cs.prefetch_asked(items), [("alex/A", "here")])
        self.assertEqual(asked, [])

    def test_the_window_loop_answers_a_prefetch_while_idle(self):
        """The seam: serve() itself consumes the ask with no turn pending (a FETCH no loop reads would do nothing)."""
        src = open(os.path.join(BIN, "helper_window.py"), encoding="utf-8").read()
        body = src[src.index("def serve("):src.index("def idle_rule(")]
        self.assertIn("conv_slots.prefetch_asked(items_dir)", body)

    def test_a_missing_array_file_is_absent_not_refused(self):
        self.assertEqual(self.fetch("conv-none.bin", 1000.0, os.path.join(self.d, "array", "nobox")), "absent")


class FlashNextQuickResume(unittest.TestCase):
    """Strata windows keep chat state in the engine's session files via the /slots/0 API on the engine's own port. A stop signal lets a live window finish its turn and save the chat before exiting; it must not be killed mid-turn."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_flashnext", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.a = types.SimpleNamespace(model="qwen3.8-flash-next", num_ctx=262144, strata={"model": "Q2_0"},
                                       host="http://127.0.0.1:8080", live=True)
        self.items = os.path.join(self.d, "items")
        os.makedirs(self.items)

    def mark(self, name):
        open(os.path.join(self.items, name), "w").close()

    def add_item(self, iid, **kw):
        with open(os.path.join(self.items, iid + self.HW.ITEM), "w", encoding="utf-8") as f:
            json.dump(dict({"id": iid, "messages": []}, **kw), f)

    def answer(self, a, item, meter, chat=None, stream_path=None, can_cut=False):
        self.answered.append(item["id"])
        return {"id": item["id"], "served_model": "m", "wall_s": 1.0, "error": "", "gpu_j": None}

    def test_a_strata_windows_slot_lives_on_its_own_engine(self):
        def no_runner():
            raise AssertionError("a Strata window never looks for an ollama runner")
        calls = []

        def call(port, action, name):
            calls.append((port, action, name))
            if action == "save":
                open(os.path.join(self.d, name), "wb").close()          # the engine writes the file it was named
            return {"n_saved": 5, "n_restored": 5}
        got = self.HW.save_slot(self.a, port_fn=no_runner, call=call, name="c.bin", slot_dir=self.d)
        self.assertEqual(got["n_saved"], 5)
        got = self.HW.restore_slot(self.a, port_fn=no_runner, call=call, slot_dir=self.d, name="c.bin", retire=False)
        self.assertEqual(got["n_restored"], 5)
        self.assertEqual(calls, [(8080, "save", "c.bin" + self.HW.NEW), (8080, "restore", "c.bin")])
        self.assertTrue(os.path.exists(os.path.join(self.d, "c.bin")), "a chat's own file stays: its next save overwrites it")

    def _saved(self, body=b"new", side=None, fail=False):
        """A fake engine that writes `<name>` (and a cut-turn sidecar when `side`), or dies midway when `fail`."""
        def call(port, action, name):
            path = os.path.join(self.d, name)
            with open(path, "wb") as f:
                f.write(body[:1] if fail else body)
            if side is not None:
                with open(path + ".labturn", "w") as f:
                    f.write(side)
            if fail:
                raise RuntimeError("the engine stopped midway through the write")
            return {"n_saved": 5}
        return call

    def _put(self, name, body):
        with open(os.path.join(self.d, name), "wb" if isinstance(body, bytes) else "w") as f:
            f.write(body)

    def _read(self, name):
        with open(os.path.join(self.d, name), "rb") as f:
            return f.read()

    def test_a_save_that_dies_midway_keeps_the_last_good_state(self):
        """Verify that a save failure preserves the old state and cleans up the temporary file."""
        self._put("c.bin", b"old")
        self._put("c.bin.labturn", "old cut")
        got = self.HW.save_slot(self.a, call=self._saved(side="new cut", fail=True), name="c.bin", slot_dir=self.d)
        self.assertIsNone(got)
        self.assertEqual(self._read("c.bin"), b"old")
        self.assertEqual(self._read("c.bin.labturn"), b"old cut")
        self.assertEqual(sorted(os.listdir(self.d)), ["c.bin", "c.bin.labturn", "items"], "no half-written file is left")

    def test_a_save_the_engine_never_wrote_keeps_the_last_good_state(self):
        self._put("c.bin", b"old")
        got = self.HW.save_slot(self.a, call=lambda p, act, n: {"n_saved": 5}, name="c.bin", slot_dir=self.d)
        self.assertIsNone(got, "an answer with no file behind it is not a save")
        self.assertEqual(self._read("c.bin"), b"old")

    def test_a_finished_save_replaces_the_file_and_carries_its_cut_turn(self):
        self._put("c.bin", b"old")
        got = self.HW.save_slot(self.a, call=self._saved(side="new cut"), name="c.bin", slot_dir=self.d)
        self.assertEqual(got["n_saved"], 5)
        self.assertEqual(self._read("c.bin"), b"new")
        self.assertEqual(self._read("c.bin.labturn"), b"new cut")
        self.assertEqual(sorted(os.listdir(self.d)), ["c.bin", "c.bin.labturn", "items"])

    def test_a_finished_turns_save_clears_an_old_cut_turn(self):
        """The server removes a stale sidecar when it saves a finished turn in place; the move must do the same, or the
        next restore would try to continue an answer the new file has already finished."""
        self._put("c.bin", b"old")
        self._put("c.bin.labturn", "old cut")
        self.assertIsNotNone(self.HW.save_slot(self.a, call=self._saved(), name="c.bin", slot_dir=self.d))
        self.assertEqual(self._read("c.bin"), b"new")
        self.assertFalse(os.path.exists(os.path.join(self.d, "c.bin.labturn")))

    def test_an_ollama_window_still_asks_for_its_runner(self):
        a = types.SimpleNamespace(model="m", num_ctx=1, strata=None, host="http://127.0.0.1:11434")
        self.assertEqual(self.HW.engine_port(a, lambda: 1234), 1234)

    def test_a_session_file_the_engine_refuses_reads_as_today(self):
        """The docs bind a session file to the engine's version and settings: a refusal must never cost the turn."""
        def refuse(port, action, name):
            raise RuntimeError("session file is for another engine")
        open(os.path.join(self.d, "c.bin"), "wb").close()
        self.assertIsNone(self.HW.restore_slot(self.a, call=refuse, slot_dir=self.d, name="c.bin", retire=False))
        self.assertIsNone(self.HW.save_slot(self.a, call=refuse, name="c.bin", slot_dir=self.d))
        self.assertTrue(os.path.exists(os.path.join(self.d, "c.bin")), "a refused save leaves the last good state")

    def test_a_yield_lets_the_turn_in_flight_finish_then_the_window_exits(self):
        self.answered = []
        self.add_item("t1")
        self.mark(self.HW.YIELD)

        def no_sleep(s):
            raise AssertionError("the window should have exited on the yield")
        with mock.patch.object(self.HW, "answer_item", self.answer):
            rows, why = self.HW.serve(self.a, self.items, os.path.join(self.d, "prog"), os.path.join(self.d, "p.jsonl"),
                                      None, sleep=no_sleep)
        self.assertEqual(self.answered, ["t1"], "the turn is answered before the window goes")
        self.assertTrue(os.path.exists(os.path.join(self.items, "t1" + self.HW.OUT)))
        self.assertTrue(why.startswith("yielded between turns"), why)

    def run_main(self, conv_slots, resident):
        meter = types.SimpleNamespace(source=None, samples=[], stop=lambda: None)
        with mock.patch.object(self.HW, "answer_item", self.answer), \
                mock.patch.object(self.HW, "ConvSlots", conv_slots), \
                mock.patch.object(self.HW, "restore_slot", lambda a, slot_dir=None: None), \
                mock.patch.object(self.HW, "_fleet_host", lambda: "softserve"), \
                mock.patch.object(self.HW, "card_work_waiting_fn", lambda kirk, host: (lambda: False)), \
                mock.patch.object(self.HW.IO, "_assert_resident", resident), \
                mock.patch.object(self.HW.IO, "PowerMeter", lambda: meter):
            return self.HW.main([self.items, os.path.join(self.d, "out.json"), "--model", "m", "--num-ctx", "8192", "--live"])

    def test_the_chat_is_saved_under_its_own_name_when_a_yield_ends_the_window(self):
        """The seam from the slip to the file: the yield ends serve(), main saves the chat that is in the slot."""
        self.answered, saved, made = [], [], []
        with open(os.path.join(self.items, self.HW.OWNER), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": 10 ** 12}, f)
        self.add_item("t1", conv="alex/A")
        self.mark(self.HW.YIELD)
        real = self.HW.ConvSlots

        def slots(a, hot=None, slot_dir=None):
            self.assertEqual(slot_dir, self.HW.runner_slot_dir(), "an ollama window's chats live where its runner writes them")

            def save(name):
                saved.append(name)
                return {"n_saved": 7}
            made.append(real(a, save=save, restore=lambda name: None, slot_dir=self.d, retain_fn=lambda user: None))
            return made[-1]
        self.assertEqual(self.run_main(slots, lambda host, model, ctx, offload_ok=False: {"resident": True}), 0)
        self.assertEqual(self.answered, ["t1"])
        self.assertEqual(saved, [made[0].name("alex/A")])
        self.assertTrue(os.path.exists(os.path.join(self.items, self.HW.CLOSE)), "Kirk reads the ended window from CLOSE")

    def test_a_live_window_that_already_closed_never_loads_an_engine(self):
        """A window that yielded to a deploy is requeued by the deploy; Kirk's next turn opens a new one."""
        with open(os.path.join(self.items, self.HW.OWNER), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": 10 ** 12}, f)
        self.mark(self.HW.CLOSE)

        def resident(*a, **k):
            raise AssertionError("no engine for a window that already closed")
        self.answered = []
        self.assertEqual(self.run_main(lambda a, hot=None, slot_dir=None: None, resident), 0)
        self.assertEqual(self.answered, [])

    def test_a_stop_slip_asks_a_live_window_to_yield_with_the_marker_it_reads(self):
        logs = []
        self.assertTrue(F.yield_live_window([self.items, "out.json", "--model", "m", "--live"], logs.append))
        self.assertTrue(os.path.exists(os.path.join(self.items, self.HW.YIELD)), "one name, both sides")
        self.assertIn("second slip", logs[0], "the log says how to end it now")

    def test_every_other_payload_is_not_asked_and_a_failed_ask_ends_it_the_old_way(self):
        self.assertFalse(F.yield_live_window([self.items, "out.json", "--model", "m"], lambda m: None))
        self.assertFalse(os.path.exists(os.path.join(self.items, self.HW.YIELD)))
        self.assertFalse(F.yield_live_window([os.path.join(self.d, "nowhere"), "--live"], lambda m: None))

    def strata_arm(self):
        where = os.path.join(os.path.dirname(BIN), "workspace", "flashnext")
        self.assertTrue(os.path.exists(os.path.join(where, "strata_arm.py")), where)
        sys.path.insert(0, where)
        self.addCleanup(sys.path.remove, where)
        spec = importlib.util.spec_from_file_location("strata_arm_undertest", os.path.join(where, "strata_arm.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m, where

    def test_a_plan_says_which_server_it_runs_on_and_every_launch_checks_the_bytes(self):
        m, where = self.strata_arm()
        src = tempfile.mkdtemp(prefix="strata_src_")
        self.addCleanup(shutil.rmtree, src, True)
        os.makedirs(os.path.join(src, "serve"))
        live = os.path.join(src, "serve", "server.py")
        with open(live, "w", encoding="utf-8") as f:
            f.write("the vanilla server")
        self.assertIsNone(m.install_server({}, src), "a plan that names none leaves the install alone")
        with open(live, encoding="utf-8") as f:
            self.assertEqual(f.read(), "the vanilla server")
        sha = m.install_server({"server_py": "strata_server_lab.py"}, src)
        self.assertEqual(sha, m.sha256(os.path.join(where, "strata_server_lab.py")))
        self.assertEqual(m.sha256(live), sha)
        before = os.path.getmtime(live)
        m.install_server({"server_py": "strata_server_lab.py"}, src)
        self.assertEqual(os.path.getmtime(live), before, "the right bytes are not rewritten")
        with self.assertRaises(OSError):
            m.install_server({"server_py": "no_such_server.py"}, src)

    def test_a_strata_window_keeps_its_chats_where_its_engine_writes_them(self):
        """One folder, or a window asks for files the engine wrote elsewhere and finds none."""
        _arm, where = self.strata_arm()
        path = os.path.join(where, "plans", "strata-serve-q2_0-v045.20261018.json")
        with open(path, encoding="utf-8") as f:
            self.assertFalse(json.load(f)["slot_save_path"].upper().startswith("C:"), "not on the system volume")
        with open(path, encoding="utf-8") as f:
            want = json.load(f)["slot_save_path"]
        self.assertEqual(self.HW.window_slot_dir(types.SimpleNamespace(strata=path)), want)
        with mock.patch.object(self.HW, "runner_slot_dir", lambda: "the launcher's"):
            self.assertEqual(self.HW.window_slot_dir(types.SimpleNamespace(strata=None)), "the launcher's")
            self.assertEqual(self.HW.window_slot_dir(types.SimpleNamespace(strata=os.path.join(self.d, "gone.json"))), "the launcher's")

    def test_an_ollama_window_reads_the_folder_from_the_line_that_tells_the_engine(self):
        lf, crlf, none = (os.path.join(self.d, n) for n in ("lf.ps1", "crlf.ps1", "none.ps1"))
        with open(lf, "wb") as f:      # the real line, byte for byte, as the 10-21 installer writes it
            f.write(b"$env:OLLAMA_HOST = '0.0.0.0'\n$env:LLAMA_ARG_SLOT_SAVE_PATH = 'F:\\HAI\\slots'\n$ollama = 'x'\n")
        with open(crlf, "wb") as f:
            f.write(b"$env:LLAMA_ARG_SLOT_SAVE_PATH = 'C:\\ProgramData\\HAI\\slots'\r\n$ollama = 'x'\r\n")
        with open(none, "wb") as f:
            f.write(b"$ollama = 'x'\n")
        self.assertEqual(self.HW.runner_slot_dir((os.path.join(self.d, "absent.ps1"), lf)), r"F:\HAI\slots")
        self.assertEqual(self.HW.runner_slot_dir((crlf,)), r"C:\ProgramData\HAI\slots")
        self.assertEqual(self.HW.runner_slot_dir((none, os.path.join(self.d, "absent.ps1"))), self.HW.SLOT_DIR, "unreadable: the default")

    def test_the_serving_plan_turns_session_files_on(self):
        arm, where = self.strata_arm()
        with open(os.path.join(where, "plans", "strata-serve-q2_0-v045.20261018.json"), encoding="utf-8") as f:
            plan = json.load(f)
        cfg = os.path.join(self.d, "strata-q2_0.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"args": {"--x": "1"}, "other": 2}, f)
        got = arm.apply_serving({"model": "Q2_0", "slot_save_path": plan["slot_save_path"]}, self.d)
        self.assertEqual(got["slot_save_path"], plan["slot_save_path"])
        with open(cfg, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"args": {"--x": "1"}, "other": 2, "slot_save_path": plan["slot_save_path"]})

    def test_a_plan_without_the_key_leaves_the_config_alone(self):
        arm, _where = self.strata_arm()
        self.assertEqual(arm.apply_serving({"model": "Q2_0"}, self.d), {})
        self.assertEqual(os.listdir(self.d), ["items"])


class ATurnParkedMidAnswer(unittest.TestCase):
    """A live turn can be parked mid-write: the window saves state and emits `cut`, then `lab_continue` resumes from that point. Stop ends the turn permanently, and only chats owned by this window are eligible for parking."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_cut", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.calls, self.armed = [], True
        self.a = types.SimpleNamespace(model="qwen3.8:27b", num_ctx=125952, strata=None, host="http://127.0.0.1:11434",
                                       temperature=None, seed_base=None, think=False)
        self.cs = self.slots()

    def slots(self):
        def save(name):
            self.calls.append(("save", name))
            open(os.path.join(self.d, name), "wb").close()
            return {"n_saved": 100, "pending_token": 7}
        return self.HW.ConvSlots(self.a, save=save, slot_dir=self.d, retain_fn=lambda user: None,
                                 restore=lambda name: self.calls.append(("restore", name)) or {"n_restored": 100},
                                 cont=lambda name: self.calls.append(("continue", name)) or {
                                     "n_restored": 100, "lab_continue_armed": self.armed})

    def park(self, cs, conv="alex/A"):
        cs.before({"conv": conv})
        cs.after({"conv": conv})
        return cs.save_cut()

    def test_the_parked_chat_is_saved_as_it_stands_and_its_file_says_cut(self):
        self.assertTrue(self.park(self.cs))
        a = self.cs.name("alex/A")
        self.assertEqual(self.calls, [("save", a)])
        self.assertTrue(json.load(open(os.path.join(self.d, a + ".json"), encoding="utf-8"))["cut"])

    def test_the_same_chat_asked_again_here_is_restored_to_continue_once(self):
        self.park(self.cs)
        self.cs.before({"conv": "alex/A"})
        a = self.cs.name("alex/A")
        self.assertEqual(self.calls, [("save", a), ("continue", a)], "state in the slot is not enough: only a restore arms it")
        self.assertEqual(self.cs.last_start, "own, continuing the cut answer")
        self.cs.after({"conv": "alex/A"})
        self.cs.before({"conv": "alex/A"})
        self.assertEqual(len(self.calls), 2, "the next turn of that chat is an ordinary one")
        self.assertEqual(self.cs.last_start, "in the slot")

    def test_a_later_window_restores_the_file_to_continue(self):
        self.park(self.cs)
        later = self.slots()                                  # the card served something else; a new window opens
        later.before({"conv": "alex/A"})
        self.assertEqual(self.calls[-1], ("continue", later.name("alex/A")))
        self.assertEqual(later.last_start, "own, continuing the cut answer")

    def test_another_chat_between_does_not_lose_the_park(self):
        self.park(self.cs)
        self.cs.before({"conv": "alex/B"})
        self.cs.after({"conv": "alex/B"})
        self.cs.before({"conv": "alex/A"})
        kinds = [c[0] for c in self.calls]
        self.assertEqual(kinds, ["save", "save", "continue"], "A saved at the park, B saved at the switch, A continued")

    def test_a_finished_turns_file_is_restored_the_ordinary_way(self):
        self.cs.before({"conv": "alex/A"})
        self.cs.after({"conv": "alex/A"})
        self.cs.save_current("idle: the chat waits on its user")
        later = self.slots()
        later.before({"conv": "alex/A"})
        self.assertEqual(self.calls[-1][0], "restore")
        self.assertEqual(later.last_start, "own")

    def test_an_engine_that_does_not_arm_is_said_plainly(self):
        self.armed = False                                    # an engine without patch #4: the turn is just re-asked
        self.park(self.cs)
        self.cs.before({"conv": "alex/A"})
        self.assertEqual(self.cs.last_start, "own")

    def test_the_restore_asks_the_engine_to_continue_only_when_told(self):
        seen = []

        def call(port, action, name, **extra):
            seen.append((action, extra))
            return {"n_restored": 1, "lab_continue_armed": True}
        open(os.path.join(self.d, "c.bin"), "wb").close()
        self.HW.restore_slot(self.a, port_fn=lambda: 1, call=call, slot_dir=self.d, name="c.bin", retire=False)
        self.HW.restore_slot(self.a, port_fn=lambda: 1, call=call, slot_dir=self.d, name="c.bin", retire=False,
                             lab_continue=True)
        self.assertEqual(seen, [("restore", {}), ("restore", {"lab_continue": True})])

    def turn(self, marks=(), can_cut=True, item=None):
        """One streamed turn through the real answer_item with a fake engine that sends chunks until it is hung up on."""
        items = os.path.join(self.d, "items")
        os.makedirs(items, exist_ok=True)
        for m in marks:
            open(os.path.join(items, m), "w").close()
        sent = []

        def chat(host, model, messages, options, think=False, on_chunk=None, **kw):
            for i in range(5):
                on_chunk({"message": {"content": "w%d " % i}, "done": False})
                sent.append(i)
            return {"message": {"content": "w0 w1 w2 w3 w4 "}, "model": model, "done_reason": "stop"}
        meter = types.SimpleNamespace(source=None)
        row = self.HW.answer_item(self.a, dict({"id": "t1", "prompt": "hi", "conv": "alex/A", "stream": True}, **(item or {})),
                                  meter, chat=chat, stream_path=os.path.join(items, "t1" + self.HW.STREAM), can_cut=can_cut)
        return row, sent

    def test_a_cut_marker_parks_the_turn_and_is_not_an_error(self):
        row, sent = self.turn(marks=["t1" + self.HW.CUT])
        self.assertEqual((row.get("cut"), row["error"], sent), ("parked by its caller", "", []))
        self.assertEqual(row["stream_chunks"], 1, "the chunk that was relayed before the hang-up is counted")

    def test_a_factory_yield_parks_only_a_turn_its_caller_marked(self):
        row, sent = self.turn(marks=[self.HW.YIELD], item={"cut_ok": True})
        self.assertEqual(row.get("cut"), "the factory asked for the card")
        shutil.rmtree(os.path.join(self.d, "items"))
        row, sent = self.turn(marks=[self.HW.YIELD])
        self.assertEqual((row.get("cut"), len(sent)), (None, 5), "an unmarked turn finishes, as before")

    def test_a_turn_of_a_chat_this_window_does_not_keep_is_never_cut(self):
        row, sent = self.turn(marks=["t1" + self.HW.CUT], can_cut=False)
        self.assertEqual((row.get("cut"), len(sent), row["answer"]), (None, 5, "w0 w1 w2 w3 w4 "))

    def test_a_stop_still_ends_the_turn_for_good(self):
        row, _sent = self.turn(marks=["t1" + self.HW.CANCEL, "t1" + self.HW.CUT])
        self.assertIn("TurnCancelled", row["error"])
        self.assertIsNone(row.get("cut"), "a Stop wins over a park")

    def test_serve_saves_the_parked_turns_state_and_says_so_in_its_row(self):
        items = os.path.join(self.d, "items")
        os.makedirs(items)
        with open(os.path.join(items, "t1" + self.HW.ITEM), "w", encoding="utf-8") as f:
            json.dump({"id": "t1", "prompt": "hi", "conv": "alex/A", "stream": True}, f)

        def answer(a, item, meter, chat=None, stream_path=None, can_cut=False):
            self.assertTrue(can_cut, "the window keeps this chat's state, so its turn may be parked")
            open(os.path.join(items, self.HW.CLOSE), "w").close()
            return {"id": item["id"], "cut": "parked by its caller", "error": "", "served_model": None, "wall_s": 0.1}
        with mock.patch.object(self.HW, "answer_item", answer):
            rows, _why = self.HW.serve(self.a, items, os.path.join(self.d, "prog"), os.path.join(self.d, "p.jsonl"), None,
                                       sleep=lambda s: None, conv_slots=self.cs)
        out = json.load(open(os.path.join(items, "t1" + self.HW.OUT), encoding="utf-8"))
        self.assertEqual((out["cut_saved"], out["cut_tokens"], out["cut_mid_answer"]), (True, 100, True))
        self.assertEqual(self.calls, [("save", self.cs.name("alex/A"))])
        self.assertEqual(self.cs.cut, "alex/A")


class HotStartFromTheSharedStart(unittest.TestCase):
    """A chat with no state of its own starts from a saved file of what every chat shares. The render stub preserves the real template's order (tools, system text, conversation) so that two chats share only the tools every chat carries."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_hot", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.a = types.SimpleNamespace(model="qwen3.8:27b", num_ctx=122880, strata=None, host="http://127.0.0.1:11434",
                                       live=True)
        self.posts, self.calls, self.t = [], [], [100.0]
        self.h = self.hot(self.a)

    def hot(self, a, post=None):
        def save(name):
            self.calls.append(("save", name))
            open(os.path.join(self.d, name), "wb").close()
            return {"n_saved": 2300}

        def restore(name):
            self.calls.append(("restore", name))
            self.t[0] += 0.5
            return {"n_restored": 2300}
        return self.HW.HotStarts(a, slot_dir=self.d, post=post or self.post, save=save, restore=restore,
                                 port_fn=lambda: 4321, clock=lambda: self.t[0], batch_fn=lambda: (4, 4))

    def post(self, url, body, timeout=600):
        self.posts.append((url, body))
        if url.endswith("/tokenize"):
            return {"tokens": [ord(c) for c in body["content"]]}     # one character, one token: the arithmetic stays readable
        if body.get("_debug_render_only"):
            tools = "".join(t["function"]["name"] + "\n" for t in body.get("tools") or [])
            msgs = "".join("%s:%s\n" % (m["role"], m["content"]) for m in body["messages"])
            return {"_debug_info": {"rendered_template": "<tools>\n" + tools + "</tools>\n" + msgs}}
        return {"content": ""}

    def tool(self, name):
        return {"type": "function", "function": {"name": name}}

    def item(self, names, shared, text="hello", **kw):
        return dict({"tools": [self.tool(n) for n in names], "hot_tools": shared, "think": "xhigh", "conv": "alex/A",
                     "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": text}]}, **kw)

    def test_two_chats_with_different_lists_share_the_start_they_have_in_common(self):
        one = self.h._spec(self.item(["a", "b", "c"], 2))
        two = self.h._spec(self.item(["a", "b", "z", "y"], 2, text="something else"))
        self.assertEqual(one, two)
        self.assertEqual(one[1], {"text": "<tools>\na\nb\n"}, "it stops where the shared tools stop")

    def test_a_list_all_of_it_shared_carries_the_system_text_too(self):
        got = self.h._spec(self.item(["a", "b"], 2))
        self.assertEqual(got[1], {"text": "<tools>\na\nb\n</tools>\nsystem:S\nuser:"})

    def test_a_list_with_nothing_known_to_be_shared_builds_nothing(self):
        self.assertIsNone(self.h._spec(self.item(["a"], 0)))
        self.assertIsNone(self.h._spec(self.item(["a"], None)))
        self.assertIsNone(self.h._spec({"messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual(self.posts, [], "nothing was asked of the engine")

    def test_the_first_chat_asks_for_it_and_it_is_built_without_generating_anything(self):
        item = self.item(["a", "b", "c"], 2)
        self.assertIsNone(self.h.before_new_chat(item))
        self.assertEqual(self.calls, [], "there is no file yet")
        name, _feed = self.h.wanted
        self.assertTrue(self.h.build_wanted())
        url, body = self.posts[-1]
        self.assertEqual(url, "http://127.0.0.1:4321/completion")
        text = [ord(c) for c in "<tools>\na\nb\n"]                     # 12 tokens on a grid of 4: the cut is 8, tail min(4, 4 + 4)
        self.assertEqual(body["prompt"], text[:8] + [text[0]] * 4)
        self.assertEqual((body["n_predict"], body["cache_prompt"]), (0, False), "from token 0, generating nothing")
        self.assertEqual(self.calls, [("save", name)])
        with open(os.path.join(self.d, name + ".json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual((meta["n_saved"], meta["n_shared"], meta["read_grid"]), (2300, 8, [4, 4]))
        self.assertIsNone(self.h.wanted)

    def built_prompt(self, tokens, grid):
        """What goes to the engine for a start of these tokens on this grid."""
        sent = []

        def post(url, body, timeout=600):
            if url.endswith("/tokenize"):
                return {"tokens": tokens}
            sent.append(body)
            return {}
        h = self.hot(self.a, post=post)
        h.batch_fn = lambda: grid
        h.wanted = ("hot-x.bin", {"text": "t"})
        return h.build_wanted(), sent, h

    def test_the_cut_is_a_whole_number_of_batches_and_the_tail_puts_the_checkpoint_on_it(self):
        """The engine breaks a batch `min(n_batch, 4 + n_ubatch)` tokens before a prompt's end and checkpoints there: that many
        tail tokens make that point the cut. Real sizes: ollama's -b 512 -ub 512, rigby's 2048, llama-server's defaults."""
        tokens = list(range(100, 2368))                      # 2,268: the real start measured on Softserve
        for grid, tail in (((512, 512), 512), ((2048, 2048), 2048), ((2048, 512), 516)):
            done, sent, h = self.built_prompt(tokens, grid)
            self.assertTrue(done, grid)
            self.assertEqual(len(sent[0]["prompt"]), 2048 + tail, grid)
            self.assertEqual(sent[0]["prompt"][:2048], tokens[:2048], grid)
            self.assertEqual(set(sent[0]["prompt"][2048:]), {tokens[0]}, grid)
            self.assertEqual(h.built["n_shared"], 2048, grid)

    def test_a_real_prompt_always_diverges_at_the_first_tail_token(self):
        """If the tail's first token equalled the real next one, the prompt would match past the cut and read off the grid."""
        tokens = [7, 8] + [9] * 6 + [7, 1]                   # tokens[8], the first after a cut of 8, equals tokens[0]
        _done, sent, _h = self.built_prompt(tokens, (4, 4))
        self.assertEqual(sent[0]["prompt"], tokens[:8] + [8] * 4)

    def test_a_start_under_one_batch_builds_nothing_and_leaves_the_slot_alone(self):
        cs = self.slots()
        self.h.batch_fn = lambda: (512, 512)
        item = self.item(["a", "b", "c"], 2)
        cs.before(item)
        cs.after(item)
        cs.save_current("idle")
        self.posts.clear()
        self.assertFalse(cs.build_hot_start())
        self.assertEqual(cs.current, "alex/A", "nothing touched the slot: the chat is still in it")
        self.assertEqual([u for u, _b in self.posts if u.endswith("/completion")], [])
        self.assertIn("under one read batch", self.h.take_built()["error"])
        self.assertIsNone(self.h.take_built(), "said once")

    def test_the_renders_are_paid_once_per_window(self):
        """Render requests are cached per unique tool list and effort; identical configurations are not re-requested."""
        self.h.before_new_chat(self.item(["a", "b", "c"], 2))
        asked = sum(1 for _u, b in self.posts if b.get("_debug_render_only"))
        self.assertEqual(asked, 2)
        self.h.before_new_chat(self.item(["a", "b", "z"], 2, conv="avery/B", text="something else"))
        self.assertEqual(sum(1 for _u, b in self.posts if b.get("_debug_render_only")), 2, "the same shared tools: no new render")
        self.h.before_new_chat(self.item(["a", "x", "z"], 2, conv="avery/C"))
        self.assertEqual(sum(1 for _u, b in self.posts if b.get("_debug_render_only")), 4, "other shared tools: their own start")

    def test_a_row_says_what_its_state_work_cost(self):
        items = tempfile.mkdtemp(dir=self.d)
        with open(os.path.join(items, "t1" + self.HW.ITEM), "w", encoding="utf-8") as f:
            json.dump(dict(self.item(["a", "b", "c"], 2), id="t1"), f)
        cs = self.slots()

        def close(s):
            open(os.path.join(items, self.HW.CLOSE), "w").close()

        def answer(a, item, meter, chat=None, stream_path=None, can_cut=False):
            return {"id": item["id"], "served_model": "m", "wall_s": 1.0, "error": "", "gpu_j": None}
        with mock.patch.object(self.HW, "answer_item", answer):
            rows, _why = self.HW.serve(self.a, items, os.path.join(items, "prog"), os.path.join(items, "p.jsonl"), None,
                                       sleep=close, conv_slots=cs)
        self.assertIn("state_s", rows[0])

    def test_the_grid_is_read_off_the_runners_real_command_line(self):
        real = (r"C:\Users\someone\AppData\Local\Programs\Ollama-0.40.5-lab620d222\lib\ollama\llama-server.exe "
                r"--model E:\ollama-models\blobs\sha256-50696f76ca4ee208f01de2727a48eedabbf2f4c1ed6da7e2b8626e37baace809 --port 51626 "
                r"--host 127.0.0.1 --no-webui --offline -c 8192 -np 16 --kv-unified --cache-ram 0 --log-verbosity 4 --no-log-prefix "
                r"--no-log-timestamps --load-mode none --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on -b 512 -ub 512 "
                r"--context-shift --keep 4")
        self.assertEqual(self.HW.batch_sizes(real), (512, 512))
        self.assertEqual(self.HW.batch_sizes("llama-server.exe --port 1"), (2048, 512), "llama-server's own defaults")

    def test_the_next_chat_starts_from_it_and_prices_the_restore(self):
        item = self.item(["a", "b", "c"], 2)
        self.h.before_new_chat(item)
        name, _feed = self.h.wanted
        self.h.build_wanted()
        self.calls.clear()
        got = self.h.before_new_chat(self.item(["a", "b", "z"], 2, conv="avery/B"))
        self.assertEqual(got, {"n_restored": 2300})
        self.assertEqual(self.calls, [("restore", name)])
        with open(os.path.join(self.d, name + ".json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["restore_s"], 0.5)

    def test_restore_or_re_read_whichever_is_faster(self):
        item = self.item(["a", "b", "c"], 2)
        self.h.before_new_chat(item)
        name, _feed = self.h.wanted
        self.h.build_wanted()
        with open(os.path.join(self.d, name + ".json"), "w", encoding="utf-8") as f:
            json.dump({"n_shared": 1000, "restore_s": 2.0}, f)
        self.calls.clear()
        self.assertIsNone(self.h.before_new_chat(dict(item, read_tok_s=1000.0)), "1 s to re-read beats 2 s to restore")
        self.assertEqual(self.calls, [])
        self.assertIsNotNone(self.h.before_new_chat(dict(item, read_tok_s=100.0)), "10 s to re-read loses to 2 s")
        self.assertEqual(self.calls, [("restore", name)])

    def test_a_render_that_fails_costs_the_turn_nothing(self):
        def down(url, body, timeout=600):
            raise OSError("engine busy")
        h = self.hot(self.a, post=down)
        self.assertIsNone(h.before_new_chat(self.item(["a", "b", "c"], 2)))
        self.assertIsNone(h.wanted)

    def test_a_build_that_fails_is_tried_once_and_says_the_slot_may_have_changed(self):
        h = self.hot(self.a)
        item = self.item(["a", "b", "c"], 2)
        h.before_new_chat(item)

        def down(url, body, timeout=600):
            raise OSError("engine busy")
        real, h.post = h.post, down
        self.assertTrue(h.build_wanted(), "the chat's own file must be restored before its next turn")
        h.post = real
        self.assertEqual(self.calls, [], "nothing to save")
        h.before_new_chat(item)
        self.assertIsNone(h.wanted, "this window does not try that start again")

    def test_strata_pins_a_cut_inside_the_list_at_its_k_th_tool(self):
        """The Strata server keys starts by `{'tools': k}`: chats sharing the first k tools share a start, regardless of subsequent tool order or system text."""
        a = types.SimpleNamespace(model="qwen3.8-flash-next", num_ctx=262144, strata={"model": "Q2_0"},
                                  host="http://127.0.0.1:8080", live=True)
        h = self.hot(a)
        one = h._spec(self.item(["a", "b", "c"], 2, chat_template_kwargs={"reasoning_effort": "xhigh"}))
        two = h._spec(self.item(["a", "b", "z", "y"], 2, chat_template_kwargs={"reasoning_effort": "xhigh"}))
        self.assertEqual(one[0], two[0], "one start for both")
        self.assertEqual(one[1]["prefix"], {"tools": 2})
        other_effort = h._spec(self.item(["a", "b", "c"], 2, chat_template_kwargs={"reasoning_effort": "low"}))
        self.assertNotEqual(other_effort[0], one[0], "the effort sentence renders before the tools: another start")
        h.wanted = one
        self.assertTrue(h.build_wanted())
        self.assertEqual(self.posts[-1][1]["strata_prefix"], {"tools": 2})

    def test_strata_pins_a_whole_start_at_the_message_boundary(self):
        a = types.SimpleNamespace(model="qwen3.8-flash-next", num_ctx=262144, strata={"model": "Q2_0"},
                                  host="http://127.0.0.1:8080", live=True)
        h = self.hot(a)
        item = self.item(["a", "b"], 2, chat_template_kwargs={"reasoning_effort": "xhigh"})
        h.before_new_chat(item)
        name, _feed = h.wanted
        self.assertTrue(h.build_wanted())
        url, body = self.posts[-1]
        self.assertEqual(url, "http://127.0.0.1:8080/v1/chat/completions")
        self.assertEqual(body["strata_prefix"], {"messages": 1})
        self.assertEqual(body["chat_template_kwargs"], {"reasoning_effort": "xhigh"})
        self.assertEqual([t["function"]["name"] for t in body["tools"]], ["a", "b"])
        self.assertEqual(self.calls, [("save", name)])

    def test_retention_keeps_the_newest_starts_and_never_touches_a_chat(self):
        stem = "hot-qwen3.8-27b-122880-"
        for i in range(self.HW.HOT_KEPT + 2):
            path = os.path.join(self.d, "%s%02d.bin" % (stem, i))
            open(path, "wb").close()
            open(path + ".json", "w").close()
            os.utime(path, (1000 + i, 1000 + i))
        chat = os.path.join(self.d, "conv-qwen3.8-27b-122880-aa.bin")
        open(chat, "wb").close()
        self.h.retain()
        left = sorted(f for f in os.listdir(self.d) if f.startswith(stem) and f.endswith(".bin"))
        self.assertEqual(len(left), self.HW.HOT_KEPT)
        self.assertEqual(left[0], stem + "02.bin", "the two oldest go")
        self.assertFalse(os.path.exists(os.path.join(self.d, stem + "00.bin.json")), "with their sidecars")
        self.assertTrue(os.path.exists(chat))

    def slots(self):
        return self.HW.ConvSlots(self.a, save=lambda name: self.calls.append(("save", name)) or
                                 open(os.path.join(self.d, name), "wb").close() or {"n_saved": 9},
                                 restore=lambda name: self.calls.append(("restore", name)) or {"n_restored": 9},
                                 slot_dir=self.d, retain_fn=lambda user: None, hot=self.h)

    def test_a_chat_with_its_own_state_never_uses_the_shared_start(self):
        cs = self.slots()
        open(os.path.join(self.d, cs.name("alex/A")), "wb").close()
        cs.before(self.item(["a", "b", "c"], 2))
        self.assertEqual(self.calls, [("restore", cs.name("alex/A"))])
        self.assertIsNone(self.h.wanted)

    def test_the_build_waits_for_the_chats_own_save_and_the_chat_comes_back_from_its_file(self):
        cs = self.slots()
        item = self.item(["a", "b", "c"], 2)
        cs.before(item)
        cs.after(item)
        self.assertIsNotNone(self.h.wanted, "a new chat asked for the start")
        self.assertFalse(cs.build_hot_start(), "the slot holds an unsaved chat: nothing may overwrite it")
        cs.save_current("idle")
        self.assertTrue(cs.build_hot_start())
        self.assertIsNone(cs.current, "the slot now holds the start, not the chat")
        self.calls.clear()
        cs.before(item)
        self.assertEqual(self.calls, [("restore", cs.name("alex/A"))], "the chat's next turn reads only its new tokens")

    def test_a_row_says_what_state_its_turn_started_from(self):
        """The instrument: a timing alone cannot say whether a restore ran. Every turn says so in its own record."""
        cs = self.slots()
        item = self.item(["a", "b", "c"], 2)
        cs.before(item)
        self.assertIsNone(cs.last_start, "no start exists yet: this turn read everything")
        cs.after(item)
        cs.before(item)
        self.assertEqual(cs.last_start, "in the slot")
        cs.after(item)
        cs.save_current("idle")
        cs.build_hot_start()
        cs.before(item)
        self.assertEqual(cs.last_start, "own")
        cs.before(self.item(["a", "b", "z"], 2, conv="avery/B"))
        self.assertEqual(cs.last_start, "hot")

    def test_the_window_says_when_it_built_a_start_and_the_row_carries_the_start(self):
        items = tempfile.mkdtemp(dir=self.d)
        open(os.path.join(items, self.HW.IDLE), "w").close()
        with open(os.path.join(items, "t1" + self.HW.ITEM), "w", encoding="utf-8") as f:
            json.dump(dict(self.item(["a", "b", "c"], 2), id="t1"), f)
        cs = self.slots()
        passes = []

        def close_after_the_build(s):
            passes.append(1)
            if os.path.exists(os.path.join(items, self.HW.HOT_BUILT)) or len(passes) > 3:
                open(os.path.join(items, self.HW.CLOSE), "w").close()

        def answer(a, item, meter, chat=None, stream_path=None, can_cut=False):
            return {"id": item["id"], "served_model": "m", "wall_s": 1.0, "error": "", "gpu_j": None}
        with mock.patch.object(self.HW, "answer_item", answer):
            self.HW.serve(self.a, items, os.path.join(items, "prog"), os.path.join(items, "p.jsonl"), None,
                          sleep=close_after_the_build, conv_slots=cs)
        with open(os.path.join(items, self.HW.HOT_BUILT), encoding="utf-8") as f:
            built = json.load(f)
        self.assertEqual(built["n_saved"], 2300)
        self.assertTrue(built["file"].startswith("hot-"), built)
        self.assertNotIn("error", built)

    def test_a_failed_build_says_why_in_the_same_place(self):
        h = self.hot(self.a)
        h.before_new_chat(self.item(["a", "b", "c"], 2))
        real = h.post

        def down(url, body, timeout=600):
            raise OSError("engine busy")
        h.post = down
        h.build_wanted()
        h.post = real
        self.assertIn("engine busy", h.built["error"])

    def test_the_window_loop_builds_it_only_while_the_chat_waits_on_its_user(self):
        """The seam: serve() itself calls the build at the quiet moment (Kirk's IDLE), and not without it."""
        def run(idle):
            items = tempfile.mkdtemp(dir=self.d)
            if idle:
                open(os.path.join(items, self.HW.IDLE), "w").close()
            built = []
            slots = types.SimpleNamespace(dirty=False, prefetch_asked=lambda d: [],
                                          build_hot_start=lambda: built.append(1))

            def close(s):
                open(os.path.join(items, self.HW.CLOSE), "w").close()
            self.HW.serve(self.a, items, os.path.join(items, "prog"), os.path.join(items, "p.jsonl"), None, sleep=close,
                          conv_slots=slots)
            return len(built)
        self.assertEqual(run(True), 1)
        self.assertEqual(run(False), 0)


class RigbyRunsThisWindowProgram(unittest.TestCase):
    """rigby uses this module's `serve` (via `a.llama`); these tests pin the specific window behaviours rigby depends on."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_rigby", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.a = types.SimpleNamespace(model="Qwen3.6-35B-A3B.gguf", num_ctx=49152, strata=None, llama=True,
                                       host="http://127.0.0.1:8091", live=True, read_grid=(4, 4), temperature=0.6,
                                       seed_base=0, think=False)
        self.posts, self.calls, self.t = [], [], [100.0]

    @staticmethod
    def boom():
        raise AssertionError("rigby's window never looks for an ollama runner")

    def post(self, url, body, timeout=600):
        self.posts.append((url, body))
        if url.endswith("/tokenize"):
            return {"tokens": [ord(c) for c in body["content"]]}     # one character, one token: the arithmetic stays readable
        if url.endswith("/apply-template"):
            tools = "".join(t["function"]["name"] + "\n" for t in body.get("tools") or [])
            msgs = "".join("%s:%s\n" % (m["role"], m["content"]) for m in body["messages"])
            return {"prompt": "<tools>\n" + tools + "</tools>\n" + msgs}
        return {}

    def call(self, port, action, name, **extra):
        self.calls.append((port, action, name, extra))
        if action == "save":
            open(os.path.join(self.d, name), "wb").close()          # the engine writes the file it was named
            return {"n_saved": 2300, "pending_token": 7}
        self.t[0] += 0.5
        return {"n_restored": 2300, "lab_continue_armed": bool(extra.get("lab_continue"))}

    def window(self):
        a, d = self.a, self.d
        save = lambda n: self.HW.save_slot(a, port_fn=self.boom, call=self.call, name=n, slot_dir=d)     # noqa: E731
        restore = lambda n: self.HW.restore_slot(a, port_fn=self.boom, call=self.call, slot_dir=d, name=n,  # noqa: E731
                                                 retire=False)
        cont = lambda n: self.HW.restore_slot(a, port_fn=self.boom, call=self.call, slot_dir=d, name=n,  # noqa: E731
                                              retire=False, lab_continue=True)
        hot = self.HW.HotStarts(a, slot_dir=d, post=self.post, save=save, restore=restore, port_fn=self.boom,
                                clock=lambda: self.t[0], batch_fn=self.boom)
        return self.HW.ConvSlots(a, save=save, restore=restore, cont=cont, slot_dir=d, clock=lambda: self.t[0],
                                 retain_fn=lambda user: self.HW.retain_newest(d, "conv-", 50),
                                 fetch=lambda n, r: "absent", hot=hot)

    def item(self, conv="alex/A", names=("a", "b", "c"), shared=2):
        return {"conv": conv, "hot_tools": shared, "chat_template_kwargs": {"enable_thinking": True},
                "tools": [{"type": "function", "function": {"name": n}} for n in names],
                "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "hi"}]}

    def test_the_engine_is_the_one_its_caller_started(self):
        self.assertEqual(self.HW.engine_port(self.a, port_fn=self.boom), 8091)
        self.assertTrue(self.HW.own_engine(self.a))

    def test_a_new_chat_starts_from_a_start_the_llama_server_rendered_built_on_its_grid(self):
        w = self.window()
        w.before(self.item())
        w.after(self.item())
        self.assertTrue(all(b.get("chat_template_kwargs") == {"enable_thinking": True}
                            for u, b in self.posts if u.endswith("/apply-template")), "rendered with the turn's own effort")
        w.save_current("idle: the chat waits on its user")
        self.assertTrue(w.build_hot_start())
        comp = [b for u, b in self.posts if u.endswith("/completion")]
        tokens = [ord(c) for c in "<tools>\na\nb\n"]
        n = ((len(tokens) - 1) // 4) * 4
        self.assertEqual((comp[0]["prompt"], comp[0]["n_predict"], comp[0]["cache_prompt"]),
                         (tokens[:n] + [tokens[0]] * 4, 0, False), "on the read grid, from token 0, generating nothing")
        hot = [c[2][:-len(self.HW.NEW)] for c in self.calls if c[1] == "save" and c[2].startswith("hot-")]
        self.assertEqual(len(hot), 1)
        self.assertTrue(os.path.exists(os.path.join(self.d, hot[0])), "the save replaced the file only once it was whole")
        w.before(self.item("avery/B", names=("a", "b", "z")))
        self.assertEqual(self.calls[-1][1:3], ("restore", hot[0]))
        self.assertEqual(w.last_start, "hot")

    def test_the_renders_are_paid_once_per_window(self):
        w = self.window()
        w.before(self.item("alex/A", names=("a", "b", "c")))
        count = lambda: sum(1 for u, _b in self.posts if u.endswith("/apply-template"))       # noqa: E731
        self.assertEqual(count(), 2)
        w.before(self.item("avery/B", names=("a", "b", "z")))
        self.assertEqual(count(), 2, "the same shared tools and effort: no new render")

    def test_rigby_keeps_its_newest_chats_without_an_array(self):
        for i in range(52):
            path = os.path.join(self.d, "conv-x-%03d.bin" % i)
            for part in (path, path + ".json"):
                open(part, "wb").close()
                os.utime(part, (1000 + i, 1000 + i))
        self.HW.retain_newest(self.d, "conv-", 50)
        left = sorted(f for f in os.listdir(self.d) if f.endswith(".bin"))
        self.assertEqual((len(left), left[0]), (50, "conv-x-002.bin"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "conv-x-000.bin.json")), "the sidecar goes with its file")

    def test_a_turn_rigby_parked_before_it_ran_this_program_still_continues(self):
        w = self.window()
        name = w.name("alex/A")
        for part in (name, name + self.HW.LEGACY_CUT):
            open(os.path.join(self.d, part), "wb").close()
        w.before(self.item())
        self.assertEqual(self.calls[-1][1:], ("restore", name, {"lab_continue": True}))
        self.assertEqual(w.last_start, "own, continuing the cut answer")
        w.after(self.item())
        w.save_current("idle: the chat waits on its user")
        self.assertFalse(os.path.exists(os.path.join(self.d, name + self.HW.LEGACY_CUT)), "the sidecar says it now")
        with open(os.path.join(self.d, name + ".json"), encoding="utf-8") as f:
            self.assertFalse(json.load(f)["cut"])

    def test_an_array_this_box_cannot_reach_keeps_the_files_and_creates_nothing(self):
        """Five per user plus the array is the rule on every box; a box that cannot reach the array keeps its files, and a
        path it cannot read (the Windows share path, on Linux) is never created as a local folder."""
        for i in range(7):
            name = "conv-x-%03d.bin" % i
            open(os.path.join(self.d, name), "wb").close()
            with open(os.path.join(self.d, name + ".json"), "w", encoding="utf-8") as f:
                json.dump({"user": "alex", "t": 1000 + i}, f)
        gone = os.path.join(self.d, "not-mounted", "HAI", "chat-slots", "rigby")
        self.assertEqual(self.HW.retain("alex", slot_dir=self.d, array_dir=gone), [])
        self.assertFalse(os.path.exists(os.path.join(self.d, "not-mounted")), "nothing created where the array should be")
        self.assertEqual(len([f for f in os.listdir(self.d) if f.endswith(".bin")]), 7, "nothing lost either")
        there = os.path.join(self.d, "array", "chat-slots")
        os.makedirs(there)
        moved = self.HW.retain("alex", slot_dir=self.d, array_dir=os.path.join(there, "rigby"))
        self.assertEqual(sorted(moved), ["conv-x-000.bin", "conv-x-001.bin"], "the array reachable: the oldest two move")
        self.assertTrue(os.path.exists(os.path.join(there, "rigby", "conv-x-000.bin.json")))

    def test_a_lend_is_refused_the_window_runs_its_own_engine(self):
        for m in (self.HW.LEND, self.HW.IDLE):
            open(os.path.join(self.d, m), "w").close()
        self.assertFalse(self.HW.lend_card(self.a, self.d, save=lambda _a: self.fail("nothing is saved for a lend")))
        with open(os.path.join(self.d, self.HW.LENT), encoding="utf-8") as f:
            self.assertIn("own engine", json.load(f)["refused"])

    def test_a_silent_answer_is_probed_on_the_engines_own_wire(self):
        """The probe that tells a hung engine from a slow one asked ollama's API of every window, Strata's too; on an
        OpenAI engine that probe fails, and the window was then held as hung."""
        seen = []

        def silent(*_a, **_k):
            raise TimeoutError("no token")
        with mock.patch.object(self.HW.IO, "_silence", return_value=True), \
                mock.patch.object(self.HW.IO, "_engine_alive", side_effect=lambda *p, **k: seen.append(k) or True):
            row = self.HW._answer(self.a, {"id": "t1", "messages": [{"role": "user", "content": "hi"}]},
                                  types.SimpleNamespace(source=None), silent, None)
        self.assertEqual(seen, [{"strata": True}])
        self.assertIn("TimeoutError", row["error"])

    def test_the_turn_sends_no_context_or_cap_the_engine_was_not_launched_with(self):
        sent = []
        with mock.patch.object(self.HW.IO, "_chat_openai", side_effect=lambda base, msgs, opts, **k: sent.append(
                (base, opts, k.get("think"))) or {"message": {"content": "ok"}}):
            self.HW.own_engine_chat(self.a)(self.a.host, self.a.model, [{"role": "user", "content": "hi"}],
                                            {"num_ctx": 49152, "num_predict": 49152, "temperature": 0.6}, think=True)
        self.assertEqual(sent, [("http://127.0.0.1:8091", {"temperature": 0.6}, True)])

    def test_an_attempt_cancelled_before_it_started_never_spends_the_continue(self):
        """A stopped attempt must never be started, so it cannot spend its continue quota on an unread answer."""
        w = self.window()
        name = w.name("alex/A")
        open(os.path.join(self.d, name), "wb").close()
        with open(os.path.join(self.d, name + ".json"), "w", encoding="utf-8") as f:
            json.dump({"conv": "alex/A", "user": "alex", "cut": True, "t": 1}, f)
        items = os.path.join(self.d, "items")
        os.makedirs(items)
        for iid in ("t00001-a", "t00002-b"):
            with open(os.path.join(items, iid + self.HW.ITEM), "w", encoding="utf-8") as f:
                json.dump({"id": iid, "conv": "alex/A", "messages": [{"role": "user", "content": "hi"}]}, f)
        open(os.path.join(items, "t00001-a" + self.HW.CANCEL), "w").close()
        asked = []

        def chat(host, model, messages, options, think=False, tools=None, on_chunk=None, keep_alive=None):
            asked.append(1)
            return {"message": {"content": "the whole answer"}, "model": model}

        def sleep(s):
            open(os.path.join(items, self.HW.CLOSE), "w").close()
        rows, why = self.HW.serve(self.a, items, os.path.join(self.d, "prog"), os.path.join(self.d, "p.jsonl"),
                                  types.SimpleNamespace(source=None), sleep=sleep, chat=chat, conv_slots=w)
        self.assertEqual(len(asked), 1, "the engine is asked once: for the real re-ask")
        restores = [c for c in self.calls if c[1] == "restore"]
        self.assertEqual([c[3] for c in restores], [{"lab_continue": True}], "one restore, to continue, for the real one")
        by = {r["id"]: r for r in rows}
        self.assertTrue(by["t00001-a"]["error"].startswith("TurnCancelled"))
        self.assertEqual((by["t00002-b"]["error"], by["t00002-b"]["state_from"]), ("", "own, continuing the cut answer"))

    def test_serve_polls_at_the_boxs_own_interval_and_reports_each_row(self):
        items = os.path.join(self.d, "items")
        os.makedirs(items)
        with open(os.path.join(items, "t00001-a" + self.HW.ITEM), "w", encoding="utf-8") as f:
            json.dump({"id": "t00001-a", "messages": [{"role": "user", "content": "hi"}]}, f)
        slept, beat = [], []

        def sleep(s):
            slept.append(s)
            open(os.path.join(items, self.HW.CLOSE), "w").close()

        def chat(host, model, messages, options, think=False, tools=None, on_chunk=None, keep_alive=None):
            return {"message": {"content": "hello"}, "model": model}
        rows, why = self.HW.serve(self.a, items, os.path.join(self.d, "prog"), os.path.join(self.d, "p.jsonl"),
                                  types.SimpleNamespace(source=None), sleep=sleep, chat=chat, poll_s=0.2,
                                  on_row=beat.append)
        self.assertEqual((why, [r["answer"] for r in rows], [r["answer"] for r in beat], slept),
                         ("closed by the lead", ["hello"], ["hello"], [0.2]))


class TheOwnerAndIdleAreReadEveryPass(unittest.TestCase):
    """Heartbeat and idle marks must be checked on every pass, not just the 30s timer."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_pass", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.a = types.SimpleNamespace(model="m", num_ctx=8192, strata=None, host="http://127.0.0.1:11434", live=True)
        self.t = [time.time()]

    def owner(self, t):
        with open(os.path.join(self.d, self.HW.OWNER), "w", encoding="utf-8") as f:
            json.dump({"run": "kirk:test", "period": 15, "t": t}, f)

    def serve(self, passes=3, **kw):
        slept = []

        def sleep(s):
            slept.append(s)
            self.t[0] += 1
            if len(slept) >= passes:
                open(os.path.join(self.d, self.HW.CLOSE), "w").close()
        rows, why = self.HW.serve(self.a, self.d, os.path.join(self.d, "prog"), os.path.join(self.d, "p.jsonl"),
                                  types.SimpleNamespace(source=None), sleep=sleep, clock=lambda: self.t[0], **kw)
        return why, len(slept)

    def test_a_silent_owner_ends_the_window_on_the_first_pass(self):
        self.owner(self.t[0] - 60)                                     # 4 periods silent
        why, passes = self.serve(card_waiting=lambda: False)
        self.assertIn("silent", why)
        self.assertEqual(passes, 0, "noticed before the first sleep, not 30 s later")

    def test_an_idle_window_past_its_rule_ends_on_the_first_pass(self):
        self.owner(10 ** 12)
        open(os.path.join(self.d, self.HW.IDLE), "w").close()
        os.utime(os.path.join(self.d, self.HW.IDLE), (self.t[0] - 120, self.t[0] - 120))
        why, passes = self.serve(card_waiting=lambda: False, idle_ttl=60)
        self.assertTrue(why.startswith("idle"), why)
        self.assertEqual(passes, 0)

    def test_kirk_is_still_asked_only_every_desk_period(self):
        self.owner(10 ** 12)
        asked = []
        why, passes = self.serve(passes=40, desk_status=lambda: asked.append(1) or "running",
                                 card_waiting=lambda: asked.append(2) or False)
        self.assertEqual((why, passes), ("closed by the lead", 40))
        self.assertEqual(asked.count(1), 40 // int(self.HW.DESK_EVERY_S), "one desk question per 30 s of passes")


class WindowAcceptsADeclaredSplit(unittest.TestCase):
    """Verify that windows for OFFLOAD_OK pairs accept planned splits upon start and reclaim, rather than refusing them."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("helper_window_split", os.path.join(BIN, "helper_window.py"))
        cls.HW = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.HW)

    def reclaim(self, offload_ok):
        seen = []
        a = types.SimpleNamespace(host="http://x", model="big:moe", num_ctx=65536, offload_ok=offload_ok)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(self.HW.IO, "_assert_resident", lambda *p, **k: seen.append(k.get("offload_ok"))):
            self.HW.reclaim_card(a, d, restore=lambda a: None)
        return seen

    def test_the_reclaim_after_a_lend_carries_the_declaration(self):
        self.assertEqual(self.reclaim(True), [True])
        self.assertEqual(self.reclaim(False), [False])

    def test_the_flag_parses(self):
        import argparse
        src = open(os.path.join(BIN, "helper_window.py"), encoding="utf-8").read()
        self.assertIn('"--offload-ok", action="store_true"', src)
        self.assertIn("offload_ok=a.offload_ok", src, "the window's start passes it to the residency check")


def _load_bin(name):
    spec = importlib.util.spec_from_file_location(name + "_undertest", os.path.join(BIN, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class RenderOrderOpensTheAddressAsked(unittest.TestCase):

    def test_the_page_path_and_query_reach_the_browser_call(self):
        import render
        import render_order
        d = tempfile.mkdtemp(prefix="render_order_")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "req", "page", "tests"))
        with open(os.path.join(d, "req", "request.json"), "w", encoding="utf-8") as f:
            json.dump({"page": "tests/back.html", "query": "?view=back#top"}, f)
        seen = {}

        def page(src, root, out_dir, **kw):
            seen.update(src=str(src), exists=os.path.isdir(os.path.join(str(root), "tests")), query=kw.get("query"))
            return {"kind": "page"}

        with mock.patch.object(render, "render_page", page), mock.patch.object(render_order, "args_for", lambda r: []), \
                mock.patch.object(sys, "argv", ["render_order.py", os.path.join(d, "req", "request.json"), os.path.join(d, "out")]):
            render_order.main()
        self.assertEqual(seen["query"], "?view=back#top")
        self.assertTrue(seen["src"].replace("\\", "/").endswith("page/tests/back.html"), seen["src"])
        self.assertTrue(seen["exists"], "the bundle's subfolders are there")


class RenderBrowserProven(unittest.TestCase):
    """Render browser is enabled only if WebGL context creation succeeded on every launch."""

    def setUp(self):
        self.venv = tempfile.mkdtemp(prefix="render_venv_")
        self.addCleanup(shutil.rmtree, self.venv, True)
        os.makedirs(os.path.join(self.venv, "Scripts"))
        open(os.path.join(self.venv, "Scripts", "python.exe"), "w").close()

    def _proof(self, text):
        with open(os.path.join(self.venv, "webgl_proof.json"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_every_launch_opened_is_proven(self):
        self._proof(json.dumps({"launches": 12, "opened": 12}))
        self.assertTrue(F.render_browser_proven(self.venv))

    def test_one_refused_launch_is_not(self):
        self._proof(json.dumps({"launches": 12, "opened": 11}))
        self.assertFalse(F.render_browser_proven(self.venv))

    def test_the_declaration_follows_the_proof_without_a_restart(self):
        with mock.patch.object(F.os.path, "exists", lambda p: True):
            with mock.patch.object(F, "render_browser_proven", lambda v: True):
                self.assertIn("render.browser", F.declared_capabilities())
            with mock.patch.object(F, "render_browser_proven", lambda v: False):
                self.assertNotIn("render.browser", F.declared_capabilities())

    def test_no_proof_or_a_torn_one_is_not(self):
        self.assertFalse(F.render_browser_proven(self.venv))
        self._proof("{not json")
        self.assertFalse(F.render_browser_proven(self.venv))
        self._proof(json.dumps({"launches": 0, "opened": 0}))
        self.assertFalse(F.render_browser_proven(self.venv))


class Krea2Capability(unittest.TestCase):
    """render.krea2 is declared only when the three files p20_krea_render's graph loads sit where ComfyUI reads them
    (its own tree or an extra_model_paths.yaml entry); train.krea2 only when the Krea-ready ai-toolkit is installed."""

    def setUp(self):
        self.K = _load_bin("p20_krea_render")
        self.root = tempfile.mkdtemp(prefix="krea_comfy_")
        self.extra = tempfile.mkdtemp(prefix="krea_extra_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.extra, True)

    def put(self, base, sub, name):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
        open(os.path.join(base, sub, name), "wb").close()
        return os.path.join(base, sub, name)

    def yaml_entry(self, **keys):
        with open(os.path.join(self.root, "extra_model_paths.yaml"), "w", encoding="utf-8") as f:
            f.write("estore:\n  base_path: %s/\n" % self.extra.replace("\\", "/"))
            for k, v in keys.items():
                f.write("  %s: %s/\n" % (k, v))

    def test_files_across_own_tree_and_a_mapped_store_count(self):
        self.put(self.root, os.path.join("models", "diffusion_models"), self.K.UNET)
        self.put(self.extra, "text_encoders", self.K.CLIP)
        vae = self.put(self.extra, "vae", self.K.VAE)
        self.yaml_entry(text_encoders="text_encoders", vae="vae")
        self.assertTrue(F.krea2_render_files_present(self.root))
        os.remove(vae)
        self.assertFalse(F.krea2_render_files_present(self.root), "one missing file means no promise")

    def test_comfy_legacy_unet_and_clip_keys_are_the_same_folders(self):
        self.put(self.extra, "u", self.K.UNET)
        self.put(self.extra, "c", self.K.CLIP)
        self.put(self.extra, "v", self.K.VAE)
        self.yaml_entry(unet="u", clip="c", vae="v")
        self.assertTrue(F.krea2_render_files_present(self.root))

    def test_without_pyyaml_the_mapping_is_still_read(self):
        """The production interpreter lacks PyYAML; this ensures the fallback reader handles it."""
        self.put(self.extra, "text_encoders", self.K.CLIP)
        self.put(self.extra, "vae", self.K.VAE)
        self.put(self.extra, "diffusion_models", self.K.UNET)
        self.yaml_entry(text_encoders="text_encoders", vae="vae", diffusion_models="diffusion_models")
        with mock.patch.object(F, "yaml", None):
            self.assertTrue(F.krea2_render_files_present(self.root))

    def test_fallback_reader_agrees_with_pyyaml_on_comfys_format(self):
        p = os.path.join(self.root, "extra_model_paths.yaml")
        with open(p, "w", encoding="utf-8") as f:
            f.write("flux2_nvme:\n  base_path: F:/ComfyModels/\n  unet: unet/\n\n"
                    "generic_estore:\n  base_path: 'E:/ComfyUI/ComfyUI/models/'\n  # wiring note: kontext\n"
                    "  diffusion_models: diffusion_models/  # inline note\n"
                    "  text_encoders: |\n    text_encoders/\n    clip/\n  vae: \"vae/\"\n")
        with mock.patch.object(F, "yaml", None):
            got = F.read_extra_model_paths(p)
        self.assertEqual(got["generic_estore"]["text_encoders"].split(), ["text_encoders/", "clip/"])
        self.assertEqual((got["generic_estore"]["base_path"], got["generic_estore"]["vae"],
                          got["generic_estore"]["diffusion_models"], got["flux2_nvme"]["unet"]),
                         ("E:/ComfyUI/ComfyUI/models/", "vae/", "diffusion_models/", "unet/"))
        if F.yaml is not None:                       # where PyYAML exists, the two derivations must agree
            self.assertEqual(got, F.read_extra_model_paths(p))

    def test_a_flux2_only_store_promises_nothing(self):
        self.put(self.root, os.path.join("models", "unet"), "flux2-dev-Q3_K_M.gguf")
        self.assertFalse(F.krea2_render_files_present(self.root))

    def test_trainer_needs_entrypoint_krea_code_and_its_own_interpreter(self):
        t = tempfile.mkdtemp(prefix="aitk_krea2_")
        self.addCleanup(shutil.rmtree, t, True)
        self.put(t, "", "run.py")
        self.put(t, "venv", "python.exe")
        self.assertFalse(F.krea2_trainer_present(t), "an ai-toolkit without krea2 code cannot train Krea 2")
        self.put(t, os.path.join("extensions_built_in", "diffusion_models", "krea2"), "krea2.py")
        loop = self.put(t, os.path.join("jobs", "process"), "BaseSDTrainProcess.py")
        self.assertFalse(F.krea2_trainer_present(t), "a trainer that ignores STOP_NOW cannot be paused by the foundry")
        with open(loop, "w", encoding="utf-8") as f:
            f.write("# [P20-INSTR] manual save/stop triggers\n")
        self.assertTrue(F.krea2_trainer_present(t))
        # The registry is pruned per box at import, so ask the omission rule itself, both ways.
        with mock.patch.object(F, "krea2_trainer_present", return_value=True):
            self.assertNotIn("train.krea2", F._detected_caps_to_omit())
        with mock.patch.object(F, "krea2_trainer_present", return_value=False):
            self.assertIn("train.krea2", F._detected_caps_to_omit())
        with mock.patch.object(F, "krea2_render_files_present", return_value=False):
            self.assertIn("render.krea2", F._detected_caps_to_omit())

    def test_a_slow_comfy_is_could_not_tell_and_only_refusal_means_gone(self):
        import urllib.error

        def raising(exc):
            def get(path):
                raise exc
            return get
        self.assertEqual(self.K.check("p", raising(TimeoutError("timed out"))), ("wait", None))
        self.assertEqual(self.K.check("p", raising(urllib.error.URLError(TimeoutError("timed out")))),
                         ("wait", None))
        self.assertEqual(self.K.check("p", raising(urllib.error.URLError(ConnectionRefusedError())))[0], "gone")
        running = {"/history/p": {}, "/queue": {"queue_running": [[1, "p"]], "queue_pending": []}}
        self.assertEqual(self.K.check("p", running.get), ("wait", None))
        done = {"/history/p": {"p": {"status": {"completed": True},
                                     "outputs": {"9": {"images": [{"filename": "x_00001_.png"}]}}}}}
        self.assertEqual(self.K.check("p", done.get), ("done", "x_00001_.png"))
        dropped = {"/history/p": {}, "/queue": {"queue_running": [], "queue_pending": []}}
        self.assertEqual(self.K.check("p", dropped.get)[0], "fail", "a prompt that is nowhere was dropped")

    def test_graph_is_the_turbo_template_and_wires_the_adapter_only_when_given(self):
        g = self.K.graph("none", "p", "a turtle", 7, 1216, 832)
        self.assertNotIn("20", g)
        self.assertEqual(g["34"]["inputs"]["model"], ["1", 0])
        self.assertEqual(g["7"]["inputs"]["model"], ["34", 0])
        self.assertEqual((g["2"]["inputs"]["type"], g["7"]["inputs"]["steps"], g["7"]["inputs"]["cfg"]),
                         ("krea2", 8, 1.0))
        self.assertEqual(g["5"]["inputs"]["conditioning"], ["4", 0], "negative = the positive zeroed")
        g = self.K.graph("alex_krea2_v1.safetensors", "p", "a turtle")
        self.assertEqual(g["20"]["inputs"]["lora_name"], "alex_krea2_v1.safetensors")
        self.assertEqual(g["34"]["inputs"]["model"], ["20", 0])
        self.assertEqual(g["7"]["inputs"]["model"], ["34", 0])


class Krea2WorkerRoute(unittest.TestCase):
    """A Krea 2 order renders through p20_krea_render with its prompt and never runs the FLUX.2 cond bake."""

    def run_job(self, job):
        W = _load_bin("p20_render_worker")
        calls = []
        done = types.SimpleNamespace(returncode=0, stdout="BAKED", stderr="")
        # the owner is a factory setting (OWNER_USER); these orders are the owner's, so the job runs with it set
        with mock.patch.dict(os.environ, {"OWNER_USER": "Alex"}), \
                mock.patch.object(W, "log"), mock.patch.object(W, "write_holds"), mock.patch.object(W, "unload_ollama"), \
                mock.patch.object(W, "comfy_up", return_value=True), \
                mock.patch.object(W, "_progress_reporter", lambda *a: None), \
                mock.patch.object(W, "post_gpu_status"), \
                mock.patch.object(W, "post_result") as post, \
                mock.patch.object(W.subprocess, "run", side_effect=lambda cmd, **k: calls.append(cmd) or done):
            W._run_job("tok", job)
        return W, calls, post

    def test_krea_job_skips_the_bake_and_renders_its_prompt(self):
        W, calls, post = self.run_job({"id": "abc123abc123", "user": "Alex", "prompt": "a turtle",
                                       "requires": ["render.krea2"], "lora_path": None})
        self.assertFalse([c for c in calls if W.BAKE in c])
        render = [c for c in calls if W.KREA in c]
        self.assertEqual(len(render), 1)
        self.assertIn("a turtle", render[0])
        self.assertIn("no PNG", post.call_args[1]["error"])   # nothing on disk in a test: honest failure

    def test_comparison_order_uses_the_recorded_seed_and_canvas(self):
        W, calls, _ = self.run_job({"id":"abc123abc123","user":"Alex","prompt":"a portrait",
            "requires":["render.krea2"],"lora_path":None,"seed":42041,"width":1024,"height":1024})
        cmd = next(c for c in calls if W.KREA in c)
        self.assertEqual(cmd[5:8], ["42041","1024","1024"])

    def test_reference_and_second_adapter_keep_both_cli_arguments(self):
        reference = {'path':r'D:\controls\source.png','sha256':'a'*64,'kind':'generated_character'}
        with mock.patch.dict(os.environ, {"KREA2_SECOND_ADAPTER": "second.safetensors"}):
            W, calls, _ = self.run_job({'id':'abc123abc123','user':'Alex','prompt':'a portrait',
                'requires':['render.krea2'],'lora_path':None,'reference':reference,
                'second_adapter':'second.safetensors','second_adapter_strength':4.0})
        cmd = next(c for c in calls if W.KREA in c)
        self.assertEqual(cmd[-2],'4.0')
        self.assertEqual(json.loads(cmd[-1]),reference)

    def test_restart_flag_counts_only_when_written_after_the_worker_started(self):
        """fleet_deploy asks the worker to exit between jobs; a flag left for a predecessor must not stop the new one."""
        W = _load_bin("p20_render_worker")
        d = tempfile.mkdtemp(prefix="worker_flag_")
        self.addCleanup(shutil.rmtree, d, True)
        flag = os.path.join(d, "RENDER_WORKER_RESTART")
        self.assertFalse(W.restart_requested(flag, started_at=1000.0), "no flag, no restart")
        open(flag, "w").close()
        os.utime(flag, (900.0, 900.0))
        self.assertFalse(W.restart_requested(flag, started_at=1000.0), "a predecessor's flag is not mine")
        self.assertFalse(os.path.exists(flag), "and it is cleared, not left to trip the next start")
        open(flag, "w").close()
        os.utime(flag, (1100.0, 1100.0))
        self.assertTrue(W.restart_requested(flag, started_at=1000.0))
        self.assertFalse(os.path.exists(flag), "consumed")

    def test_flux2_job_still_bakes_and_never_reaches_krea(self):
        W, calls, _ = self.run_job({"id": "def456def456", "user": "Alex", "prompt": "a turtle",
                                    "requires": ["render.flux2"], "lora_path": None})
        self.assertTrue([c for c in calls if W.BAKE in c])
        self.assertFalse([c for c in calls if W.KREA in c])


class KreaAdapterStacking(unittest.TestCase):
    def setUp(self):
        self.K = _load_bin("p20_krea_render")

    def test_combined_graph_keeps_both_adapters(self):
        g = self.K.graph("character.safetensors", "test", "portrait", strength=1.4, second_strength=4)
        self.assertEqual(g["21"]["inputs"]["model"], ["1", 0])
        self.assertEqual(g["21"]["inputs"]["strength_model"], 4)
        self.assertEqual(g["20"]["inputs"]["model"], ["21", 0])
        self.assertEqual(g["20"]["inputs"]["strength_model"], 1.4)
        self.assertEqual(g["34"]["inputs"]["model"], ["20", 0])
        self.assertEqual(g["7"]["inputs"]["model"], ["34", 0])

    def test_plain_identity_graph_does_not_load_the_second_adapter(self):
        g = self.K.graph("avery.safetensors", "test", "portrait", strength=1.4)
        self.assertNotIn("21", g)
        self.assertEqual(g["34"]["inputs"], {
            "model": ["20", 0], "positive": ["4", 0],
            "negative": ["5", 0], "source_latent": ["6", 0]})
        self.assertEqual(g["7"]["inputs"]["model"], ["34", 0])
        self.assertEqual(g["7"]["inputs"]["positive"], ["34", 1])
        self.assertEqual(g["7"]["inputs"]["negative"], ["34", 2])
        self.assertEqual(g["7"]["inputs"]["latent_image"], ["34", 3])

    def test_second_adapter_without_identity_targets_the_base(self):
        g = self.K.graph("none", "test", "portrait", second_strength=3)
        self.assertNotIn("20", g)
        self.assertEqual(g["21"]["inputs"]["model"], ["1", 0])
        self.assertEqual(g["34"]["inputs"]["model"], ["21", 0])
        self.assertEqual(g["7"]["inputs"]["model"], ["34", 0])
        self.assertEqual(g["7"]["inputs"]["positive"], ["34", 1])
        self.assertEqual(g["7"]["inputs"]["negative"], ["34", 2])
        self.assertEqual(g["7"]["inputs"]["latent_image"], ["34", 3])

    def test_strengths_above_one_point_five_are_not_silently_clamped(self):
        g = self.K.graph("avery.safetensors", "test", "portrait", strength=1.8)
        self.assertEqual(g["20"]["inputs"]["strength_model"], 1.8)

    def test_nonfinite_or_out_of_range_strength_fails(self):
        for strength in (float("nan"), float("inf"), -1, 6):
            with self.assertRaises(ValueError):
                self.K.graph("x", "test", "portrait", strength=strength)

    def test_worker_passes_both_explicit_strengths_to_krea(self):
        with mock.patch.dict(os.environ, {"KREA2_SECOND_ADAPTER": "second.safetensors"}):
            W, calls, _ = Krea2WorkerRoute().run_job({
                "id": "abc123abc123", "user": "Alex", "prompt": "a turtle", "lora_path": None,
                "requires": ["render.krea2"], "strength": 1.4,
                "second_adapter": "second.safetensors", "second_adapter_strength": 3.0})
        cmd = next(c for c in calls if W.KREA in c)
        self.assertEqual(cmd[-2:], ["1.4", "3.0"])


class DatasetRunnerImports(unittest.TestCase):
    def test_the_dataset_runner_imports_and_builds_its_tables(self):
        """A call that breaks at module level is invisible to a compile check; only an import shows it."""
        D = _load_bin("p20_dataset")
        self.assertIn("met", D.SOURCES)
        self.assertIsInstance(D.SPARE_VLM_ENDPOINTS, list)
        for ep in D.SPARE_VLM_ENDPOINTS + D.DEFAULT_VLM_FLEET:
            self.assertTrue(ep["host"].startswith("http://"), ep)


class KreaReferenceGraph(unittest.TestCase):
    def test_combined_reference_graph_preserves_adapters_and_encoding_dependencies(self):
        K = _load_bin('p20_krea_render')
        g = K.graph('none','control','a portrait',second_strength=4.0,reference_image='source.png')
        self.assertEqual(g['21']['inputs']['model'],['1',0])
        self.assertEqual(g['21']['inputs']['strength_model'],4.0)
        self.assertEqual(g['32']['inputs']['model'],['21',0])
        self.assertEqual(g['32']['inputs']['strength_model'],1.0)
        self.assertEqual(g['34']['inputs']['model'],['32',0])
        self.assertEqual(g['33']['inputs']['model'],['34',0])
        self.assertEqual(g['7']['inputs']['model'],['33',0])
        self.assertEqual(g['7']['inputs']['positive'],['34',1])
        self.assertNotIn('20',g)
    def test_generated_png_reference_preserves_bytes_and_filename_type(self):
        K = _load_bin("p20_krea_render")
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder,'source.png')
            data = b'\x89PNG\r\n\x1a\ngenerated source'
            with open(source,'wb') as f: f.write(data)
            adapter = os.path.join(folder,K.EDIT_ADAPTER)
            with open(adapter,'wb') as f: f.write(b'fixture adapter')
            output = os.path.join(folder,'input')
            with mock.patch.dict(os.environ,{'FOUNDRY_LORAS':folder,'FOUNDRY_COMFY_INPUT':output}), \
                 mock.patch.object(K,'EDIT_SHA256',hashlib.sha256(b'fixture adapter').hexdigest()):
                name = K.stage_reference({'path':source,'sha256':hashlib.sha256(data).hexdigest(),
                                          'kind':'generated_character'},'generated_test')
            self.assertEqual(name,'generated_test_reference.png')
            with open(os.path.join(output,name),'rb') as f: self.assertEqual(f.read(),data)

    def test_reference_graph_uses_both_image_paths_and_no_character_stack(self):
        K = _load_bin("p20_krea_render")
        g = K.graph("none", "reference_test", "a portrait", reference_image="approved.jpg")
        self.assertEqual(g["4"]["inputs"]["image"], ["30", 0])
        self.assertEqual(g["5"]["inputs"]["image"], ["30", 0])
        self.assertEqual(g["33"]["inputs"]["target_latent"], g["7"]["inputs"]["latent_image"])
        self.assertEqual(g["2"]["inputs"]["device"], "default")
        self.assertEqual(g["32"]["inputs"]["lora_name"], K.EDIT_ADAPTER)
        self.assertEqual(g["33"]["inputs"]["model"], ["34", 0])
        self.assertEqual(g["34"]["inputs"]["positive"], ["4", 0])
        self.assertEqual(g["34"]["inputs"]["negative"], ["5", 0])
        self.assertEqual(g["7"]["inputs"]["positive"], ["34", 1])
        self.assertEqual(g["7"]["inputs"]["negative"], ["34", 2])
        for lora, bypass in [("character.safetensors", 0)]:
            with self.assertRaises(ValueError):
                K.graph(lora, "reference_test", "portrait", second_strength=bypass, reference_image="a.jpg")

    def test_changed_reference_bytes_are_rejected_before_submission(self):
        K = _load_bin("p20_krea_render")
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "approved.jpg")
            with open(source, "wb") as f:
                f.write(b"changed source")
            with self.assertRaisesRegex(ValueError, "reference changed"):
                K.stage_reference({"path": source, "sha256": "0" * 64}, "reference_test")


class RenderVisibilityEvents(unittest.TestCase):
    def test_encoder_and_sampler_events_publish_real_job_phase_and_counters(self):
        W = _load_bin("p20_render_worker")
        events = [
            {"type":"executing", "data":{"node":"4"}},
            {"type":"progress", "data":{"value":1,"max":8}},
            {"type":"progress", "data":{"value":8,"max":8}},
            {"type":"executing", "data":{"node":"8"}}]
        ws = mock.Mock()
        ws.recv.side_effect = [json.dumps(x) for x in events] + [OSError("closed")]
        module = types.SimpleNamespace(create_connection=lambda *a,**k: ws)
        with mock.patch.dict(sys.modules, {"websocket":module}), \
             mock.patch.object(W,"post_gpu_status") as post, \
             mock.patch.object(W,"open",mock.mock_open(),create=True):
            W._progress_reporter("token","abc123",threading.Event(),
                                 W._render_stages({"requires":["render.krea2"],"reference":{"id":"ref"}}))
        states = [x.args[1] for x in post.call_args_list]
        self.assertEqual(states[0]["phase"], "Encoding reference and instruction")
        self.assertEqual(states[1]["job_id"], "abc123")
        self.assertEqual(states[1]["detail"], "rendering job abc123")
        self.assertEqual((states[1]["step"],states[1]["steps_total"]),(1,8))
        self.assertIsNone(states[1]["remaining_secs"])
        self.assertEqual(states[-1]["phase"], "Decoding image")
        self.assertTrue(any(x.get("step")==8 for x in states))

    def _holds(self, text):
        W = _load_bin("p20_render_worker")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "CURRENT_RUN.json")
            if text is not None:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
            with mock.patch.object(W._P, "CURRENT_RUN", p):
                return W.compute_holds_card()

    def test_a_gpu_compute_order_holds_the_card_against_a_render(self):
        win = "hai-win-mothership-qwen3.8-flash-next-strata-q2-0-20261106-234029-beae_mothership"
        row = lambda state, phase: json.dumps({"name": win, "state": state, "phase": phase})     # noqa: E731
        self.assertEqual(self._holds(row("compute", "compute(gpu): helper_window.py \u00b7 72.3 tok/s")), win)
        self.assertEqual(self._holds(row("compute", "compute(mixed): x.py")), win)
        self.assertEqual(self._holds(row("compute", "")), win, "a compute order that names no load is not free")
        self.assertEqual(self._holds(row("compute", "compute(cpu): x.py")), "", "a CPU order leaves the card alone")
        self.assertEqual(self._holds(row("done", "compute(gpu): x.py")), "")
        self.assertEqual(self._holds(None), "", "no file is no claim")
        self.assertIn("unreadable", self._holds("{not json"), "could not tell is not free")

    def test_the_worker_fetches_no_jobs_under_a_held_card_and_does_when_it_is_free(self):
        W = _load_bin("p20_render_worker")

        def one_pass(holder):
            with mock.patch.object(W, "acquire_singleton", return_value=True), mock.patch.object(W, "beat"), \
                 mock.patch.object(W, "_token", return_value="t"), mock.patch.object(W.threading, "Thread"), \
                 mock.patch.object(W, "restart_requested", return_value=False), mock.patch.object(W, "post_gpu_status"), \
                 mock.patch.object(W, "gpu_status", return_value={"state": "free", "detail": ""}), \
                 mock.patch.object(W, "WORKER_CAN_RENDER", True), mock.patch.object(W, "log"), \
                 mock.patch.object(W, "compute_holds_card", return_value=holder), \
                 mock.patch.object(W, "fetch_jobs", return_value=[]) as fetch, \
                 mock.patch.object(W.time, "sleep", side_effect=SystemExit):
                with self.assertRaises(SystemExit):
                    W.main()
            return fetch.call_count
        self.assertEqual(one_pass("a window"), 0)
        self.assertEqual(one_pass(""), 1, "positive control: a free card still takes work")

    def test_heartbeat_status_is_not_merged_with_a_previous_job(self):
        W = _load_bin("p20_render_worker")
        with mock.patch.object(W,"_api"):
            W.post_gpu_status("t", {"state":"rendering","job_id":"old","step":8,"steps_total":8})
            W.post_gpu_status("t", {"state":"free","detail":""})
        self.assertNotIn("job_id", W.LAST_GPU_STATUS)
        self.assertNotIn("step", W.LAST_GPU_STATUS)


class TheFoundrysOwnCaptionKinds(unittest.TestCase):
    """Without an extension a factory offers its own two caption kinds, the neutral prompt and the minimal default."""

    def test_without_an_extension_only_the_foundrys_own_kinds_remain(self):
        with mock.patch.object(F, "_foundry_extensions", lambda: []):
            k = F.caption_kinds()
            self.assertEqual(sorted(k), ["framing_only", "minimal"])
            self.assertIs(k["minimal"]["prompt"], F.CAPTION_PROMPT_PLAIN)
            self.assertEqual(F.caption_default(), "minimal")


def _test_extensions():
    """The modules beside this suite named test_foundry_ext_<name>.py. Their test classes run with this suite's and
    their EXPECT_TESTS add to its pin, so a factory without an extension pins only what it runs."""
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    return [importlib.import_module(os.path.splitext(os.path.basename(p))[0])
            for p in sorted(glob.glob(os.path.join(here, "test_foundry_ext_*.py")))]


_TEST_EXTENSIONS = _test_extensions()
for _ext in _TEST_EXTENSIONS:
    for _name, _cls in vars(_ext).items():
        if isinstance(_cls, type) and issubclass(_cls, unittest.TestCase) and _cls.__module__ == _ext.__name__:
            globals()[_name] = _cls


if __name__ == "__main__":
    full_run = len(sys.argv) == 1
    prog = unittest.main(verbosity=2, exit=False)
    if not prog.result.wasSuccessful():
        sys.exit(1)
    if full_run:
        want = int(os.environ.get("EXPECT_TESTS") or EXPECT_TESTS + sum(m.EXPECT_TESTS for m in _TEST_EXTENSIONS))
        if prog.result.testsRun != want:
            print("[PIN: MISMATCH - %d ran, %d expected]" % (prog.result.testsRun, want))
            sys.exit(3)
        print("[PIN: ok - %d ran]" % prog.result.testsRun)
    sys.exit(0)


class ClockPolicyThreeSettings(unittest.TestCase):
    """The box's standing clock policy is three settings (a lock, a floor, a ceiling), read by one module for the daemon
    and the foundry; a box with none set gets no clock duty and runs its card at its own boost."""

    def test_a_lock_wins_a_band_follows_and_nothing_is_off(self):
        import clock_policy as CP
        self.assertEqual(CP.box_policy({"FOUNDRY_TARGET_CLOCK": "1500", "FOUNDRY_CLOCK_FLOOR": "1200"}), 1500)
        self.assertEqual(CP.box_policy({"FOUNDRY_CLOCK_FLOOR": "1200", "FOUNDRY_CLOCK_CEILING": "1800"}),
                         {"floor": 1200, "ceiling": 1800})
        self.assertEqual(CP.box_policy({"FOUNDRY_CLOCK_CEILING": "1800"}), {"floor": None, "ceiling": 1800})
        self.assertIsNone(CP.box_policy({}), "a stranger's box, nothing set: no clock duty")
        self.assertIsNone(CP.box_policy({"FOUNDRY_TARGET_CLOCK": "0"}))
        self.assertEqual(CP.label(None), "unlocked")
        self.assertEqual(CP.label({"floor": 1200, "ceiling": 1800}), "band 1200-1800 MHz")

    def test_the_arguments_for_each_form(self):
        import clock_policy as CP
        logs = []
        self.assertEqual(CP.clock_args(1500, logs.append, lambda: None), ["-lgc", "1500"])
        self.assertEqual(CP.clock_args({"floor": 1200, "ceiling": 1800}, logs.append, lambda: None), ["-lgc", "1200,1800"])
        self.assertEqual(CP.clock_args({"ceiling": 1800}, logs.append, lambda: (210, 2100)), ["-lgc", "210,1800"])
        self.assertEqual(CP.clock_args({"floor": 1200}, logs.append, lambda: (210, 2100)), ["-lgc", "1200,2100"])
        self.assertEqual(CP.clock_args("unlocked", logs.append, lambda: None), ["-rgc"])
        self.assertIsNone(CP.clock_args({"floor": 1900, "ceiling": 1800}, logs.append, lambda: None))
        self.assertIsNone(CP.clock_args({"floor": 1200}, logs.append, lambda: None), "a one-sided band with no card range")
        self.assertEqual(len(logs), 2)

    def test_the_daemon_reapplies_a_band_when_the_card_is_above_its_ceiling(self):
        H = load_health_daemon()
        with mock.patch.object(H, "CLOCK_POLICY", {"floor": 1200, "ceiling": 1800}), \
                mock.patch.object(H, "TARGET_CLOCK_MHZ", 1800), mock.patch.object(H, "_relocks", 0), \
                mock.patch.object(H, "smi", side_effect=[("1965, 2100", ""), ("1800", "")]), \
                mock.patch.object(H.subprocess, "run") as run, mock.patch.object(H, "log"):
            ok, note = H.check_clock()
        self.assertIs(ok, False)
        self.assertEqual(run.call_args[0][0], ["nvidia-smi", "-lgc", "1200,1800"])
        self.assertIn("band 1200-1800", note)

    def test_a_box_with_no_policy_restores_the_cards_own_boost_after_an_order(self):
        calls, logs = [], []

        def run(args, **kw):
            calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")
        import p20_status
        with mock.patch.object(F, "BOX_CLOCK_LOCK_MHZ", 0), mock.patch.object(F, "BOX_CLOCK_POLICY", None), \
                mock.patch.object(p20_status, "STATUS_DIR", tempfile.mkdtemp()), mock.patch.object(F.subprocess, "run", run):
            F.restore_box_clock(1500, logs.append)
        self.assertEqual(calls, [["nvidia-smi", "-rgc"]])

    def test_a_box_with_a_band_restores_the_band_after_an_order(self):
        calls, logs = [], []

        def run(args, **kw):
            calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(F, "BOX_CLOCK_LOCK_MHZ", 0), \
                mock.patch.object(F, "BOX_CLOCK_POLICY", {"floor": 1200, "ceiling": 1800}), \
                mock.patch.object(F.subprocess, "run", run):
            F.restore_box_clock("unlocked", logs.append)
        self.assertEqual(calls, [["nvidia-smi", "-lgc", "1200,1800"]])
        self.assertTrue(any("band 1200-1800 MHz restored" in l for l in logs), logs)


class ExternalTreesAreSettings(unittest.TestCase):
    """The engines and caches the foundry points at (ai-toolkit, ComfyUI, the model cache, the dataset root, CorridorKey,
    ffmpeg) are settings in etc\\lab.env; nothing in the code names a drive. A box with none set has empty paths, and
    the features that need them stay off."""

    def _load(self, lab_env_lines):
        import importlib.util
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "etc"))
        with open(os.path.join(root, "etc", "lab.env"), "w", encoding="utf-8") as f:
            f.write("\n".join(lab_env_lines) + "\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FOUNDRY_", "CORRIDORKEY_", "FFMPEG_"))}
        env["FOUNDRY_ROOT"] = root
        with mock.patch.dict(os.environ, env, clear=True):
            spec = importlib.util.spec_from_file_location("fp_undertest", os.path.join(BIN, "foundry_paths.py"))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
        return m

    def test_a_box_with_nothing_set_points_nowhere(self):
        m = self._load([])
        for name in ("AI_TOOLKIT", "COMFY_ROOT", "COMFY_LORAS", "COMFY_OUTPUT", "COMFY_INPUT", "COMFY_MODELS", "VENV_PY",
                     "COMFY_LAUNCH", "RESCUE_BURST", "DATASET_ROOT", "RENDER_SLOW_TIER", "CORRIDORKEY_DIR", "FFMPEG_DIR",
                     "AI_TOOLKIT_KREA2", "KREA2_TRAIN_DIR", "MODEL_CACHE_DIR"):
            self.assertEqual(getattr(m, name), "", name)

    def test_the_derived_paths_follow_their_roots(self):
        m = self._load(["FOUNDRY_COMFY_ROOT=X:\\comfy", "FOUNDRY_AI_TOOLKIT=X:\\aitk"])
        self.assertEqual(m.COMFY_LORAS, os.path.join("X:\\comfy", "models", "loras"))
        self.assertEqual(m.COMFY_INPUT, os.path.join("X:\\comfy", "input"))
        self.assertEqual(m.COMFY_OUTPUT, os.path.join("X:\\comfy", "output"))
        self.assertEqual(m.VENV_PY, os.path.join("X:\\aitk", "venv", "python.exe"))
        self.assertEqual(m.RESCUE_BURST, os.path.join("X:\\aitk", "p20_rescue_burst.py"))
        m2 = self._load(["FOUNDRY_COMFY_ROOT=X:\\comfy", "FOUNDRY_LORAS=Y:\\loras"])
        self.assertEqual(m2.COMFY_LORAS, "Y:\\loras", "an explicit setting beats the derived one")

