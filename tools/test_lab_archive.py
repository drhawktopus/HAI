"""lab_archive: one generation = one dir + manifest; files copied are hash-verified; retention per class; the listing."""
import io
import contextlib
import os
import tempfile
import unittest

import lab_archive as A


class Store(unittest.TestCase):
    def test_a_generation_holds_its_manifest_and_verified_files(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "pkg.exe")
            open(src, "wb").write(b"driver bytes")
            m = A.archive("nvidia-driver", "617.42", "vendor", "run install.ps1 pkg.exe", box="bighonker",
                          files=[src], stage="driver", store=os.path.join(d, "store"))
            self.assertTrue(os.path.exists(os.path.join(d, "store", "nvidia-driver", "%s-617.42" % m["stamp"], "manifest.json")))
            self.assertEqual(m["files"][0]["copied"], True)
            self.assertEqual(m["files"][0]["bytes"], 12)
            g = A.generations(store=os.path.join(d, "store"))
            self.assertEqual([x["version"] for x in g], ["617.42"])

    def test_retention_keeps_two_vendor_generations_and_every_lab_one(self):
        with tempfile.TemporaryDirectory() as d:
            st = os.path.join(d, "store")
            for v in ("1", "2", "3"):
                A.archive("plex", v, "vendor", "winget install --version " + v, store=st, stamp="2026110%s-000000" % v)
                A.archive("lab-engine", v, "lab", "fleet_deploy --engine-rollback", location="/x/%s" % v, store=st,
                          stamp="2026110%s-000000" % v)
            gone = A.prune(apply=False, store=st)
            self.assertEqual(len(gone), 1, "vendor keeps 2 of 3; lab keeps all")
            self.assertIn(os.path.join("plex", "20261101-000000-1"), gone[0])
            self.assertEqual(len(A.generations("lab-engine", store=st)), 3)
            A.prune(apply=True, store=st)
            self.assertEqual(len(A.generations("plex", store=st)), 2)

    def test_a_missing_file_is_said_not_raised(self):
        with tempfile.TemporaryDirectory() as d:
            m = A.archive("x", "1", "vendor", "cmd", files=[os.path.join(d, "nope")], store=os.path.join(d, "s"))
            self.assertEqual(m["files"][0]["copied"], False)
            self.assertIn("error", m["files"][0])

    def test_the_listing_names_the_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            st = os.path.join(d, "s")
            A.archive("app/Git.Git", "2.54.0", "vendor", "winget install --id Git.Git --version 2.54.0", store=st)
            buf = io.StringIO()
            A.STORE_BACKUP = A.STORE
            try:
                A.STORE = st
                with contextlib.redirect_stdout(buf):
                    A.main(["list"])
            finally:
                A.STORE = A.STORE_BACKUP
            self.assertIn("winget install --id Git.Git --version 2.54.0", buf.getvalue())
            self.assertIn("1 generation(s)", buf.getvalue(), "the listing read the overridden store, not the one bound at import")


if __name__ == "__main__":
    unittest.main()
