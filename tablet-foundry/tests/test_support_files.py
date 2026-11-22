"""support_files: extra files staged beside a map payload on the node (the GPU factories' payloads import siblings from
their bin; a tablet payload gets the same through this field)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tablet_foundry
import tablet_fulfil


def _spec(**overrides):
    spec = {"kind": "map", "name": "support-test", "payload": "/x/grade_jobs.py", "batch": 2,
            "items": ["a", "b"], "control": {"item": "ctl", "expect": "ok"}}   # a control that repeats an item is refused
    spec.update(overrides)
    return spec


class SupportFilesSpec(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f1 = os.path.join(self.d, "scorer_a.py")
        self.f2 = os.path.join(self.d, "scorer_b.py")
        for p in (self.f1, self.f2):
            open(p, "w").close()

    def test_existing_support_files_are_accepted(self):
        self.assertEqual(tablet_foundry.spec_defects(_spec(support_files=[self.f1, self.f2])), [])

    def test_a_missing_support_file_is_refused(self):
        defects = tablet_foundry.spec_defects(_spec(support_files=[self.f1, os.path.join(self.d, "gone.py")]))
        self.assertTrue(any("not found" in x for x in defects), defects)

    def test_a_name_clash_with_the_payload_is_refused(self):
        clash = os.path.join(self.d, "grade_jobs.py")
        open(clash, "w").close()
        defects = tablet_foundry.spec_defects(_spec(support_files=[clash]))
        self.assertTrue(any("distinct file names" in x for x in defects), defects)

    def test_not_a_list_is_refused(self):
        self.assertTrue(tablet_foundry.spec_defects(_spec(support_files=self.f1)))


class SupportFilesStaged(unittest.TestCase):
    def test_each_support_file_is_pushed_beside_the_payload(self):
        pushed = []

        def scp(host, src, dst, timeout=None):
            pushed.append((os.path.basename(src), dst))
            return 0, ""

        def ssh(host, cmd, timeout=None):
            if "items.txt out.json" in cmd:
                return 1, "stop here"
            return 0, ""

        spec = _spec(support_files=["/s/scorer_a.py", "/s/scorer_b.py"])
        with mock.patch.object(tablet_fulfil, "_scp", scp), mock.patch.object(tablet_fulfil, "_ssh", ssh), \
             mock.patch.object(tablet_fulfil, "log", lambda *a, **k: None):
            try:
                tablet_fulfil.run_order(spec, ["h1"])
            except Exception:
                pass
        names = [n for n, _ in pushed]
        self.assertIn("scorer_a.py", names)
        self.assertIn("scorer_b.py", names)
        payload_dst = next(d for n, d in pushed if n == "grade_jobs.py")
        for n, d in pushed:
            if n.startswith("scorer_"):
                self.assertEqual(os.path.dirname(d), os.path.dirname(payload_dst), "staged in the payload's folder")


if __name__ == "__main__":
    unittest.main()
