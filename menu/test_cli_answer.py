"""cli_answer against the REAL captured stdout from the two probe orders, plus refusal controls."""
import json, pathlib, sys, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cli_answer as C  # noqa: E402

OUTBOX = pathlib.Path(r"E:\SSDShared\tabfoundry-jobs\outbox")
CAPTURES = HERE / "fixtures" / "cli_captures"      # the same two result files, byte-identical, readable in any container


def rows(prefix):
    files = sorted(CAPTURES.glob(prefix + ".*.results.json")) or sorted(OUTBOX.glob(prefix + ".*.results.json"))
    assert len(files) == 1, files
    return [r for r in json.loads(files[0].read_text(encoding="utf-8"))["arms"][0]["rows"] if r.get("ran")]


SHORT_PROMPT = 'Restart the redis service. Respond with ONLY a JSON object {"tool": "<name>", "arguments": {...}}.'


class RealBytes(unittest.TestCase):
    def test_short_prompt_full_echo(self):
        r = next(x for x in rows("probe-cli-output.20260927-192910") if x["item"].startswith("probe~tool"))
        a, err = C.llama_cli_answer(r["stdout"], SHORT_PROMPT)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(a), {"tool": "restart_service", "arguments": {"service": "redis"}})

    def test_long_prompt_truncated_echo(self):
        r = next(x for x in rows("probe-cli-multiline.20260927-193433") if x["item"].startswith("probe~toolml"))
        self.assertGreater(len(r["prompt"]), C.LLAMA_CLI_ECHO_LIMIT)          # the case under test
        a, err = C.llama_cli_answer(r["stdout"], r["prompt"])
        self.assertEqual(err, "")
        self.assertEqual(json.loads(a), {"tool": "none", "arguments": {}})

    def test_vlm_whole_stdout(self):
        vs = [x for x in rows("probe-cli-output.20260927-192910") if x["item"].startswith("probe~vlm")]
        self.assertEqual(len(vs), 2)
        for r in vs:
            self.assertEqual(C.mtmd_answer(r["stdout"]), ("42.", ""))


class Refusals(unittest.TestCase):
    def setUp(self):
        r = next(x for x in rows("probe-cli-multiline.20260927-193433") if x["item"].startswith("probe~toolml"))
        self.out, self.prompt = r["stdout"], r["prompt"]

    def test_no_stats_line(self):
        cut = self.out[:self.out.rfind(C.LLAMA_CLI_STATS)]
        self.assertEqual(C.llama_cli_answer(cut, self.prompt)[1], "answer_boundary:stats_line_missing")

    def test_wrong_prompt(self):
        self.assertEqual(C.llama_cli_answer(self.out, "something else entirely")[1],
                         "answer_boundary:prompt_echo_found_0_times")

    def test_echo_end_changed(self):
        broken = self.out.replace(C.LLAMA_CLI_TRUNCATED, " [cut]\n")
        self.assertEqual(C.llama_cli_answer(broken, self.prompt)[1], "answer_boundary:prompt_echo_end_unrecognised")

    def test_empty_vlm(self):
        self.assertEqual(C.mtmd_answer("\n\n  \n"), (None, "answer_boundary:empty_stdout"))

    def test_multiline_answer_kept_whole(self):
        a, err = C.llama_cli_answer(self.out, self.prompt)
        self.assertEqual(a.count("\n"), 3)       # the whole 4-line JSON, not its first line


if __name__ == "__main__":
    unittest.main(verbosity=1)
