"""Verify the medical review architecture: the lead's helper is a read-only, menu-placed errand (via a1_harness._Errands) that submits drafts to a tool-less II-Medical 8B for review, with results returned to the lead. Ensures no reviewer fixes are applied to the lead's state and text integrity is preserved."""
import threading
import types
import unittest
from unittest import mock

import cancellation
import config
import medical_review as MR
import ollama_client
import scheduler
import tool_selection
import tools

PLACED = ("bighonker", "http://box", "qwen3-vl:8b", "off")


def turn(content="", calls=()):
    """The real shape of ollama_client._chat_once."""
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in calls]
    return {"message": message, "done_reason": "stop"}


class Script:
    """Stands in for _chat_once: plays back turns and records what each was given."""

    def __init__(self, *turns, gate=None):
        self.turns, self.seen, self.gate = list(turns), [], gate

    def __call__(self, url, model, messages, tools_list, num_ctx, keep_alive, timeout=None, think=False):
        self.seen.append({"url": url, "model": model, "messages": list(messages), "tools": [t["function"]["name"] for t in tools_list],
                          "think": think, "user": getattr(ollama_client._CALL_CONTEXT, "chat_user", None),
                          "slot": getattr(ollama_client._CALL_CONTEXT, "ckpt_slot", None)})
        if self.gate:
            self.gate.wait(10)
        t = self.turns.pop(0) if self.turns else turn("the end")
        if isinstance(t, Exception):
            raise t
        return t


class PlainCodeChecksTheIds(unittest.TestCase):
    TEXT = "See PMID 40664062, https://pubmed.ncbi.nlm.nih.gov/40664062/ and PMC12036543 and PMC999999."

    def test_finds_each_id_once(self):
        self.assertEqual(MR.find_ids(self.TEXT), {"pubmed": ["40664062"], "pmc": ["12036543", "999999"]})

    def test_says_which_exist_and_which_do_not(self):
        def get(_url, params):
            if params["db"] == "pubmed":
                res = {"40664062": {"title": "Self-efficacy scale", "sortfirstauthor": "Bresolin P", "pubdate": "2025 Aug"}}
            else:
                res = {"12036543": {"title": "Norwegian SECP", "authors": [{"name": "Olaussen C"}], "pubdate": "2025 Apr"},
                       "999999": {"error": "cannot get document summary"}}
            return mock.Mock(json=lambda: {"result": res})
        with mock.patch.object(MR.pubmed, "_get", side_effect=get):
            out = MR.check_ids(self.TEXT)
        self.assertIn("PMID 40664062 exists: Bresolin P, 2025", out)
        self.assertIn("PMC12036543 exists: Olaussen C, 2025", out)
        self.assertIn("PMC999999 NOT FOUND", out)

    def test_could_not_ask_is_neither_real_nor_fake(self):
        with mock.patch.object(MR.pubmed, "_get", side_effect=OSError("down")):
            out = MR.check_ids("PMID 40664062")
        self.assertIn("could not be checked", out)
        self.assertNotIn("NOT FOUND", out)
        self.assertNotIn("exists", out)

    def test_no_ids_says_nothing_and_asks_nothing(self):
        with mock.patch.object(MR.pubmed, "_get") as g:
            self.assertEqual(MR.check_ids("no sources here"), "")
        g.assert_not_called()


