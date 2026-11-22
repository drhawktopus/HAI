"""Lab patch: Flash-Next's mid-token resume, on Strata's Python server (serve/server.py, Strata 0.1.45).

The same contract as lab patch #4 on llama.cpp (slot_continue_patch.py): a turn cut mid-answer and saved, restored with
lab_continue, then the SAME request asked again, and the caller receives the whole answer, the part before the cut
replayed through the normal output path, generation going on from the cut.

Why no engine change is expected: Strata's sampler draws Philox(seed, position) (kernels/sampler.hpp; generate.cpp "a seed
gives the same text whether a token comes from this path or from the speculative loop"), so the only sampler state is the
seed. The server must therefore choose and keep the seed: a request with none gets a clock-based one in the engine.

    python strata_continue_patch.py <path to serve/server.py>

Every replacement must match exactly once, or nothing is written. The proof on the card is the factory's
workspace/flashnext/strata_continue_proof.py; what it has shown is in the library's quick-resume design note."""
import sys

REPL = []

# --- the engine wrapper: keep the tokens the engine wrote that the consumer never saw. It holds them in its state, so
# leaving them out would make the continued prompt differ from what the session file holds.
REPL.append(("""        # an image request takes the same sampling keys as text (#75: it used to decode greedily whatever was asked)
        head = f"GENI {int(max_new)}{self.sampling_keys(sampling or {})} {embeddings}" if embeddings else \\
""", """        self.cut_tail = []                               # LAB: tokens the engine wrote after the consumer left
        # an image request takes the same sampling keys as text (#75: it used to decode greedily whatever was asked)
        head = f"GENI {int(max_new)}{self.sampling_keys(sampling or {})} {embeddings}" if embeddings else \\
"""))

REPL.append(("""                if line.startswith("T "):
                    allow = silence
                    if cancel.is_set():
                        return
                    yield int(line[2:])
                elif line.startswith("PP "):
                    f = line.split()
                    if len(f) >= 3 and f[1].isdigit() and f[2].isdigit():
                        self.progress = (int(f[1]), int(f[2]))             # prompt progress, one per chunk: also a heartbeat (the
""", """                if line.startswith("T "):
                    allow = silence
                    if cancel.is_set():
                        self.cut_tail.append(int(line[2:]))   # LAB: read but never handed on
                        return
                    yield int(line[2:])
                elif line.startswith("PP "):
                    f = line.split()
                    if len(f) >= 3 and f[1].isdigit() and f[2].isdigit():
                        self.progress = (int(f[1]), int(f[2]))             # prompt progress, one per chunk: also a heartbeat (the
"""))

REPL.append(("""                    if line.startswith("DONE"):
                        self._parse_done(line)
                        break
                    if not self.can_stop:
                        heard = time.monotonic()

    def _activity(self)""", """                    if line.startswith("DONE"):
                        self._parse_done(line)
                        break
                    if line.startswith("T "):             # LAB: written before the STOP landed
                        self.cut_tail.append(int(line[2:]))
                    if not self.can_stop:
                        heard = time.monotonic()

    def _activity(self)"""))

# --- the service's state
REPL.append(("""        self.slot_save_path = None                    # --slot-save-path""",
             """        self.lab_turn = None                          # LAB: the last turn, when it was cut mid-answer
        self.lab_armed = None                         # LAB: a restored cut turn, waiting for its own request again
        self.slot_save_path = None                    # --slot-save-path"""))

