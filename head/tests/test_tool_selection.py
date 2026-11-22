"""Test deterministic logic of the tool selector.

Uses synthetic tools and injected vectors to avoid dependency on the live
embed host. The goal is to ensure the selection mechanism narrows the
toolbox sufficiently to fit within context limits, without pruning the
critical 'ask for help' (escalation) tool."""
import json
import unittest

import config
import tool_selection


def _tool(name, description="does a thing", padding=0):
    """A tool schema of controllable size, so budget behaviour can be asserted exactly."""
    props = {"arg%d" % k: {"type": "string", "description": "x" * 40} for k in range(padding)}
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props}}}


CORE_SAMPLE = ["search_docs", "escalate_to_mothership", "remember"]


class _Patched:
    """Inject a tool matrix and query vectors; no network, no embed host, no cache."""

    def __init__(self, names, vecs, qvecs):
        self.names, self.vecs, self.qvecs = names, vecs, qvecs

    def __enter__(self):
        self._matrix = tool_selection._matrix
        self._embed = tool_selection.embeddings.embed_queries
        tool_selection._matrix = lambda tools_list: (self.names, self.vecs)
        tool_selection.embeddings.embed_queries = lambda texts: self.qvecs[:len(texts)]
        return self

    def __exit__(self, *a):
        tool_selection._matrix = self._matrix
        tool_selection.embeddings.embed_queries = self._embed