class ThePlacementIsTheMenus(unittest.TestCase):
    def row(self, box, model, think="off"):
        return {"host": box, "box": box, "entry": {"model": model, "think": think}}

    def test_the_errand_takes_the_tool_family_on_the_8gb_tier_as_a1_does(self):
        band = [self.row("bighonker", "qwen3-vl:8b")]
        with mock.patch.object(scheduler, "weigh_rows", return_value=(band, [])) as w, \
                mock.patch.object(ollama_client, "reachable", return_value=True), \
                mock.patch.object(scheduler, "occupied_for", return_value=None):
            got = MR.place_helper()
        self.assertEqual(w.call_args.args[:2], ("tool", "8gb"))
        self.assertIn(scheduler.FELLOWSHIP_HOST, w.call_args.kwargs["exclude"], "its turns need a card and tools")
        self.assertEqual(got, ("bighonker", scheduler.CARDS["bighonker"]["url"], "qwen3-vl:8b", "off"))

    def test_a_held_card_is_passed_over_and_nothing_free_is_none(self):
        band = [self.row("bighonker", "m1"), self.row("softserve", "m2")]
        held = lambda u, m=None, takes_window=False: {"holder": "job"} if m == "m1" else None
        with mock.patch.object(scheduler, "weigh_rows", return_value=(band, [])), \
                mock.patch.object(ollama_client, "reachable", return_value=True), \
                mock.patch.object(scheduler, "occupied_for", side_effect=held):
            self.assertEqual(MR.place_helper()[0], "softserve")
        with mock.patch.object(scheduler, "weigh_rows", return_value=(band, [])), \
                mock.patch.object(ollama_client, "reachable", return_value=True), \
                mock.patch.object(scheduler, "occupied_for", return_value={"holder": "job"}):
            self.assertIsNone(MR.place_helper())

    def test_the_reader_needs_a_box_that_has_it_and_yields_to_live_work(self):
        self.assertTrue(scheduler.KINDS["medical_review"]["yields_to_live"])
        with mock.patch.object(scheduler, "factory_capabilities", return_value=set()), mock.patch.object(scheduler, "place") as p:
            self.assertIsNone(MR.place_reader())
        p.assert_not_called()

    def hosts(self, kind, own, ram=30.0, loaded=()):
        """place() on BigHonker with a window on the card: its claim, its `compute` state and a chat's live claim all standing."""
        with mock.patch.object(ollama_client, "models_loaded", return_value=list(loaded)), \
                mock.patch.object(ollama_client, "model_state", return_value=("—", "cold")), \
                mock.patch.object(scheduler, "own_window", return_value=own), \
                mock.patch.object(scheduler, "lendable_window", return_value=None), \
                mock.patch.object(scheduler, "gpu_health", return_value=(True, "")), \
                mock.patch.object(scheduler, "gpu_state", return_value=(True, "a window's claim")), \
                mock.patch.object(scheduler, "training_busy", return_value=(True, "a window on this card")), \
                mock.patch.object(scheduler, "active_claim", return_value={"holder": "hai-chat:x"}), \
                mock.patch.object(scheduler, "ram_free_gb", return_value=ram), \
                mock.patch.object(scheduler, "cpu_load_pct", return_value=5.0), \
                mock.patch.object(scheduler, "record_placement"):
            return [h for h, _s, _w in scheduler.place(kind, only=("bighonker",))]

    def test_the_readers_own_open_window_does_not_refuse_its_next_read(self):
        self.assertEqual(self.hosts("medical_review", "ii-medical:8b-q4km"), ["bighonker"])
        self.assertEqual(self.hosts("medical_review", None), [], "with no window of Kirk's, a claim on the card still refuses it")
        self.assertEqual(self.hosts("review", "ii-medical:8b-q4km"), [], "other background kinds still yield to the window")

    def test_a_loaded_reader_is_not_refused_for_the_ram_its_own_weights_map(self):
        model = config.MEDICAL_REVIEW_MODEL
        self.assertEqual(self.hosts("medical_review", model, ram=1.0, loaded=[model]), ["bighonker"])
        self.assertEqual(self.hosts("medical_review", model, ram=1.0, loaded=[]), [], "not loaded: the spill gate stands")

    def test_the_reader_joins_its_own_window_but_not_another_models_in_use(self):
        model = config.MEDICAL_REVIEW_MODEL
        url = scheduler.CARDS["bighonker"]["url"]
        win = lambda u, resource="gpu": {"holder": "hai-win-bighonker-x_bighonker"} if resource == "gpu" else None

        def read(own):
            with mock.patch.object(scheduler, "factory_capabilities", return_value={"model." + model}), \
                    mock.patch.object(scheduler, "place", return_value=[("bighonker", 1.0, "")]), \
                    mock.patch.object(ollama_client, "reachable", return_value=True), \
                    mock.patch.object(scheduler, "own_window", return_value=own), \
                    mock.patch.object(scheduler, "idle_own_window", return_value=None), \
                    mock.patch.object(scheduler, "render_busy", return_value=(False, "")), \
                    mock.patch.object(scheduler, "claim_for_url", side_effect=win):
                return MR.place_reader()
        self.assertEqual(read(model), ("bighonker", url))
        self.assertIsNone(read("qwen3.5:9b"), "another model's window in use holds the card")

    def test_what_a_helper_said_is_kept_since_the_person_never_sees_it(self):
        with mock.patch.object(MR, "place_reader", return_value=("bighonker", "http://box")), \
                mock.patch.object(MR, "read_draft", return_value="the report"), \
                mock.patch.object(MR, "_run_id", return_value=None), \
                mock.patch.object(MR.notes, "diag") as d:
            self.assertEqual(MR.errand("check this", draft="a draft", user="u"), "the report")
        rec = d.call_args.args[0]
        self.assertEqual((rec["report"], rec["draft"], rec["task"], rec["host"]), ("the report", "a draft", "check this", "bighonker"))