# --- the helpers, beside slot_action
REPL.append(("""    def slot_action(self, slot: str, action: str, filename) -> tuple[int, dict]:
""", """    # ---- LAB: the mid-token resume (lab-engine/patches/strata_continue_patch.py). None of these may raise: a
    # resume that cannot be offered is a plain re-ask, never a failure of the turn or the save.
    def _lab_note_turn(self, ids, raw_ids, finish, cancel, prompt, emb, par, sampling, replayed):
        \"\"\"At the end of a turn, holding the FIFO: remember it when it was cut mid-answer in one plain pass (a thinking
        budget's wrap-up or a recovery changed the prompt, and those are not continued).\"\"\"
        try:
            self.lab_turn = None
            cut = finish in ("cancel", "disconnect") or cancel.is_set()
            seed = (sampling or {}).get("seed")
            if not cut or prompt is not ids or emb or par or not isinstance(seed, int):
                return
            done = [int(t) for t in raw_ids] + [int(t) for t in getattr(self.engine, "cut_tail", None) or []]
            if replayed and len(done) < len(replayed):     # cut again during the replay: nothing new, nothing lost
                done = list(replayed)
            if done:
                self.lab_turn = {"ids": [int(t) for t in ids], "generated": done, "seed": seed}
        except Exception as e:  # noqa: BLE001
            self.lab_turn = None
            print(f"[strata] lab_continue: the cut turn was not kept ({e!r})", flush=True)

    def _lab_take(self, ids, emb, par):
        \"\"\"A new turn, holding the FIFO: any request spends the arming; only its own request uses it.\"\"\"
        armed, self.lab_armed = self.lab_armed, None
        try:
            self.engine.cut_tail = []
            if not armed or emb or par or armed["ids"] != [int(t) for t in ids]:
                return None
            print(f"[strata] lab_continue: replaying {len(armed['generated'])} tokens and going on from the cut", flush=True)
            return armed
        except Exception as e:  # noqa: BLE001
            print(f"[strata] lab_continue: not continued ({e!r})", flush=True)
            return None

    @staticmethod
    def _lab_seeded(sampling, armed):
        \"\"\"The seed this turn draws with: the cut turn's, the request's own, else one chosen here (what the engine would
        have done from its clock, but known, so a cut can go on with the same draws).\"\"\"
        import secrets
        out = dict(sampling or {})
        seed = out.get("seed")
        if armed:
            out["seed"] = armed["seed"]
        elif not (isinstance(seed, int) and not isinstance(seed, bool) and seed > 0):
            out["seed"] = secrets.randbelow(2 ** 62) + 1
        return out

    def _lab_replay(self, saved, ids, max_new, sampling, cancel):
        \"\"\"The token stream of a continued turn: what was written before the cut, then the engine from there.\"\"\"
        for t in saved:
            if cancel.is_set():
                return
            yield t
        left = max_new - len(saved)
        if left > 0 and not cancel.is_set():
            yield from self.engine.generate([int(t) for t in ids] + saved, left, sampling, cancel)

    def _lab_after_session(self, action, path, r, lab_continue):
        \"\"\"After a session file moved, holding the FIFO: a save writes or clears the cut turn's sidecar; a restore arms
        it when asked and when the sidecar belongs to this exact file. -> armed (restore with lab_continue only).\"\"\"
        side = path + ".labturn"
        try:
            if action == "save":
                turn = self.lab_turn
                if turn:
                    tmp = side + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(dict(turn, n_saved=r["tokens"], n_bytes=r["bytes"]), f)
                    os.replace(tmp, side)
                elif os.path.exists(side):
                    os.remove(side)                         # a finished turn's save: the old cut no longer describes it
                return False
            self.lab_turn = self.lab_armed = None
            if not lab_continue or not os.path.exists(side):
                return False
            with open(side, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("n_saved") != r["tokens"] or data.get("n_bytes") != r["bytes"]:
                print("[strata] lab_continue: the sidecar does not describe this file: not armed", flush=True)
                return False
            self.lab_armed = {"ids": data["ids"], "generated": data["generated"], "seed": data["seed"]}
            return True
        except Exception as e:  # noqa: BLE001
            self.lab_armed = None
            print(f"[strata] lab_continue: {action} sidecar failed ({e!r})", flush=True)
            return False

    def slot_action(self, slot: str, action: str, filename, lab_continue: bool = False) -> tuple[int, dict]:
"""))

