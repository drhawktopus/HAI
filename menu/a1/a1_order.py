"""A1 as a factory order (compute payload): one lead model solves the Aider-Polyglot Python exercises in the A1 tool
loop on this box's own ollama, on the foundry's standard line.

    a1_order.py <ORDER_OUT> <plan.json>

plan.json: {"model": tag, "num_ctx": int (the model's tested max on this box), "think": bool,
            "mode": "agentic" | "aider", "exercises": [names] | "all", "control": exercise name,
            "host": "http://127.0.0.1:11434" (optional), "options": {...} (optional; temperature is left to the
            model's configured vendor settings unless given), "note": str}

What the order inherits instead of rebuilding (it imports the foundry's infer_ollama): the residency + shared-memory
spill guard (`_assert_resident`, rc 2 = refused and HELD by the foundry), the hang-proof streamed call that ends only
on silence or a loop (`_chat`, now with tools) and its `[beat]` liveness lines.
Built-in CONTROL: before any model call, one exercise's reference solution must pass its hidden tests and its untouched
stub must be graded and fail - otherwise the grader on this box measures nothing and the order is VOID (exit 1).
Deliverables (ORDER_OUT): results.json (one row per exercise + the summary), transcripts/<exercise>.json (every model
turn: what came back, thinking length, tool calls, stats - #35 data). Byproduct: work/ (temp workspaces), removed at the
end. Progress: `[a1] i/N <exercise> ... X tok/s` per exercise (the foundry's compute progress protocol). An order run
again under its own name resumes: exercises already in results.json are kept."""
import datetime
import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("FOUNDRY_ROOT") or os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(ROOT, "bin")]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import a1_branch as B  # noqa: E402
import a1_validity as V  # noqa: E402
import a1_harness as H  # noqa: E402
import a1_ollama as A  # noqa: E402
import infer_ollama as IO  # noqa: E402

EXERCISES = os.path.join(HERE, "polyglot-benchmark", "python", "exercises", "practice")
# The task set's provenance (34-A1 spec § Build plan: fetched and archived with its manifest).
TASK_SET = "Aider-AI/polyglot-benchmark@7e0611e7 (python, 34)"


def engine_up(host):
    """Does this order's engine answer at all? (ollama /api/version; an OpenAI-compatible server such as Strata
    answers /health instead)."""
    for path in ("/api/version", "/health"):
        try:
            urllib.request.urlopen(host.rstrip("/") + path, timeout=10).read()
            return True
        except Exception:                                                    # noqa: BLE001
            pass
    return False


def strata_module():
    """workspace\\flashnext\\strata_arm - Strata's ONE lifecycle (prepare / enter / launch / stop), shared with the
    Flash-Next arms. `engine: "strata"` plans."""
    d = os.path.join(ROOT, "workspace", "flashnext")
    if d not in sys.path:
        sys.path.append(d)
    import strata_arm
    return strata_arm


def wait_for_engine(host, why):
    """Before rerunning a void exercise: a retry against a dead engine changes nothing. Wait on STATUS, never a clock: the factory's progress watch
    respawns the order if the engine never returns, and a requeue resumes from the checkpoint."""
    n = 0
    while not engine_up(host):
        if n % 4 == 0:
            print("[a1] engine not answering - waiting before the rerun (%s)" % why, flush=True)
        n += 1
        time.sleep(15)


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _is_tool_parse_error(e):
    """ollama could not parse the tool call the MODEL emitted. That is the model's malformed call, which the
    harness hands back to it; any other failure is still an error."""
    s = str(e)
    return s.startswith("ollama:") and ("XML syntax error" in s or "error parsing tool call" in s)