class ToolSelectionTests(unittest.TestCase):

    def setUp(self):
        # A box that comfortably overflows a small window, so selection always engages.
        self.box = ([_tool(n) for n in CORE_SAMPLE]
                    + [_tool("dataset_review", padding=6), _tool("dataset_candidates", padding=6),
                       _tool("docker_restart", padding=6), _tool("seerr_add", padding=6)]
                    + [_tool("filler_%d" % k, padding=12) for k in range(40)])
        self.names = [t["function"]["name"] for t in self.box]
        self.ctx = 2048
        # the box plays a GENERAL pool, the way HEAVY_TOOLS / BRAIN_TOOLS do in production (tools.py registers them)
        self._pool = tool_selection.register_pool(self.box)

    def tearDown(self):
        if self._pool in tool_selection._GENERAL:
            tool_selection._GENERAL.remove(self._pool)

    def _vecs(self, aligned_with):
        """Unit-ish vectors: `aligned_with` names point one way, everything else another."""
        return [[1.0, 0.0] if n in aligned_with else [0.0, 1.0] for n in self.names]

    # --- the whole point: it must actually shrink -------------------------------------------------

    def test_narrows_an_overflowing_toolbox(self):
        with _Patched(self.names, self._vecs({"dataset_review", "dataset_candidates"}),
                      [[1.0, 0.0]]):
            picked, note = tool_selection.select(self.box, "Alex", "review the candidates", self.ctx,
                                                 corpus=("", 0.0))
        full = len(json.dumps(self.box)) // 4
        got = len(json.dumps(picked)) // 4
        self.assertLess(got, full // 2, "selection did not meaningfully shrink the toolbox")
        self.assertLessEqual(got, int(self.ctx * config.TOOL_SELECT_BUDGET_FRAC),
                             "selection blew its own token budget")
        self.assertIsNotNone(note)

    def test_leaves_a_fitting_toolbox_completely_alone(self):
        """A purpose-built SET (no bigger than a narrowed box) that fits passes untouched."""
        small = [_tool("a"), _tool("b")]
        picked, note = tool_selection.select(small, "Alex", "anything", 65536)
        self.assertEqual(picked, small)
        self.assertIsNone(note)

    def test_a_general_pool_is_narrowed_even_when_the_window_holds_it(self):
        huge = 122880
        self.assertLessEqual(len(json.dumps(self.box)) // 4, int(huge * config.TOOL_SELECT_BUDGET_FRAC),
                             "precondition: the pool must FIT the window, or this proves nothing new")
        with _Patched(self.names, self._vecs({"dataset_review", "dataset_candidates"}), [[1.0, 0.0]]):
            picked, note = tool_selection.select(self.box, "Alex", "review the candidates", huge,
                                                 corpus=("", 0.0))
        names = [t["function"]["name"] for t in picked]
        self.assertIn("dataset_review", names)
        self.assertNotIn("filler_0", names)
        self.assertTrue(set(CORE_SAMPLE) <= set(names), "CORE is never dropped")
        self.assertIsNotNone(note, "the model is told it holds a narrowed box")

    def test_every_relevant_tool_is_kept_there_is_no_count_cap(self):
        """No count cap: all relevant tools must be returned, even if > old limits (e.g. 30/100)."""
        pool = [_tool(n) for n in CORE_SAMPLE] + [_tool("t_%d" % k, padding=2) for k in range(100)]
        names = [t["function"]["name"] for t in pool]
        relevant = {"t_%d" % k for k in range(30)}
        tool_selection.register_pool(pool)
        try:
            with _Patched(names, [[1.0, 0.0] if n in relevant else [0.0, 1.0] for n in names], [[1.0, 0.0]]):
                picked, _ = tool_selection.select(pool, "Alex", "the thirty things", 122880, corpus=("", 0.0))
        finally:
            tool_selection._GENERAL.remove(frozenset(names))
        got = set(t["function"]["name"] for t in picked)
        self.assertTrue(relevant <= got, "a relevant tool was dropped: %s" % sorted(relevant - got))
        self.assertNotIn("t_99", got)

    def test_a_registered_purpose_built_set_passes_whole(self):
        """A coding set is a SET by name, not by size: a caller handing it (or a subset) gets every tool in it."""
        own = [_tool("own_%d" % k) for k in range(40)]
        tool_selection.register_set(own)
        try:
            picked, note = tool_selection.select(own[:35], "Alex", "anything", 122880)
            self.assertEqual(picked, own[:35])
            self.assertIsNone(note)
        finally:
            tool_selection._PURPOSE_BUILT.remove(frozenset(t["function"]["name"] for t in own))

    def test_an_index_outage_never_strips_a_model_whose_window_holds_everything(self):
        """Relevance cannot be judged without embeddings: the 3.8 (whole pool fits) gets every tool; a small
        window that the pool would saturate falls back to CORE, as before."""
        tool_selection._matrix, keep = (lambda tools_list: None), tool_selection._matrix
        try:
            big, _ = tool_selection.select(self.box, "Alex", "anything", 122880, corpus=("", 0.0))
            small, _ = tool_selection.select(self.box, "Alex", "anything", self.ctx, corpus=("", 0.0))
        finally:
            tool_selection._matrix = keep
        self.assertEqual(big, self.box)
        self.assertEqual({t["function"]["name"] for t in small}, set(CORE_SAMPLE))

    # --- the failure this module exists to end ----------------------------------------------------

    def test_escalation_survives_even_when_nothing_matches(self):
        """A brain that cannot ask for help is the original defect. CORE is never dropped."""
        with _Patched(self.names, self._vecs(set()), [[1.0, 0.0]]):
            picked, _ = tool_selection.select(self.box, "Alex", "unrelated question", self.ctx,
                                              corpus=("", 0.0))
        names = [t["function"]["name"] for t in picked]
        self.assertIn("escalate_to_mothership", names)
        self.assertIn("search_docs", names)

    def test_embeddings_unavailable_falls_back_to_core_not_to_everything(self):
        """Sending all 86 schemas is the KNOWN-BROKEN path, so a dead embed host must not restore it."""
        original = tool_selection._matrix
        tool_selection._matrix = lambda tools_list: None
        try:
            info = {}
            picked, note = tool_selection.select(self.box, "Alex", "review the candidates", self.ctx,
                                                 corpus=("", 0.0), info=info)
        finally:
            tool_selection._matrix = original
        self.assertIs(info.get("outage"), True, "the caller is told this list is not a pick (a held block keeps itself)")
        names = [t["function"]["name"] for t in picked]
        self.assertLess(len(picked), len(self.box))
        self.assertIn("escalate_to_mothership", names)
        self.assertIn("core tools", (note or "").lower())

    # --- signal 4: in-flight state ----------------------------------------------------------------

    def test_open_review_promotes_the_review_tools(self):
        """State is a fact. With a review open, an otherwise-unremarkable request reaches its tools."""
        vecs = self._vecs(set())          # nothing is semantically close
        with _Patched(self.names, vecs, [[1.0, 0.0]]):
            without, _ = tool_selection.select(self.box, "Alex", "I'm done with those", self.ctx,
                                               corpus=("", 0.0))
            with_state, _ = tool_selection.select(self.box, "Alex", "I'm done with those", self.ctx,
                                                  state_tools={"dataset_review"}, corpus=("", 0.0))
        self.assertNotIn("dataset_review", [t["function"]["name"] for t in without])
        self.assertIn("dataset_review", [t["function"]["name"] for t in with_state])

    def test_state_boosts_but_does_not_override(self):
        """An open review must not hijack a confident, unrelated request."""
        vecs = self._vecs({"docker_restart"})
        with _Patched(self.names, vecs, [[1.0, 0.0]]):
            picked, _ = tool_selection.select(self.box, "Alex", "restart the plex container",
                                              self.ctx, state_tools={"dataset_review"},
                                              corpus=("", 0.0))
        names = [t["function"]["name"] for t in picked]
        self.assertIn("docker_restart", names)
        self.assertGreater(names.index("dataset_review") if "dataset_review" in names else 99,
                           names.index("docker_restart"))

    # --- ambiguity must produce a QUESTION, not a guess --------------------------------------------

    def test_scattered_picks_ask_and_cohesive_picks_do_not(self):
        """Uncertainty is the picks DISAGREEING, not the top score being low (measured: a unanimous
        six-tool dataset read and a scattered fantasy-football read led by 0.107 vs 0.105)."""
        cohesive = [[1.0, 0.0] if n in ("dataset_review", "dataset_candidates") else [0.0, 1.0]
                    for n in self.names]
        with _Patched(self.names, cohesive, [[1.0, 0.0]]):
            _, quiet = tool_selection.select(self.box, "Alex", "dedupe the dataset", self.ctx,
                                             corpus=("", 0.0))
        # Same leads, but the two winners point in different directions -> disagreement.
        scattered = list(cohesive)
        scattered[self.names.index("dataset_candidates")] = [0.7, 0.71]
        with _Patched(self.names, scattered, [[1.0, 0.0]]):
            _, asks = tool_selection.select(self.box, "Alex", "review the candidates", self.ctx,
                                            corpus=("", 0.0))
        self.assertNotIn("ASK before", quiet or "")
        self.assertIn("ASK before", asks or "")

    def test_corpus_and_open_review_both_claiming_the_words_asks(self):
        with _Patched(self.names, self._vecs({"dataset_review", "dataset_candidates"}),
                      [[1.0, 0.0], [1.0, 0.0]]):
            _, note = tool_selection.select(
                self.box, "Alex", "review the candidates", self.ctx,
                state_tools={"dataset_review"},
                corpus=("interview notes: candidate shortlist, three resumes", 0.80))
        self.assertIn("ASK before", note or "")

    # --- signal 2: the conversation ---------------------------------------------------------------

    def test_recent_context_skips_the_latest_user_message_and_tool_results(self):
        msgs = [{"role": "system", "content": "you are HAI"},
                {"role": "user", "content": "show me the next batch of images"},
                {"role": "assistant", "content": "here are 120 thumbnails"},
                {"role": "tool", "content": "x" * 5000},
                {"role": "user", "content": "I'm done with those"}]
        ctx = tool_selection.recent_context(msgs)
        self.assertIn("120 thumbnails", ctx)
        self.assertIn("next batch", ctx)
        self.assertNotIn("I'm done with those", ctx, "the query must not be its own context")
        self.assertNotIn("x" * 100, ctx, "tool results are data, not intent")

    def test_conversation_changes_which_tools_load(self):
        """The same words in a different conversation must reach different tools — this is the whole
        reason selection is retrieval and not a word list.

        Three axes: images, tv, and an unrelated bulk direction the filler tools occupy. The request
        itself sits BETWEEN images and tv (genuinely ambiguous, as "show me more of those" is), so
        only the conversation can break the tie.
        """
        images, tv, other = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]
        vecs = [images if n in ("dataset_review", "dataset_candidates")
                else tv if n == "seerr_add" else other for n in self.names]
        ambiguous = [0.5, 0.5, 0.1]

        def first_real(picked):
            return next(t["function"]["name"] for t in picked
                        if t["function"]["name"] not in tool_selection.CORE_TOOL_NAMES)

        with _Patched(self.names, vecs, [ambiguous, images]):
            about_images, _ = tool_selection.select(self.box, "Alex", "show me more of those",
                                                    self.ctx, corpus=("", 0.0),
                                                    history="here are 120 candidate thumbnails")
        with _Patched(self.names, vecs, [ambiguous, tv]):
            about_tv, _ = tool_selection.select(self.box, "Alex", "show me more of those",
                                                self.ctx, corpus=("", 0.0),
                                                history="two episodes finished importing to plex")
        self.assertIn(first_real(about_images), ("dataset_review", "dataset_candidates"))
        self.assertEqual(first_real(about_tv), "seerr_add")

    def test_info_reports_the_turns_own_strongest_tool_and_whether_that_reading_is_sure(self):
        """The append-stable block grows on an UNSURE turn only with the raw reading's top pick, and only when that
        reading is sure (ollama_client.sticky_tools) - a turn that changed the subject inside another conversation."""
        images, tv, ops, other = [1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]
        vecs = [images if n in ("dataset_review", "dataset_candidates") else tv if n == "seerr_add"
                else ops if n == "docker_restart" else other for n in self.names]
        info = {}
        with _Patched(self.names, vecs, [ops, images]):
            tool_selection.select(self.box, "Alex", "restart caddy", self.ctx, corpus=("", 0.0),
                                  history="here are 120 candidate thumbnails", info=info)
        self.assertEqual((info["raw_top"], info["raw_sure"]), ("docker_restart", True))
        self.assertIn("unsure", info)
        info = {}
        with _Patched(self.names, vecs, [[0.5, 0.5, 0.5, 0.5], images]):
            tool_selection.select(self.box, "Alex", "thanks", self.ctx, corpus=("", 0.0),
                                  history="here are 120 candidate thumbnails", info=info)
        self.assertFalse(info["raw_sure"], "words that point nowhere are not a sure reading")

    def test_info_states_the_always_offered_set_it_picked(self):
        """select() says which tools it offered as core, so sticky_tools keeps them in a held block."""
        images, tv, ops, other = [1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]
        vecs = [images if n in ("dataset_review", "dataset_candidates") else tv if n == "seerr_add"
                else ops if n == "docker_restart" else other for n in self.names]
        info = {}
        with _Patched(self.names, vecs, [[0.5, 0.5, 0.5, 0.5], images]):
            picked, _ = tool_selection.select(self.box, "Alex", "thanks", self.ctx, corpus=("", 0.0),
                                              history="here are 120 candidate thumbnails", info=info)
        always = tool_selection.CORE_TOOL_NAMES | tool_selection.CAPABILITY_ENTRY_NAMES
        expected = [n for n in (t["function"]["name"] for t in self.box) if n in always]
        self.assertTrue(expected, "the fixture holds core tools - otherwise this compares nothing")
        self.assertEqual(sorted(info["core"]), sorted(expected))
        self.assertTrue(set(info["core"]) <= {t["function"]["name"] for t in picked})

    def test_enum_values_stay_out_of_the_embedded_document(self):
        """`_ground_dataset_sets` injects the live set names per request; including them would change
        the doc text on every call and re-embed a tool whose MEANING never changed."""
        t = _tool("dataset_acquire", "acquire images")
        t["function"]["parameters"]["properties"] = {
            "sets": {"type": "array", "items": {"type": "string", "enum": ["met-form-v2", "nga-v1"]}}}
        doc = tool_selection._tool_doc(t)
        self.assertIn("sets", doc)
        self.assertNotIn("met-form-v2", doc)


class ToolSelectionWiringTests(unittest.TestCase):
    """Testing the helper proves nothing about what the model is actually handed."""

    def test_chat_calls_the_selector(self):
        import inspect
        import ollama_client
        src = inspect.getsource(ollama_client.chat)
        self.assertIn("tool_selection.select", src)
        self.assertIn("live_state_tools", src, "the in-flight state signal must be wired in")
        self.assertIn("recent_context", src, "the conversation signal must be wired in")

    def test_core_names_exist_in_the_real_toolbox(self):
        """A typo in CORE_TOOL_NAMES would silently drop the escalation path in production."""
        import tools
        real = {t["function"]["name"] for t in tools.BRAIN_TOOLS}
        missing = tool_selection.CORE_TOOL_NAMES - real
        self.assertFalse(missing, "CORE names not present in BRAIN_TOOLS: %s" % sorted(missing))


if __name__ == "__main__":
    unittest.main()