# --- slot_action: under the FIFO, then the reply says whether it armed
REPL.append(("""                    with self.status_lock:
                        self.status["busy"] = False
                    self.last_request_at = time.time()
        finally:
            if waiting:                                     # never reached the FIFO (an exception while waiting)
""", """                    with self.status_lock:
                        self.status["busy"] = False
                    self.last_request_at = time.time()
                lab_was_armed = self._lab_after_session(action, path, r, lab_continue)
        finally:
            if waiting:                                     # never reached the FIFO (an exception while waiting)
"""))

REPL.append(("""        return 200, {"id_slot": 0, "filename": filename, "n_restored": r["tokens"], "n_read": r["bytes"],
                     "timings": {"restore_ms": r["ms"]}}
""", """        body = {"id_slot": 0, "filename": filename, "n_restored": r["tokens"], "n_read": r["bytes"],
                "timings": {"restore_ms": r["ms"]}}
        if lab_continue:
            body["lab_continue_armed"] = lab_was_armed
        return 200, body
"""))

# --- the HTTP door
REPL.append(("""                        code, body = svc.slot_action(path[len("/slots/"):], (query.get("action") or [""])[0],
                                                     req.get("filename"))
""", """                        code, body = svc.slot_action(path[len("/slots/"):], (query.get("action") or [""])[0],
                                                     req.get("filename"), lab_continue=req.get("lab_continue") is True)
"""))

# --- the turn
REPL.append(("""        rate = collections.deque(maxlen=32) if par else self.rate
""", """        rate = collections.deque(maxlen=32) if par else self.rate
        prompt, lab_saved, lab_replayed = None, None, None   # LAB: defined for the finally below, whatever ends this
"""))

REPL.append(("""                    prompt, thought = ids, 0            # thought: the reasoning tokens so far (the budget's count)
""", """                    prompt, thought = ids, 0            # thought: the reasoning tokens so far (the budget's count)
                    lab_armed = self._lab_take(ids, emb, par)
                    if not par and self.slot_save_path and hasattr(self.engine, "session_file"):
                        sampling = self._lab_seeded(sampling, lab_armed)   # LAB: the turn's seed, chosen here and kept
                    lab_saved = lab_armed["generated"] if lab_armed else None
"""))

REPL.append(("""                        gen = self.engine.generate(prompt, max_new - n, sampling, cancel, embeddings=emb) if emb \\
                            else self.engine.generate(prompt, max_new - n, sampling, cancel)
""", """                        if lab_saved:                   # LAB: the same loop reads the saved tokens first, so the
                            #                             parser, the budget and the guards see the whole answer
                            gen = self._lab_replay(lab_saved, ids, max_new, sampling, cancel)
                            lab_replayed, lab_saved = lab_saved, None
                        else:
                            gen = self.engine.generate(prompt, max_new - n, sampling, cancel, embeddings=emb) if emb \\
                                else self.engine.generate(prompt, max_new - n, sampling, cancel)
"""))

REPL.append(("""                    # #266: settle this request's status, history and totals while still holding the fifo: once
""", """                    self._lab_note_turn(ids, raw_ids, finish, cancel, prompt, emb, par, sampling, lab_replayed)
                    # #266: settle this request's status, history and totals while still holding the fifo: once
"""))


def main(path):
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    crlf = "\r\n" in text
    if crlf:
        text = text.replace("\r\n", "\n")
    for i, (a, b) in enumerate(REPL):
        n = text.count(a)
        if n != 1:
            sys.exit("replacement %d matched %d times, nothing written: %r" % (i, n, a[:80]))
    for a, b in REPL:
        text = text.replace(a, b)
    if crlf:
        text = text.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print("applied %d of %d replacements to %s" % (len(REPL), len(REPL), path))


if __name__ == "__main__":
    main(sys.argv[1])
