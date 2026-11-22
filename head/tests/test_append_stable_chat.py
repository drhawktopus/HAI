"""A chat's next prompt must EXTEND its last one: on hybrid models, any difference before the end of the prompt triggers a full re-read. Keep tools stable, append per-turn notes at the end, replay earlier turns exactly as sent, and use the brain's measured history window."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

import config
import history
import ollama_client


def fresh_held(case):
    """Use a temporary, isolated store to prevent tests from modifying live on-disk tool blocks."""
    d = tempfile.mkdtemp()
    case.addCleanup(shutil.rmtree, d, ignore_errors=True)
    p = mock.patch.object(ollama_client, "TOOL_BLOCK_DIR", d)
    p.start()
    case.addCleanup(p.stop)
    ollama_client._STICKY_TOOLS.clear()
    case.addCleanup(ollama_client._STICKY_TOOLS.clear)


def forget_all_held():
    """A brand-new conversation store: memory and disk both (memory alone now reloads from disk, by design)."""
    ollama_client._STICKY_TOOLS.clear()
    for n in os.listdir(ollama_client.TOOL_BLOCK_DIR) if os.path.isdir(ollama_client.TOOL_BLOCK_DIR) else []:
        os.remove(os.path.join(ollama_client.TOOL_BLOCK_DIR, n))


def tool(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {}}}


def names(ts):
    return [t["function"]["name"] for t in ts]


AVAILABLE = [tool(n) for n in ("search_docs", "fleet_status", "web_search", "remember", "check_host", "catch_up")]


class StickyTools(unittest.TestCase):
    def setUp(self):
        fresh_held(self)
        self.msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "first question"}]

    def test_a_conversation_keeps_its_tools_in_order_and_grows_only_at_the_end(self):
        t1 = ollama_client.sticky_tools("U", self.msgs, [AVAILABLE[1], AVAILABLE[0]], AVAILABLE, 10 ** 6)
        self.assertEqual(names(t1), ["fleet_status", "search_docs"])
        later = self.msgs + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]
        # this turn's selection picks a DIFFERENT set in a different order: the old ones stay where they were,
        # the new one is appended, nothing is dropped or reshuffled
        t2 = ollama_client.sticky_tools("U", later, [AVAILABLE[2], AVAILABLE[0]], AVAILABLE, 10 ** 6)
        self.assertEqual(names(t2), ["fleet_status", "search_docs", "web_search"])
        t3 = ollama_client.sticky_tools("U", later + [{"role": "user", "content": "x"}], [AVAILABLE[0]], AVAILABLE,
                                        10 ** 6)
        self.assertEqual(names(t3), names(t2), "a narrower pick does not shrink the set")

    def test_turn_twos_replay_of_turn_one_with_its_note_is_still_the_same_conversation(self):
        # turn 1 is keyed BEFORE its selection note is appended; turn 2 replays it WITH the note
        t1 = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "doc " * 900}]
        ollama_client.sticky_tools("U", t1, [AVAILABLE[1], AVAILABLE[0]], AVAILABLE, 10 ** 6)
        replayed = ollama_client.note_at_end(list(t1), "[TOOL SELECTION] note")
        t2 = replayed + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]
        got = ollama_client.sticky_tools("U", t2, [AVAILABLE[2]], AVAILABLE, 10 ** 6)
        self.assertEqual(names(got), ["fleet_status", "search_docs", "web_search"], "turn 1's tools, in order, + new")

    def test_another_conversation_or_user_is_its_own_set(self):
        ollama_client.sticky_tools("U", self.msgs, [AVAILABLE[1]], AVAILABLE, 10 ** 6)
        other = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "another chat"}]
        self.assertEqual(names(ollama_client.sticky_tools("U", other, [AVAILABLE[3]], AVAILABLE, 10 ** 6)), ["remember"])
        self.assertEqual(names(ollama_client.sticky_tools("V", self.msgs, [AVAILABLE[4]], AVAILABLE, 10 ** 6)),
                         ["check_host"])

    def test_a_full_set_keeps_its_tools_and_only_a_set_that_cannot_fit_starts_over(self):
        later = self.msgs + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]   # the same chat, on
        ollama_client.sticky_tools("U", self.msgs, AVAILABLE[:2], AVAILABLE, 10 ** 6)
        two = ollama_client.tool_selection._approx_tokens(AVAILABLE[:2])
        t = ollama_client.sticky_tools("U", later, [AVAILABLE[5]], AVAILABLE, two)       # room for exactly the two
        self.assertEqual(names(t), ["search_docs", "fleet_status"], "full: the set stands, the new pick is left out")
        t = ollama_client.sticky_tools("U", later, [AVAILABLE[5]], AVAILABLE, 1)         # the set itself no longer fits
        self.assertEqual(names(t), ["catch_up"], "a set that cannot fit starts over from this turn's pick")
        forget_all_held()
        ollama_client.sticky_tools("U", self.msgs, [AVAILABLE[0], AVAILABLE[1]], AVAILABLE, 10 ** 6)
        t = ollama_client.sticky_tools("U", later, [AVAILABLE[0]], AVAILABLE[:1], 10 ** 6)
        self.assertEqual(names(t), ["search_docs"], "a tool no longer available is not sent")

    def test_two_chats_that_open_with_the_same_words_are_two_conversations(self):
        """A chat identified by id is that chat; a helper call within it (other messages, same id) is distinct."""
        ctx = ollama_client._CALL_CONTEXT
        self.addCleanup(lambda: setattr(ctx, "window_conv", None))
        later = self.msgs + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]
        ctx.window_conv = "U/chat-one"
        ollama_client.sticky_tools("U", self.msgs, [AVAILABLE[1]], AVAILABLE, 10 ** 6)
        ctx.window_conv = "U/chat-two"                                   # the same opening, another chat
        self.assertEqual(names(ollama_client.sticky_tools("U", self.msgs, [AVAILABLE[3]], AVAILABLE, 10 ** 6)), ["remember"])
        ctx.window_conv = "U/chat-one"                                   # each goes on with its own
        self.assertEqual(names(ollama_client.sticky_tools("U", later, [], AVAILABLE, 10 ** 6)), ["fleet_status"])
        helper = [{"role": "system", "content": "Summarise."}, {"role": "user", "content": "the text"}]
        self.assertEqual(ollama_client.sticky_tools("U", helper, [], [], 10 ** 6), [], "a helper call inside the chat")
        self.assertEqual(names(ollama_client.sticky_tools("U", later, [], AVAILABLE, 10 ** 6)), ["fleet_status"],
                         "and it did not touch the chat's own block")

    def test_a_chat_already_under_way_keeps_the_block_it_was_held_under_before_chats_were_keyed_by_id(self):
        ctx = ollama_client._CALL_CONTEXT
        self.addCleanup(lambda: setattr(ctx, "window_conv", None))
        later = self.msgs + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]
        ollama_client.sticky_tools("U", self.msgs, [AVAILABLE[1], AVAILABLE[0]], AVAILABLE, 10 ** 6)     # held by its opening
        ctx.window_conv = "U/chat-one"
        self.assertEqual(names(ollama_client.sticky_tools("U", later, [], AVAILABLE, 10 ** 6)),
                         ["fleet_status", "search_docs"], "no open chat re-reads for the change of key")

    def test_a_first_turn_known_only_by_its_opening_starts_its_own(self):
        """A script or an eval repeats one prompt in separate one-turn calls: the second is not the first one going on."""
        ollama_client.sticky_tools("U", self.msgs, [AVAILABLE[1]], AVAILABLE, 10 ** 6)
        self.assertEqual(names(ollama_client.sticky_tools("U", self.msgs, [AVAILABLE[3]], AVAILABLE, 10 ** 6)), ["remember"])


class GrowsOnlyOnTheSelectorsOwnVerdict(unittest.TestCase):
    """Growth re-reads the whole conversation. An UNSURE turn adds only the strongest tool if its reading is sure."""

    def setUp(self):
        fresh_held(self)
        self.msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "first question"}]
        self.later = self.msgs + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "thanks"}]
        ollama_client.sticky_tools("U", self.msgs, AVAILABLE[:2], AVAILABLE, 10 ** 6)

    def grow(self, picked, **info):
        return names(ollama_client.sticky_tools("U", self.later, picked, AVAILABLE, 10 ** 6, info=info))

    def test_an_unsure_turn_with_nothing_sure_on_its_own_words_keeps_the_block(self):
        self.assertEqual(self.grow(AVAILABLE[2:5], unsure=True, raw_top="web_search", raw_sure=False),
                         ["search_docs", "fleet_status"], "weak picks never re-read the conversation")

    def test_an_unsure_turn_that_changed_the_subject_adds_its_strongest_tool_only(self):
        self.assertEqual(self.grow(AVAILABLE[2:5], unsure=True, raw_top="check_host", raw_sure=True),
                         ["search_docs", "fleet_status", "check_host"])

    def test_a_core_tool_joins_a_held_block_even_on_an_unsure_turn(self):
        """Core tools are always included and appended at the end, even on unsure turns."""
        got = self.grow(AVAILABLE[2:5], unsure=True, raw_top="web_search", raw_sure=False, core=["catch_up"])
        self.assertEqual(got, ["search_docs", "fleet_status", "catch_up"], "the weak picks stay out; the core tool joins")
        again = self.grow([], unsure=True, raw_sure=False, core=["catch_up", "search_docs"])
        self.assertEqual(again, ["search_docs", "fleet_status", "catch_up"], "already held: nothing moves or repeats")

    def test_a_sure_turn_adds_every_pick_and_turn_one_takes_its_picks_whatever_the_verdict(self):
        self.assertEqual(self.grow(AVAILABLE[2:4], unsure=False),
                         ["search_docs", "fleet_status", "web_search", "remember"], "positive control: still dynamic")
        forget_all_held()
        first = names(ollama_client.sticky_tools("U", self.msgs, AVAILABLE[2:4], AVAILABLE, 10 ** 6,
                                                 info={"unsure": True, "raw_sure": False}))
        self.assertEqual(first, ["web_search", "remember"], "nothing held yet: the turn's own selection stands")


class ThawThenSeason(unittest.TestCase):
    """Held blocks persist across restarts with their original text; updated wording is applied exactly once upon completion."""

    def setUp(self):
        fresh_held(self)
        self.msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "first question"}]
        self.later = self.msgs + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]

    def test_a_restart_keeps_the_conversations_block(self):
        ollama_client.sticky_tools("U", self.msgs, AVAILABLE[:2], AVAILABLE, 10 ** 6)
        ollama_client._STICKY_TOOLS.clear()                  # a new Kirk process: memory gone, the disk is not
        got = names(ollama_client.sticky_tools("U", self.later, [AVAILABLE[4]], AVAILABLE, 10 ** 6,
                                               info={"unsure": True, "raw_sure": False}))
        self.assertEqual(got, ["search_docs", "fleet_status"], "the held block, not a fresh pick (no re-read)")

    def test_changed_wording_keeps_the_held_text_and_is_announced_once_at_the_end(self):
        ollama_client.sticky_tools("U", self.msgs, AVAILABLE[:2], AVAILABLE, 10 ** 6)
        reworded = [dict(t) for t in AVAILABLE]
        reworded[0] = {"type": "function", "function": {"name": "search_docs", "description": "NEW wording",
                                                        "parameters": {}}}
        ollama_client._STICKY_TOOLS.clear()                  # and across a restart (the deploy that reworded it)
        info = {"unsure": True, "raw_sure": False}
        sent = ollama_client.sticky_tools("U", self.later, [], reworded, 10 ** 6, info=info)
        self.assertEqual(sent[0], AVAILABLE[0], "THAW: the block is resent exactly as the conversation was sent it")
        self.assertIn("NEW wording", info["update_note"])
        self.assertTrue(info["update_note"].startswith(ollama_client.TOOL_UPDATE_NOTE))
        again = {"unsure": True, "raw_sure": False}
        ollama_client.sticky_tools("U", self.later + [{"role": "user", "content": "x"}], [], reworded, 10 ** 6, info=again)
        self.assertNotIn("update_note", again, "SEASON once: the note rides that turn's message and stays in the chat")

    def test_chat_puts_the_update_at_the_end_of_the_turn_and_sends_the_held_text(self):
        seen = []

        def once(host, model, messages, tools_list, *a, **k):
            seen.append(([dict(m) for m in messages], list(tools_list)))   # a snapshot: chat() appends its answer after
            return {"message": {"role": "assistant", "content": "an answer long enough"}, "prompt_eval_count": 9,
                    "eval_count": 3}
        reworded = [dict(t) for t in AVAILABLE]
        reworded[0] = {"type": "function", "function": {"name": "search_docs", "description": "NEW wording",
                                                        "parameters": {}}}
        for pool, turn in ((AVAILABLE, self.msgs), (reworded, self.later)):     # the first turn, then the chat going on
            with mock.patch.object(ollama_client, "_chat_once", once), \
                    mock.patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                    mock.patch.object(ollama_client.tool_selection, "select",
                                      lambda tl, *a, **k: (tl[:2], None)), \
                    mock.patch.object(ollama_client, "can_think", lambda h, m: False):
                ollama_client.chat("http://h", "m", [dict(m) for m in turn], list(pool), num_ctx=100000)
        (_m1, t1), (m2, t2) = seen[0], seen[-1]
        self.assertEqual(t2[0], t1[0], "the second turn sends the FIRST turn's text for search_docs")
        last_user = [m for m in m2 if m.get("role") == "user"][-1]
        self.assertIn("NEW wording", str(last_user.get("content")), "the update rides the end of the turn's own message")
        self.assertNotIn("NEW wording", str(m2[0].get("content")), "never above the conversation")


class GrowthIsSeasonedNotBaked(unittest.TestCase):
    """Tools are appended to the end of the message list, never inserted at the beginning."""

    def setUp(self):
        fresh_held(self)
        self.msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "first question"}]
        self.later = self.msgs + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]

    def turn(self, picked, msgs, season):
        info = {}
        out = ollama_client.sticky_tools("U", msgs, picked, AVAILABLE, 10 ** 6, info=info, season=season)
        return names(out), info

    def test_the_block_stays_and_the_new_tool_rides_at_the_end_once(self):
        self.turn(AVAILABLE[:2], self.msgs, True)
        block, info = self.turn([AVAILABLE[2]], self.later, True)
        self.assertEqual(block, ["search_docs", "fleet_status"], "the block above the conversation is not touched")
        self.assertTrue(info["season_note"].startswith(ollama_client.TOOL_ADDED_NOTE))
        self.assertIn('"web_search"', info["season_note"])
        block, info = self.turn([AVAILABLE[2]], self.later + [{"role": "user", "content": "again"}], True)
        self.assertEqual(block, ["search_docs", "fleet_status"])
        self.assertNotIn("season_note", info, "described once: the conversation already holds it")
        self.assertEqual(ollama_client.seasoned_tool_names("U", self.msgs), ["web_search"])

    def test_an_engine_that_cannot_season_takes_them_into_its_block(self):
        self.turn(AVAILABLE[:2], self.msgs, True)
        self.turn([AVAILABLE[2]], self.later, True)
        block, info = self.turn([], self.later, False)
        self.assertEqual(block, ["search_docs", "fleet_status", "web_search"], "a moved conversation can still call it")
        self.assertEqual(ollama_client.seasoned_tool_names("U", self.msgs), [])

    def test_a_new_conversation_is_one_read_whatever_the_engine(self):
        block, info = self.turn(AVAILABLE[:3], self.msgs, True)
        self.assertEqual(block, ["search_docs", "fleet_status", "web_search"])
        self.assertNotIn("season_note", info)

    def test_the_engine_is_asked_only_when_a_held_block_would_change(self):
        asked = []
        self.turn(AVAILABLE[:2], self.msgs, lambda: asked.append(1) or True)
        self.turn(AVAILABLE[:2], self.later, lambda: asked.append(1) or True)
        self.assertEqual(asked, [], "no growth, no question (a box that cannot be reached costs nothing)")
        self.turn([AVAILABLE[3]], self.later, lambda: asked.append(1) or True)
        self.assertEqual(asked, [1])

    def test_an_index_outage_adds_nothing_to_a_held_conversation(self):
        self.turn(AVAILABLE[:2], self.msgs, True)
        for season in (True, False):
            info = {"outage": True}
            out = ollama_client.sticky_tools("U", self.later, list(AVAILABLE), AVAILABLE, 10 ** 6, info=info, season=season)
            self.assertEqual(names(out), ["search_docs", "fleet_status"], "the block it has, whatever the engine")
            self.assertNotIn("season_note", info)
            self.assertEqual(info["outage_note"], ollama_client.TOOL_OUTAGE_NOTE)
        self.assertEqual(ollama_client.seasoned_tool_names("U", self.msgs), [])
        forget_all_held()
        fresh = {"outage": True}
        out = ollama_client.sticky_tools("U", self.msgs, list(AVAILABLE), AVAILABLE, 10 ** 6, info=fresh, season=True)
        self.assertEqual(len(out), len(AVAILABLE), "a NEW conversation still gets the whole pool (an outage strips nothing)")
        self.assertNotIn("outage_note", fresh)

    def test_which_engines_season(self):
        def show(modelfile=None, fail=False):
            def post(url, json=None, timeout=None):
                if fail:
                    raise ollama_client.httpx.ConnectError("no")
                return mock.Mock(raise_for_status=lambda: None, json=lambda: {"modelfile": modelfile})
            return post
        import scheduler
        cases = (("FROM x\nRENDERER qwen3.8\nPARSER qwen3.5\n", True), ("FROM x\nPARSER qwen3-coder\n", True),
                 ("FROM x\nTEMPLATE {{ .Prompt }}\n", False), ("FROM x\nPARSER harmony\n", False))
        cards = {"b%d" % i: {"url": "http://box%d:11434" % i} for i in range(len(cases))}
        cards["gone"] = {"url": "http://gone:11434"}
        with mock.patch.object(scheduler, "CARDS", cards):
            for i, (mf, want) in enumerate(cases):
                with mock.patch.object(ollama_client.httpx, "post", show(mf)), \
                        mock.patch.dict(ollama_client._CAN_SEASON, {}, clear=True):
                    self.assertIs(ollama_client.can_season("http://box%d:11434" % i, "m"), want, mf)
            posts = []
            with mock.patch.object(ollama_client.httpx, "post",
                                   lambda *a, **k: posts.append(1) or show(fail=True)(*a, **k)), \
                    mock.patch.dict(ollama_client._CAN_SEASON, {}, clear=True), \
                    mock.patch.dict(ollama_client._SEASON_QUIET, {}, clear=True):
                self.assertIs(ollama_client.can_season("http://gone:11434", "m"), False, "unknown: the old growth")
                self.assertIs(ollama_client.can_season("http://gone:11434", "m"), False)
            self.assertEqual(len(posts), 1, "one wait a minute, not one a turn")
        with mock.patch.object(ollama_client.httpx, "post", lambda *a, **k: self.fail("asked a box the fleet does not list")):
            self.assertIs(ollama_client.can_season("http://h", "m"), False, "not a fleet box: unproven, nothing asked")
        self.assertIs(ollama_client.can_season(config.TOLKIEN_WINDOW_URL, "m"), False, "rigby is unproven")

    def test_a_seasoned_calls_text_arguments_are_typed_by_its_schema(self):
        t = {"type": "function", "function": {"name": "x", "parameters": {"type": "object", "properties": {
            "n": {"type": "integer"}, "on": {"type": "boolean"}, "ids": {"type": "array"}, "s": {"type": "string"},
            "f": {"type": "number"}}}}}
        got = ollama_client._typed_args(t, {"n": "3", "on": "true", "ids": "[1, 2]", "s": "07", "f": "2.5"})
        self.assertEqual(got, {"n": 3, "on": True, "ids": [1, 2], "s": "07", "f": 2.5})
        self.assertEqual(ollama_client._typed_args(t, {"n": "three"}), {"n": "three"}, "unparseable: the tool says why")

    def test_chat_keeps_the_block_and_the_seasoned_call_runs_typed(self):
        pool = [tool("search_docs"), tool("fleet_status"),
                {"type": "function", "function": {"name": "check_host", "description": "d", "parameters": {
                    "type": "object", "properties": {"minutes": {"type": "integer"}}}}}]
        sent, ran = [], []
        state = {"n": 0}

        def once(host, model, messages, tools_list, *a, **k):
            sent.append(([dict(m) for m in messages], [_t["function"]["name"] for _t in tools_list]))
            state["n"] += 1
            if state["n"] == 2:     # the second turn calls the tool it was only DESCRIBED, its argument as text
                return {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "check_host", "arguments": {"minutes": "5"}}}]}}
            return {"message": {"role": "assistant", "content": "an answer long enough"}, "prompt_eval_count": 9,
                    "eval_count": 3}
        picks = iter([["search_docs", "fleet_status"], ["check_host"], ["check_host"]])

        def select(tl, *a, **k):
            want = next(picks, [])
            return [t for t in tl if t["function"]["name"] in want], None
        for turn in (self.msgs, self.later):                                    # the first turn, then the chat going on
            with mock.patch.object(ollama_client, "_chat_once", once), \
                    mock.patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                    mock.patch.object(ollama_client.tools, "run_tool",
                                      lambda fn, args, *a, **k: ran.append((fn, args)) or "ok"), \
                    mock.patch.object(ollama_client.tool_selection, "select", select), \
                    mock.patch.object(ollama_client, "can_season", lambda h, m: True), \
                    mock.patch.object(ollama_client, "can_think", lambda h, m: False):
                ollama_client.chat("http://h", "m", [dict(m) for m in turn], list(pool), num_ctx=100000)
        self.assertEqual(sent[1][1], ["search_docs", "fleet_status"], "turn 2's block is turn 1's")
        last_user = [m for m in sent[1][0] if m["role"] == "user"][-1]["content"]
        self.assertIn(ollama_client.TOOL_ADDED_NOTE.strip(), last_user)
        self.assertIn('"check_host"', last_user)
        self.assertEqual(ran, [("check_host", {"minutes": 5})], "called by name, typed by Kirk")


class TheMasterStart(unittest.TestCase):
    """The 'core' block is the stable, always-offered base for new chats; message-specific picks are appended at the end, not mixed into the core."""

    CORE = ["search_docs", "fleet_status"]

    def setUp(self):
        fresh_held(self)
        self.msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "first question"}]

    def new_chat(self, picked, user="U", season=True, master=True, **extra):
        info = dict({"core": list(self.CORE)}, **extra)
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "first question from %s" % user}]
        out = ollama_client.sticky_tools(user, msgs, picked, AVAILABLE, 10 ** 6, info=info, season=season, master=master)
        return names(out), info

    def test_every_new_chat_gets_the_same_block_and_its_own_picks_at_the_end(self):
        one, info1 = self.new_chat([AVAILABLE[2], AVAILABLE[0]], user="A")           # web_search + a core tool
        two, info2 = self.new_chat([AVAILABLE[4], AVAILABLE[3]], user="B")           # check_host + remember
        self.assertEqual((one, two), (self.CORE, self.CORE), "the block is the core, in the pool's order, whatever was picked")
        self.assertEqual((info1["master_tools"], info2["master_tools"]), (2, 2), "Kirk tells the window all of it is shared")
        self.assertIn('"web_search"', info1["season_note"])
        self.assertNotIn('"search_docs"', info1["season_note"], "a core tool is in the block, not described again")
        self.assertIn('"check_host"', info2["season_note"])
        self.assertIn('"remember"', info2["season_note"])

    def test_the_next_turn_keeps_that_block_and_does_not_describe_the_picks_twice(self):
        self.new_chat([AVAILABLE[2]], user="A")
        later = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "first question from A"},
                 {"role": "assistant", "content": "a"}, {"role": "user", "content": "next"}]
        info = {"core": list(self.CORE)}
        out = ollama_client.sticky_tools("A", later, [AVAILABLE[2]], AVAILABLE, 10 ** 6, info=info, season=True, master=True)
        self.assertEqual(names(out), self.CORE)
        self.assertNotIn("season_note", info)
        self.assertFalse(info.get("master_tools"), "only a NEW chat is a master block")

    def test_where_there_is_no_master(self):
        picked = [AVAILABLE[2], AVAILABLE[0]]
        for why, kw in (("an engine that cannot be called by description", dict(season=False)),
                        ("a job: its block becomes its coding set", dict(master=False)),
                        ("an index outage: the list is not a pick", dict(outage=True))):
            forget_all_held()
            block, info = self.new_chat(picked, **kw)
            self.assertEqual(block[0], "web_search", why)
            self.assertNotIn("season_note", info, why)
            self.assertFalse(info.get("master_tools"), why)

    def test_a_picked_person_renderer_stays_in_the_block(self):
        """The one regression the short-list test found (the 9B: a real person's picture went to generate_image 5 of 5
        where today's shape chose render_person 3 of 5) is on the consent path."""
        pool = AVAILABLE + [tool("render_person"), tool("generate_image")]
        info = {"core": list(self.CORE)}
        out = ollama_client.sticky_tools("P", self.msgs, [pool[-2], pool[-1]], pool, 10 ** 6, info=info, season=True,
                                         master=True)
        self.assertEqual(names(out), self.CORE + ["render_person"])
        self.assertIn('"generate_image"', info["season_note"])
        self.assertEqual(info["master_tools"], 0, "not the shared block: this chat reads cold, as it does today")

    def test_chat_sends_no_seed_and_tells_the_window_the_block_is_shared(self):
        """Only the PROMPT is shared. Nothing on Kirk's side of a chat turn sets a seed, so each request rolls its own."""
        seen = {}

        def once(host, model, messages, tools_list, num_ctx, keep_alive, timeout=None, think=False):
            seen.update(tools=[t["function"]["name"] for t in tools_list], shared=ollama_client._CALL_CONTEXT.master_tools,
                        last=[m for m in messages if m["role"] == "user"][-1]["content"])
            return {"message": {"role": "assistant", "content": "an answer long enough"}, "prompt_eval_count": 9,
                    "eval_count": 3}

        def select(tl, *a, info=None, **k):
            info.update(core=list(self.CORE))
            return [t for t in tl if t["function"]["name"] in ("web_search", "search_docs")], None
        with mock.patch.object(ollama_client, "_chat_once", once), \
                mock.patch.object(ollama_client.tools, "tools_for", lambda tl, u: tl), \
                mock.patch.object(ollama_client.tool_selection, "select", select), \
                mock.patch.object(ollama_client, "can_season", lambda h, m: True), \
                mock.patch.object(ollama_client, "can_think", lambda h, m: False):
            ollama_client.chat("http://h", "m", [dict(m) for m in self.msgs], list(AVAILABLE), num_ctx=100000)
        self.assertEqual((seen["tools"], seen["shared"]), (self.CORE, 2))
        self.assertIn('"web_search"', seen["last"])
        import contextlib
        bodies = []

        @contextlib.contextmanager
        def door(url, body, clock_s, watch=None):
            bodies.append(body)
            raise RuntimeError("captured: the request as it would be sent")
            yield
        with mock.patch.object(ollama_client, "_open_chat_stream", door):
            try:
                ollama_client._chat_once_raw("http://h", "m", [dict(m) for m in self.msgs], [], 1000, "5m")
            except Exception:
                pass
        self.assertTrue(bodies, "the request was built")
        self.assertNotIn("seed", bodies[0])
        self.assertNotIn("seed", bodies[0].get("options") or {}, "a chat request carries no seed: the engine draws one per request")


class NotesAtTheEnd(unittest.TestCase):
    def test_a_note_rides_on_the_turns_own_message_not_near_the_top(self):
        m = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a"},
             {"role": "user", "content": "q2"}]
        out = ollama_client.note_at_end(list(m), "[TOOL SELECTION] note")
        self.assertEqual([x["role"] for x in out], ["system", "user", "assistant", "user"], "no message inserted")
        self.assertEqual(out[1]["content"], "q1", "earlier turns untouched")
        self.assertEqual(out[3]["content"], "q2\n\n[TOOL SELECTION] note")
        self.assertEqual(m[3]["content"], "q2", "the caller's message dict is not mutated")
        none = ollama_client.note_at_end([{"role": "system", "content": "SYS"}], "n")
        self.assertEqual(none[1], {"role": "system", "content": "n"}, "no user message: the old placement")

    def test_the_chat_loop_and_the_tasks_note_use_it(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "ollama_client.py"), encoding="utf-8").read()
        self.assertIn("messages = note_at_end(messages, _select_note)", src)
        self.assertIn("tools_list = sticky_tools(user, messages, tools_list, _available,", src)
        app = open(os.path.join(here, "app.py"), encoding="utf-8").read()
        self.assertIn("ollama_client.note_at_end(messages, (", app)


