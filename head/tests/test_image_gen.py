"""Project #19 P1 — image generation: workflows, quotas, gating, wiring, and the render whitelist.

The invariants: quality maps to the measured VRAM choreography (fast coexists, best evicts);
quotas are per-user-per-day with admins unlimited; every generation is audited; and the ONLY thing
the UI will ever render as an image is a same-origin /images/ path.
"""
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import comfy_client
import config


class WorkflowTests(unittest.TestCase):
    def test_fast_is_sdxl_best_is_flux(self):
        fast = comfy_client.build_workflow("a cat", quality="fast")["prompt"]
        best = comfy_client.build_workflow("a cat", quality="best")["prompt"]
        self.assertEqual(fast["1"]["inputs"]["ckpt_name"], "sd_xl_base_1.0.safetensors")
        self.assertEqual(best["1"]["inputs"]["ckpt_name"], "flux1-dev-fp8.safetensors")
        self.assertEqual(fast["5"]["inputs"]["cfg"], 7.0)
        self.assertEqual(best["5"]["inputs"]["cfg"], 1.0)   # Flux runs cfg 1.0

    def test_sizes_and_default(self):
        wide = comfy_client.build_workflow("x", size="wide")["prompt"]["4"]["inputs"]
        self.assertEqual((wide["width"], wide["height"]), (1344, 768))
        default = comfy_client.build_workflow("x", size="bogus")["prompt"]["4"]["inputs"]
        self.assertEqual((default["width"], default["height"]), (1024, 1024))

    def test_wallpaper_appends_upscale_tail(self):
        wf = comfy_client.build_workflow("x", size="wallpaper")["prompt"]
        self.assertEqual(wf["10"]["inputs"]["model_name"], comfy_client.UPSCALE_MODEL)
        self.assertEqual(wf["11"]["class_type"], "ImageUpscaleWithModel")
        self.assertEqual((wf["12"]["inputs"]["width"], wf["12"]["inputs"]["height"]),
                         comfy_client.UPSCALE_SIZES["wallpaper"])
        self.assertEqual(wf["7"]["inputs"]["images"], ["12", 0])   # save the upscaled image
        plain = comfy_client.build_workflow("x", size="wide")["prompt"]
        self.assertNotIn("10", plain)                              # no tail on ordinary sizes
        self.assertEqual(plain["7"]["inputs"]["images"], ["6", 0])
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('args["size"] = "wallpaper"', body)
        self.assertIn('"wallpaper"', tools_src.split("_GENERATE_IMAGE", 1)[1].split("def ", 1)[0])

    def test_ultrawide_for_the_owners_monitor(self):
        wf = comfy_client.build_workflow("x", size="ultrawide")["prompt"]
        self.assertEqual((wf["4"]["inputs"]["width"], wf["4"]["inputs"]["height"]), (1536, 640))
        self.assertEqual((wf["12"]["inputs"]["width"], wf["12"]["inputs"]["height"]),
                         (3440, 1440))
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('args["size"] = "ultrawide"', body)
        self.assertIn('args.get("size") in ("wallpaper", "ultrawide")', body)   # auto-best
        # nearest_size (controlnet restyles) never picks an upscale-tail size
        self.assertNotIn(comfy_client.nearest_size(3440, 1440),
                         comfy_client.UPSCALE_SIZES.values())

    def test_prompt_clipped_and_seed_set(self):
        wf = comfy_client.build_workflow("y" * 5000)["prompt"]
        self.assertLessEqual(len(wf["2"]["inputs"]["text"]), 2000)
        self.assertIsInstance(wf["5"]["inputs"]["seed"], int)


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = comfy_client._images_dir
        comfy_client._images_dir = lambda: pathlib.Path(self.tmp)

    def tearDown(self):
        comfy_client._images_dir = self._orig

    def test_admin_unlimited_user_decrements(self):
        admin = sorted(config.ADMIN_USERS)[0] if config.ADMIN_USERS else "Alex"
        self.assertIsNone(comfy_client.quota_remaining(admin))
        self.assertEqual(comfy_client.quota_remaining("Riley"), config.IMG_GEN_DAILY_QUOTA)
        comfy_client.record_use("Riley")
        comfy_client.record_use("Riley")
        self.assertEqual(comfy_client.quota_remaining("Riley"),
                         config.IMG_GEN_DAILY_QUOTA - 2)

    def test_save_image_sanitizes(self):
        name = comfy_client.save_image("Fra nk/ie", "cool cat!! <script>", b"png-bytes")
        self.assertTrue(name.endswith(".png"))
        saved = list(pathlib.Path(self.tmp).rglob("*.png"))
        self.assertEqual(len(saved), 1)
        self.assertNotIn("<", saved[0].name)