def control(ex_dir):
    """(ok, detail): the reference passes every hidden test AND the stub is graded and fails. A failed control carries
    the grader's own output, so a VOID says why."""
    name = os.path.basename(ex_dir).replace("-", "_")
    out, texts = {}, {}
    for which in ("reference", "stub"):
        d = tempfile.mkdtemp(prefix="a1ctl-")
        try:
            for f in os.listdir(ex_dir):
                p = os.path.join(ex_dir, f)
                if os.path.isfile(p) and not f.endswith("_test.py"):
                    shutil.copy2(p, d)
            if which == "reference":
                shutil.copy2(os.path.join(ex_dir, ".meta", "example.py"), os.path.join(d, name + ".py"))
            p, t, text = H.grade(d, ex_dir)
            out[which], texts[which] = (p, t), text
        finally:
            shutil.rmtree(d, ignore_errors=True)
    (rp, rt), (sp, st) = out["reference"], out["stub"]
    # the stub fails if graded below total, or if the tests ran and could not import it (the child's A1-LOADFAIL)
    stub_failed = (st > 0 and sp < st) or "A1-LOADFAIL" in (texts.get("stub") or "")
    ok = rt > 0 and rp == rt and stub_failed
    detail = "reference %d/%d, stub %d/%d" % (rp, rt, sp, st)
    if not ok:
        detail += " | reference grader output: " + (texts.get("reference") or "")[-1200:].replace("\r", "")
    return ok, detail


