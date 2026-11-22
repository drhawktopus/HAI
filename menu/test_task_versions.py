"""A versioned task asks the same question under a new id (task_versions): against the payload generation that ran the
original, the new task differs ONLY in the named text, its checks and answer key are the same, the original file still
defines the original id (its results stay graded by it) and not the new one, the menu voids both ids alike, and the
filler counts a cell measured on the original as measured for the new id."""
import importlib.util
import json
import os
import re
import unittest

import menu_paths as MP
import task_versions as TV

import lab_settings as LS  # noqa: E402  (menu_paths put the Lab tools on the path)

PAYLOADS = MP.PAYLOADS
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
LAN = LS.setting("LAN_PREFIX")          # the lab's own address prefix: what a version takes out of the text


def _masked(task):
    """A task with every IPv4 address masked: a version that only replaced an address is equal to its original."""
    return IPV4.sub("<address>", json.dumps(task, sort_keys=True))


def _load(name):
    spec = importlib.util.spec_from_file_location(name[:-3], os.path.join(PAYLOADS, name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tasks(mod):
    """A payload's task table; a payload with none (a probe, the gauntlet) has no task to version."""
    return getattr(mod, "TASKS", {})


class TheVersionIsTheSameQuestion(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not PAYLOADS:
            raise unittest.SkipTest("no tablet payloads here: set TABLET_JOBS_HOST, or keep tablet-payloads beside the menu")
        names = {n for pair in TV.PAYLOAD_GENERATIONS.items() for n in pair}
        absent = sorted(n for n in names if not os.path.isfile(os.path.join(PAYLOADS, n)))
        if absent:
            raise unittest.SkipTest("%d payload generations are not here (the originals stay with their results in the "
                                    "lab that ran them; a public tree carries the newest)" % len(absent))
        cls.mods = {n: _load(n) for n in names}

    def test_against_the_payload_that_ran_the_original_only_the_address_changed(self):
        # A generation either versions a task its parent defines (old id there, new id here) or carries the parent's
        # tasks unchanged (a generation that changed only comments or code around the tasks).
        compared = set()
        for old_pay, new_pay in TV.PAYLOAD_GENERATIONS.items():
            old, new = _tasks(self.mods[old_pay]), _tasks(self.mods[new_pay])
            for new_id, old_id in TV.SUPERSEDES.items():
                if new_id not in new or old_id not in old:
                    continue
                compared.add(new_id)
                a, b = old[old_id], new[new_id]
                self.assertNotEqual(a, b, "%s changed nothing" % new_id)
                self.assertEqual(_masked(a), _masked(b), "%s changed more than an address" % new_id)
                if LAN:
                    self.assertIn(LAN, json.dumps(a), "the original %s named the lab's address" % old_id)
                    self.assertNotIn(LAN, json.dumps(b), new_id)
            same = set(old) - set(TV.SUPERSEDES.values())
            self.assertEqual({t: old[t] for t in same}, {t: new[t] for t in same},
                             "every other task of %s is unchanged in %s" % (old_pay, new_pay))
        import advisor_tasks as AT
        advisor = set(AT.TASKS) | set(AT.TASKS_V2)
        in_payloads = {n for pair in TV.PAYLOAD_GENERATIONS.items() for n in _tasks(self.mods[pair[1]])}
        for new_id in TV.SUPERSEDES:
            self.assertTrue(new_id in in_payloads or new_id in advisor, "%s is defined nowhere" % new_id)
        self.assertEqual(compared, {n for n in TV.SUPERSEDES if n in in_payloads},
                         "every payload version was compared with its original")

    def test_an_advisor_version_keeps_its_labels_and_its_original_id_grades(self):
        import advisor_tasks as AT
        table = AT.by_id(AT.TASKS)
        for new_id, old_id in TV.SUPERSEDES.items():
            if new_id in AT.TASKS:
                self.assertNotIn(old_id, AT.TASKS, "the original is not submitted again")
                self.assertIs(table[old_id], table[new_id])
        self.assertEqual(len(AT.TASKS), 48)

    def test_each_generation_defines_its_own_ids_only(self):
        for old_pay, new_pay in TV.PAYLOAD_GENERATIONS.items():
            for new_id, old_id in TV.SUPERSEDES.items():
                if old_id in _tasks(self.mods[old_pay]):
                    self.assertNotIn(new_id, _tasks(self.mods[old_pay]))
                    self.assertIn(new_id, _tasks(self.mods[new_pay]))
                    self.assertNotIn(old_id, _tasks(self.mods[new_pay]))

    def test_no_lab_address_in_any_current_task(self):
        if not LAN:
            self.skipTest("LAN_PREFIX is not set: no lab address to look for")
        import hard_tasks
        import hard_tasks_c1
        import hard_tasks_f2
        import hard_tasks_w1
        import hard_tasks_w1ops
        n = 0
        for mod in (hard_tasks, hard_tasks_c1, hard_tasks_f2, hard_tasks_w1, hard_tasks_w1ops):
            for tid, t in mod.TASKS.items():
                n += 1
                self.assertNotIn(LAN, json.dumps(t), "%s.%s" % (mod.__name__, tid))
        self.assertGreater(n, 50, "scanned only %d tasks" % n)

    def test_the_letter_shuffled_copy_keeps_its_letters(self):
        import hard_tasks_w1ops
        self.assertEqual(hard_tasks_w1ops.TARGET["ops_macvlan_host_v2"], ("A", "E"))
        self.assertIn("ops_macvlan_host_v2_sh", hard_tasks_w1ops.TASKS)


class TheHelpers(unittest.TestCase):

    def test_ids(self):
        self.assertEqual(TV.current("ops_macvlan_host"), "ops_macvlan_host_v2")
        self.assertEqual(TV.current("ops_macvlan_host_sh"), "ops_macvlan_host_v2_sh")
        self.assertEqual(TV.current("code_ttl_lru"), "code_ttl_lru")
        self.assertEqual(TV.predecessors("ops_macvlan_host_v2"), ["ops_macvlan_host"])
        self.assertEqual(TV.predecessors("code_ttl_lru"), [])

    def test_the_calibrated_coding_band_keeps_its_place_under_the_current_id(self):
        import hard_bench_matrix as mx
        self.assertIn("valid_ipv4_v2", mx.IN_BAND)
        self.assertEqual(len(mx.IN_BAND), 8)

    def test_payloads(self):
        self.assertEqual(TV.newest_payload("map_swarm_code_hard_w1.py"), "map_swarm_code_hard_w1_v3.py")
        self.assertEqual(TV.newest_payload("map_swarm_code_hard.py"), "map_swarm_code_hard_v3.py")
        self.assertEqual(TV.newest_payload("/ssdshared/tabfoundry-jobs/payloads/map_swarm_code_hard_w1.py"),
                         "/ssdshared/tabfoundry-jobs/payloads/map_swarm_code_hard_w1_v3.py")
        self.assertEqual(TV.newest_payload("map_swarm_code_hard_f2.py"), "map_swarm_code_hard_f2_v2.py")
        self.assertEqual(TV.newest_payload("not_a_versioned_payload.py"), "not_a_versioned_payload.py")
        self.assertEqual(TV.generations("map_swarm_code_hard_w1ops.py"),
                         ["map_swarm_code_hard_w1ops.py", "map_swarm_code_hard_w1ops_v2.py",
                          "map_swarm_code_hard_w1ops_v3.py"])


class TheMenuKeepsThemApart(unittest.TestCase):

    def test_the_letter_biased_void_covers_the_original_id_and_its_version(self):
        import build_menu as B
        rows = [{"family": "work-w1", "task": t} for t in
                ("ops_macvlan_host", "ops_macvlan_host_v2", "ops_macvlan_host_v2_sh", "ops_macvlan_host_sh")]
        self.assertEqual(B.supersede_letter_biased_ops(rows), 2)
        self.assertEqual([r.get("usable") for r in rows], ["False", "False", None, None])

    def test_each_row_is_graded_by_the_generation_that_defines_its_id(self):
        """Counting mode: no grading, no cache write; the planned job names the payload that would grade the row."""
        for name in TV.generations("map_swarm_code_hard_w1.py"):
            if not (PAYLOADS and os.path.isfile(os.path.join(PAYLOADS, name))):
                self.skipTest("%s is not here (the originals stay with their results in the lab that ran them)" % name)
        import build_menu as B
        G = B.G
        saved = (G.counting, list(G.jobs), set(G.planned_keys), G.planned, G.planned_new)
        G.counting, G.jobs, G.planned_keys = True, [], set()
        try:
            self.assertIsNotNone(G.grade("map_swarm_code_hard_w1.py", "ops_macvlan_host", "no answer here 1"))
            self.assertIsNotNone(G.grade("map_swarm_code_hard_w1.py", "ops_macvlan_host_v2", "no answer here 2"))
            self.assertIsNone(G.grade("map_swarm_code_hard_w1.py", "no_such_task", "x"))
            self.assertEqual([j["payload"] for j in G.jobs],
                             ["map_swarm_code_hard_w1.py", "map_swarm_code_hard_w1_v2.py"])
        finally:
            G.counting, G.jobs, G.planned_keys, G.planned, G.planned_new = saved[0], saved[1], saved[2], saved[3], saved[4]


class TheFillerSeesOneQuestion(unittest.TestCase):

    def test_a_cell_measured_on_the_original_has_nothing_left_to_run(self):
        import menu_fill as M
        cell = ("rigby", "some-model.gguf", "w1", "off", "0.6", "1")
        want = M.rigby_pairs(cell)
        self.assertIn(("ops_macvlan_host_v2", 1), want)
        self.assertNotIn(("ops_macvlan_host", 1), want)
        import hard_tasks_w1
        before = set(hard_tasks_w1.TASKS) - {"ops_macvlan_host_v2"} | {"ops_macvlan_host"}
        measured = {(TV.current(t), r) for t in before for r in (1, 2, 3)}
        self.assertEqual(want - measured, set(), "no re-run is ordered for the version")


if __name__ == "__main__":
    unittest.main()