class Img2ImgTests(unittest.TestCase):
    """#19 P2: img2img workflows + upload plumbing."""

    def test_img2img_workflow_shape(self):
        wf = comfy_client.build_img2img_workflow("a watercolor", "user-photo.png",
                                                 strength=0.5)["prompt"]
        self.assertEqual(wf["8"]["class_type"], "LoadImage")
        self.assertEqual(wf["8"]["inputs"]["image"], "user-photo.png")
        self.assertEqual(wf["5"]["inputs"]["denoise"], 0.5)
        self.assertEqual(wf["5"]["inputs"]["latent_image"], ["9", 0])   # encoded photo, not noise

    def test_strength_clamped(self):
        low = comfy_client.build_img2img_workflow("x", "p.png", strength=0.01)["prompt"]
        high = comfy_client.build_img2img_workflow("x", "p.png", strength=5)["prompt"]
        self.assertEqual(low["5"]["inputs"]["denoise"], 0.2)
        self.assertEqual(high["5"]["inputs"]["denoise"], 0.95)

    def test_latest_upload_ordering(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        orig = comfy_client._images_dir
        comfy_client._images_dir = lambda: tmp
        try:
            d = comfy_client.uploads_dir("F")
            d.mkdir(parents=True)
            (d / "20260101T000000Z-old.png").write_bytes(b"x")
            (d / "20260711T000000Z-new.jpg").write_bytes(b"x")
            (d / "notes.txt").write_bytes(b"x")                      # non-image ignored
            self.assertEqual(comfy_client.latest_upload("F"), "20260711T000000Z-new.jpg")
            self.assertIsNone(comfy_client.latest_upload("Nobody"))
        finally:
            comfy_client._images_dir = orig

    def test_tool_img2img_branch_and_upload_endpoint(self):
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('source == "latest"', body)                    # latest resolution
        self.assertIn("build_img2img_workflow", body)
        self.assertIn("upload_to_comfy", body)                       # re-push at generation time
        app_src = open("app.py", encoding="utf-8").read()
        up = app_src.split("async def upload_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_enforce_user_param(request, user)", up)
        self.assertIn('b"\\x89PNG', up)                              # magic-byte validation
        self.assertIn("15 * 1024 * 1024", up)                        # size cap


class ControlNetRestyleTests(unittest.TestCase):

    def test_workflow_shape(self):
        wf = comfy_client.build_controlnet_restyle_workflow(
            "graphite sketch", "robot.png", width=1216, height=832)["prompt"]
        self.assertEqual(wf["20"]["class_type"], "Canny")
        self.assertEqual(wf["22"]["inputs"]["type"], "canny/lineart/anime_lineart/mlsd")
        self.assertEqual(wf["23"]["inputs"]["strength"], comfy_client.CN_RESTYLE_STRENGTH)
        self.assertEqual(wf["5"]["inputs"]["denoise"], 1.0)          # FULL restyle
        self.assertEqual(wf["5"]["inputs"]["latent_image"], ["4", 0])  # from noise, not the photo
        self.assertEqual(wf["4"]["inputs"]["width"], 1216)
        self.assertEqual(wf["19"]["inputs"]["width"], 1216)          # canny input matches latent
        self.assertEqual(wf["1"]["inputs"]["ckpt_name"], "sd_xl_base_1.0.safetensors")  # SDXL-only

    def test_nearest_size_matches_aspect(self):
        self.assertEqual(comfy_client.nearest_size(3000, 2000), comfy_client.SIZES["landscape"])
        self.assertEqual(comfy_client.nearest_size(800, 1200), comfy_client.SIZES["portrait"])
        self.assertEqual(comfy_client.nearest_size(500, 500), comfy_client.SIZES["square"])
        self.assertEqual(comfy_client.nearest_size(0, 0), comfy_client.SIZES["square"])  # fail-safe

    def test_tool_prefers_cn_for_preset_restyles(self):
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("controlnet_available()", body)                # fail-closed availability gate
        self.assertIn('args.get("strength") is None', body)          # explicit strength = classic img2img
        self.assertIn('quality == "fast" and style_key in comfy_client.STYLES', body)
        self.assertIn("build_controlnet_restyle_workflow", body)
        self.assertIn("mode={mode}", body)                           # audited: cn / i2i / t2i


class StyleAndUnitsTests(unittest.TestCase):

    def test_sketch_preset_forces_real_transformation(self):
        prompt, neg, strength = comfy_client.apply_style("my robot photo", "", "sketch")
        self.assertIn("graphite", prompt)
        self.assertIn("monochrome", prompt)
        self.assertIn("color", neg)                      # strength=0.5: higher values melt the subject (e.g., robot → urn)
        self.assertEqual(strength, 0.5)

    def test_mono_styles_grayscale_the_source(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")   # a red square
        out = comfy_client.prep_source(buf.getvalue(), "sketch")
        px = Image.open(BytesIO(out)).getpixel((4, 4))
        self.assertEqual(px[0], px[1])                   # R==G==B: no color survives
        self.assertEqual(px[1], px[2])
        untouched = comfy_client.prep_source(buf.getvalue(), "watercolor")
        self.assertEqual(untouched, buf.getvalue())      # color styles keep their source
        garbage = comfy_client.prep_source(b"not-a-png", "sketch")
        self.assertEqual(garbage, b"not-a-png")          # fail-open
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("prep_source", body)               # wired into the upload path

    def test_explicit_strength_beats_preset_and_unknown_style_noop(self):
        _, _, s = comfy_client.apply_style("x", "", "sketch", strength=0.4)
        self.assertEqual(s, 0.4)
        p2, n2, s2 = comfy_client.apply_style("x", "neg", "not-a-style", strength=None)
        self.assertEqual((p2, n2, s2), ("x", "neg", None))

    def test_best_costs_three_units(self):
        self.assertEqual(comfy_client.BEST_QUOTA_COST, 3)
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("BEST_QUOTA_COST", body)
        self.assertIn("remaining < units", body)          # can't start a best on 1 unit left
        self.assertIn("record_use(user, units=units)", body)

    def test_text_in_image_upgrades_to_best(self):
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("auto_best", body)
        self.assertIn('quality, auto_best = "best", True', body)
        self.assertIn('units, auto_best = count, False', body)   # degrade keeps the batch count
        self.assertIn("remaining < units and auto_best", body)

    def test_batch_generation_wired(self):
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('max(1, min(4, int(args.get("count") or 1)))', body)
        self.assertIn('count = min(count, 2)', body)               # Flux batches cap at 2
        self.assertIn("* count", body)                             # units scale with the batch
        self.assertIn("for i in range(count):", body)
        self.assertIn("base_seed + i * 7919", body)                # distinct variations
        self.assertIn("n={count}", body)                           # audited
        self.assertIn('"count":', tools_src.split("_GENERATE_IMAGE", 1)[1].split("def ", 1)[0])

    def test_deterministic_source_attachment(self):
        # Must attach source_image tool-side, never trusting the model to carry the parameter.
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('args["source_image"] = "latest"', body)
        self.assertIn("latest_upload(user)", body)          # only when an upload actually exists
        self.assertIn("src=", body)                          # audit shows the source used

    def test_pure_restyle_uses_style_only_prompt(self):
        # In img2img restyling, the prompt must contain ONLY style descriptors.
        # Content is provided by the source image; including content in the prompt
        # causes the model to hallucinate/override the source (e.g., inventing subjects).
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("restyle_ask", body)
        self.assertIn('styled_prompt = comfy_client.STYLES[style_key][0].strip(" ,")', body)
        # and the description warns the model off inventing photo contents
        self.assertIn("CANNOT see that photo", tools_src)

    def test_sketch_negatives_block_color_bleed(self):
        # img2img at 0.75 denoise let the source's colors through; the preset now forbids them
        # from both directions (monochrome/black-and-white positive + colorful negatives).
        suffix, neg, _ = comfy_client.STYLES["sketch"]
        self.assertIn("black and white", suffix)
        self.assertIn("colorful", neg)

    def test_audit_shows_asked_vs_prompt(self):
        tools_src = open("tools.py", encoding="utf-8").read()
        body = tools_src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("asked=", body)
        self.assertIn("prompt=", body)
        self.assertIn('def _generate_image(user, args, user_text=""):', tools_src)

    def test_units_quota_math(self):
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        orig = comfy_client._images_dir
        comfy_client._images_dir = lambda: tmp
        try:
            comfy_client.record_use("F", units=3)
            self.assertEqual(comfy_client.quota_remaining("F"),
                             config.IMG_GEN_DAILY_QUOTA - 3)
        finally:
            comfy_client._images_dir = orig


class ToolWiringTests(unittest.TestCase):
    def test_registered_in_both_tool_lists(self):
        import tools
        for lst in (tools.BRAIN_TOOLS, tools.HEAVY_TOOLS):
            names = [t["function"]["name"] for t in lst if isinstance(t, dict)]
            self.assertIn("generate_image", names)

    def test_gating_choreography_and_audit(self):
        src = open("tools.py", encoding="utf-8").read()
        body = src.split("def _generate_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("quota_remaining", body)                  # quota gate
        self.assertIn("gaming_busy", body)                      # never stutter a game
        self.assertIn("ensure_up", body)                        # WoL-aware
        self.assertIn('quality == "best"', body)
        self.assertIn("unload_lead_llms", body)           # Flux evicts LLMs...
        self.assertIn("free_vram", body)                        # ...and returns the card
        self.assertIn("IMG_GEN", body)                          # audit line
        self.assertIn('if name == "generate_image":', src)      # dispatched


class ServeAndRenderTests(unittest.TestCase):
    def test_images_endpoint_enforced_and_sanitized(self):
        app_src = open("app.py", encoding="utf-8").read()
        body = app_src.split("def serve_image(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_enforce_user_param(request, img_user)", body)
        self.assertIn('re.sub(r"[^A-Za-z0-9._-]", "", filename)', body)  # traversal-proof
        self.assertIn('endswith(".png")', body)

    def test_markdown_images_whitelisted_to_local_paths(self):
        app_src = open("app.py", encoding="utf-8").read()
        # the ONLY img-producing rule targets /images/ paths; no generic http image rule exists
        self.assertIn("genimg", app_src)
        self.assertIn("\\\\/images\\\\/", app_src.replace("(\\/images\\/", "(\\\\/images\\\\/"))
        self.assertNotIn("<img class=\"genimg\" src=\"http", app_src)

    def test_config_defaults(self):
        # A render box is a setting: with nothing configured there is no renderer, and that is not a crash.
        self.assertTrue(config.COMFY_URL == "" or config.COMFY_URL.startswith("http"))
        self.assertGreater(config.IMG_GEN_TIMEOUT, 200)         # covers Flux cold (205s measured)
        self.assertGreater(config.IMG_GEN_DAILY_QUOTA, 0)

    def test_no_renderer_configured_is_not_a_live_renderer(self):
        with patch.object(config, "COMFY_URL", ""):
            self.assertFalse(comfy_client.healthy())
            self.assertFalse(comfy_client.ensure_up(timeout=1))


if __name__ == "__main__":
    unittest.main()
