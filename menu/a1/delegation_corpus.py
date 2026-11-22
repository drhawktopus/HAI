"""Delegation corpus: every brief a lead wrote, what the helper answered, whether the
lead used it, how the exercise ended, and what the piece cost - one JSONL row per piece, from delivered team arms.
This is the "lab experience" the system-directed delegation design (the advisor deciding whether and how to split)
and #35's training corpus read; it is computed from results already on the share, never from a new run.

    python delegation_corpus.py --gpu <delivered order name> [--gpu...] [--out <path.jsonl>]

Row: {arm, exercise, k, item/order, brief, brief_chars, helper, helper_model, answer, answer_chars, helper_wall_s,
      helper_tokens, helper_gpu_j, lead_wait_s, used_defs (helper function names the lead defined afterwards),
      pasted (a long helper line appears verbatim in a later lead write), lead_turns_before, lead_turns_after,
      exercise_passed, tests, misses (hidden tests the exercise still failed)}.
Default out: mothership-build\\workspace\\a1\\summary\\delegation-corpus-<stamp>.jsonl (+ a.md digest)."""
import datetime
import glob
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import order_paths as op  # noqa: E402

import menu_paths as MP  # noqa: E402  the foundry mirror: a setting
WS = pathlib.Path(MP.FOUNDRY_MIRROR) / "workspace" / "a1"
DELEGATED, DELEGATIONS, SUMMARY = WS / "delegated", WS / "delegations", WS / "summary"


def delivered_dir(name):
    recs = op.desk_records(name)
    assert recs, "no desk record for %s" % name
    deliv = op.delivered(recs[0])
    assert deliv, "%s not delivered" % name
    return pathlib.Path(deliv[0])


def brief_of(piece):
    """The brief as the lead wrote it: per-piece orders keep it in delegations\\<order>.json; window pieces in the
    window's items dir (<item>.item.json)."""
    order = piece.get("order") or ""
    if piece.get("item"):
        for p in glob.glob(str(WS / "windows" / order / (piece["item"] + ".item.json"))):
            try:
                return json.load(open(p, encoding="utf-8")).get("prompt") or ""
            except (OSError, ValueError):
                pass
    p = DELEGATIONS / (order + ".json")
    if p.exists():
        try:
            items = json.load(open(p, encoding="utf-8")).get("items") or []
            return (items[0].get("prompt") or "") if items else ""
        except (OSError, ValueError):
            return ""
    return ""


