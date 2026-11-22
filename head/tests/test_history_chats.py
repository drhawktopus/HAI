"""Project #18 Phase 1 — multi-chat history: migration, index, switching, isolation.

The invariants that matter:
  * legacy single-thread users migrate UNTOUCHED into chat 'default' (idempotent);
  * chats are isolated — append/reset/context in one chat never leaks into another;
  * chat=None means the active chat everywhere, so every pre-#18 call site behaves identically;
  * 'New chat' preserves the old thread (the whole point of the feature).
"""
import json
import os
import pathlib
import tempfile
import unittest

import config
import history


class _ChatBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = history.HISTORY_DIR
        history.HISTORY_DIR = pathlib.Path(self.tmp)

    def tearDown(self):
        history.HISTORY_DIR = self._orig


class MigrationTests(_ChatBase):
    def test_legacy_thread_migrates_untouched_into_default(self):
        legacy = [{"role": "user", "content": "old q"}, {"role": "assistant", "content": "old a"}]
        history.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        (history.HISTORY_DIR / "Riley.json").write_text(json.dumps(legacy), encoding="utf-8")
        (history.HISTORY_DIR / "Riley.summary.json").write_text(
            json.dumps({"summary": "s", "covered_messages": 2, "compactions": 1}), encoding="utf-8")
        self.assertEqual(history.load("Riley"), legacy)                      # content intact
        self.assertEqual(history.load_summary("Riley")["compactions"], 1)    # summary followed
        idx = history.list_chats("Riley")
        self.assertEqual(idx["active"], "default")
        self.assertEqual([c["id"] for c in idx["chats"]], ["default"])
        self.assertFalse((history.HISTORY_DIR / "Riley.json").exists())      # moved, not copied

    def test_fresh_user_gets_default_chat(self):
        idx = history.list_chats("Morgan")
        self.assertEqual(idx["active"], "default")
        self.assertEqual(history.load("Morgan"), [])

    def test_migration_is_idempotent(self):
        history.list_chats("Jordan")
        history.new_chat("Jordan")
        idx_before = history.list_chats("Jordan")
        history.list_chats("Jordan")   # re-touch must not re-migrate or reset the index
        self.assertEqual(history.list_chats("Jordan"), idx_before)


class ChatLifecycleTests(_ChatBase):
    def test_new_chat_preserves_the_old_thread(self):
        history.append("Riley", "first question", "first answer")
        old_id = history.list_chats("Riley")["active"]
        entry = history.new_chat("Riley")
        self.assertEqual(history.list_chats("Riley")["active"], entry["id"])
        self.assertEqual(history.load("Riley"), [])                     # new chat is empty
        self.assertEqual(len(history.load("Riley", old_id)), 2)         # old thread SAFE

    def test_switch_and_isolation(self):
        history.append("Riley", "chat A question", "A answer")
        a = history.list_chats("Riley")["active"]
        b = history.new_chat("Riley")["id"]
        history.append("Riley", "chat B question", "B answer")
        self.assertEqual(history.switch_chat("Riley", a), a)
        self.assertIn("chat A", history.load("Riley")[0]["content"])
        summary, msgs = history.context_messages("Riley")               # context = active chat only
        self.assertTrue(all("chat B" not in m["content"] for m in msgs))
        self.assertEqual(history.switch_chat("Riley", "nope"), None)    # unknown id refused

    def test_reset_clears_one_chat_only(self):
        history.append("Riley", "keep me", "kept")
        a = history.list_chats("Riley")["active"]
        b = history.new_chat("Riley")["id"]
        history.append("Riley", "wipe me", "wiped")
        history.reset("Riley")                                          # resets ACTIVE (b) only
        self.assertEqual(history.load("Riley", b), [])
        self.assertEqual(len(history.load("Riley", a)), 2)

    def test_title_set_from_first_message_and_clears_on_reset(self):
        history.append("Riley", "  How do   rubrics work?  ", "like this")
        chats = history.list_chats("Riley")["chats"]
        self.assertEqual(chats[0]["title"], "How do rubrics work?")
        history.reset("Riley")
        self.assertEqual(history.list_chats("Riley")["chats"][0]["title"], "")

    def test_resolve_chat_falls_back_to_active(self):
        a = history.list_chats("Riley")["active"]
        self.assertEqual(history.resolve_chat("Riley", None), a)
        self.assertEqual(history.resolve_chat("Riley", "bogus"), a)
        b = history.new_chat("Riley")["id"]
        self.assertEqual(history.resolve_chat("Riley", a), a)           # explicit valid id honored

    def test_list_chats_most_recent_first(self):
        history.append("Riley", "older", "x")
        b = history.new_chat("Riley")["id"]
        history.append("Riley", "newer", "y")
        chats = history.list_chats("Riley")["chats"]
        self.assertEqual(chats[0]["id"], b)


