"""A1 / A1-team -> PRICED ENTRIES (the deliverable: 34-Shape-Catalogue scorecard, Phase 7's inputs), never a chat read-out.

    python reduce_a1.py --gpu <desk order name> [--gpu...] --rigby <tolkien order name> [--rigby...] [--label x]

Per arm (one delivered order): the same exercises side by side - success, tests, turns, wall, energy, pieces, the
lead's waiting time; per piece - helper, host, wall, size, status; totals per arm with energy per solved and the
scorecard's spread. Energy = the WHOLE BOX at the wall, the menu's own rule; rigby
= its whole box (menu_policy.rigby_watts: era idle + the placement's working watts) x wall; tablet pieces are reported as
EFFORT (tablet-seconds), the scorecard's unit where no per-node meter exists - never a made-up joule. Output: markdown + json under workspace\\a1\\summary\\, printed too. Every table
is stamped PRELIMINARY until a shape reproduces once (scorecard rule)."""
import datetime
import glob
import json
import os
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import order_paths as op  # noqa: E402
import menu_wall as MW  # noqa: E402  the whole-box wall price per row window - one derivation with the menu
import menu_policy as MPOL  # noqa: E402  rigby_watts - rigby's whole box, the menu's rule
sys.path.insert(0, str(HERE))
import a1_validity as V  # noqa: E402

import menu_paths as MP  # noqa: E402  the tablet desk and the foundry mirror: settings
OUTBOX = pathlib.Path(MP.need("OUTBOX"))
SUMMARY = pathlib.Path(MP.FOUNDRY_MIRROR) / "workspace" / "a1" / "summary"
DELEGATED = pathlib.Path(MP.FOUNDRY_MIRROR) / "workspace" / "a1" / "delegated"   # helper + review orders' deliveries


def wall_j(host, t0, t1, card_j, wall_s):
    """The whole box at the wall for one window (menu_wall.price); the card meter when the box has no wall price."""
    w = MW.price(host, t0, t1, card_j, wall_s)["wall_j"]
    return w if w is not None else (float(card_j) if card_j not in (None, "") else None)