def answer_of(piece):
    step = piece.get("step") or ""
    p = DELEGATED / step / "out.json"
    if not p.exists():
        return ""
    try:
        rows = json.load(open(p, encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        return ""
    if piece.get("item"):
        rows = [r for r in rows if r.get("id") == piece["item"]]
    return (rows[0].get("answer") or rows[0].get("thinking") or "") if rows else ""


def lead_writes(messages):
    """[(message index, text written)] for every lead write in a transcript."""
    out = []
    for i, m in enumerate(messages):
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            if fn.get("name") in ("coding_create", "coding_patch", "write_file"):
                a = fn.get("arguments") or {}
                out.append((i, (a.get("content") or "") + "\n" + (a.get("new") or "")))
    return out


def usage(answer, messages, writes):
    """(used_defs, pasted, turns_before, turns_after): where the helper's answer landed in the lead's conversation."""
    defs = sorted(set(re.findall(r"^\s*def (\w+)\(", answer, re.M)))
    key = answer[:60]
    at = next((i for i, m in enumerate(messages) if m.get("role") == "tool" and key and key in (m.get("content") or "")),
              None)
    later = [w for i, w in writes if at is None or i > at]
    used = [d for d in defs if any(re.search(r"\bdef %s\(" % re.escape(d), w) for w in later)]
    long_line = max((l.strip() for l in answer.splitlines() if len(l.strip()) > 30), key=len, default="")
    pasted = bool(long_line) and any(long_line in w for w in later)
    n_assist = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    before = sum(1 for i in n_assist if at is not None and i < at)
    return used, pasted, before, len(n_assist) - before


def corpus(name):
    d = delivered_dir(name)
    res = json.load(open(d / "results.json", encoding="utf-8"))
    team = res.get("team") or {}
    rows = []
    for x in res.get("rows") or []:
        pieces = x.get("delegations") or []
        if not pieces:
            continue
        tpath = d / "transcripts" / (x["exercise_dir"] + ".json")
        try:
            tr = json.load(open(tpath, encoding="utf-8"))
        except (OSError, ValueError):
            tr = {}
        messages = tr.get("conversation") or tr.get("messages") or []
        writes = lead_writes(messages)
        for p in pieces:
            brief, answer = brief_of(p), answer_of(p)
            used, pasted, before, after = usage(answer, messages, writes)
            rows.append({
                "arm": name, "exercise": x["exercise"], "k": p.get("k"), "order": p.get("order"), "item": p.get("item"),
                "brief": brief, "brief_chars": len(brief), "helper": team.get("helper"), "helper_model": team.get("helper_model"),
                "mode": team.get("mode"), "answer": answer, "answer_chars": len(answer),
                "helper_wall_s": p.get("helper_wall_s"), "helper_tokens": p.get("helper_tokens"),
                "helper_gpu_j": p.get("helper_gpu_j"), "status": p.get("status"),
                "lead_wait_s": round((p.get("done") or 0) - (p.get("submitted") or 0), 1) if p.get("done") else None,
                "used_defs": used, "pasted": pasted, "lead_turns_before": before, "lead_turns_after": after,
                "exercise_passed": bool(x.get("passed")), "tests": [x.get("tests_passed"), x.get("tests_total")],
                "misses": [str(f.get("test", "")).rsplit(".", 1)[-1] for f in (x.get("failing") or [])]})
    return rows


def digest(rows):
    by_arm = {}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)
    out = ["# Delegation corpus digest — %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "",
           "| arm | pieces | used (def or pasted) | pasted verbatim | brief chars median | answer chars median | "
           "helper wall median | lead wait median | pieces on solved exercises |", "|---|---|---|---|---|---|---|---|---|"]
    import statistics as st
    for arm, rs in by_arm.items():
        med = lambda k: round(st.median([r[k] for r in rs if r.get(k) is not None]), 1) if any(r.get(k) is not None for r in rs) else "?"
        out.append("| %s | %d | %d | %d | %s | %s | %s s | %s s | %d |" % (
            arm.split(".")[0].replace("a1v22-py-agentic-hai-", ""), len(rs),
            sum(1 for r in rs if r["used_defs"] or r["pasted"]), sum(1 for r in rs if r["pasted"]),
            med("brief_chars"), med("answer_chars"), med("helper_wall_s"), med("lead_wait_s"),
            sum(1 for r in rs if r["exercise_passed"])))
    out += ["", "## Briefs (first 160 chars each)", ""]
    for r in rows:
        out.append("- **%s d%s** (%s, %d chars → %d chars, used: %s%s): %s" % (
            r["exercise"], r["k"], "PASS" if r["exercise_passed"] else "fail", r["brief_chars"], r["answer_chars"],
            ",".join(r["used_defs"]) or "-", " +pasted" if r["pasted"] else "", r["brief"][:160].replace("\n", " ")))
    return "\n".join(out) + "\n"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    names, out = [], None
    i = 0
    while i < len(argv):
        if argv[i] == "--gpu":
            names.append(argv[i + 1])
        elif argv[i] == "--out":
            out = pathlib.Path(argv[i + 1])
        else:
            raise SystemExit("unknown arg %r" % argv[i])
        i += 2
    assert names, "--gpu <delivered order name>"
    rows = [r for n in names for r in corpus(n)]
    assert rows, "no delegated pieces in %s" % names
    SUMMARY.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    out = out or (SUMMARY / ("delegation-corpus-%s.jsonl" % stamp))
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    md = digest(rows)
    pathlib.Path(str(out).rsplit(".", 1)[0] + ".md").write_text(md, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(md)
    print("written: %s (%d rows)" % (out, len(rows)))


if __name__ == "__main__":
    main()