class ManagementTests(_ChatBase):
    """#18 P2: rename, archive (with active-chat handoff), soft-delete to .trash."""

    def test_rename_clips_and_collapses(self):
        history.append("F", "q", "a")
        cid = history.list_chats("F")["active"]
        c = history.rename_chat("F", cid, "  My   Chat  " + "x" * 100)
        self.assertTrue(c["title"].startswith("My Chat"))
        self.assertLessEqual(len(c["title"]), 60)
        self.assertIsNone(history.rename_chat("F", "bogus", "t"))

    def test_archive_hides_from_default_list_and_hands_off_active(self):
        history.append("F", "keep", "a")
        a = history.list_chats("F")["active"]
        b = history.new_chat("F")["id"]
        history.archive_chat("F", b)                       # archive the ACTIVE chat
        idx = history.list_chats("F")
        self.assertEqual(idx["active"], a)                  # handed off to the live chat
        self.assertNotIn(b, [c["id"] for c in idx["chats"]])
        self.assertIn(b, [c["id"] for c in
                          history.list_chats("F", include_archived=True)["chats"]])
        history.archive_chat("F", b, archived=False)        # unarchive brings it back
        self.assertIn(b, [c["id"] for c in history.list_chats("F")["chats"]])

    def test_archiving_last_live_chat_creates_a_fresh_one(self):
        a = history.list_chats("F")["active"]
        history.archive_chat("F", a)
        idx = history.list_chats("F", include_archived=True)
        self.assertEqual(len(idx["chats"]), 2)              # fresh landing chat created
        self.assertNotEqual(idx["active"], a)

    def test_active_archived_chat_still_listed(self):
        a = history.list_chats("F")["active"]
        history.new_chat("F")
        history.archive_chat("F", a)
        history.switch_chat("F", a)                         # viewing an archived chat is allowed
        self.assertIn(a, [c["id"] for c in history.list_chats("F")["chats"]])

    def test_delete_moves_to_trash_and_reassigns(self):
        history.append("F", "trash me", "a")
        a = history.list_chats("F")["active"]
        self.assertEqual(history.delete_chat("F", a), a)
        idx = history.list_chats("F", include_archived=True)
        self.assertNotIn(a, [c["id"] for c in idx["chats"]])
        trash = list((history.HISTORY_DIR / "F" / ".trash").glob("*.json"))
        self.assertTrue(trash)                              # recoverable, never hard-deleted
        self.assertIsNone(history.delete_chat("F", "bogus"))


class SearchTests(_ChatBase):
    """#18 P3: plain-text search — own chats only, archived in, trash out, bounded output."""

    def test_search_finds_snippets_across_chats(self):
        history.append("F", "tell me about rubrics", "rubrics are grading guides")
        a = history.list_chats("F")["active"]
        history.new_chat("F")
        history.append("F", "what is a mannequin lab", "a simulation lab")
        res = history.search_chats("F", "RUBRIC")           # case-insensitive
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["chat_id"], a)
        self.assertTrue(any("rubric" in m["snippet"].lower() for m in res[0]["matches"]))

    def test_search_includes_archived_excludes_trash_and_empty_query(self):
        history.append("F", "needle here", "a")
        a = history.list_chats("F")["active"]
        history.archive_chat("F", a)
        self.assertEqual(history.search_chats("F", "needle")[0]["chat_id"], a)
        history.delete_chat("F", a)
        self.assertEqual(history.search_chats("F", "needle"), [])
        self.assertEqual(history.search_chats("F", "   "), [])

    def test_matches_capped_at_three_per_chat(self):
        for i in range(5):
            history.append("F", f"needle {i}", f"answer needle {i}")
        res = history.search_chats("F", "needle")
        self.assertEqual(len(res[0]["matches"]), 3)


class PinAndExtrasTests(_ChatBase):
    """#18 P4: per-chat pins (priority boost, cap 10) + trail extras on deliberative answers."""

    def test_pin_toggle_and_cap(self):
        history.append("F", "q", "a")
        cid = history.list_chats("F")["active"]
        self.assertEqual(history.pin_doc("F", cid, "rubric.pdf"), ["rubric.pdf"])
        self.assertEqual(history.chat_pins("F"), ["rubric.pdf"])
        self.assertEqual(history.pin_doc("F", cid, "rubric.pdf", pinned=False), [])
        for i in range(12):
            history.pin_doc("F", cid, f"doc{i}.pdf")
        self.assertEqual(len(history.chat_pins("F")), 10)          # capped
        self.assertIsNone(history.pin_doc("F", cid, "   "))        # empty source refused

    def test_pins_are_per_chat(self):
        a = history.list_chats("F")["active"]
        history.pin_doc("F", a, "one.pdf")
        history.new_chat("F")
        self.assertEqual(history.chat_pins("F"), [])               # new chat: no pins
        self.assertEqual(history.chat_pins("F", a), ["one.pdf"])

    def test_extras_persist_on_assistant_message_only(self):
        history.append("F", "hard q", "deep answer",
                       extras={"work_journal": [{"phase": "first_pass"}],
                               "transcript": [{"tool": "web_search"}],
                               "role": "hacker", "content": "nope", "empty": []})
        msgs = history.load("F")
        self.assertEqual(msgs[0], {"role": "user", "content": "hard q"})     # user msg untouched
        a = msgs[1]
        self.assertEqual((a["role"], a["content"]), ("assistant", "deep answer"))  # protected keys
        self.assertEqual(a["work_journal"], [{"phase": "first_pass"}])
        self.assertEqual(a["transcript"], [{"tool": "web_search"}])
        self.assertNotIn("empty", a)                               # empty values dropped