class TheErrandRules(unittest.TestCase):
    def run_it(self, *turns):
        s = Script(*turns)
        with mock.patch.object(ollama_client, "_chat_once", s), mock.patch.object(MR.config, "ctx_for", return_value=47104), \
                mock.patch.object(ollama_client, "placed_effort", return_value=False), \
                mock.patch.object(tools, "run_tool", return_value="an abstract") as rt:
            rec = MR.run_errand("JOB: check it", PLACED, "U", "nursing")
        return rec, s, rt

    def test_it_looks_then_reports(self):
        rec, s, rt = self.run_it(turn(calls=[("search_evidence", {"query": "a"})]), turn("Calcium gluconate. PMID 1."))
        self.assertEqual((rec["status"], rec["report"], rec["calls"]), ("done", "Calcium gluconate. PMID 1.", 1))
        rt.assert_called_once()
        self.assertEqual(rt.call_args.kwargs["persona"], "nursing", "library lookups read the same corpus as the lead")
        self.assertIn("Today's date is", s.seen[0]["messages"][0]["content"])

    def test_it_only_has_look_tools_and_anything_else_is_refused(self):
        rec, s, rt = self.run_it(turn(calls=[("write_config", {"path": "x"})]), turn("done"))
        self.assertEqual(s.seen[0]["tools"], ["search_evidence", "search_docs", "get_document"])
        rt.assert_not_called()
        self.assertIn("not available on an errand", s.seen[1]["messages"][-1]["content"])

    def test_an_answer_that_looked_at_nothing_gets_one_nudge_then_is_flagged(self):
        rec, s, _ = self.run_it(turn("a guess"), turn("still a guess"))
        self.assertEqual(rec["status"], "unlooked")
        self.assertEqual(s.seen[1]["messages"][-1]["content"], MR.LOOK_FIRST)
        self.assertIn("WITHOUT looking", MR._say(rec, 1.0))

    def test_a_nudge_that_works_is_a_normal_report(self):
        rec, _, _ = self.run_it(turn("a guess"), turn(calls=[("search_evidence", {"query": "a"})]), turn("found it"))
        self.assertEqual((rec["status"], rec["report"]), ("done", "found it"))

    def test_looked_then_said_nothing_gets_one_ask_then_is_no_answer(self):
        rec, s, _ = self.run_it(turn(calls=[("search_evidence", {"query": "a"})]), turn(""), turn(""))
        self.assertEqual(rec["status"], "no_answer")
        self.assertEqual(s.seen[2]["messages"][-1]["content"], MR.ANSWER_NOW)

    def test_repeating_itself_is_stopped(self):
        same = [turn(calls=[("search_evidence", {"query": "a"})]) for _ in range(MR.NO_PROGRESS_STALL + 1)]
        rec, _, _ = self.run_it(*same)
        self.assertEqual(rec["status"], "no_progress")
        self.assertEqual(rec["turns"], MR.NO_PROGRESS_STALL + 1)

    def test_a_stop_ends_it(self):
        with mock.patch.object(cancellation, "check", side_effect=cancellation.Cancelled()):
            with self.assertRaises(cancellation.Cancelled):
                MR.run_errand("JOB", PLACED, "U", None)


