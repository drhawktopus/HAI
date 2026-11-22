"""The roster: the scheduler's per-device tables come from roster records, and for the lab's roster they equal the tables
the code held before it read one (same keys, same values, same order), so no placement moves. Also the loader's rules:
no file = one device with every role; a named, unreadable or invalid file stops the import."""
import json
import os
import tempfile
import unittest
from unittest import mock

import config
import scheduler

from _no_live_probes import start_no_live_probes, stop_no_live_probes

LAB_ROSTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "lab_roster.json")
BRAIN_WORDS = config.roster_aliases("bighonker")     # the brain machine's own words (its PC<n>_ALIASES setting)


def setUpModule():
    start_no_live_probes()


def tearDownModule():
    stop_no_live_probes()


def literal_tables():
    """The literal tables scheduler.py held for the lab's devices before it read a roster: the lab roster must derive
    exactly these."""
    M, B, S, U = "mothership", "bighonker", "softserve", "sundae"
    return {
        "LEAD_BOX": M, "BRAIN": B, "FAILOVER": S, "HEAD": U, "ADVISOR_HOME": S,
        "CARDS": {
            M: {"url": config.HEAVY_HOST, "vram_gb": 24.0, "ram_gb": 31.9, "cpu": 16, "label": "Mothership",
                "gpu": "RX 7900 XTX", "big_jobs_only_above_gb": 8.0},
            B: {"url": config.BRAIN_HOST, "vram_gb": 8.0, "ram_gb": 15.9, "cpu": 6, "label": "BigHonker",
                "gpu": "RTX 3070", "cpu_heavy_ok": False},
            S: {"url": config.FAILOVER_HOST, "vram_gb": 8.0, "ram_gb": 47.3, "cpu": 16, "label": "Softserve",
                "gpu": "RTX 3070 Ti", "hosts_services": True, "cpu_heavy_ok": True},
            U: {"url": None, "vram_gb": 0.0, "ram_gb": 15.0, "cpu": 6, "label": "Sundae", "gpu": None,
                "cpu_heavy_ok": True, "hosts_services": True, "parent": S, "container_env": True}},
        "RAM_RESERVE_GB": {M: 6.0, B: 3.0, S: 4.0, U: 3.0},
        "CPU_BUSY_PCT": {M: 60.0, B: 50.0, S: 40.0, U: 70.0},
        "CAPTION_S_PER_IMG": {M: 2.75, B: 3.55, S: 5.1},
        "CHAT_TIER": {B: 0, M: 1, S: 2},
        "ORDER_HOST_DEPRIORITY": {S: 1},
        "TRIAGE_CLASSIFY_S_PER_ROW": {M: 3.22, B: 1.82, S: 1.06},
        "TRIAGE_REWRITE_S_PER_SECTION": {M: 17.0, B: 7.4},
        "HOST_CAPTION_MODEL": {M: "aha2025/llama-joycaption-beta-one-hf-llava:Q8_0",
                               B: "aha2025/llama-joycaption-beta-one-hf-llava:Q4_K_M",
                               S: "aha2025/llama-joycaption-beta-one-hf-llava:Q4_K_M"},
        "HOST_CAPTION_WORKERS": {M: 4, B: 4, S: 2},
        "HOST_COMPUTE_PY": {M: r"F:\ai-toolkit\venv\python.exe", B: r"F:\comfy-bench\venv\Scripts\python.exe"},
        "TABLETS": {
            "gandalf": {"label": "gandalf", "cpu": 8, "ram_gb": 2.79, "gflops": 19.25},
            "frodo": {"label": "frodo", "cpu": 8, "ram_gb": 1.76, "gflops": 17.38},
            "sam": {"label": "sam", "cpu": 8, "ram_gb": 1.76, "gflops": 15.16},
            "merry": {"label": "merry", "cpu": 4, "ram_gb": 1.82, "gflops": 10.28},
            "pippin": {"label": "pippin", "cpu": 4, "ram_gb": 1.82, "gflops": 10.07},
            "legolas": {"label": "legolas", "cpu": 8, "ram_gb": 1.76, "gflops": 17.16},
            "aragorn": {"label": "aragorn", "cpu": 8, "ram_gb": 2.79, "gflops": 18.92},
            "smeagol": {"label": "smeagol", "cpu": 4, "ram_gb": 1.39, "gflops": 0.0, "arch": "armv7l",
                        "runtime_bits": 32, "direct_cpu_eligible": False, "provisional": True},
            "gimli": {"label": "gimli", "cpu": 8, "ram_gb": 1.76, "gflops": 17.27, "provisional": True},
            "boromir": {"label": "boromir", "cpu": 8, "ram_gb": 1.76, "gflops": 17.27, "provisional": True},
            "faramir": {"label": "faramir", "cpu": 8, "ram_gb": 1.76, "gflops": 17.27, "provisional": True}},
    }