def piece_energy(p):
    """A GPU piece's joules, the WHOLE helper box at the wall: the helper's own infer_ollama row (`gpu_j` + its
    window), read from the delivered piece by its step name - the one source every arm has. None when unmetered."""
    if p.get("helper_gpu_j") is not None:
        return wall_j(p.get("host"), None, None, p["helper_gpu_j"], p.get("wall_s"))
    step = p.get("step")
    if not step:
        return None
    out = DELEGATED / step / "out.json"
    try:
        d = json.load(open(out, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = d.get("rows") or []
    if p.get("item"):                       # a helper WINDOW delivers every piece's row in one out.json: find by id
        rows = [r for r in rows if r.get("id") == p["item"]]
    if not rows or rows[0].get("gpu_j") is None:
        return None
    r = rows[0]
    return wall_j(d.get("host") or p.get("host"), r.get("t0"), r.get("t1"), r["gpu_j"], r.get("wall_s"))


LEAD_KEYS = ("model", "think", "num_ctx", "mode", "task_set", "placement")


def lead_settings(r):
    """The lead's own settings as its results file states them (the policy table reads them from the summary)."""
    return {k: r.get(k) for k in LEAD_KEYS if r.get(k) is not None}


def gpu_arm(name, results_path=None, host=None):
    """One GPU order's rows keyed by exercise. `results_path` / `host` override the desk lookup (tests)."""
    if results_path is None:
        recs = op.desk_records(name)
        assert recs, "no desk record for %s" % name
        host = host or recs[0].get("host") or recs[0].get("claimed_by")
        deliv = op.delivered(recs[0])
        assert deliv, "%s not delivered" % name
        results_path = os.path.join(deliv[0], "results.json")
    with open(results_path, encoding="utf-8") as fh:
        r = json.load(fh)
    host = host or r.get("host")
    rows = {}
    for x in r.get("rows") or []:
        rows[_exercise(x)] = _gpu_row(x, host)
    ceiling = ceiling_attempts(r, os.path.join(os.path.dirname(results_path), "transcripts"))
    for ex, (x, k, rerun) in ceiling.items():
        rows[ex] = dict(_gpu_row(x, host), ceiling_attempt=k, rerun_passed=rerun)
    plan = r.get("plan") or {}
    return {"name": name, "layer": "gpu", "team": r.get("team"), "rows": rows, "lead": lead_settings(r),
            "finish_gate": bool(plan.get("finish_gate")), "review": r.get("review") or plan.get("review"),
            "behaviour": plan.get("behaviour"), "stopped_early": r.get("stopped_early"), "branch": r.get("branch"),
            "ceiling": {ex: (k, bool(x.get("passed")), rerun) for ex, (x, k, rerun) in ceiling.items()}}


def _exercise(x):
    return (x.get("exercise") or x.get("exercise_dir") or "").replace("-", "_")


def _gpu_row(x, host=None):
    tc = x.get("tool_counts") or {}
    p = MW.price(host, x.get("t0"), x.get("t1"), x.get("gpu_j"), x.get("wall_s"))
    return {"passed": bool(x.get("passed")), "tests": (x.get("tests_passed"), x.get("tests_total")),
            "turns": x.get("turns"), "wall_s": float(x.get("wall_s") or 0),
            "energy_j": round(p["wall_j"]) if p["wall_j"] is not None else None, "card_j": x.get("gpu_j"),
            "energy_src": "wall (%s)" % p["wall_src"] if p["wall_j"] is not None else "unmetered",
            "pieces": list(x.get("delegations") or []),
            "wait_s": float(x.get("delegate_wait_s") or 0), "ended": x.get("ended"),
            "caps": x.get("cap_hits") or x.get("capped") or x.get("capped_turns"),
            # finish gate (rung 1): refusals and own-test runs; A1-review (rung 2): verdict, repair, its wait
            "finish_refused": int(tc.get("finish_refused") or 0), "own_tests_runs": int(tc.get("own_tests_runs") or 0),
            "review": review_of(x), "review_pieces": list(x.get("review_pieces") or []),
            "review_wait_s": float(x.get("review_wait_s") or 0),
            # which hidden tests a fail missed (recorded per row since LAB-TOOL-GAPS #5): the one-test-short
            # misses the gate and review arms exist to close are named, so a reader sees WHICH case moved
            "misses": [str(f.get("test", "")).rsplit(".", 1)[-1] for f in (x.get("failing") or [])],
            "behaviour": x.get("behaviour")}


def ceiling_attempts(r, transcripts_dir):
    """Attempts the order VOIDED for a `length` cut that in fact filled the model's whole tested context: under repair (a1_validity.at_ceiling) such an attempt is the model's result, scored as graded, and the re-run
    it triggered is discarded. The order's own code applies this live 10:50; this re-reads the attempts
    the earlier code voided (their per-call counts are in `transcripts/<exercise>.voidN.json`) so one rule reads every
    row. {exercise: (void attempt row, attempt number, the kept re-run's passed or None)} - the FIRST such attempt."""
    out = {}
    num_ctx = r.get("num_ctx") or (r.get("plan") or {}).get("num_ctx")
    scored = {_exercise(x): x for x in r.get("rows") or []}
    for v in r.get("void_rows") or []:
        ex = _exercise(v)
        if ex in out:
            continue
        ex_dir = v.get("exercise_dir") or (v.get("exercise") or "").replace("_", "-")
        for k in (1, 2):
            p = os.path.join(transcripts_dir, "%s.void%d.json" % (ex_dir, k))
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as fh:
                turns = (json.load(fh) or {}).get("turns") or []
            if V.at_ceiling(turns, num_ctx):
                out[ex] = (v, k, bool(scored[ex].get("passed")) if ex in scored else None)
                break
    return out


def behaviour_word(b):
    """One row's behaviour record as a short verdict: the arm's own result, plus UNVERIFIED when it finished failing
    code with no test after its last edit (recorded on every run)."""
    if not b:
        return ""
    arm, parts = b.get("arm"), []
    if arm == "recover":
        parts.append("recover %s%s" % (b.get("recover_action", "?"), " ✓" if b.get("recovered") else ""))
    elif arm == "correct":
        parts.append("correction %s" % ("PASS" if b.get("correction_pass") else
                                        "fail (missing %s)" % ",".join(b.get("correction_missing") or []) if not
                                        b.get("correction_applied") else "fail (broke tests)"))
    elif arm == "plan":
        parts.append("plan %s" % ("kept" if b.get("plan_kept") else "broken (new %s; changed %s)" % (
            ",".join(b.get("plan_new_files") or []) or "-", ",".join(b.get("plan_signature_changes") or []) or "-")))
    if b.get("unverified_success"):
        parts.append("UNVERIFIED")
    return " · ".join(parts)


def review_of(x):
    """One row's A1-review outcome as a word the table can carry: none (no reviewer) / clean / error /
    repaired (findings, the lead changed the workspace) / unchanged (findings, the lead finished as it was)."""
    rv = x.get("review")
    if not rv:
        return None
    if rv.get("error"):
        word = "error"
    elif rv.get("clean"):
        word = "clean"
    else:
        word = "repaired" if rv.get("repaired") else "unchanged"
    return {"word": word, "findings_chars": rv.get("findings_chars"), "wall_s": rv.get("wall_s")}


def rigby_arm(name, results_path=None):
    """One Tolkien llm order's rows keyed by task (the newest results generation in the outbox)."""
    if results_path is None:
        hits = sorted(glob.glob(str(OUTBOX / ("%s.*.results.json" % name))), key=os.path.getmtime)
        assert hits, "%s has no results in the outbox" % name
        results_path = hits[-1]
    r = json.load(open(results_path, encoding="utf-8"))
    rows = {}
    team = None
    not_measured = []
    for x in r.get("rows") or []:
        if str(x.get("task", "")).startswith("control"):
            continue
        team = team or x.get("team")
        if x.get("ran") is False:            # cut by a stop slip / priced out: not a measurement (tolkien_llm CUT rows)
            not_measured.append("%s (%s)" % (x.get("task"), x.get("finish_reason") or x.get("error") or "not run"))
            continue
        wall = float(x.get("wall_s") or 0)
        tc = x.get("tool_counts") or {}
        watts, why = MPOL.rigby_watts(x.get("model"))
        rows[str(x["task"]).replace("-", "_")] = {
            "passed": bool(x.get("ok")), "tests": (x.get("checks_passed"), x.get("checks_total")),
            "turns": x.get("turns"), "wall_s": wall, "energy_j": round(wall * watts), "card_j": None,
            "energy_src": why,
            "pieces": list(x.get("delegations") or []), "wait_s": float(x.get("delegate_wait_s") or 0),
            "ended": x.get("ended"), "caps": x.get("cap_hits") or x.get("finish_reason"),
            # the same gate / review / misses fields as a GPU row
            "finish_refused": int(tc.get("finish_refused") or 0), "own_tests_runs": int(tc.get("own_tests_runs") or 0),
            "review": review_of(x), "review_pieces": list(x.get("review_pieces") or []),
            "review_wait_s": float(x.get("review_wait_s") or 0),
            "misses": [str(f.get("test", "")).rsplit(".", 1)[-1] for f in (x.get("failing") or [])],
            "behaviour": x.get("behaviour")}
    first = next((x for x in (r.get("rows") or []) if not str(x.get("task", "")).startswith("control")), {})
    return {"name": name, "layer": "rigby", "team": team, "rows": rows, "usable": r.get("usable"), "lead": lead_settings(r),
            "stopped": r.get("stopped"), "not_measured": not_measured,
            "finish_gate": bool(first.get("finish_gate")), "review": first.get("review_spec")}


# Exercises that cannot separate settings or models: shown in every table, left out of the headline score, raw kept beside
# it. paasio: its hidden `test_meteredfile_iteration` patches `paasio.super` with a mock, so a correct
# wrapper that calls `self.readline()` fails it; the contract is in no brief and the test is in the hidden half - xhigh,
# medium and thinking-off all fail exactly that one test (reference.meta/example.py passes 25/25).
NON_DISCRIMINATING = {"paasio": "mock contract (super() for every read) the model is never shown"}


def totals(arm):
    rows = arm["rows"]
    solved = sum(1 for x in rows.values() if x["passed"])
    scored = {e: x for e, x in rows.items() if e not in NON_DISCRIMINATING}
    walls = [x["wall_s"] for x in rows.values()]
    energy = [x["energy_j"] for x in rows.values() if x["energy_j"] is not None]
    pieces = [p for x in rows.values() for p in x["pieces"]]
    reviews = [x["review"]["word"] for x in rows.values() if x.get("review")]
    pj = [piece_energy(p) for x in rows.values() for p in x["pieces"] + (x.get("review_pieces") or [])]
    pj_known = [j for j in pj if j is not None]
    pieces_j = round(sum(pj_known)) if pj_known else None
    return {"n": len(rows), "solved": solved, "wall_s": round(sum(walls), 1),
            "scored_n": len(scored), "scored_solved": sum(1 for x in scored.values() if x["passed"]),
            "pieces_energy_j": pieces_j, "pieces_metered": "%d/%d" % (len(pj_known), len(pj)),
            "energy_incl_pieces_per_solved_j": (round((sum(energy) + (pieces_j or 0)) / solved)
                                                if energy and solved else None),
            "wall_median_s": round(statistics.median(walls), 1) if walls else None,
            "wall_spread_s": (round(min(walls), 1), round(max(walls), 1)) if walls else None,
            "energy_j": round(sum(energy)) if energy else None,
            "energy_per_solved_j": round(sum(energy) / solved) if energy and solved else None,
            "energy_rows": len(energy), "pieces": len(pieces),
            "pieces_done": sum(1 for p in pieces if p.get("status") == "done"),
            "wait_s": round(sum(x["wait_s"] for x in rows.values()), 1),
            "lead_turns": sum(int(x["turns"] or 0) for x in rows.values()),
            "capped_rows": sum(1 for x in rows.values() if x.get("caps")),
            "finish_refused": sum(int(x.get("finish_refused") or 0) for x in rows.values()),
            "reviews": {w: reviews.count(w) for w in ("clean", "repaired", "unchanged", "error") if w in reviews},
            "review_wait_s": round(sum(float(x.get("review_wait_s") or 0) for x in rows.values()), 1),
            # behaviour (A1 spec "Work behaviour, scored by code"): unverified success on every run that recorded it,
            # and the behaviour arm's own pass count (recovered / correction passed / plan kept)
            "verify_recorded": sum(1 for x in rows.values() if "verified_before_finish" in (x.get("behaviour") or {})),
            "unverified": sum(1 for x in rows.values() if (x.get("behaviour") or {}).get("unverified_success")),
            "behaviour_pass": sum(1 for x in rows.values() if (x.get("behaviour") or {}).get(
                {"recover": "recovered", "correct": "correction_pass", "plan": "plan_kept"}.get(
                    (x.get("behaviour") or {}).get("arm"), "-"))),
            "behaviour_rows": sum(1 for x in rows.values() if (x.get("behaviour") or {}).get("arm"))}


def markdown(arms, label):
    exercises = sorted(set(e for a in arms for e in a["rows"]))
    short = {a["name"]: a["name"].split(".")[0].replace("a1v22-py-agentic-hai-", "").replace("a1v22-rigby-", "")
             for a in arms}
    out = ["# A1 / A1-team priced entries — %s (PRELIMINARY: n=1 per arm; reproduces-once not met)" % label,
           "", "Generated %s by hard-bench\\a1\\reduce_a1.py. Energy: the WHOLE box at the wall (menu_wall: measured from "
           "the box's wall log, else its fitted line on the card meter `gpu_j`); rigby = wall x its whole box "
           "(menu_policy.rigby_watts); tablet pieces = effort in tablet-seconds, no joule claimed."
           % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    out.append("| exercise | " + " | ".join(short[a["name"]] for a in arms) + " |")
    out.append("|---|" + "---|" * len(arms))
    for e in exercises:
        cells = []
        for a in arms:
            x = a["rows"].get(e)
            if not x:
                cells.append("—")
                continue
            cells.append("%s %s/%s · %st · %.0fs · %s J · %d piece(s)%s%s%s%s" % (
                "PASS" if x["passed"] else "fail", x["tests"][0], x["tests"][1], x["turns"], x["wall_s"],
                "%.0f" % x["energy_j"] if x["energy_j"] is not None else "?", len(x["pieces"]),
                (" wait %.0fs" % x["wait_s"]) if x["pieces"] else "",
                (" · gate refused ×%d" % x["finish_refused"]) if x.get("finish_refused") else "",
                (" · review %s%s" % (x["review"]["word"], (" wait %.0fs" % x["review_wait_s"]) if x.get("review_wait_s") else ""))
                if x.get("review") else "",
                (" · CAPPED %s" % x["caps"]) if x.get("caps") else ""))
            if behaviour_word(x.get("behaviour")):
                cells[-1] += " · " + behaviour_word(x.get("behaviour"))
            if not x["passed"] and x.get("misses"):
                cells[-1] += " · misses: " + ", ".join(x["misses"][:4]) + (" +%d" % (len(x["misses"]) - 4) if len(x["misses"]) > 4 else "")
        label = ("%s *(non-discriminating: %s)*" % (e, NON_DISCRIMINATING[e])) if e in NON_DISCRIMINATING else e
        out.append("| %s | %s |" % (label, " | ".join(cells)))
    out += ["", "| arm | layer | team | solved | wall total | wall median (min..max) | energy | J per solved | pieces | lead wait | lead turns | gate | review | capped rows |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in arms:
        t = totals(a)
        team = a.get("team") or {}
        rv = a.get("review") or {}
        out.append("| %s | %s | %s | **%d/%d** (raw %d/%d) | %.0f s | %s (%s..%s) | %s J (%d rows) | %s | %d (%d done) | %.0f s | %d | %s | %s | %d |" % (
            short[a["name"]], a["layer"], ("%s %s %s" % (team.get("helper"), team.get("helper_model"), team.get("mode") or "organic"))
            if team else "solo", t["scored_solved"], t["scored_n"], t["solved"], t["n"], t["wall_s"], t["wall_median_s"], *(t["wall_spread_s"] or ("?", "?")),
            t["energy_j"] if t["energy_j"] is not None else "?", t["energy_rows"],
            t["energy_per_solved_j"] if t["energy_per_solved_j"] is not None else "?", t["pieces"], t["pieces_done"],
            t["wait_s"], t["lead_turns"],
            ("on, %d refusal(s)" % t["finish_refused"]) if a.get("finish_gate") else "off",
            ("%s %s: %s, wait %.0f s" % (rv.get("helper"), rv.get("helper_model"),
                                         ", ".join("%d %s" % (n, w) for w, n in t["reviews"].items()) or "none yet",
                                         t["review_wait_s"])) if rv else "none",
            t["capped_rows"]))
    if any(totals(a)["verify_recorded"] or a.get("behaviour") for a in arms):
        out += ["", "| arm | behaviour arm | arm verdict | unverified success (finished failing code, no test after its "
                "last edit) |", "|---|---|---|---|"]
        for a in arms:
            t = totals(a)
            out.append("| %s | %s | %s | %s |" % (
                short[a["name"]], a.get("behaviour") or "—",
                ("**%d/%d** %s" % (t["behaviour_pass"], t["behaviour_rows"], {"recover": "recovered", "correct":
                 "correction passed", "plan": "plan kept"}.get(a.get("behaviour"), ""))) if a.get("behaviour") else "—",
                ("%d of %d recorded runs" % (t["unverified"], t["verify_recorded"])) if t["verify_recorded"]
                else "not recorded (harness before 2026-10-07)"))
    for a in arms:
        t = totals(a)
        if t["pieces_energy_j"] is not None:
            out.append("")
            out.append("%s: pieces' own board joules (helper + review orders, metered on their box, %s metered): "
                       "%d J → **%s J per solved incl. pieces**" % (
                           short[a["name"]], t["pieces_metered"], t["pieces_energy_j"],
                           t["energy_incl_pieces_per_solved_j"] if t["energy_incl_pieces_per_solved_j"] is not None else "?"))
    if any(t_ for t_ in (totals(a)["capped_rows"] for a in arms)):
        out += ["", "⚠ Capped rows (doctrine 13a): a row whose turns hit a limit (`length`, `loop`, `priced_out`) measures the limit "
                "as much as the model; the count is stated per arm above and the exercise cell names the cap."]
    for a in arms:
        if a.get("ceiling"):                # repair applied to attempts the earlier code voided
            out += ["", "⛰ **%s: scored at the model's own context ceiling** (a1_validity.at_ceiling, repair 2026-10-09): "
                    "a `length` cut that filled the whole tested `num_ctx` is the model's result, so the attempt is scored "
                    "as graded and the re-run it triggered is discarded - %s." % (
                        short[a["name"]], "; ".join(
                            "%s attempt %d %s%s" % (ex, k, "PASS" if p else "fail",
                                                    "" if rr is None else " (re-run %s discarded)" % ("PASS" if rr else "fail"))
                            for ex, (k, p, rr) in sorted(a["ceiling"].items())))]
        if a.get("stopped_early"):          # a partial count must never read as a full run
            s = a["stopped_early"]
            out += ["", "⏹ **%s STOPPED EARLY** (a1_stop): %s; not run: %s." % (
                short[a["name"]], s.get("why"), ", ".join(s.get("not_run") or []) or "-")]
        if a.get("branch"):
            out += ["", "⑂ **%s is BRANCHED** from `%s` at `%s` (a1_branch): paired with its parent; its live part is "
                    "priced, the parent's prefix is in each row's `branch.prefix_wall_s`." % (
                        short[a["name"]], a["branch"].get("from"), a["branch"].get("point"))]
    for a in arms:
        if a.get("not_measured"):
            out.append("")
            out.append("%s: not measured (cut, not graded): %s" % (short[a["name"]], ", ".join(a["not_measured"])))
    pieces = [(a, e, "piece", p) for a in arms for e, x in sorted(a["rows"].items()) for p in x["pieces"]]
    pieces += [(a, e, "review", p) for a in arms for e, x in sorted(a["rows"].items()) for p in x.get("review_pieces") or []]
    if pieces:
        out += ["", "## Pieces (per worker) — helper pieces and review orders", "",
                "| arm | exercise | kind | k | helper | host | status | helper wall | answer chars | lead wait | brief chars |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for a, e, kind, p in pieces:
            wait = (p.get("done") or 0) - (p.get("submitted") or 0) if p.get("done") else None
            t_ = (a.get("review") if kind == "review" else a.get("team")) or {}
            out.append("| %s | %s | %s | %s | %s %s | %s | %s | %s s | %s | %s | %s |" % (
                short[a["name"]], e, kind, p.get("k"), p.get("helper", t_.get("helper", "")),
                p.get("helper_model", t_.get("helper_model", "")), p.get("host", t_.get("helper", "?")),
                p.get("status"), p.get("helper_wall_s", "?"), p.get("answer_chars", "?"),
                ("%.0f s" % wait) if wait is not None else "?", p.get("brief_chars", "?")))
    else:
        out += ["", "No delegated pieces in these arms."]
    return "\n".join(out) + "\n"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    arms, label = [], "a1"
    i = 0
    while i < len(argv):
        if argv[i] == "--gpu":
            arms.append(gpu_arm(argv[i + 1]))
            i += 2
        elif argv[i] == "--rigby":
            arms.append(rigby_arm(argv[i + 1]))
            i += 2
        elif argv[i] == "--label":
            label = argv[i + 1]
            i += 2
        else:
            raise SystemExit("unknown arg %r" % argv[i])
    assert arms, "name at least one arm"
    md = markdown(arms, label)
    SUMMARY.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    (SUMMARY / ("%s-%s.md" % (stamp, label))).write_text(md, encoding="utf-8")
    (SUMMARY / ("%s-%s.json" % (stamp, label))).write_text(
        json.dumps({"label": label, "arms": [{"name": a["name"], "layer": a["layer"], "team": a.get("team"),
                                               "lead": a.get("lead") or {}, "finish_gate": a.get("finish_gate"),
                                               "review": a.get("review"), "totals": totals(a), "rows": a["rows"]}
                                              for a in arms]}, indent=1),
        encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(md)
    print("written:", SUMMARY / ("%s-%s.md" % (stamp, label)))


if __name__ == "__main__":
    main()