class TheSpecialistReadsADraft(unittest.TestCase):
    READ = ('<think>protamine</think>{"verdict": "changes_requested", "summary": "s", "findings": '
            '[{"severity": "high", "explanation": "The dose looks high.", "recommended_action": "Give 7 mg instead."}]}')

    def test_its_thinking_and_fixes_stop_here(self):
        self.assertEqual(MR.parse_read(self.READ), [("high", "The dose looks high.")])

    def test_a_clean_read_and_a_bad_reply_differ(self):
        self.assertEqual(MR.parse_read('{"verdict": "pass", "findings": []}'), [])
        for bad in ("", "prose", "{broken", '{"summary": "no verdict"}'):
            self.assertIsNone(MR.parse_read(bad), bad)

    def read(self, answer, draft="the draft", sources="", ids=""):
        chat = mock.Mock(side_effect=answer) if isinstance(answer, Exception) else mock.Mock(return_value=(answer, None))
        with mock.patch.object(MR.config, "ctx_for", return_value=25600), mock.patch.object(ollama_client, "chat", chat), \
                mock.patch.object(MR, "check_ids", return_value=ids):
            return MR.read_draft(("bighonker", "http://box"), "the question", draft, sources, "U"), chat

    def test_what_it_raises_reaches_the_lead_as_concerns_to_check(self):
        out, _ = self.read(self.READ)
        self.assertIn("SECOND PERSPECTIVE", out)
        self.assertIn("not an authority", out)
        self.assertIn("search_evidence", out)
        self.assertIn("- [high] The dose looks high.", out)
        self.assertNotIn("7 mg", out, "readers invented doses on 2026-11-06: their fixes do not reach the lead")

    def test_it_has_no_tools_and_gets_the_date_the_whole_text_and_the_sources(self):
        draft = "x" * 9500
        _, chat = self.read(self.READ, draft=draft, sources="the abstract")
        args = chat.call_args.args
        self.assertEqual(args[3], [], "the 8B has no tools")
        self.assertIn("Today's date is", args[2][0]["content"])
        self.assertIn(draft, args[2][1]["content"], "the first test cut answers at 9,000 characters")
        self.assertIn("the abstract", args[2][1]["content"])

    def test_a_clean_read_says_so(self):
        self.assertIn("raised no clinical concern", self.read('{"verdict": "pass", "findings": []}')[0])

    def test_too_long_is_refused_not_cut_and_keeps_the_id_check(self):
        out, chat = self.read(self.READ, draft="x" * 200000, ids="ID check: fine")
        self.assertIn("too long", out)
        self.assertIn("ID check: fine", out)
        chat.assert_not_called()

    def test_a_failure_or_a_bad_reply_gives_no_perspective(self):
        out, _ = self.read(OSError("boom"), ids="ID check: fine")
        self.assertIn("could not finish", out)
        self.assertIn("ID check: fine", out)
        self.assertNotIn("SECOND PERSPECTIVE", out)
        self.assertIn("could not be read", self.read("prose")[0])


class TheLookupJob(unittest.TestCase):
    def work(self, script, task="find the antidote", sources="", ids=""):
        with mock.patch.object(ollama_client, "_chat_once", script), mock.patch.object(MR.config, "ctx_for", return_value=47104), \
                mock.patch.object(ollama_client, "placed_effort", return_value=False), mock.patch.object(MR, "check_ids", return_value=ids), \
                mock.patch.object(tools, "run_tool", return_value="an abstract"):
            return MR.look_up(PLACED, task, sources, "U", "nursing")

    def test_the_lead_gets_a_caveated_report(self):
        out = self.work(Script(turn(calls=[("search_evidence", {"query": "a"})]), turn("Calcium gluconate.")))
        self.assertIn("not an authority", out)
        self.assertIn("Calcium gluconate.", out)
        self.assertIn("1 lookups", out)

    def test_too_long_is_refused_not_cut(self):
        s = Script()
        self.assertIn("too long", self.work(s, task="x" * 400000))
        self.assertEqual(s.seen, [])

    def test_a_failure_is_a_report_and_keeps_the_id_check(self):
        out = self.work(Script(OSError("boom")), ids="ID check: fine")
        self.assertIn("could not finish", out)
        self.assertIn("ID check: fine", out)

    def test_its_own_report_ids_are_checked(self):
        with mock.patch.object(MR.pubmed, "_get", return_value=mock.Mock(json=lambda: {"result": {}})):
            out = MR._say({"status": "done", "report": "See PMID 40664062.", "model": "m", "host": "h", "calls": 2}, 1.0)
        self.assertIn("PMID 40664062 NOT FOUND", out, "a helper that invents a citation is caught by the lookup")