class HistoryReplaysWhatWasSent(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        p = mock.patch.object(history, "HISTORY_DIR", __import__("pathlib").Path(self.d))
        p.start()
        self.addCleanup(p.stop)

    def test_the_sent_text_is_kept_beside_the_plain_text_and_sized_by(self):
        history.append("U", "plain question", "answer", user_sent="plain question\n\n[notes ...]")
        msgs = history.load("U")
        self.assertEqual(msgs[0]["content"], "plain question", "the transcript shows the plain text")
        self.assertEqual(msgs[0]["sent"], "plain question\n\n[notes ...]")
        history.append("U", "same", "answer2", user_sent="same")
        self.assertNotIn("sent", history.load("U")[2], "nothing extra when the model got exactly the text")
        _s, ctx = history.context_messages("U")
        self.assertEqual(ctx[0]["sent"], "plain question\n\n[notes ...]")

    def test_the_budget_follows_the_brains_measured_window(self):
        self.assertEqual(config.CONTEXT_CHAR_BUDGET, int(os.getenv("CONTEXT_CHAR_BUDGET", str(2 * config.BRAIN_NUM_CTX))))
        self.assertGreater(config.CONTEXT_CHAR_BUDGET, 18000, "the old fixed 18,000 chars made chat forget at ~4.5k tok")


if __name__ == "__main__":
    unittest.main()