def derived_tables(roster):
    return {
        "LEAD_BOX": config.lead_box(roster), "BRAIN": scheduler.roster_role("brain", roster),
        "FAILOVER": scheduler.roster_role("failover", roster), "HEAD": scheduler.roster_role("head", roster),
        "ADVISOR_HOME": scheduler.roster_role("advisor", roster),
        "CARDS": scheduler.roster_cards(roster),
        "RAM_RESERVE_GB": scheduler.roster_table("ram_reserve_gb", roster),
        "CPU_BUSY_PCT": scheduler.roster_table("cpu_busy_pct", roster),
        "CAPTION_S_PER_IMG": scheduler.roster_table("caption_s_per_img", roster),
        "CHAT_TIER": scheduler.roster_table("chat_tier", roster, order_by_value=True),
        "ORDER_HOST_DEPRIORITY": scheduler.roster_table("order_deprioritize", roster),
        "TRIAGE_CLASSIFY_S_PER_ROW": scheduler.roster_table("triage_classify_s_per_row", roster),
        "TRIAGE_REWRITE_S_PER_SECTION": scheduler.roster_table("triage_rewrite_s_per_section", roster),
        "HOST_CAPTION_MODEL": scheduler.roster_table("caption_model", roster),
        "HOST_CAPTION_WORKERS": scheduler.roster_table("caption_workers", roster),
        "HOST_COMPUTE_PY": scheduler.roster_table("compute_py", roster),
        "TABLETS": scheduler.roster_tablets(roster),
    }


def ordered(v):
    """A value with every dict turned into its item list, so equality also checks key ORDER (ties follow it)."""
    if isinstance(v, dict):
        return [(k, ordered(x)) for k, x in v.items()]
    return v


class LabRosterEqualsTheOldTables(unittest.TestCase):
    def setUp(self):
        with open(LAB_ROSTER, encoding="utf-8") as f:
            self.roster = scheduler.validate_roster(json.load(f))

    def test_every_derived_table_equals_the_literal_one_in_order(self):
        old, new = literal_tables(), derived_tables(self.roster)
        self.assertEqual(sorted(old), sorted(new))
        for name in old:
            self.assertEqual(ordered(new[name]), ordered(old[name]), name)

    def test_the_gate_runs_on_the_lab_roster(self):
        """The suite's scheduler was built from this fixture (tests/run_suite.py names it), so every placement test
        in the gate is a weighing on the derived tables."""
        self.assertEqual(os.path.abspath(scheduler.ROSTER_SOURCE or ""), os.path.abspath(LAB_ROSTER))
        old = literal_tables()
        for name in old:
            self.assertEqual(ordered(getattr(scheduler, name)), ordered(old[name]), name)
        self.assertEqual(scheduler.KINDS["advisor"]["home"], "softserve")


