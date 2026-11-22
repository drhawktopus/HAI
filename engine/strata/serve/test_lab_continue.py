"""serve/test_lab_continue.py - the lab's mid-token resume on the server (lab-engine/patches/strata_continue_patch.py).

A stand-in engine, no GPU, no pack. Its output is a function of (seed, position), as the real sampler's draws are, so a test
can tell the same draws from different ones. This proves the server's half only: the real engine's half (a save right after
a STOP, token identity on the card) is the live proof on Mothership.

    python -m unittest serve.test_lab_continue -v
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve.frontend import ChatTemplate  # noqa: E402
from serve.server import ByteTokenizer, EngineDied, Service, StrataEngine, serve  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EOS = 257                                   # ByteTokenizer's <|im_end|>
PROMPT = [ord(c) for c in "a prompt"]
LETTERS = "abcdefghijklmnopqrstuvwxyz"
AFTER_STOP = 3                              # tokens the engine still writes before a STOP lands


def draw(seed: int, pos: int) -> int:
    """The stand-in's Philox(seed, position): a letter, depending on both."""
    return ord(LETTERS[hashlib.sha256(b"%d:%d" % (seed, pos)).digest()[0] % 26])


class Proc:
    """Answers the engine's stdin lines. GEN runs on its own thread, one token per step, until the answer's end or a STOP."""

    def __init__(self, engine):
        self.engine = engine
        self.stdin = self
        self.stop = threading.Event()
        self.gens: list[dict] = []

    def poll(self):
        return None

    def flush(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0

    def write(self, s):
        s = s.strip()
        cmd, _, rest = s.partition(" ")
        if cmd == "STOP":
            self.stop.set()
        elif cmd == "SAVE":
            if self.engine.save_fails:
                self.engine.lines.put("SERR invalid 0 no complete session to save\n")
                return
            Path(rest).write_bytes(b"x" * 1234)
            self.engine.saved[rest] = self.engine.held     # a file holds the tokens it was saved with, as the real one does
            self.engine.lines.put(f"SAVED {self.engine.held} 1234 1.0\n")
        elif cmd == "RESTORE":
            self.engine.held = self.engine.saved.get(rest, self.engine.held)
            self.engine.lines.put(f"RESTORED {self.engine.held} {os.path.getsize(rest)} 1.0\n")
        elif cmd in ("GEN",):
            parts = rest.split()
            keys = dict(p.split("=", 1) for p in parts[1:-1] if "=" in p)
            ids = [int(t) for t in parts[-1].split(",")]
            g = {"max_new": int(parts[0]), "seed": int(keys["seed"]) if "seed" in keys else None, "ids": ids}
            self.gens.append(g)
            self.stop.clear()
            threading.Thread(target=self.run, args=(g,), daemon=True).start()

    def run(self, g):
        e, pos, written = self.engine, len(g["ids"]), 0
        seed = g["seed"] if g["seed"] is not None else int(time.time_ns() % 1000003) + 1
        if e.die_at_gen:
            e.lines.put(None)
            return
        end = (e.prompt_len or len(PROMPT)) + e.answer_len
        finish, extra = "length", 0
        while written < g["max_new"]:
            if self.stop.is_set():
                if extra >= AFTER_STOP:
                    finish = "cancel"
                    break
                extra += 1
            if pos >= end:
                e.lines.put(f"T {EOS}\n")
                written += 1
                finish = "stop"
                break
            e.lines.put(f"T {draw(seed, pos)}\n")
            pos, written = pos + 1, written + 1
            time.sleep(e.step_s)
        e.held = pos
        e.lines.put(f"DONE {written} {len(g['ids'])} 1.0 1.0 {finish}\n")


class Engine(StrataEngine):
    def __init__(self):
        super().__init__("strata", ["--max-context", "65536"], lazy=True)
        self.max_context = 65536
        self.lines = queue.Queue()
        self.ended, self.can_stop = False, True
        self.answer_len, self.step_s, self.held = 40, 0.001, 0
        self.prompt_len = None
        self.saved = {}
        self.save_fails, self.die_at_gen = False, False
        self.proc = Proc(self)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.engine = Engine()
        self.svc = Service(self.engine, ByteTokenizer(), ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.svc.slot_save_path = self.dir.name
        self.svc.stop_ids = {EOS}

    max_new = 1000

    def turn(self, ids=None, sampling=None, cut_after=None, close=False, thinking=False):
        """One turn -> (the text it streamed, its finish). `cut_after`: cancel (or hang up) after that many chars."""
        cancel = threading.Event()
        run = self.svc.run(list(ids if ids is not None else PROMPT), thinking, None, self.max_new, dict(sampling or {}), cancel)
        text, finish = "", None
        for kind, value in run:
            if kind == "event" and value.kind in ("content", "reasoning"):
                text += value.text or ""
                if cut_after is not None and len(text) >= cut_after:
                    if close:
                        run.close()
                        return text, "disconnect"
                    cancel.set()
                    cut_after = None
            elif kind == "done":
                finish = value["finish"]
        return text, finish

    def seeded_answer(self, seed):
        return self.turn(sampling={"seed": seed})

    def cut_and_save(self, name="chat.bin", cut_after=15, seed=None, close=False):
        got = self.turn(sampling={"seed": seed} if seed else None, cut_after=cut_after, close=close)
        code, body = self.svc.slot_action("0", "save", name)
        self.assertEqual(code, 200, body)
        return got

    def restore(self, name="chat.bin", lab_continue=True):
        code, body = self.svc.slot_action("0", "restore", name, lab_continue=lab_continue)
        self.assertEqual(code, 200, body)
        return body


class TheCutTurnContinues(Base):
    def test_the_controls_can_fail(self):
        """Without these the comparisons below prove nothing: a seed decides the text, and a different one differs."""
        a, b, c = self.seeded_answer(5), self.seeded_answer(5), self.seeded_answer(6)
        self.assertEqual(a, b)
        self.assertEqual(len(a[0]), 40)
        self.assertNotEqual(a[0], c[0])

    def _resumed_equals_uninterrupted(self, close):
        whole, finish = self.seeded_answer(7)
        self.assertEqual(finish, "stop")
        cut, _ = self.cut_and_save(seed=7, close=close)
        self.assertLess(len(cut), len(whole))
        self.assertTrue(self.restore()["lab_continue_armed"])
        again, finish = self.turn()                 # the request carries no seed: the cut turn's is used
        self.assertEqual(again, whole)
        self.assertEqual(finish, "stop")

    def test_a_cancel_resumes_to_the_same_answer(self):
        self._resumed_equals_uninterrupted(close=False)

    def test_a_hang_up_resumes_to_the_same_answer(self):
        self._resumed_equals_uninterrupted(close=True)

    def test_a_request_with_no_seed_still_resumes(self):
        cut, _ = self.cut_and_save()
        seed = self.engine.proc.gens[-1]["seed"]
        self.assertIsNotNone(seed, "the server must choose and send a seed")
        self.assertTrue(self.restore()["lab_continue_armed"])
        again, finish = self.turn()
        self.assertEqual(finish, "stop")
        self.assertEqual(again, self.seeded_answer(seed)[0])
        self.assertTrue(again.startswith(cut))

    def test_the_engine_goes_on_from_every_token_it_wrote(self):
        """The tokens written after the STOP are in the engine's state, so the continued prompt must carry them."""
        self.cut_and_save(seed=9)
        held = self.engine.held
        turn = self.svc.lab_turn
        self.assertEqual(len(turn["ids"]) + len(turn["generated"]), held)
        self.restore()
        self.turn()
        last = self.engine.proc.gens[-1]
        self.assertEqual(len(last["ids"]), held)
        self.assertEqual(last["ids"][:len(PROMPT)], PROMPT)
        self.assertEqual(last["seed"], 9)
        self.assertEqual(last["max_new"], 1000 - (held - len(PROMPT)))

    def test_cut_again_during_the_replay_loses_nothing(self):
        whole, _ = self.seeded_answer(11)
        self.cut_and_save(seed=11, cut_after=25)
        self.restore()
        self.turn(cut_after=3)                      # cut while the saved tokens are being replayed
        self.svc.slot_action("0", "save", "chat.bin")
        self.assertTrue(self.restore()["lab_continue_armed"])
        again, _ = self.turn()
        self.assertEqual(again, whole)

    def test_thinking_goes_through_the_same_parser(self):
        self.engine.answer_len = 30
        whole, _ = self.turn(sampling={"seed": 13}, thinking=True)
        self.cut_and_save(seed=13)
        self.restore()
        again, _ = self.turn(thinking=True)
        self.assertEqual(again, whole)
        self.assertEqual(len(whole), 30)

    def test_through_the_http_door(self):
        self.cut_and_save(seed=17)
        httpd = serve(self.svc, port=0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/slots/0?action=restore",
                                     data=json.dumps({"filename": "chat.bin", "lab_continue": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.assertIs(json.load(r)["lab_continue_armed"], True)


class ALongConversation(Base):
    """The case the first proofs did not reach: a long history and a deep cut. History at the length of the longest real conversations; the answer is kept to what a test can stream in time."""

    HISTORY, ANSWER, CUT = 200000, 12000, 9000
    max_new = 100000

    def setUp(self):
        super().setUp()
        self.engine.max_context, self.engine.prompt_len = 262144, self.HISTORY
        self.engine.answer_len, self.engine.step_s = self.ANSWER, 0
        self.long = [ord("x")] * self.HISTORY

    def whole(self, seed):
        return self.turn(ids=self.long, sampling={"seed": seed})

    def cut(self, name, seed, at, close):
        got = self.turn(ids=self.long, sampling={"seed": seed}, cut_after=at, close=close)
        code, body = self.svc.slot_action("0", "save", name)
        self.assertEqual(code, 200, body)
        return got

    def test_the_controls_can_fail(self):
        a, b, c = self.whole(5), self.whole(5), self.whole(6)
        self.assertEqual(a, b)
        self.assertEqual(len(a[0]), self.ANSWER)
        self.assertNotEqual(a[0], c[0])

    def test_a_deep_cut_comes_back_character_for_character(self):
        for close in (False, True):
            whole, finish = self.whole(7)
            self.assertEqual(finish, "stop")
            cut, _ = self.cut("long.bin", 7, self.CUT, close)
            self.assertGreaterEqual(len(cut), self.CUT)
            self.assertLess(len(cut), self.ANSWER)
            self.assertTrue(self.restore("long.bin")["lab_continue_armed"])
            again, finish = self.turn(ids=self.long)
            self.assertEqual((again == whole, finish), (True, "stop"), "cut at %d, closed=%s" % (len(cut), close))

    def test_the_engine_is_handed_the_whole_history_and_every_token_written(self):
        cut, _ = self.cut("long.bin", 8, self.CUT, False)
        self.restore("long.bin")
        self.turn(ids=self.long)
        ids = self.engine.proc.gens[-1]["ids"]
        self.assertGreaterEqual(len(ids), self.HISTORY + len(cut), "nothing dropped on the way back")
        self.assertEqual(ids[:self.HISTORY], self.long)

    def test_cut_again_after_the_resume_loses_nothing(self):
        whole, _ = self.whole(9)
        self.cut("long.bin", 9, 4000, False)
        self.restore("long.bin")
        got = self.turn(ids=self.long, cut_after=8000)
        code, body = self.svc.slot_action("0", "save", "long2.bin")
        self.assertEqual(code, 200, body)
        self.assertTrue(whole.startswith(got[0][:8000]))
        self.restore("long2.bin")
        again, finish = self.turn(ids=self.long)
        self.assertEqual((again == whole, finish), (True, "stop"))

    def test_the_sidecar_stays_small_at_this_length(self):
        self.cut("long.bin", 10, self.CUT, False)
        side = Path(self.dir.name) / "long.bin.labturn"
        self.assertTrue(side.exists())
        self.assertLess(side.stat().st_size, 2_000_000, "it holds the seed and what was written, not the whole history over again")


class ItIsNeverWorseThanAReAsk(Base):
    def test_a_different_request_spends_the_arming(self):
        self.cut_and_save(seed=21)
        self.restore()
        other = PROMPT + [ord("x")]
        self.engine.answer_len = 40
        self.turn(ids=other)
        self.assertEqual(self.engine.proc.gens[-1]["ids"], other, "no replay into someone else's request")
        self.assertIsNone(self.svc.lab_armed)
        self.turn()
        self.assertEqual(self.engine.proc.gens[-1]["ids"], PROMPT, "spent: the original request is a plain re-ask now")

    def test_a_plain_restore_does_not_arm(self):
        self.cut_and_save(seed=23)
        self.assertNotIn("lab_continue_armed", self.restore(lab_continue=False))
        self.assertIsNone(self.svc.lab_armed)
        self.turn()
        self.assertEqual(self.engine.proc.gens[-1]["ids"], PROMPT)

    def test_a_finished_turn_clears_the_old_cut(self):
        self.cut_and_save(seed=25)
        side = os.path.join(self.dir.name, "chat.bin.labturn")
        self.assertTrue(os.path.exists(side))
        self.seeded_answer(25)
        self.svc.slot_action("0", "save", "chat.bin")
        self.assertFalse(os.path.exists(side))
        self.assertFalse(self.restore()["lab_continue_armed"])

    def test_a_sidecar_for_another_file_is_refused(self):
        self.cut_and_save(seed=27)
        Path(self.dir.name, "chat.bin").write_bytes(b"y" * 99)     # the file changed under the sidecar
        self.assertFalse(self.restore()["lab_continue_armed"])
        self.assertIsNone(self.svc.lab_armed)

    def test_a_corrupt_sidecar_does_not_fail_the_restore(self):
        self.cut_and_save(seed=29)
        Path(self.dir.name, "chat.bin.labturn").write_text("{not json")
        self.assertFalse(self.restore()["lab_continue_armed"])

    def test_a_cut_while_reading_keeps_nothing(self):
        self.engine.answer_len = 0                  # no token before the end
        self.turn(cut_after=None)
        self.assertIsNone(self.svc.lab_turn)

    def test_a_request_keeps_its_own_seed(self):
        self.seeded_answer(31)
        self.assertEqual(self.engine.proc.gens[-1]["seed"], 31)

    def test_two_requests_without_a_seed_draw_differently(self):
        a, _ = self.turn()
        b, _ = self.turn()
        self.assertNotEqual(a, b)

    def test_the_real_exception_survives(self):
        """Nothing in the cleanup may replace the exception in flight."""
        self.engine.die_at_gen = True
        with self.assertRaises(EngineDied):
            self.turn()
        self.assertIsNone(self.svc.lab_turn)


if __name__ == "__main__":
    unittest.main()
