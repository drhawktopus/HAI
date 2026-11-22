"""Tests for update_notify diff logic. Fixtures must be static copies from test-fixtures/update-inventory; never read the live update-inventory folder."""
import copy
import json
import os
import re
import tempfile
import unittest

import update_notify as N

HERE = os.path.dirname(os.path.abspath(__file__))


FIXTURES = os.path.join(HERE, "test-fixtures", "update-inventory")
STAY_HOME = "the captured machine inventories are run output and stay with the lab; these tests run there"


def reading(box):
    if not os.path.isdir(FIXTURES):
        raise unittest.SkipTest(STAY_HOME)
    return json.load(open(os.path.join(FIXTURES, "%s.json" % box), encoding="utf-8"))


class Diff(unittest.TestCase):
    def test_an_unchanged_box_is_quiet(self):
        for box in ("mothership", "softserve"):
            r = reading(box)
            self.assertEqual(N.diff_windows(r, copy.deepcopy(r)), ([], []), box)
        r = reading("rigby")
        self.assertEqual(N.diff_linux(r, copy.deepcopy(r)), ([], []))

    def test_a_driver_program_or_build_change_is_installed(self):
        prev = reading("mothership")
        cur = copy.deepcopy(prev)
        gpu = next(d for d in cur["drivers"] if d["class"] == "DISPLAY" and "7900" in d["device"])
        gpu["version"] = "32.0.99999.1"
        prog = next(p for p in cur["programs"] if p["name"].startswith("Git"))
        prog["version"] = "9.9.9"
        cur["os"]["ubr"] = 9999
        installed, waiting = N.diff_windows(prev, cur)
        self.assertTrue(any("driver AMD Radeon RX 7900 XTX" in x and "32.0.99999.1" in x for x in installed), installed)
        self.assertTrue(any(x.startswith("Git") and "9.9.9" in x for x in installed), installed)
        self.assertTrue(any(x.startswith("Windows ") and "9999" in x for x in installed), installed)
        self.assertEqual(waiting, [])

    def test_an_engine_package_change_is_installed(self):
        prev = reading("mothership")
        cur = copy.deepcopy(prev)
        cur["engines"][0]["packages"]["torch"] = "2.13.0+rocm7.16"
        installed, _w = N.diff_windows(prev, cur)
        self.assertTrue(any(x.startswith("ENGINE ") and "torch" in x and "2.13.0" in x for x in installed), installed)

    def test_an_updater_task_switched_back_on_is_installed_news(self):
        prev = reading("mothership")
        for t in prev["update_tasks"]:
            if t["name"] == "AMDAutoUpdate":
                t["state"] = "Disabled"
        cur = reading("mothership")          # the fixture: AMDAutoUpdate Ready
        installed, _w = N.diff_windows(prev, cur)
        self.assertIn("updater task AMDAutoUpdate: Disabled -> Ready", installed)

    def test_defender_signatures_are_not_news_but_a_new_driver_offer_is(self):
        prev = reading("bighonker")
        cur = copy.deepcopy(prev)
        cur["wu_pending"].append({"title": "Security Intelligence Update for Microsoft Defender Antivirus - KB2267602 (Version 9)"})
        cur["wu_pending"].append({"title": "NVIDIA - Display - 32.0.16.9999"})
        _i, waiting = N.diff_windows(prev, cur)
        self.assertEqual(waiting, ["Windows Update: NVIDIA - Display - 32.0.16.9999"])

    def test_new_apt_packages_and_a_kernel_change(self):
        prev = reading("rigby")
        cur = copy.deepcopy(prev)
        cur["upgradable"].append("zzz-newpkg/noble-updates 1.2 amd64 [upgradable from: 1.1]")
        cur["kernel"] = "6.8.0-99-generic"
        installed, waiting = N.diff_linux(prev, cur)
        self.assertIn("kernel 6.8.0-31-generic -> 6.8.0-99-generic", installed)
        self.assertEqual(waiting, ["apt: 1 new (zzz-newpkg)"])


class Winget(unittest.TestCase):
    def test_the_real_mothership_table_parses_every_row(self):
        text = reading("mothership")["winget_upgrade"]["text"]
        said = int(re.search(r"(\d+) upgrades? available", text).group(1))
        t = N.winget_table(text)
        self.assertEqual(len(t), said, "exactly as many rows as winget itself counted")
        self.assertFalse([k for k in t if set(k) <= {"-"}], "the separator line is not a package")
        self.assertEqual(t["Ollama.Ollama"], ("0.32.13", "0.40.14"))
        self.assertEqual(t["tesseract-ocr.tesseract"], ("< 5.5.3", "5.5.3"))

    def test_a_broken_source_is_an_empty_table_not_an_error(self):
        self.assertEqual(N.winget_table(reading("bighonker")["winget_upgrade"]["text"]), {})


class EnginePin(unittest.TestCase):
    def test_a_new_ollama_release_reports_its_llama_cpp_pin_against_the_lab_engine(self):
        self.assertIn("no rebuild needed", N.ollama_pin_line("v0.40.5", fetch=lambda t: "b11669"))
        line = N.ollama_pin_line("v0.40.14", fetch=lambda t: "b11963", state_dir=tempfile.mkdtemp())   # no pipeline state yet
        self.assertIn("pins llama.cpp b11963; the lab engine is on b11669", line)
        self.assertIn("engine pipeline rebuilds", line)
        # once the pipeline has moved the fleet, its state file is the pin - the constant is only the 10-22 fallback
        d = tempfile.mkdtemp()
        json.dump({"phase": "done", "live": {"short": "39febd2", "build": "b11963"}}, open(os.path.join(d, "engine.json"), "w"))
        self.assertIn("no rebuild needed", N.ollama_pin_line("v0.40.14", fetch=lambda t: "b11963", state_dir=d))
        self.assertIn("could not be read", N.ollama_pin_line("v9", fetch=lambda t: (_ for _ in ()).throw(OSError("x"))))


class Field(unittest.TestCase):
    def test_the_field_fits_discord_and_says_how_many_it_left_out(self):
        per_box = {"mothership": {"installed": ["x" * 90] * 30, "waiting": [], "counts": "WU 1, winget 38"}}
        text = N.field_text("STAMP", per_box, [])
        self.assertLessEqual(len(text), 1024)
        self.assertIn("more in State/lab-updates/report-STAMP.json", text)
        self.assertTrue(text.endswith("Waiting: mothership WU 1, winget 38"), "the totals line survives a cut")

    def test_the_labs_own_tasks_are_not_vendor_news(self):
        prev = reading("softserve")
        cur = copy.deepcopy(prev)
        cur["update_tasks"].append({"name": "HAI Lab Update Notify", "path": "\\", "state": "Running"})
        self.assertEqual(N.diff_windows(prev, cur), ([], []))

    def test_could_not_ask_is_said_and_quiet_is_said(self):
        text = N.field_text("S", {"mordecai": {"could_not_ask": "timed out"},
                                  "rigby": {"installed": [], "waiting": [], "counts": "apt 55"}}, [])
        self.assertIn("mordecai: could not ask", text)
        self.assertIn("Nothing new since the last reading.", text)
        self.assertIn("rigby apt 55", text)


if __name__ == "__main__":
    unittest.main()