class OtherBoxTablesEqualTheOldOnes(unittest.TestCase):
    """The head's other copies of the box list now read the roster; for the lab's roster each equals what it held
    as a literal (same keys, values and order)."""

    def test_the_tolkien_box(self):
        import chat_window
        import gpu_render
        self.assertEqual(scheduler.TOLKIEN_BOX, "rigby")
        self.assertEqual(chat_window.TOLKIEN_BOX, "rigby")
        self.assertEqual(gpu_render.TOLKIEN_BOX, "rigby")
        self.assertEqual(scheduler.FELLOWSHIP_BOXES, ("rigby", "fellowship"))
        self.assertNotIn("rigby", scheduler.CARDS, "Tolkien's box is reached through its desk, never a card")

    def test_the_pin_labels_and_the_address_map(self):
        import app
        import row_pins
        self.assertEqual(ordered(row_pins.LABELS), ordered({"mothership": "Mothership", "bighonker": "BigHonker",
                                                            "softserve": "Softserve", "rigby": "rigby"}))
        self.assertEqual(ordered(app._LLM_HOSTS), ordered({"bighonker": config.BRAIN_HOST,
                                                           "softserve": config.FAILOVER_HOST,
                                                           "mothership": config.HEAVY_HOST}))

    def test_the_host_status_table_and_its_names(self):
        import infra
        self.assertEqual(ordered(infra.HOSTS), ordered({
            "bighonker": {"name": "BigHonker", "ollama": config.BRAIN_HOST, "glances": None,
                          "role": "primary brain (%s)" % config.BRAIN_MODEL},
            "softserve": {"name": "Softserve", "ollama": config.FAILOVER_HOST,
                          "glances": config.device_setting("softserve", "GLANCES") or None,
                          "role": "failover brain + media/virtualization host"},
            "mothership": {"name": "Mothership", "ollama": config.HEAVY_HOST,
                           "glances": config.device_setting("mothership", "GLANCES") or None,
                           "role": "heavyweight (%s)" % config.HEAVY_MODEL}}))

        def old_resolve(target):
            t = (target or "").lower()
            if not t or "all" in t:
                return None
            for k in ("bighonker", "softserve", "mothership"):
                if k in t:
                    return k
            if any(a in t for a in BRAIN_WORDS):
                return "bighonker"
            if "mother" in t or "heavy" in t or "27" in t:
                return "mothership"
            if "soft" in t or "plex" in t or "failover" in t:
                return "softserve"
            return "?"
        words = ["", "all", "mothership", "the 27b box", "heavy", "Mother", "softserve", "plex box", "failover",
                 "bighonker", "honker", "primary", "brain", "toaster", "soft and heavy"] + list(BRAIN_WORDS)
        for w in words:
            self.assertEqual(infra._resolve(w), old_resolve(w), w)

    def test_the_static_brain_ladder_when_placement_raises(self):
        import app
        with mock.patch.object(scheduler, "place", side_effect=RuntimeError("placement down")):
            self.assertEqual(app.brain_order(), ["bighonker", "mothership", "softserve"])
            self.assertEqual(app.brain_order(allow_lead=False), ["bighonker", "softserve"])

    def test_the_wake_macs_and_names(self):
        with mock.patch.dict(os.environ, {"PC1_MAC": "mac-of-the-lead", "PC2_MAC": "mac-of-the-brain"}):
            os.environ.pop("PC3_MAC", None)          # patch.dict puts the whole environment back afterwards
            self.assertEqual(config.device_mac(config.LEAD_BOX), "mac-of-the-lead")
            self.assertEqual(config.role_mac("brain"), "mac-of-the-brain")
            self.assertEqual(config.role_mac("failover"), "", "a device with no MAC is never woken")

        def old_wake(tgt):
            tgt = (tgt or "").lower()
            if any(k in tgt for k in ("mother", "heavy", "27", "big guns")):
                return "mothership"
            if any(k in tgt for k in BRAIN_WORDS + ("brain",)) or tgt == "big":
                return "bighonker"
            return None
        words = ["mothership", "Mothership please", "the heavy box", "27b", "big guns", "bighonker", "the brain",
                 "big", "big box", "", "softserve", "toaster"] + list(BRAIN_WORDS)
        for w in words:
            self.assertEqual(config.wake_device(w), old_wake(w), w)

    def test_the_wake_endpoint(self):
        import app
        with mock.patch.dict(os.environ, {"PC1_MAC": "M1", "PC2_MAC": "B1"}), \
                mock.patch("wol.wake", return_value=["test"]) as sent:
            self.assertEqual(app.wake("")["mac"], "M1", "nothing named wakes the lead")
            self.assertEqual(app.wake("mothership")["mac"], "M1")
            self.assertEqual(app.wake("bighonker")["mac"], "B1")
            self.assertEqual(app.wake("anything else")["mac"], "B1", "an unnamed box wakes the brain, as before")
        self.assertEqual(sent.call_count, 4)

    def test_the_window_clock_and_the_advisors_resident_box(self):
        self.assertIsNone(os.getenv("HAI_WINDOW_GPU_CLOCK"), "the gate must read the roster's value, not an override")
        self.assertIsNone(os.getenv("MCCOY_RESIDENT_HOSTS"), "the gate must read the roster's value, not an override")
        self.assertEqual(config.WINDOW_GPU_CLOCK, {"bighonker": {"floor": 1965}})
        self.assertEqual(config.MCCOY_RESIDENT_HOSTS, {"softserve"})

    def test_the_tool_descriptions_the_model_reads(self):
        import tools

        def desc(t, prop=None):
            f = t["function"]
            return f["parameters"]["properties"][prop]["description"] if prop else f["description"]
        self.assertEqual(desc(tools._HOST), "Check a machine's status: awake/asleep, which Ollama model is loaded, and "
                         "CPU/GPU/RAM load. target: 'bighonker', 'softserve', 'mothership', or 'all'. Use for 'is X "
                         "awake?', 'how loaded is X?'.")
        self.assertEqual(desc(tools._WAKE), "Send a Wake-on-LAN magic packet to wake a sleeping machine. target: "
                         "'mothership' (the big 27b heavyweight host) or 'bighonker' (the primary brain host). Use when "
                         "the user asks to wake/turn on a machine.")
        self.assertTrue(desc(tools._GENERATE_IMAGE).startswith(
            "Generate an image from a text description on Mothership's GPU — the home render host (nothing leaves "
            "the house). NOTE the render runs on MOTHERSHIP, not on the chat brain's host; if you mention where an "
            "image was made, say Mothership. Quality: judge it from the ask"))
        for t in (tools._HOST_ADMIN_RUN, tools._HOST_ADMIN_EXEC):
            self.assertEqual(desc(t, "host"), "Registered host name (from host_admin_list), e.g. 'rigby'.")

    def test_the_acquisition_host_and_its_set_root(self):
        import tools
        self.assertEqual(config.ACQUISITION_HOST, "softserve")
        self.assertEqual(config.acquisition_set_root(), "Z:\\Datasets")
        self.assertEqual(tools.ACQUISITION_SET_ROOT, "Z:\\Datasets")
        props = tools._DATASET_ACQUIRE["function"]["parameters"]["properties"]
        self.assertEqual(props["host"]["description"],
                         "Which machine downloads. 'softserve' is the acquisition host — its SSD is a bounded cache "
                         "draining to the 46 TB array, both local to it, so nothing needs a network share. "
                         "'mothership' (default) downloads straight to the training box. Prefer softserve for bulk or "
                         "archive work; mothership only when the set is needed on the training box immediately.")

    def test_the_renderers_keep_their_ids_and_the_page_its_options(self):
        import app
        import render_options
        self.assertEqual((render_options.FLUX2_RENDERER, render_options.KREA2_RENDERER, render_options.REFERENCE_RENDERER),
                         ("flux2_mothership", "krea2_mothership", "krea2_reference_mothership"),
                         "saved choices and recorded orders name these ids")
        for rid, label in (("flux2_mothership", "Flux.2 · Mothership"), ("krea2_mothership", "Krea 2 · Mothership"),
                           ("krea2_reference_mothership", "Krea 2 Reference · Mothership")):
            self.assertEqual((render_options.RENDERERS[rid]["label"], render_options.RENDERERS[rid]["host"]),
                             (label, "mothership"))
        self.assertIn('      <option value="auto">Auto</option>\n'
                      '      <option value="flux2_mothership">Flux.2 · Mothership</option>\n'
                      '      <option value="krea2_mothership">Krea 2 · Mothership</option>\n'
                      '    </select>', app.CHAT_HTML)
        self.assertNotIn("<!--RENDERER_OPTIONS-->", app.CHAT_HTML)
        for marker in ("@@LEAD_BOX@@", "@@BRAIN@@", "@@FAILOVER@@"):
            self.assertNotIn(marker, app.CHAT_HTML)
        self.assertIn("Uses BigHonker first, then Mothership small model before Softserve.", app.CHAT_HTML)
        self.assertIn('">Computed by Mothership · \'+(d.model||', app.CHAT_HTML)

    def test_every_tool_text_is_filled_from_the_roster(self):
        import json as _json
        import tools
        pools = _json.dumps(tools.HEAVY_TOOLS + tools.BRAIN_TOOLS, ensure_ascii=False)
        for word in ("{LEAD_BOX}", "{BRAIN}", "{FAILOVER}", "{LEAD_BOX_ID}", "{BRAIN_ID}", "{ACQUISITION}"):
            self.assertNotIn(word, pools, "a role placeholder reached the model")
        self.assertIn("Renders on Mothership's GPU (say Mothership if asked where it was made)", pools)
        self.assertIn("e.g. mothership. Omit to let Starfleet place it.", pools)
        self.assertIn("(Mothership, BigHonker, Softserve, the Fellowship)", pools)

    def test_a_head_with_no_roster_puts_every_tier_on_brain_host(self):
        import subprocess
        import sys
        import re
        env = {k: v for k, v in os.environ.items()
               if k not in ("HAI_ROSTER_FILE", "HEAVY_HOST", "FAILOVER_HOST", "MCCOY_RESIDENT_HOSTS")
               and not re.match(r"PC\d+(_VM\d+)?_", k)}      # a lab's own machine settings are not a stranger's
        env.update(HAI_STATE_DIR=tempfile.mkdtemp(), BRAIN_HOST="http://only-box.invalid:11434")
        out = subprocess.run([sys.executable, "-c", "import config as c; print(c.ROSTER_SOURCE, c.BRAIN_HOST, "
                              "c.FAILOVER_HOST, c.HEAVY_HOST, repr(c.device_mac(c.LEAD_BOX)), "
                              "repr(sorted(c.MCCOY_RESIDENT_HOSTS)))"],
                             capture_output=True, text=True, env=env, timeout=60,
                             cwd=os.path.dirname(config.__file__) or ".")
        # the last field: on one card McCoy is resident nowhere (he and the person's own model would keep reloading)
        self.assertEqual(out.stdout.split(), ["None"] + ["http://only-box.invalid:11434"] * 3 + ["''", "[]"],
                         out.stderr[-400:])

    def test_a_lead_with_no_wake_address_is_reported_at_once(self):
        import app
        with mock.patch.object(app.ollama_client, "reachable_direct", return_value=False), \
                mock.patch.object(app.ollama_client, "port_open", return_value=False), \
                mock.patch.dict(os.environ, {"PC1_MAC": ""}), mock.patch("wol.wake") as woke, \
                mock.patch.object(app.time, "sleep", side_effect=AssertionError("waited")):
            self.assertFalse(app._ensure_lead())
        woke.assert_not_called()
        self.assertIn("no wake address", app.LEAD_UNAVAILABLE["why"])

    def test_a_head_with_nothing_wakeable_fails_the_ladder_without_waiting(self):
        import app
        with mock.patch.object(app.ollama_client, "reachable", return_value=False), \
                mock.patch.object(app.ollama_client, "reachable_direct", return_value=False), \
                mock.patch.object(app.ollama_client, "port_open", return_value=False), \
                mock.patch.dict(os.environ, {"PC1_MAC": "", "PC2_MAC": ""}), \
                mock.patch.object(app, "failover_available", return_value=(False, "test: down")), \
                mock.patch("wol.wake") as woke, \
                mock.patch.object(app.time, "sleep", side_effect=AssertionError("waited for a wake never sent")):
            got = app.pick_brain()
        self.assertIsNone(got[0])
        woke.assert_not_called()

    def test_no_ssh_reach_asks_nothing(self):
        import lead_ssh
        with mock.patch.object(config, "LEAD_BOX_SSH_HOST", ""), \
                mock.patch.object(lead_ssh.subprocess, "run", side_effect=AssertionError("ran ssh")), \
                mock.patch.object(lead_ssh, "_reported_free_mb", return_value=None):
            self.assertIsNone(lead_ssh.gpu_free_mb())
            self.assertFalse(lead_ssh.restart_ollama())
            self.assertFalse(lead_ssh.start_comfy())
            self.assertFalse(lead_ssh._lead_python("print(1)")[0])

    def test_the_measured_context_keys(self):
        self.assertEqual(config.roster_box_at("HEAVY_HOST"), "mothership")
        self.assertEqual(config.roster_box_at("BRAIN_HOST"), "bighonker")
        self.assertEqual(config.roster_box_at("FAILOVER_HOST"), "softserve")
        self.assertIsNone(config.roster_box_at("NO_SUCH_HOST"))