class AutoTitleTests(_ChatBase):
    """#18 auto-naming: the guarded title upgrade — manual renames always win; junk suggestions
    never land."""

    def test_guarded_set_respects_manual_rename(self):
        history.append("F", "first question about rubrics", "answer")
        cid = history.list_chats("F")["active"]
        placeholder = history.title_clip("first question about rubrics")
        # manual rename lands first -> the auto-titler's guarded set must refuse
        history.rename_chat("F", cid, "My Rubric Chat")
        self.assertFalse(history.set_title_if_unchanged("F", cid, "Generated Title",
                                                        only_if_current=placeholder))
        self.assertEqual(history.list_chats("F")["chats"][0]["title"], "My Rubric Chat")
        # fresh chat, placeholder untouched -> upgrade lands
        history.new_chat("F")
        history.append("F", "another question", "a")
        cid2 = history.list_chats("F")["active"]
        ph2 = history.title_clip("another question")
        self.assertTrue(history.set_title_if_unchanged("F", cid2, "Generated Title",
                                                       only_if_current=ph2))
        self.assertEqual(history.list_chats("F")["chats"][0]["title"], "Generated Title")
        self.assertFalse(history.set_title_if_unchanged("F", cid2, "   ", only_if_current=None))

    def test_sanitize_title(self):
        import app
        self.assertEqual(app._sanitize_title('"Rubric Grading Basics."'), "Rubric Grading Basics")
        self.assertEqual(app._sanitize_title("Title: Sim Lab Questions"), "Sim Lab Questions")
        self.assertEqual(app._sanitize_title("First line\nSecond line"), "First line")
        self.assertIsNone(app._sanitize_title("   "))
        self.assertIsNone(app._sanitize_title('""'))
        self.assertLessEqual(len(app._sanitize_title("x" * 200)), 60)

    def test_auto_title_wired_fail_soft(self):
        app_src = open("app.py", encoding="utf-8").read()
        body = app_src.split("def _persist(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("len(msgs) == 2", body)                    # first exchange only
        self.assertIn("_auto_title_worker", body)
        self.assertIn("daemon=True", body)                       # never blocks the answer
        worker = app_src.split("def _auto_title_worker(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("set_title_if_unchanged", worker)
        self.assertIn("only_if_current=placeholder", worker)     # manual rename wins
        self.assertIn("except Exception", worker)                # fail-soft


class P4EndpointTests(unittest.TestCase):
    def test_pin_export_wired_boost_applied(self):
        app_src = open("app.py", encoding="utf-8").read()
        for route, fn in (('@app.post("/chats/pin")', "chats_pin"),
                          ('@app.get("/chats/export")', "chats_export")):
            self.assertIn(route, app_src)
            body = app_src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
            self.assertIn("_enforce_user_param(request, user)", body)
        self.assertIn("config.RAG_PIN_BOOST", app_src)             # boost wired in retrieval
        self.assertIn("history.chat_pins(q.user", app_src)
        # The persisted trail is the COMPACT copy (the full one stays in memory for the critics).
        self.assertIn('extras = {"work_journal": state.journal(), "transcript": ollama_client.compact_trail(_tr_steps)}',
                      app_src)
        self.assertIn("_persist(q, task_text, final_answer, extras=extras)", app_src)
        import config
        self.assertGreater(config.RAG_PIN_BOOST, 0)


class ManagementEndpointTests(unittest.TestCase):
    def test_p2_p3_endpoints_wired_and_enforced(self):
        app_src = open("app.py", encoding="utf-8").read()
        for route, fn in (('@app.post("/chats/rename")', "chats_rename"),
                          ('@app.post("/chats/archive")', "chats_archive"),
                          ('@app.post("/chats/delete")', "chats_delete"),
                          ('@app.get("/chats/search")', "chats_search")):
            self.assertIn(route, app_src)
            body = app_src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
            self.assertIn("_enforce_user_param(request, user)", body)


class EndpointWiringTests(unittest.TestCase):
    def test_chat_endpoints_identity_enforced_and_wired(self):
        app_src = open("app.py", encoding="utf-8").read()
        for route in ('@app.get("/chats")', '@app.post("/chats/new")', '@app.post("/chats/switch")'):
            self.assertIn(route, app_src)
        for fn in ("chats_list", "chats_new", "chats_switch"):
            body = app_src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
            self.assertIn("_enforce_user_param(request, user)", body)      # no cross-user reach
        self.assertIn('chat_id:(activeChat||undefined)', app_src)          # /query carries the chat
        self.assertIn('history.append(q.user, user_text, answer, chat=getattr(q, "chat_id", None)',
                      app_src)   # the persist call carries the chat (assert the fragment, not the whole line)


if __name__ == "__main__":
    unittest.main()
