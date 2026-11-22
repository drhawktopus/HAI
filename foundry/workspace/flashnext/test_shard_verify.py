"""Shard verification. A recorded hash may stand in for a re-hash only on the daily serving path, and only while the
shard is provably the same file (same size, same mtime_ns, recorded hash equal to the hash the plan wants). Every
other case re-hashes in full, and a mismatch raises either way. The measurement path never trusts the record.

    python test_shard_verify.py
"""
import hashlib, inspect, json, os, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strata_arm as S  # noqa: E402


def digest(b):
    return hashlib.sha256(b).hexdigest()


class ShardVerify(unittest.TestCase):
    """Fake shards in a temp folder: no Strata, no engine, no GPU - only the verification."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="shard_verify_")
        self.gguf = os.path.join(self.d, "gguf")
        os.makedirs(self.gguf)
        self.want_a = digest(b"model bytes one\n")
        self.want_b = digest(b"second shard body\n")
        self.plan = {"gguf_dir": self.gguf, "shards": {"a.gguf": self.want_a, "b.gguf": self.want_b}}
        with open(os.path.join(self.gguf, "a.gguf"), "wb") as f:
            f.write(b"model bytes one\n")
        with open(os.path.join(self.gguf, "b.gguf"), "wb") as f:
            f.write(b"second shard body\n")
        self.lines = []
        self.hashes = []
        real = S.sha256

        def counting(p):
            self.hashes.append(p)
            return real(p)

        self.patch = mock.patch.object(S, "sha256", counting)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(shutil.rmtree, self.d, True)

    def _verify(self, daily=True):
        S.verify_shards(self.plan, allow_cached=daily, log=lambda m, flush=True: self.lines.append(m))
        return self.hashes

    def _record(self):
        with open(S._sidecar_path(self.gguf), encoding="utf-8") as f:
            return json.load(f)

    def test_the_first_daily_verify_hashes_every_shard_and_records_it(self):
        self.assertEqual(self._verify(), [os.path.join(self.gguf, "a.gguf"), os.path.join(self.gguf, "b.gguf")])
        self.assertTrue(all("OK" in line and "verified earlier" not in line for line in self.lines), self.lines)
        rec = self._record()
        st = os.stat(os.path.join(self.gguf, "a.gguf"))
        self.assertEqual(rec["a.gguf"], {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": self.want_a})
        self.assertEqual(set(rec), {"a.gguf", "b.gguf"})

    def test_the_second_daily_verify_trusts_the_record_and_hashes_nothing(self):
        self._verify()
        self.lines.clear()
        self.hashes.clear()
        self.assertEqual(self._verify(), [], "no shard was opened for hashing")
        self.assertTrue(all("verified earlier, unchanged" in line for line in self.lines), self.lines)
        self.assertEqual(len(self.lines), len(self.plan["shards"]), "one line per shard")

    def test_a_changed_mtime_re_hashes(self):
        self._verify()
        p = os.path.join(self.gguf, "a.gguf")
        os.utime(p, (1000000000, 1000000000))
        self.lines.clear()
        self.hashes.clear()
        self.assertEqual(self._verify(), [p], "the mtime moved, so it is hashed again")
        self.assertIn("verified earlier, unchanged", self.lines[1], "b is still trusted")
        self.assertEqual(self._record()["a.gguf"]["mtime_ns"], os.stat(p).st_mtime_ns)

    def test_a_changed_size_re_hashes_and_still_passes_when_the_plan_wants_the_new_bytes(self):
        self._verify()
        with open(os.path.join(self.gguf, "a.gguf"), "ab") as f:
            f.write(b"extra")
        self.plan["shards"]["a.gguf"] = digest(b"model bytes one\nextra")
        self.lines.clear()
        self.hashes.clear()
        self.assertEqual(self._verify(), [os.path.join(self.gguf, "a.gguf")])
        self.assertEqual(self._record()["a.gguf"]["size"], os.path.getsize(os.path.join(self.gguf, "a.gguf")))

    def test_a_shard_whose_bytes_no_match_the_plan_refuses(self):
        self._verify()
        with open(os.path.join(self.gguf, "a.gguf"), "wb") as f:
            f.write(b"corrupted\n")
        with self.assertRaises(AssertionError) as got:
            self._verify()
        self.assertIn("does not match its archive hash", str(got.exception))

    def test_a_plan_naming_a_different_hash_than_the_record_re_hashes_and_refuses(self):
        self._verify()
        self.plan["shards"]["a.gguf"] = digest(b"the real bytes are these")
        self.hashes.clear()
        with self.assertRaises(AssertionError) as got:
            self._verify()
        self.assertIn(os.path.join(self.gguf, "a.gguf"), self.hashes, "the wanted hash moved, so it re-hashed")
        self.assertIn("does not match its archive hash", str(got.exception))

    def test_the_measurement_path_never_trusts_the_record(self):
        self._verify(daily=True)
        self.lines.clear()
        self.hashes.clear()
        self.assertEqual(self._verify(daily=False),
                         [os.path.join(self.gguf, n) for n in ("a.gguf", "b.gguf")],
                         "a start that measures re-hashes every shard")
        self.assertTrue(all("verified earlier" not in line for line in self.lines), self.lines)

    def test_measurement_never_writes_a_record(self):
        self._verify(daily=False)
        self.assertFalse(os.path.exists(S._sidecar_path(self.gguf)), "only the daily path records")

    def test_an_unreadable_record_re_hashes_and_still_refuses_a_bad_shard(self):
        self._verify()
        with open(S._sidecar_path(self.gguf), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.plan["shards"]["a.gguf"] = "0" * 64
        self.hashes.clear()
        with self.assertRaises(AssertionError):
            self._verify()
        self.assertIn(os.path.join(self.gguf, "a.gguf"), self.hashes,
                      "nothing was trusted from a record that could not be read")

    def test_a_shard_the_plan_names_but_is_not_there_raises(self):
        self.plan["shards"]["missing.gguf"] = "0" * 64
        with self.assertRaises(OSError):
            self._verify()

    def test_the_record_survives_a_second_call_as_a_fresh_read(self):
        self._verify()
        rec = self._record()
        self._verify()
        self.assertEqual(self._record(), rec, "the record is stable while nothing changes")

    def test_prepare_defaults_to_a_full_hash_and_only_the_daily_callers_opt_in(self):
        """prepare's trust_verified defaults to False: a caller that does not say daily gets the full re-hash."""
        self.assertEqual(inspect.signature(S.prepare).parameters["trust_verified"].default, False)
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(S.__file__))))
        with open(os.path.join(root, "infer_ollama.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("S.prepare(plan, out_dir, placement, trust_verified=not frontier)", src,
                      "the measurement path (frontier) keeps the full hash, the daily path opts in")
        self.assertEqual(src.count("S.prepare("), 1, "no prepare call left without the flag")
        with open(os.path.join(os.path.dirname(os.path.abspath(S.__file__)), "strata_serve.py"), encoding="utf-8") as f:
            serve = f.read()
        self.assertIn("S.prepare(plan, _out(plan, \"install\"), res, trust_verified=True)", serve,
                      "the serving installer is the daily path")


if __name__ == "__main__":
    unittest.main(verbosity=2)