class SamePlacementsOnOldAndNewTables(unittest.TestCase):
    """The same requests and the same box states through the literal tables and through the roster's: same answers."""
    KINDS = ("chat", "embed", "advisor", "caption", "vision", "compute")

    def setUp(self):
        self.p = [mock.patch.object(scheduler, "gpu_state", side_effect=self._gpu),
                  mock.patch.object(scheduler, "ram_free_gb", side_effect=lambda h: {"mothership": 20.0,
                                    "bighonker": 6.0, "softserve": 9.0, "sundae": 5.0}.get(h, 4.0)),
                  mock.patch.object(scheduler, "cpu_load_pct", side_effect=lambda h: {"mothership": 30.0,
                                    "softserve": 45.0}.get(h, 10.0)),
                  mock.patch.object(scheduler, "training_busy", return_value=(False, "")),
                  mock.patch("app.failover_available", return_value=(True, "idle")),
                  mock.patch.object(scheduler, "record_placement"),
                  mock.patch.object(config, "POLICY_TABLE", os.path.join(tempfile.mkdtemp(), "none.json"))]
        for x in self.p:
            x.start()
        scheduler._POLICY_CACHE.update(key=None, data=None)
        self.busy = set()

    def tearDown(self):
        for x in self.p:
            x.stop()
        scheduler._POLICY_CACHE.update(key=None, data=None)

    def _gpu(self, host, *a, **kw):
        return (host in self.busy, "busy" if host in self.busy else "free")

    def answers(self):
        out = []
        for busy in ((), ("bighonker",), ("mothership",), ("bighonker", "mothership")):
            self.busy = set(busy)
            for kind in self.KINDS:
                if kind not in scheduler.KINDS:
                    continue
                for kw in ({}, {"cpu_heavy": True}, {"exclude": ("softserve",)}):
                    try:
                        out.append((busy, kind, kw, scheduler.place(kind, **kw)))
                    except Exception as exc:                                  # noqa: BLE001 - compared, not hidden
                        out.append((busy, kind, kw, "raised %s" % type(exc).__name__))
        rows = [{"host": h} for h in ("softserve", "mothership", "bighonker", "rigby")]
        out.append(("last_resort", scheduler.chat_last_resort_last(list(rows))))
        out.append(("chunk", scheduler.chunk(list(range(40)), ["mothership", "bighonker", "softserve"])))
        return out

    def test_place_and_its_helpers_answer_the_same(self):
        new = self.answers()
        old_tables = literal_tables()
        with mock.patch.multiple(scheduler, **old_tables), \
                mock.patch.dict(scheduler.KINDS["advisor"], {"home": old_tables["ADVISOR_HOME"]}):
            old = self.answers()
        self.assertGreater(len([a for a in old if len(a) == 4 and not isinstance(a[3], str)]), 20,
                           "the comparison must cover real placements, not only refusals")
        self.assertEqual(new, old)