class TheLeadKeepsWorking(unittest.TestCase):
    RUN = "run-errand-test"
    READ = '{"verdict": "changes_requested", "findings": [{"severity": "high", "explanation": "The dose looks high."}]}'

    def setUp(self):
        self.slot = types.SimpleNamespace(key="k", ledger=types.SimpleNamespace(run_id=self.RUN))
        ollama_client._CALL_CONTEXT.ckpt_slot = self.slot
        self.addCleanup(lambda: setattr(ollama_client._CALL_CONTEXT, "ckpt_slot", None))
        self.addCleanup(lambda: ollama_client.take_run_messages(self.RUN))
        for p in (mock.patch.object(MR, "place_helper", return_value=PLACED),
                  mock.patch.object(MR, "place_reader", return_value=("bighonker", "http://box")),
                  mock.patch.object(MR.config, "ctx_for", return_value=47104),
                  mock.patch.object(MR, "check_ids", return_value=""),
                  mock.patch.object(ollama_client, "placed_effort", return_value=False),
                  mock.patch.object(tools, "run_tool", return_value="an abstract")):
            p.start()
            self.addCleanup(p.stop)

    def script(self, gate=None):
        return Script(turn(calls=[("search_evidence", {"query": "a"})]), turn("Calcium gluconate."), gate=gate)

    def test_a_draft_goes_to_the_reader_returns_at_once_and_the_read_arrives_as_a_message(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        seen = {}

        def chat(url, model, messages, tools_list, num_ctx=None, user=None):
            seen.update(model=model, slot=getattr(ollama_client._CALL_CONTEXT, "ckpt_slot", None))
            gate.wait(10)
            return self.READ, None
        with mock.patch.object(ollama_client, "chat", chat), mock.patch.object(ollama_client, "_chat_once") as once:
            out = MR.errand("the question", "the draft", user="U", persona="nursing")
            self.assertIn("Keep working", out)
            messages = []
            self.assertEqual(ollama_client._drain_inbox(self.slot, messages), 0, "the lead is not waiting on it")
            gate.set()
            MR.wait_for_run(self.RUN)
        once.assert_not_called()
        self.assertEqual(seen["model"], config.MEDICAL_REVIEW_MODEL)
        self.assertIsNone(seen["slot"], "it must not take the lead's window or checkpoints")
        self.assertEqual(ollama_client._drain_inbox(self.slot, messages), 1)
        self.assertIn("the medical helper", messages[0]["content"])
        self.assertIn("- [high] The dose looks high.", messages[0]["content"])

    def test_no_draft_goes_to_the_helper(self):
        s = self.script()
        with mock.patch.object(ollama_client, "_chat_once", s), mock.patch.object(ollama_client, "chat") as chat:
            MR.errand("find the antidote", user="U")
            MR.wait_for_run(self.RUN)
        chat.assert_not_called()
        self.assertEqual(s.seen[0]["model"], PLACED[2])
        self.assertEqual(s.seen[0]["user"], "U", "the window door decides by this user")
        self.assertIsNone(s.seen[0]["slot"])
        messages = []
        ollama_client._drain_inbox(self.slot, messages)
        self.assertIn("Calcium gluconate.", messages[0]["content"])

    def test_a_run_does_not_end_before_its_errand_reports(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        with mock.patch.object(ollama_client, "_chat_once", self.script(gate)):
            MR.errand("find the antidote", user="U")
            threading.Timer(0.6, gate.set).start()
            MR.wait_for_run(self.RUN)
        self.assertTrue(gate.is_set(), "it waited for the report")
        self.assertIsNone(MR._OPEN.get(self.RUN), "nothing left open")

    def test_it_is_all_back_end_the_person_never_sees_it(self):
        with mock.patch.object(ollama_client, "_chat_once", self.script()):
            MR.errand("find the antidote", user="U")
            MR.wait_for_run(self.RUN)
        messages, shown = [], []
        with mock.patch.object(ollama_client, "_delta", side_effect=shown.append):
            self.assertEqual(ollama_client._drain_inbox(self.slot, messages), 1, "the model gets it")
        self.assertEqual(shown, [], "no row on the page")
        self.assertEqual(ollama_client.take_run_messages(self.RUN), ([], []), "nothing saved with the chat")

    def test_a_late_report_is_not_kept_with_the_chat_but_a_persons_message_is(self):
        ollama_client.post_to_run(self.RUN, "a report", "the medical helper", internal=True)
        ollama_client.post_to_run(self.RUN, "wait, one more thing", "Riley")
        said, late = ollama_client.take_run_messages(self.RUN)
        self.assertEqual([m["text"] for m in late], ["wait, one more thing"])
        self.assertEqual(said, [])

    def test_a_persons_message_still_shows_on_the_page(self):
        ollama_client.post_to_run(self.RUN, "wait, one more thing", "Riley")
        shown = []
        with mock.patch.object(ollama_client, "_delta", side_effect=shown.append):
            ollama_client._drain_inbox(self.slot, [])
        self.assertEqual(shown[0]["kind"], "user_message")
        self.assertEqual(ollama_client.take_run_messages(self.RUN)[0][0]["text"], "wait, one more thing")

    def test_the_chat_loop_asks_before_it_ends(self):
        with open("ollama_client.py", encoding="utf-8") as f:
            src = f.read()
        self.assertLess(src.index("OPEN_ERRANDS_HOOK(_slot.ledger.run_id)"),
                        src.index("if _slot is not None and _drain_inbox(_slot, messages):\n                continue"))
        self.assertIs(ollama_client.OPEN_ERRANDS_HOOK, MR.wait_for_run)

    def test_nobody_free_says_so_at_once(self):
        MR.place_reader.return_value = None
        MR.place_helper.return_value = None
        self.assertIn("No medical helper", MR.errand("the question", "the draft", user="U"))
        self.assertIn("No medical helper", MR.errand("find the antidote", user="U"))
        self.assertIsNone(MR._OPEN.get(self.RUN))

    def test_an_empty_job_is_asked_for(self):
        self.assertIn("needs `task`", MR.errand("  "))

    def test_outside_a_runs_loop_it_returns_the_report(self):
        ollama_client._CALL_CONTEXT.ckpt_slot = None
        with mock.patch.object(ollama_client, "_chat_once", self.script()):
            self.assertIn("Calcium gluconate.", MR.errand("find the antidote", user="U"))
        self.slot.key = "k.sub"
        ollama_client._CALL_CONTEXT.ckpt_slot = self.slot
        self.assertIsNone(MR._run_id(), "a sub-call has no later turn to post to")


class TheLeadCanReachIt(unittest.TestCase):
    def names(self, pool):
        return [t["function"]["name"] for t in pool]

    def test_it_is_in_both_general_pools(self):
        self.assertIn("medical_review", self.names(tools.BRAIN_TOOLS))
        self.assertIn("medical_review", self.names(tools.HEAVY_TOOLS))

    def test_run_tool_reaches_it_with_the_persona(self):
        with mock.patch.object(MR, "errand", return_value="started") as e:
            got = tools._run_tool_impl("medical_review", {"task": "t", "draft": "d", "sources": "s"}, "text", user="U", persona="nursing")
        self.assertEqual(got, "started")
        e.assert_called_once_with("t", "d", "s", user="U", persona="nursing")

    def test_a_nursing_turn_is_always_offered_it(self):
        with mock.patch.object(tool_selection, "_matrix", return_value=None):
            nursing, _ = tool_selection.select(tools.HEAVY_TOOLS, "U", "x", 2048, persona="nursing", corpus=("", 0.0))
            plain, _ = tool_selection.select(tools.HEAVY_TOOLS, "U", "x", 2048, persona="general", corpus=("", 0.0))
        self.assertIn("medical_review", self.names(nursing))
        self.assertNotIn("medical_review", self.names(plain))


if __name__ == "__main__":
    unittest.main()
