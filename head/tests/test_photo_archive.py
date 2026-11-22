"""Uses temp dirs and a fake desk. Never touches the live share or state."""
import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import history
import ollama_client
import photo_archive as PA
import tools

DAY = 86400.0


def _png(colour=(10, 120, 200)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(b, format="PNG")
    return b.getvalue()


class FakeDesk:
    """p20_order + _order_status_view + FactoryOrderReq, as app offers them."""

    def __init__(self, status="queued", host="mothership"):
        self.orders, self.status, self.host = [], status, host

    class FactoryOrderReq(dict):
        def __init__(self, **kw):
            super().__init__(**kw)

    def p20_order(self, req):
        self.orders.append(dict(req))
        return {"ok": True, "steps": [{"id": "ord%d" % len(self.orders), "host": self.host}]}

    def _order_status_view(self, oid):
        return {"ok": True, "id": oid, "status": self.status, "host": self.host}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.gen = t / "gen"
        for p in (patch.object(history, "HISTORY_DIR", t / "history"),
                  patch.object(PA, "CLOCK", str(t / "pa" / "clock.json")),
                  patch.object(PA, "PENDING", str(t / "pa" / "pending.json")),
                  patch.object(PA, "ORDER_LOCAL", str(t / "share")),
                  patch.object(PA, "_note", lambda message, dm=False: self.said.append((message, dm))),
                  patch("comfy_client.uploads_dir", lambda user: self.gen / user)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, ollama_client._CALL_CONTEXT, "window_conv", None)
        self.addCleanup(self.tmp.cleanup)
        self.said = []
        self.user = "Avery"
        self.chats = [history.new_chat(self.user)["id"] for _ in range(7)]
        history.delete_chat(self.user, "default")     # a first lookup makes a 'default' chat; the tests name every chat
        self.assertEqual(len(history._load_index(self.user)["chats"]), 7)
        self.age_chats()

    def age_chats(self, order=None):
        """Chat i was last used i days ago (chat 0 newest)."""
        idx = history._load_index(self.user)
        for i, cid in enumerate(order or self.chats):
            for c in idx["chats"]:
                if c["id"] == cid:
                    c["updated_at"] = "2026-11-%02dT12:00:00+00:00" % (20 - i)
        history._save_index(self.user, idx)

    def keep(self, chat, upload_id, data):
        d = history.chat_upload_root(self.user, chat) / ".uploads"
        d.mkdir(parents=True, exist_ok=True)
        (d / upload_id).write_bytes(data)
        return d / upload_id


class TheArchiveClock(Base):
    def test_outside_the_five_most_recent_or_archived_by_hand_is_archived(self):
        history.archive_chat(self.user, self.chats[0], True)      # the newest, archived by hand
        self.age_chats()                                          # archive_chat touched updated_at: restore the order
        archived, clock = PA.archived_chats(self.user, now=1000.0)
        self.assertEqual(sorted(archived), sorted([self.chats[0], self.chats[5], self.chats[6]]))
        self.assertEqual(set(clock), {"%s/%s" % (self.user, c) for c in archived})

    def test_a_chat_used_again_loses_its_clock(self):
        _a, clock = PA.archived_chats(self.user, now=1000.0)
        self.assertIn("%s/%s" % (self.user, self.chats[6]), clock)
        self.age_chats([self.chats[6]] + self.chats[:6])          # chat 6 is now the newest
        _a, clock = PA.archived_chats(self.user, now=2000.0, clock=clock)
        self.assertNotIn("%s/%s" % (self.user, self.chats[6]), clock)
        self.assertEqual(clock["%s/%s" % (self.user, self.chats[5])], 1000.0, "first sighting kept, not refreshed")

    def test_a_photo_is_due_only_after_two_weeks_in_archive_and_never_twice(self):
        old = self.chats[6]
        self.keep(old, "aa-photo.png", _png())
        self.keep(old, "bb-notes.txt", b"not a photo")
        self.keep(self.chats[0], "cc-recent.png", _png())          # a recent chat's photo never ages
        PA.sweep(desk=FakeDesk(), now=1000.0)                      # starts the clock
        due, _ = PA.due_photos(now=1000.0 + 13 * DAY)
        self.assertEqual(due, [])
        due, _ = PA.due_photos(now=1000.0 + 15 * DAY)
        self.assertEqual([d["upload_id"] for d in due], ["aa-photo.png"])
        root = history.chat_upload_root(self.user, old)
        (root / PA.DESCRIPTIONS).write_text(json.dumps({"aa-photo.png": {"description": "x"}}))
        self.assertEqual(PA.due_photos(now=1000.0 + 16 * DAY)[0], [], "a described photo is never sent again")

    def test_one_shot_test_accounts_are_never_swept(self):
        history.new_chat("test")
        self.assertNotIn("test", [d["user"] for d in PA.due_photos(now=10 ** 9)[0]])


class TheOrder(Base):
    def test_sweep_submits_one_background_vlm_order_carrying_each_photo(self):
        self.keep(self.chats[6], "aa-photo.png", _png())
        PA.sweep(desk=FakeDesk(), now=1000.0)
        desk = FakeDesk()
        out = PA.sweep(desk=desk, now=1000.0 + 15 * DAY)
        self.assertEqual(out["submitted"], 1, out)
        body = desk.orders[0]
        self.assertEqual((body["family"], body["tier"], body["urgency"], body["kind"]), ("vlm", "lead", "background", "review"))
        self.assertIn("{POLICY_MODEL}", body["spec"]["args"], "the weighed row picks the model; none is named here")
        self.assertNotIn("fellowship", body, "the Fellowship form carries text tasks only")
        items = json.loads(Path(json.loads(Path(PA.PENDING).read_text())["paths"]["items"]).read_text())["items"]
        self.assertEqual(base64.b64decode(items[0]["images"][0]), _png())
        self.assertIn("never guess who anyone is", items[0]["system"])

    def test_nothing_due_submits_nothing_and_says_so_without_a_dm(self):
        out = PA.sweep(desk=FakeDesk(), now=1000.0)
        self.assertEqual(out["due"], 0)
        self.assertTrue(any("nothing due" in m and not dm for m, dm in self.said))

    def test_a_desk_that_raises_never_breaks_the_sweep(self):
        class Broken(FakeDesk):
            def p20_order(self, req):
                raise RuntimeError("desk down")
        self.keep(self.chats[6], "aa-photo.png", _png())
        PA.sweep(desk=FakeDesk(), now=1000.0)
        out = PA.sweep(desk=Broken(), now=1000.0 + 15 * DAY)
        self.assertIn("error", out)
        self.assertTrue(any(dm for _m, dm in self.said), "a failure is DMed")


class CollectingTheDescriptions(Base):
    def submit(self, answer, status="done"):
        self.photo = self.keep(self.chats[6], "aa-photo.png", _png())
        self.gen.joinpath(self.user).mkdir(parents=True)
        (self.gen / self.user / "20261101T000000Z-photo.png").write_bytes(_png())       # its image-generation copy
        (self.gen / self.user / "other.png").write_bytes(_png((1, 1, 1)))               # someone else's bytes
        history.append(self.user, "What is this?\n\n[Attached: photo.png (upload aa-photo.png)]", "A blue square.",
                       chat=self.chats[6], user_sent="What is this?\n\n[Attached: photo.png (upload aa-photo.png)] +note")
        self.age_chats()
        PA.sweep(desk=FakeDesk(), now=1000.0)
        desk = FakeDesk()
        PA.sweep(desk=desk, now=1000.0 + 15 * DAY)
        pend = json.loads(Path(PA.PENDING).read_text())
        Path(pend["paths"]["out"]).write_text(json.dumps(
            {"model": "qwen3-vl:8b", "rows": [{"id": "p001", "answer": answer}]}))
        desk.status = status
        return PA.collect(desk=desk), pend

    def test_a_description_replaces_the_line_then_the_photo_and_its_copy_go(self):
        out, pend = self.submit("A small blue square on a plain background.")
        self.assertEqual(out["described"], ["aa-photo.png"], out)
        self.assertFalse(self.photo.exists())
        self.assertEqual(sorted(p.name for p in (self.gen / self.user).iterdir()), ["other.png"],
                         "only the copy with the same bytes is removed")
        rec = json.loads((history.chat_upload_root(self.user, self.chats[6]) / PA.DESCRIPTIONS).read_text())
        self.assertEqual(rec["aa-photo.png"]["description"], "A small blue square on a plain background.")
        msgs = history.load(self.user, self.chats[6])
        user_msg = [m for m in msgs if m["role"] == "user"][0]
        for key in ("content", "sent"):
            self.assertNotIn("[Attached: photo.png (upload aa-photo.png)]", user_msg[key])
            self.assertIn("described it: A small blue square", user_msg[key])
        self.assertFalse(os.path.exists(pend["paths"]["items"]), "the photos' copy on the share is removed")
        self.assertFalse(os.path.exists(PA.PENDING))

    def test_no_description_means_nothing_is_deleted(self):
        out, pend = self.submit("   ")
        self.assertEqual(out["kept"], ["aa-photo.png"])
        self.assertTrue(self.photo.exists())
        self.assertFalse(os.path.exists(pend["paths"]["items"]))

    def test_a_failed_order_keeps_every_photo(self):
        out, pend = self.submit("A square.", status="failed")
        self.assertEqual(out, {"ended": "failed"})
        self.assertTrue(self.photo.exists())
        self.assertFalse(os.path.exists(pend["paths"]["items"]))

    def test_doc_gets_the_description_after_the_photo_is_gone(self):
        self.submit("A small blue square.")
        ollama_client._CALL_CONTEXT.window_conv = "%s/%s" % (self.user, self.chats[6])
        with patch.object(config, "writable_corpus_dir", lambda user: (None, None)):
            got = json.loads(tools._look_at_upload("aa-photo.png", "q", self.user))
        self.assertEqual(got["description"], "A small blue square.")
        self.assertIn("not by the photo", got["note"])


class RewritingTheTranscript(Base):
    def test_only_that_upload_is_rewritten_and_the_writing_sample_variant_too(self):
        history.append(self.user, "[Attached: a.txt (upload x1-a.txt); my own writing, now my writing sample] and "
                       "[Attached: b.png (upload x2-b.png)]", "ok", chat=self.chats[1])
        self.assertEqual(history.rewrite_attachment(self.user, self.chats[1], "x1-a.txt", "[gone]"), 1)
        text = [m for m in history.load(self.user, self.chats[1]) if m["role"] == "user"][0]["content"]
        self.assertEqual(text, "[gone] and [Attached: b.png (upload x2-b.png)]")
        self.assertEqual(history.rewrite_attachment(self.user, "no-such-chat", "x2-b.png", "[gone]"), 0)


if __name__ == "__main__":
    unittest.main()