class LoaderRules(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, data):
        path = os.path.join(self.dir, "roster.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_no_file_and_none_named_is_one_device_holding_every_role(self):
        with mock.patch.dict(os.environ, {"HAI_ROSTER_FILE": ""}), \
                mock.patch.object(scheduler, "ROSTER_FILE", os.path.join(self.dir, "absent.json")):
            roster, source = scheduler.load_roster()
        self.assertIsNone(source)
        optional = config._OPTIONAL_ROLES
        self.assertEqual(set(optional), {"tolkien", "acquisition"})
        self.assertEqual({scheduler.roster_role(r, roster) for r in scheduler.ROSTER_ROLES if r not in optional},
                         {"local"})
        for r in optional:
            self.assertIsNone(scheduler.roster_role(r, roster), "no %s device on a one-device head" % r)
        self.assertEqual(scheduler.roster_cards(roster), {"local": {
            "url": config.BRAIN_HOST, "vram_gb": 0.0, "ram_gb": 0.0, "cpu": os.cpu_count() or 1,
            "label": "This machine", "gpu": None}})
        self.assertEqual(scheduler.roster_table("chat_tier", roster), {"local": 0})
        self.assertEqual(scheduler.roster_tablets(roster), {})

    def test_a_named_file_that_is_missing_stops_the_import(self):
        with self.assertRaisesRegex(config.RosterError, "does not exist"):
            scheduler.load_roster(os.path.join(self.dir, "absent.json"))
        with mock.patch.dict(os.environ, {"HAI_ROSTER_FILE": os.path.join(self.dir, "absent.json")}), \
                mock.patch.object(scheduler, "ROSTER_FILE", os.path.join(self.dir, "absent.json")):
            with self.assertRaisesRegex(config.RosterError, "does not exist"):
                scheduler.load_roster()

    def test_an_unreadable_file_is_never_read_as_one_device(self):
        with self.assertRaisesRegex(config.RosterError, "cannot be read"):
            scheduler.load_roster(self.write("{not json"))

    def _dev(self, name, roles, **kw):
        return dict({"name": name, "label": name, "roles": roles, "url": "http://x:11434", "gpu": None,
                     "vram_gb": 8.0, "ram_gb": 16.0, "cpu": 8}, **kw)

    def test_faults_are_named(self):
        cases = [({"devices": []}, "non-empty"),
                 ({"devices": [self._dev("a", ["brain"]), self._dev("a", [])]}, "repeated"),
                 ({"devices": [self._dev("A", ["brain"])]}, "lowercase"),
                 ({"devices": [self._dev("a", ["lead"])]}, "brain"),
                 ({"devices": [self._dev("a", ["brain"]), self._dev("b", ["brain"])]}, "held by both"),
                 ({"devices": [self._dev("a", ["brain", "chef"])]}, "unknown role"),
                 ({"devices": [self._dev("a", ["brain"], url_setting="NO_SUCH_SETTING")]}, "not a setting"),
                 ({"devices": [self._dev("a", ["brain"], flags={"parent": "zz"})]}, "parent"),
                 ({"devices": [{"name": "a", "roles": ["brain"]}]}, "lacks")]
        for data, want in cases:
            with self.assertRaisesRegex(config.RosterError, want, msg=want):
                scheduler.load_roster(self.write(data))

    def test_a_tolkien_box_needs_only_a_label_and_is_never_a_card(self):
        roster, _ = scheduler.load_roster(self.write({"devices": [self._dev("small", ["brain"]),
                                                                  {"name": "node", "label": "Node",
                                                                   "roles": ["tolkien"]}]}))
        self.assertEqual(scheduler.roster_role("tolkien", roster), "node")
        self.assertEqual(list(scheduler.roster_cards(roster)), ["small"])
        self.assertEqual(scheduler.roster_labels(roster), {"small": "small", "node": "Node"})
        with self.assertRaisesRegex(config.RosterError, "lacks label"):
            scheduler.load_roster(self.write({"devices": [self._dev("small", ["brain"]),
                                                          {"name": "node", "roles": ["tolkien"]}]}))

    def test_an_unclaimed_role_falls_to_the_brain_and_a_left_over_lead_role_is_ignored(self):
        roster, _ = scheduler.load_roster(self.write({"devices": [self._dev("big", ["lead"]),
                                                                  self._dev("small", ["brain"])]}))
        self.assertEqual(roster["devices"][0]["roles"], [], "the lead is a seat, not a device role")
        for role in ("brain", "failover", "head", "advisor"):
            self.assertEqual(scheduler.roster_role(role, roster), "small", role)

    def test_the_measured_windows_come_from_the_roster_exactly_as_the_code_held_them(self):
        roster, _ = scheduler.load_roster(LAB_ROSTER)
        held = {   # the in-code table before it moved to the roster's `model_contexts`
            "mothership": {"qwen3.6:27b": 151552, "qwen3.8:27b": 125952, "qwen3.6:35b-a3b": 84992,
                           "qwen3-coder:30b": 107520, "medgemma:27b": 130048, "qwen3:14b": 40960,
                           "qwen3-vl:8b": 221184, "gemma3:27b": 123904, "nemotron-3-nano:30b-iq4xs": 1048576,
                           "ornith-1.5:35b-a3b-q4km": 223232, "qwen3.8-flash-next-strata-q2_0": 262144},
            "bighonker": {"qwen3:8b": 23552, "qwen3-vl:8b": 17408, "nemotron-3-nano:4b": 262144,
                          "minicpm-v:4.6-q4km": 262144, "minicpm5:1b-q4km": 131072,
                          "qwen3.6:35b-a3b-ud-q3kxl-mtp": 65536},
            "softserve": {"qwen3.5:9b": 47104, "qwen3-vl:8b": 17408, "qwen3:8b": 23552, "nemotron-3-nano:4b": 262144,
                          "minicpm-v:4.6-q4km": 262144, "minicpm5:1b-q4km": 131072}}
        got = {d["name"]: d.get("model_contexts") for d in roster["devices"] if d["name"] in held}
        self.assertEqual(json.dumps(got, sort_keys=False), json.dumps(held, sort_keys=False), "same values, same order")

    def _crown(self, box):
        return self.write({"roles": {"crown": {"box": box, "model": "m"}}})

    def test_the_lead_box_is_the_crowns_machine_else_the_biggest_card(self):
        roster, _ = scheduler.load_roster(self.write({"devices": [self._dev("small", ["brain"]),
                                                                  self._dev("big", [], vram_gb=24.0)]}))
        self.assertEqual(config.lead_box(roster, self._crown("small")), "small", "the crown's machine")
        self.assertEqual(config.lead_box(roster, self._crown("elsewhere")), "big",
                         "a crown on a machine this head lacks: the biggest card")
        self.assertEqual(config.lead_box(roster, os.path.join(self.dir, "no-table.json")), "big",
                         "no menu yet: the biggest card, never unknown")
        one, _ = config.load_roster(os.path.join(self.dir, "absent.json"), named=False)
        self.assertEqual(config.lead_box(one, os.path.join(self.dir, "no-table.json")), "local")

    def test_machines_are_numbered_pcs_and_a_vm_sits_under_its_pc(self):
        data = {"devices": [self._dev("a", ["brain"]), self._dev("b", []),
                            self._dev("v", ["head"], flags={"parent": "b"}), self._dev("c", [], pc=7)]}
        config.number_devices(config.validate_roster(data), environ={})
        self.assertEqual([d["prefix"] for d in data["devices"]], ["PC1", "PC2", "PC2_VM1", "PC7"])
        with mock.patch.dict(os.environ, {"PC2_VM1_GLANCES": "http://g:1", "PC1_MAC": "m1"}):
            self.assertEqual(config.device_setting("v", "GLANCES", roster=data), "http://g:1")
            self.assertEqual(config.device_mac("a", roster=data), "m1")
            self.assertEqual(config.device_setting("nobody", "MAC", "none", roster=data), "none")

    def test_a_name_setting_renames_the_device_and_its_vms_follow(self):
        data = {"devices": [self._dev("a", ["brain"]), self._dev("v", ["head"], flags={"parent": "a"})]}
        config.number_devices(config.validate_roster(data), environ={"PC1_NAME": "Gaming-Rig"})
        self.assertEqual([(d["name"], d["label"]) for d in data["devices"]], [("gaming-rig", "Gaming-Rig"), ("v", "v")])
        self.assertEqual(data["devices"][1]["flags"]["parent"], "gaming-rig")
        with self.assertRaisesRegex(config.RosterError, "same name"):
            config.number_devices(config.validate_roster({"devices": [self._dev("a", ["brain"]), self._dev("b", [])]}),
                                  environ={"PC1_NAME": "b"})


if __name__ == "__main__":
    unittest.main()