def own_desk_id(out):
    """This order's desk record id, from its own .job.json in the foundry's queue (the runner keeps a running order's
    spec there, with the desk's `fleet_order_id`; {ORDER_OUT}'s basename is the order name). None when not found -
    the window then closes on CLOSE only."""
    import a1_delegate
    return a1_delegate.desk_id_for(out, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main():
    global H
    out, plan = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
    if plan.get("harness"):        # the protocol is named by the plan: v2.1 orders keep a1_harness, v2.2 names its own
        import importlib
        H = importlib.import_module(plan["harness"])
    os.makedirs(os.path.join(out, "transcripts"), exist_ok=True)
    host = plan.get("host", "http://127.0.0.1:11434")
    model, num_ctx, think = plan["model"], int(plan["num_ctx"]), plan.get("think", False)
    if think in ("xhigh", "medium", "low"):
        assert hasattr(IO, "think_value"), "this box's infer_ollama predates effort levels: %s would run as MEDIUM" % think
    else:
        think = bool(think)
    mode = plan.get("mode", "agentic")
    names = sorted(os.listdir(EXERCISES)) if plan.get("exercises", "all") == "all" else list(plan["exercises"])
    rpath = os.path.join(out, "results.json")
    res = json.load(open(rpath, encoding="utf-8")) if os.path.exists(rpath) else {}
    res.update({"plan": plan, "model": model, "mode": mode, "think": think, "num_ctx": num_ctx, "box_python": sys.executable})
    rows, void = {}, []
    for r in res.get("rows", []):
        # an exercise whose loop ENDED ON AN ERROR (the engine or harness threw - a1_harness `ended: "error"`) or that
        # the model never took a turn on measured the instrument, not the model
        # - it runs again. A 0-test row WITHOUT an error is the model's code not importing (`graded: "crashed"`): a
        # real failure, kept.
        if r.get("turns") and r.get("ended") != "error":
            rows[r["exercise_dir"]] = r
        else:
            void.append(r["exercise_dir"])
    if rows or void:
        print("resuming: %d exercise(s) already answered are kept; %d void row(s) run again%s"
              % (len(rows), len(void), (" (%s)" % ", ".join(void)) if void else ""), flush=True)
        # the same `[a1] N/T` line the foundry takes its progress marks from (p20_foundry.COMPUTE_PROG_RE): the kept
        # count at the resume is the FIRST mark, so the rate is this process's own units, not elapsed-since-the-
        # order-started over kept rows (the deck read 7,928 s/item for a resumed Ornith whose units ran 8-21 min)
        print("[a1] %d/%d resumed · solved %d/%d · void 0" % (
            len(rows), len(names), sum(1 for r in rows.values() if r.get("passed")), len(rows)), flush=True)

    ok, detail = control(os.path.join(EXERCISES, plan["control"]))
    res["control"] = {"exercise": plan["control"], "passed": ok, "detail": detail}
    print("control %s (%s)" % ("PASS" if ok else "FAILED", detail), flush=True)
    if not ok:
        res["result"] = "VOID: the grader control failed on this box - %s" % detail
        save(rpath, res)
        print(res["result"], flush=True)
        return 1

    branch = plan.get("branch")          # start from a saved parent run up to the arm's own difference (a1_branch)
    parent = None
    if branch:
        sha = hashlib.sha256(open(H.__file__, "rb").read()).hexdigest()[:16]
        if not os.path.exists(os.path.join(branch["from_dir"], "results.json")):
            # the parent has not delivered yet: a property of the moment, so WAIT (rc 2 = the foundry's hold-and-retry),
            # never VOID - and never branch from a half-finished parent
            print("REFUSED: parent %s not delivered yet - retried" % branch.get("from"), flush=True)
            return 2
        try:
            p_res, p_trans = B.load_parent(branch["from_dir"])
            why = B.compatible(p_res, plan, sha)
        except Exception as e:                                           # noqa: BLE001
            why = "parent unreadable (%s: %s)" % (type(e).__name__, e)
        if why:
            res["result"] = "VOID: cannot branch from %s - %s" % (branch.get("from"), why)
            save(rpath, res)
            print(res["result"], flush=True)
            return 1
        parent = p_trans
        res["branch"] = {"from": branch.get("from"), "point": branch["point"], "parent_harness": sha}
        print("branching from %s at %s (%d parent transcripts)" % (branch.get("from"), branch["point"], len(parent)),
              flush=True)

    engine = plan.get("engine", "ollama")
    if engine not in ("ollama", "strata"):
        raise SystemExit("BROKEN SPEC: engine must be ollama or strata (got %r)" % (engine,))
    if engine == "strata":
        # Strata serves the model itself (its own server, OpenAI API): no ollama residency to assert - the provider
        # checks the served context per call, and the server starts inside the run's try so the finally stops it
        strata_cfg = dict(plan["strata"])
        host = "http://127.0.0.1:%d" % int(strata_cfg.get("port", 8080))
        res["placement"] = {"engine": "strata", "model": strata_cfg.get("model"), "host": host}
    else:
        res["placement"] = IO._assert_resident(host, model, num_ctx)        # rc 2 -> the foundry holds the order
    # Stamps for #35's chronology splits and the menu's provenance: which harness, which task set, when.
    res["harness_sha256"] = hashlib.sha256(open(H.__file__, "rb").read()).hexdigest()[:16]
    res["task_set"] = TASK_SET
    res.setdefault("started", datetime.datetime.now().isoformat(timespec="seconds"))
    # Energy per exercise through the SAME meter as the menu (infer_ollama.PowerMeter: nvidia-smi at 4 Hz, or the
    # AMD box's telemetry log read at stop) with its idle baseline, so Phase 7 can price A1 work per solve.
    meter = IO.PowerMeter()
    idle = (time.time(), None)
    if meter.source:
        time.sleep(3)                            # idle baseline: resident model, no request in flight
        idle = (idle[0], time.time())
    turn_cap = int(plan.get("turn_cap") or 0)    # the runaway guard (a1_harness RUNAWAY GUARD); 0 = none
    if turn_cap >= num_ctx:
        raise ValueError("plan: turn_cap %d leaves no room in num_ctx %d (compaction keeps one reply's room)"
                         % (turn_cap, num_ctx))
    res["turn_cap"] = turn_cap
    # THE ONE CALL PATH: the lead's calls go through
    # Inspect's `lab` provider on this box's ollama (`infer_ollama._chat`, the same options / think / tools) with
    # Inspect's compaction - as rigby's do. Needs Inspect: the order runs under {FOUNDRY_ROOT}\venv-inspect.
    import a1_chat as C
    import lab_provider  # noqa: F401 - registers the "lab" provider
    from inspect_ai.model import get_model
    lab_loop = C.LoopThread()
    backend = "openai" if engine == "strata" else "foundry"
    lab = get_model("lab/" + model, memoize=False, backend=backend, num_ctx=num_ctx, think=think, host=host,
                    options=dict(plan.get("options") or {}))
    res["call_path"] = "inspect lab provider (%s) + compaction" % backend
    work = os.path.join(out, "work")
    os.makedirs(work, exist_ok=True)
    total = len(names)
    names = list(names)                          # a void exercise is appended once more (a1_validity)
    retried, flagged = set(), set()
    order_name = os.path.basename(out.rstrip("\\/"))
    team = plan.get("team")
    dlg = None
    if team:                       # A1-team: the lead's delegate() becomes a desk order on the helper (a1_delegate)
        import a1_delegate

        def token():
            import p20_foundry
            return p20_foundry._worker_token()

        def keep_warm():
            try:
                req = urllib.request.Request(host + "/api/generate", method="POST",
                                             data=json.dumps({"model": model, "keep_alive": "10m"}).encode("utf-8"),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=60).read()
            except Exception as e:                                       # noqa: BLE001
                print("keep-warm failed (%r)" % (e,), flush=True)
        unc = os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[2])))
        if team.get("errands") and not team.get("window"):
            raise SystemExit("BROKEN SPEC: team.errands needs team.window (an errand's turns go through the helper window)")
        if team.get("window"):       # helper WINDOW: one desk order for the whole run, pieces as items (a1_delegate)
            wcls = a1_delegate.PlacedWindowDelegator if team.get("placed") else a1_delegate.WindowDelegator
            dlg = wcls(team, os.path.basename(out.rstrip("\\/")), unc, token, keep_warm, lead_order_id=own_desk_id(out))
        else:
            # rigby as the helper: its pieces are Tolkien `llm` orders placed through the desk by capability
            cls = (a1_delegate.PlacedDelegator if team.get("placed")
                   else a1_delegate.RigbyDelegator if team.get("helper") == "rigby" else a1_delegate.Delegator)
            dlg = cls(team, os.path.basename(out.rstrip("\\/")), unc, token, keep_warm)
            if hasattr(dlg, "reserve"):          # one layer at a time: this run holds rigby's lane until it ends
                print("rigby reservation slip %s" % ("written" if dlg.reserve(own_desk_id(out)) else "NOT written"),
                      flush=True)
        res["team"] = team
    review = plan.get("review")
    rev = None
    if review:                     # A1-review: a DIFFERENT model on another box reads the artifact once (a1_delegate)
        import a1_delegate

        def token2():
            import p20_foundry
            return p20_foundry._worker_token()

        def keep_warm2():
            try:
                req = urllib.request.Request(host + "/api/generate", method="POST",
                                             data=json.dumps({"model": model, "keep_alive": "10m"}).encode("utf-8"),
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=60).read()
            except Exception as e:                                       # noqa: BLE001
                print("keep-warm failed (%r)" % (e,), flush=True)
        rev = a1_delegate.Delegator(review, os.path.basename(out.rstrip("\\/")),
                                    os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[2]))), token2, keep_warm2,
                                    prefix="a1review-")
        res["review"] = review
    stop_rule = plan.get("stop_rule")             # end the arm once it is clearly worse than its baseline (a1_stop)
    strata = None                                 # (module, proc, port) while this order's Strata server runs
    try:
        if engine == "strata":
            S = strata_module()
            port = int(strata_cfg.get("port", 8080))
            bat, src = S.prepare(strata_cfg, out, res["placement"])
            res["placement"]["serving"] = S.apply_serving(strata_cfg, src)   # the serving plan's env + engine flags
            assert S.port_free(port), "port %d is already in use - not starting a second server beside it" % port
            S.enter()
            strata = (S, None, port)
            proc, ok, load_s = S.launch(bat, src, port, out)
            strata = (S, proc, port)
            res["placement"]["load_s"] = load_s
            if not ok:
                raise RuntimeError("Strata died before answering (rc %s) - see server.log" % proc.returncode)
            print("strata up in %.0f s on %s" % (load_s, host), flush=True)
            # the deck's LIVE read / write / context rows: the factory polls Strata's own /metrics each beat
            # (p20_foundry.EngineRates reads this declaration; the same file infer_ollama --strata writes)
            with open(os.path.join(out, "engine_live.json"), "w", encoding="utf-8") as f:
                json.dump({"engine": "strata", "metrics": host.rstrip("/") + "/metrics",
                           "ctx": int(plan.get("num_ctx") or 0) or None}, f)
        for i, n in enumerate(names, 1):
            if n in rows:
                continue
            if stop_rule:
                import a1_stop
                verdict = a1_stop.decide(rows, names, stop_rule)
                if verdict is not None:
                    res["stopped_early"] = verdict
                    save(rpath, res)
                    print("[a1] STOPPED EARLY: %s" % verdict["why"], flush=True)
                    break
            if V.lead_alone(list(rows.values()), team):
                # the arm no longer tests delegation (its question): stop it, named - the run flag already ALERTed
                res["stopped_lead_alone"] = {"why": V.LEAD_ALONE % team.get("mode"), "answered": len(rows)}
                save(rpath, res)
                print("[a1] STOPPED: %s" % res["stopped_lead_alone"]["why"], flush=True)
                break
            turns = []
            conv = {"messages": None}    # the loop appends to ONE list, so this ends holding every tool result too
            part = os.path.join(out, "transcripts", n + ".partial.json")
            # one compaction state per exercise, calibrated on the server's own count: ollama's prompt_eval_count is
            # the FULL prompt even when its cache is reused
            cc = C.CompactingChat(lab, turn_cap, lab_loop.run, calibrate=True)

            def chat_fn(messages, tools):
                conv["messages"] = messages
                # checkpoint before every model call: a stop then costs only the turn in flight (resume replays the rest)
                save(part, {"exercise": n, "turns": turns, "conversation": messages})
                t0 = time.time()
                try:
                    o, why = cc.call(messages, tools)
                except RuntimeError as e:
                    if not _is_tool_parse_error(e):
                        raise
                    turns.append({"wall_s": round(time.time() - t0, 2), "done_reason": "invalid_tool_call",
                                  "eval_count": None, "error": str(e)[:300], "tool_calls": []})
                    return {"content": "", "tool_calls": [], "invalid_tool_call": str(e)}
                if o is None:
                    turns.append({"wall_s": round(time.time() - t0, 2), "done_reason": "context_full",
                                  "eval_count": None, "tool_calls": []})
                    return {"content": "", "tool_calls": [], "context_full": True}
                mapped = C.from_output(o, turn_cap)
                meta = o.metadata or {}
                srv = meta.get("server") or {}
                ev, ed = srv.get("eval_count"), srv.get("eval_duration")
                turns.append({"wall_s": round(time.time() - t0, 2),
                              "done_reason": "runaway" if why == "runaway" else meta.get("done_reason"),
                              "eval_count": ev, "prompt_eval_count": srv.get("prompt_eval_count"),
                              "load_s": round(srv["load_duration"] / 1e9, 2) if srv.get("load_duration") else None,
                              "prompt_eval_s": (round(srv["prompt_eval_duration"] / 1e9, 2)
                                                if srv.get("prompt_eval_duration") else None),
                              "tok_s": round(ev / (ed / 1e9), 2) if ev and ed else None,
                              "thinking_chars": meta.get("thinking_chars", 0), "content": mapped.get("content", ""),
                              "tool_calls": mapped.get("tool_calls", [])})
                return mapped

            t_start = time.time()
            if dlg is not None:
                dlg.exercise = n
            team_kw = {}
            if dlg is not None and (team or {}).get("errands"):
                # ERRAND-team (Phase 7 "Two shapes of delegation"): look-only helpers through the window while the lead
                # keeps working; the delegate tool is NOT offered, so the arm differs from A1-solo in this one tool
                team_kw["errand_fn"] = dlg.turn
            elif dlg is not None:
                team_kw["delegate_fn"] = dlg
                brief = H.TEAM_BRIEFS.get((team or {}).get("mode") or "organic")   # directed / mandatory: told to
                if brief:
                    team_kw["delegate_brief"] = brief
            if plan.get("finish_gate"):              # finish refused until the lead's own tests pass on the current code
                team_kw["finish_gate"] = True
            if plan.get("behaviour"):                # a behaviour arm: one injected event, scored apart (H.BEHAVIOURS)
                team_kw["behaviour"] = plan["behaviour"]
            if rev is not None:
                rev.exercise = n
                team_kw["review_fn"] = rev
            replay, resumed = None, None
            if os.path.exists(part):             # stopped mid-exercise: replay its checkpoint, then go live
                ck = json.load(open(part, encoding="utf-8"))
                turns.extend(ck.get("turns") or [])
                resumed = B.ReplayChat(ck, "end", chat_fn)
                print("[a1] %s: resuming from checkpoint, %d turn(s) replayed" % (n, resumed.k), flush=True)
            elif parent is not None and n in parent:
                replay = B.ReplayChat(parent[n], branch["point"], chat_fn, record=turns)
            row = H.run_task(os.path.join(EXERCISES, n), resumed or replay or chat_fn, mode=mode, work_root=work,
                             **team_kw)
            t_end = time.time()
            if resumed is not None:
                row["resumed"] = dict(resumed.rec, valid=resumed.valid())
                row["wall_s_live"] = row.get("wall_s")
                row["wall_s"] = round((row.get("wall_s") or 0) + resumed.rec["prefix_wall_s"], 2)
                if parent is not None:
                    row["branch"] = {"available": True, "resumed_from_checkpoint": True}
            elif parent is not None:
                if replay is None:
                    row["branch"] = {"available": False, "why": "no parent transcript for this exercise - ran fresh"}
                else:
                    row["branch"] = dict(replay.rec, valid=replay.valid())
            if dlg is not None:                  # waiting on helpers is priced apart from the lead's own turns
                mine = [p for p in dlg.pieces if p.get("exercise") == n]
                row["delegations"] = mine
                row["delegate_wait_s"] = round(sum((p.get("done") or t_end) - p["submitted"] for p in mine), 1)
            if rev is not None:                  # the review order and its wait, priced apart as well
                mine = [p for p in rev.pieces if p.get("exercise") == n]
                row["review_pieces"] = mine
                row["review_wait_s"] = round(sum((p.get("done") or t_end) - p["submitted"] for p in mine), 1)
            row.update({"at": datetime.datetime.fromtimestamp(t_start).isoformat(timespec="seconds"),
                        "t0": round(t_start, 2), "t1": round(t_end, 2)})
            if meter.source and meter.source != "telemetry-csv":     # live samples: price the row now (survives a stop)
                row["gpu_j"] = meter.joules(t_start, t_end)
            ev = sum(t["eval_count"] or 0 for t in turns)
            gen_s = sum(t["wall_s"] for t in turns)
            capped = [t["done_reason"] for t in turns if t["done_reason"] in ("length", "loop", "priced_out")]
            at_ceiling = V.at_ceiling(turns, num_ctx)      # a call that filled the whole tested context: scored, not void
            row["compactions"] = cc.compactions
            # tok_s = tokens written / wall of the turns (re-reading the conversation included); decode_tok_s = the
            # engine's own decode rate over the same turns - the one every tile calls tok/s
            dec_s = sum(t["eval_count"] / t["tok_s"] for t in turns if t.get("eval_count") and t.get("tok_s"))
            dec_n = sum(t["eval_count"] for t in turns if t.get("eval_count") and t.get("tok_s"))
            row.update({"exercise_dir": n, "model_calls": len(turns), "eval_tokens": ev,
                        "tok_s": round(ev / gen_s, 2) if gen_s else None,
                        "decode_tok_s": round(dec_n / dec_s, 2) if dec_s else None, "capped_turns": capped,
                        "length_at_ceiling": at_ceiling, "num_ctx": num_ctx})
            grade_output = row.pop("grade_output", "")
            rows[n] = row
            # both sides (LAB-TOOL-GAPS): the per-call stats, the whole conversation as the model saw it (tool
            # results included), and the final grade's full output - auditable without a replay
            save(os.path.join(out, "transcripts", n + ".json"),
                 {"exercise": n, "row": row, "turns": turns, "conversation": conv["messages"] or [],
                  "grade_output": grade_output})
            res["rows"] = list(rows.values())
            save(rpath, res)
            if os.path.exists(part):             # the full transcript supersedes the checkpoint
                os.remove(part)
            why = V.verdict(row)                 # measured the instrument, not the model: rerun once, never score it
            if why:
                row["void"] = why
                if n not in retried:
                    retried.add(n)
                    res.setdefault("void_rows", []).append(row)
                    rows.pop(n)
                    res["rows"] = list(rows.values())
                    save(rpath, res)
                    tpath = os.path.join(out, "transcripts", n + ".json")
                    os.replace(tpath, os.path.join(out, "transcripts", n + ".void1.json"))
                    names.append(n)
                    print("ALERT: A1 %s: %s VOID (%s) - running it once more" % (order_name, n, why), flush=True)
                    if "engine/harness error" in why:
                        wait_for_engine(host, n)
                    continue
                res["rows"] = list(rows.values())
                save(rpath, res)
                print("ALERT: A1 %s: %s VOID twice (%s) - recorded as void, not scored" % (order_name, n, why),
                      flush=True)
            for flag in V.run_flags(list(rows.values()), plan):
                if flag not in flagged:
                    flagged.add(flag)
                    print("ALERT: A1 %s: %s" % (order_name, flag), flush=True)
            valid = [r for r in rows.values() if not r.get("void")]
            print("[a1] %d/%d %s %s %s/%s · solved %d/%d · void %d · %d turns · %.0f s%s%s" % (
                len(rows), total, n, "VOID" if row.get("void") else ("PASS" if row.get("passed") else "fail"),
                row.get("tests_passed"), row.get("tests_total"), sum(1 for r in valid if r.get("passed")),
                len(valid), len(rows) - len(valid), row.get("turns") or 0, row.get("wall_s") or 0,
                " · CAPPED %s" % capped if capped else "",
                (" · %.1f tok/s" % row["decode_tok_s"]) if row.get("decode_tok_s") else ""), flush=True)
    finally:
        if strata is not None:                    # the server ends with the run, by ANY route (stop never raises)
            strata[0].stop(strata[1], strata[2])
        shutil.rmtree(work, ignore_errors=True)
        if dlg is not None and hasattr(dlg, "close"):   # the helper window ends with the run, by ANY route
            try:
                res["window"] = dlg.close()
            except Exception as e:                                       # noqa: BLE001
                print("helper window close failed (%r)" % (e,), flush=True)
        try:                                          # nothing in cleanup may raise ()
            meter.stop()
            for r in rows.values():
                if r.get("t0") and r.get("t1") and r.get("gpu_j") is None and meter.source:
                    r["gpu_j"] = meter.joules(r["t0"], r["t1"])
            res["energy"] = {"source": meter.source, "samples": len(meter.samples), "idle_w": None}
            span = (idle[1] - idle[0]) if idle[1] else 0
            idle_j = meter.joules(*idle) if meter.source and span > 0 else None
            if idle_j:
                res["energy"]["idle_w"] = round(idle_j / span, 1)
            res["rows"] = list(rows.values())
            save(rpath, res)
        except Exception as e:                                       # noqa: BLE001
            print("energy bookkeeping failed (%r) - rows keep their timings" % (e,), flush=True)
    solved = sum(1 for r in rows.values() if r.get("passed") and not r.get("void"))
    errors = sum(1 for r in rows.values() if r.get("ended") == "error")
    void = sum(1 for r in rows.values() if r.get("void"))
    res.update({"solved": solved, "answered": len(rows), "total": total, "errors": errors, "void": void,
                "run_flags": sorted(flagged), "result": "ok" if len(rows) == total else "incomplete"})
    save(rpath, res)
    print("A1 %s %s · solved %d/%d · void %d · errors %d" % (model, mode, solved, total, void, errors), flush=True)
    return 0 if res["result"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
