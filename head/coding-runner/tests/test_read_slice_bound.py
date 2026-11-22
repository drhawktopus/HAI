"""
Constraints for this test module:
1. Read size limits apply only to the slice being read, never the whole file.
2. There is NO maximum edit size; large files patch like any other.
   (This policy was explicitly removed and must not regress.)
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from runner import CodingRunner, PolicyError, ConflictError


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.ws = self.root / "ws"
        (self.ws / ".git").mkdir(parents=True)
        registry = self.root / "registry.json"
        registry.write_text(json.dumps({
            "workspaces": [{"id": "ws", "path": str(self.ws), "write": True}]
        }), encoding="utf-8")
        self.runner = CodingRunner(registry, self.root / "state")

        # A file comfortably OVER both bounds: 20k lines x ~40 bytes = ~800 KB, like app.py.
        self.big = self.ws / "big.py"
        self.big.write_text("".join(f"line {i:05d} " + "x" * 30 + "\n" for i in range(20_000)),
                            encoding="utf-8")
        self.big_size = self.big.stat().st_size
        self.assertGreater(self.big_size, self.runner.max_read_slice_bytes,
                           "instrument dead: the fixture file is not actually over the bound")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class ReadSliceBound(_Base):
    def test_narrow_range_of_an_OVER_LIMIT_file_now_succeeds(self):
        """THE BUG. This is HAI's exact blocked call shape."""
        out = self.runner.file_read("ws", "big.py", start_line=518, end_line=540)
        self.assertEqual(out["start_line"], 518)
        self.assertEqual(out["end_line"], 540)
        self.assertIn("line 00517", out["content"])         # 1-indexed -> line 518 is index 517
        self.assertEqual(len(out["content"].splitlines()), 23)

    def test_the_slice_read_returns_the_WHOLE_FILE_sha(self):
        """`apply_patch` needs this sha. Its presence is exactly why the edit refusal below matters."""
        import hashlib
        out = self.runner.file_read("ws", "big.py", start_line=1, end_line=5)
        self.assertEqual(out["sha256"], hashlib.sha256(self.big.read_bytes()).hexdigest())

    def test_an_OVER_BOUND_slice_is_refused_LOUDLY_with_a_usable_suggestion(self):
        """A named, actionable failure teaches the caller; an empty result teaches nothing. Measured
        this session: a 'No document matched ... your library: ...' message got HAI to the right
        filename on its very next call."""
        with self.assertRaises(PolicyError) as ctx:
            self.runner.file_read("ws", "big.py")            # whole file
        msg = str(ctx.exception)
        self.assertIn(str(self.runner.max_read_slice_bytes), msg, "the bound is not stated")
        self.assertIn("start_line=", msg, "no concrete retry is suggested")
        self.assertIn("end_line=", msg)
        self.assertRegex(msg, r"about \d+ lines fits")

    def test_the_suggested_range_ACTUALLY_FITS(self):
        """A suggestion that still fails is worse than none — it sends the caller round a loop."""
        with self.assertRaises(PolicyError) as ctx:
            self.runner.file_read("ws", "big.py")
        import re
        m = re.search(r"start_line=(\d+), end_line=(\d+)", str(ctx.exception))
        self.assertIsNotNone(m, "no machine-readable suggestion in the message")
        out = self.runner.file_read("ws", "big.py", start_line=int(m.group(1)),
                                    end_line=int(m.group(2)))
        self.assertTrue(out["content"])

    def test_small_files_are_unaffected(self):
        (self.ws / "small.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        self.assertIn("a = 1", self.runner.file_read("ws", "small.py")["content"])

    def test_binary_is_still_refused(self):
        (self.ws / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
        with self.assertRaises(PolicyError):
            self.runner.file_read("ws", "blob.bin")


class EditPolicyRemoved(_Base):
    """File size is not an edit gate. If these start failing on a PolicyError about size,
    someone has reintroduced the removed restriction."""

    def test_a_big_file_patches_like_any_other_with_a_valid_sha(self):
        """The exact call shape the old zero-case refused — now the ruling's positive pin."""
        got = self.runner.file_read("ws", "big.py", start_line=1, end_line=3)
        sha = got["sha256"]                                   # whole-file sha from a slice read
        out = self.runner.apply_patch("ws", "big.py", old="line 00000",
                                      new="line 00000 EDITED", expected_sha256=sha)
        self.assertEqual(out["replacements"], 1)
        self.assertIn("line 00000 EDITED", self.big.read_text(encoding="utf-8"))

    def test_the_sha_anchor_still_refuses_a_stale_copy_at_any_size(self):
        """What ACTUALLY protects a big-file edit — the anchor, not a size proxy."""
        with self.assertRaises(ConflictError):
            self.runner.apply_patch("ws", "big.py", old="line 00001",
                                    new="line 00001 X", expected_sha256="0" * 64)

    def test_patching_a_SMALL_file_still_works(self):
        p = self.ws / "small.py"
        p.write_text("a = 1\n", encoding="utf-8")
        sha = self.runner.file_read("ws", "small.py")["sha256"]
        out = self.runner.apply_patch("ws", "small.py", old="a = 1", new="a = 2",
                                      expected_sha256=sha)
        self.assertEqual(out["replacements"], 1)
        self.assertIn("a = 2", p.read_text(encoding="utf-8"))

    def test_creating_a_large_file_is_allowed(self):
        out = self.runner.file_create("ws", "new.py", content="x = 1\n" * 60_000)  # ~360 KB
        self.assertEqual(len(out["sha256"]), 64)

    def test_creating_a_file_in_a_new_directory_works(self):
        """file_create must create missing parent directories inside the workspace - a nested
        path is not a refusal case."""
        out = self.runner.file_create("ws", "newdir/sub/mod.py", content="a = 1\n")
        self.assertEqual(out["path"], "newdir/sub/mod.py")
        self.assertTrue((self.ws / "newdir" / "sub" / "mod.py").is_file())

    def test_new_directory_creation_cannot_escape_the_workspace(self):
        """The mkdir must inherit _resolve's confinement, not widen it."""
        with self.assertRaises(Exception):
            self.runner.file_create("ws", "../outside/evil.py", content="x\n")
        self.assertFalse((self.root / "outside").exists())

    def test_binary_is_still_refused_as_a_category_not_a_size(self):
        (self.ws / "blob2.bin").write_bytes(b"\x00\x01" * 200)
        import hashlib
        sha = hashlib.sha256((self.ws / "blob2.bin").read_bytes()).hexdigest()
        with self.assertRaises(PolicyError):
            self.runner.apply_patch("ws", "blob2.bin", old="x", new="y", expected_sha256=sha)


class BoundsAreSeparate(unittest.TestCase):
    """Requirement: one constant may not keep both jobs — tuning one must not move the other."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        ws = self.root / "ws"
        (ws / ".git").mkdir(parents=True)
        reg = self.root / "registry.json"
        reg.write_text(json.dumps({"workspaces": [{"id": "ws", "path": str(ws), "write": True}]}),
                       encoding="utf-8")
        self.reg, self.ws = reg, ws

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_read_slice_bound_is_settable(self):
        r = CodingRunner(self.reg, self.root / "s1", max_read_slice_bytes=1_000)
        self.assertEqual(r.max_read_slice_bytes, 1_000)

    def test_the_old_conflated_constant_is_GONE(self):
        """If `max_read_bytes` comes back, someone has re-merged two policies into one number."""
        r = CodingRunner(self.reg, self.root / "s2")
        self.assertFalse(hasattr(r, "max_read_bytes"))

    def test_the_removed_edit_bound_is_GONE(self):
        """The ruling's structural half: the attribute itself no longer exists, so no code path
        can quietly consult it."""
        r = CodingRunner(self.reg, self.root / "s3")
        self.assertFalse(hasattr(r, "max_edit_file_bytes"))


if __name__ == "__main__":
    unittest.main()
