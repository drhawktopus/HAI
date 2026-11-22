"""ERRANDS for a Terminal-Bench lead ([[34-Terminal-Bench-Lead-Level-Grid-Plan]] § "ERRAND ARMS — design"): the lead
names bounded look-up jobs in an optional `"errands"` field; each runs on the HELPER's model window while the lead keeps
working, and comes back as a short report in the lead's next observation. Pure logic, no Harbor import (the subclass in
lab_terminus.py wires it to Terminus-2), so it is tested anywhere.

The helper may only LOOK (the A1 ERRAND_READONLY family), and runs on the A1 errand rules (a1_harness): look before
answering (else `unlooked`), the same commands 3 turns in a row or 8 turns with no new output ⇒ the answer-now nudge,
no report after it ⇒ `no_answer`. No clock anywhere.
"""
import json
import re

READ_ONLY = {"ls", "cat", "head", "tail", "grep", "egrep", "find", "wc", "stat", "file", "pwd", "du", "tree",
             "which", "whereis", "readlink", "realpath", "basename", "dirname", "sort", "uniq", "cut", "nl", "od",
             "xxd", "hexdump", "diff", "cmp", "md5sum", "sha256sum", "printenv", "id", "uname", "date", "ps", "df"}
# what turns one of those into a change or a second command; refused wherever it appears
FORBIDDEN = re.compile(r"(>|<|`|\$\(|;|&|\|\||\btee\b|-exec|-execdir|-ok\b|-delete|-fprint|-fls|\bsort\b[^|]*\s-o)")
REPEAT_END, STALL_END = 3, 8          # the A1 cutter's NO_PROGRESS_REPEAT / NO_PROGRESS_STALL

LEAD_ADDENDUM = """
Errands (optional): you may also include an "errands" field - a list of short briefs, e.g.
  "errands": [{"brief": "Find which file defines the CLI entry point and report its path and the function name"}]
Each errand goes to a helper that can only LOOK at this same machine (ls, cat, grep, find, ...) and reports a short
conclusion back in your next observation, while you keep working. Use errands for bounded look-ups that would take
you several steps; if one command answers it, run that command yourself instead.
"""

HELPER_BRIEF = """You are on a bounded ERRAND for a lead agent working in a Linux container. You can only LOOK.

Errand: {brief}

Reply with JSON only. To look, reply {{"commands": ["ls -la /app", "grep -rn main /app/src"]}} using read-only commands
(ls, cat, head, tail, grep, find, wc, stat, file, ...; no writing, no installing, no redirection). When you know the
answer, reply {{"report": "<the short conclusion the lead needs - paths, names, values - not file dumps>"}}.
Look before you answer."""
LOOK_FIRST = 'You have not looked yet. Run read-only commands first: reply {"commands": [...]}.'
ANSWER_NOW = 'Stop looking now and answer with what you found: reply {"report": "..."}.'

# B8 ([[34-Phase-7-Placement-Policy-Design]] § "Two shapes": "the next errand arm adds the tools a real errand has -
# FETCH and simple COMMANDS - with the answer key locked"). Terminal-Bench locks the key by construction (Harbor copies a
# task's tests in only at grading), so the guards here are: a RUN goes to the errand's PRIVATE COPY of the working
# directory and may name no path outside it (relative paths only, no installing, one command - no chaining), and a
# FETCH never reaches a benchmark source; every fetch is recorded for the post-run audit (fetch_audit.py), which VOIDs a
# trial whose fetched text holds its own tests. Mode "look" (the A4 arm) is unchanged byte for byte.
MODES = ("look", "commands")
RUNNERS = {"python", "python3", "node", "ruby", "perl", "php", "make", "cmake", "gcc", "g++", "cc", "clang", "go",
           "cargo", "rustc", "javac", "java", "pytest", "bash", "sh"}
FILE_OPS = {"mkdir", "touch", "cp", "mv", "rm", "sed", "awk", "echo", "printf", "tr", "patch", "tar", "gzip", "gunzip",
            "unzip", "chmod", "true", "false", "test", "expr", "seq", "xargs"}
CHAIN = re.compile(r"(;|&&|\|\||`|\$\(|(?:^|[^>&<])&(?:$|[^>&\d]))")
OUTSIDE = re.compile(r"(?:^|[\s'\"=(,:])(?:/(?!dev/null\b)|~)|\.\.")     # an absolute path, home, or a step up
INSTALL = re.compile(r"\b(?:pip3?|npm|apt|apt-get|yum|dnf|apk|conda|gem|sudo|su)\b|\bcargo\s+install\b|-m\s+(?:pip|venv)\b")
URL_OK = re.compile(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+$")
BENCHMARK_SOURCES = ("terminal-bench", "terminalbench", "tbench", "t-bench", "laude-institute")

LEAD_ADDENDUM_COMMANDS = """
Errands (optional): you may also include an "errands" field - a list of short briefs, e.g.
  "errands": [{"brief": "Run the visible tests in a copy of the project and report which fail and on what line"}]
Each errand goes to a helper that can LOOK at this same machine, RUN simple commands in its OWN COPY of the working
directory (so it can try code or run a check without touching your files) and FETCH a web page; it reports a short
conclusion back in your next observation, while you keep working. Use errands for bounded jobs that would take you
several steps; if one command answers it, run that command yourself instead.
"""

HELPER_BRIEF_COMMANDS = """You are on a bounded ERRAND for a lead agent working in a Linux container.

Errand: {brief}

Reply with JSON only, one of:
  {{"commands": ["ls -la /app", "grep -rn main /app/src"]}} - LOOK with read-only commands (ls, cat, head, tail, grep,
      find, wc, stat, file, ...), anywhere on the machine.
  {{"run": ["python3 check.py", "make test"]}} - RUN simple commands in YOUR OWN COPY of the working directory:
      relative paths only, one command each (no ; && ||), no installing. The lead's files are never changed.
  {{"fetch": "https://docs.python.org/3/library/csv.html"}} - read one web page.
  {{"report": "<the short conclusion the lead needs - paths, names, values, results - not file dumps>"}}
Look or run before you answer."""


def is_simple_command(cmd):
    """True for ONE simple command (pipes allowed, each stage an allowed program) that touches only the errand's own
    copy: no chaining or backgrounding, no absolute path / home / `..`, no installer, `bash`/`sh` only on a script."""
    cmd = (cmd or "").strip()
    if not cmd or CHAIN.search(cmd) or OUTSIDE.search(cmd) or INSTALL.search(cmd):
        return False
    for stage in cmd.split("|"):
        words = stage.split()
        if not words or words[0] not in (READ_ONLY | RUNNERS | FILE_OPS):
            return False
        if words[0] in ("bash", "sh") and (len(words) < 2 or words[1].startswith("-")):
            return False
    return True


def fetch_refusal(url):
    """None when `url` may be fetched, else why: a plain http(s) URL only, never a benchmark source."""
    url = (url or "").strip()
    if not URL_OK.match(url):
        return "not a plain http(s) URL"
    if any(s in url.lower() for s in BENCHMARK_SOURCES):
        return "a benchmark source - it could hand the helper the task's own tests"
    return None


def is_look(cmd):
    """True when every piped stage of `cmd` is a read-only command (and nothing turns it into a change)."""
    cmd = (cmd or "").strip()
    if not cmd or FORBIDDEN.search(cmd):
        return False
    for stage in cmd.split("|"):
        words = stage.split()
        if not words or words[0] not in READ_ONLY:
            return False
    return True


def parse_json(text):
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        v = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def briefs_of(answer):
    """The errand briefs in a lead's Terminus JSON answer ([] when none)."""
    v = parse_json(answer) or {}
    out = []
    for e in v.get("errands") or []:
        b = e.get("brief") if isinstance(e, dict) else e
        if isinstance(b, str) and b.strip():
            out.append(b.strip())
    return out


async def run_errand(brief, ask, look, limit=lambda s: s, mode="look", run=None, fetch=None):
    """One errand to its end. `ask(messages) -> str` is the helper model; `look(cmd) -> str` runs one read-only
    command in the task container. Returns {"brief", "status", "report", "turns", "looks"}; status is answered |
    unlooked | no_answer. Ends on the A1 rules, never on a clock. Mode "commands" (B8) also takes `run(cmd) -> str`
    (one command in the errand's private copy) and `fetch(url) -> str`; the record then carries `runs` and `fetches`
    (url + the text the helper saw, for fetch_audit.py)."""
    assert mode in MODES, mode
    cmds_mode = mode == "commands"
    msgs = [{"role": "user", "content": (HELPER_BRIEF_COMMANDS if cmds_mode else HELPER_BRIEF).format(brief=brief)}]
    looks, turns, nudged_look, nudged_answer = 0, 0, False, False
    seen, prev, repeat, stall = set(), None, 0, 0
    runs, fetches = 0, []

    def record(status, report=""):
        rec = {"brief": brief, "status": status, "report": report, "turns": turns, "looks": looks}
        if cmds_mode:
            rec.update(runs=runs, fetches=fetches)
        return rec
    while True:
        turns += 1
        reply = await ask(msgs)
        msgs.append({"role": "assistant", "content": reply or ""})
        v = parse_json(reply) or {}
        if isinstance(v.get("report"), str) and v["report"].strip():
            if looks == 0 and not nudged_look:
                nudged_look = True
                msgs.append({"role": "user", "content": LOOK_FIRST})
                continue
            return record("answered" if looks else "unlooked", v["report"].strip())
        if nudged_answer:
            return record("no_answer")
        cmds = [c for c in (v.get("commands") or []) if isinstance(c, str)]
        out = []
        for c in cmds:
            if is_look(c):
                looks += 1
                out.append("$ %s\n%s" % (c, limit(await look(c))))
            else:
                out.append("$ %s\nREFUSED: errands can only look (read-only commands, no writing)" % c
                           if not cmds_mode else
                           "$ %s\nREFUSED: \"commands\" only LOOKS (read-only); to run something use \"run\"" % c)
        wanted_runs = [c for c in (v.get("run") or []) if isinstance(c, str)] if cmds_mode else []
        for c in wanted_runs:
            if is_simple_command(c):
                looks += 1
                runs += 1
                out.append("[copy] $ %s\n%s" % (c, limit(await run(c))))
            else:
                out.append("[copy] $ %s\nREFUSED: one simple command on relative paths in your copy (no ; && ||, no "
                           "absolute path or .., no installing)" % c)
        url = v.get("fetch") if cmds_mode and isinstance(v.get("fetch"), str) else None
        if url:
            why = fetch_refusal(url)
            if why:
                out.append("fetch %s\nREFUSED: %s" % (url, why))
            else:
                looks += 1
                text = limit(await fetch(url.strip()))
                fetches.append({"url": url.strip(), "chars": len(text), "text": text})
                out.append("fetch %s\n%s" % (url, text))
        obs = "\n\n".join(out) or ("No commands found. Reply with JSON: {\"commands\": [...]} or {\"report\": \"...\"}."
                                   if not cmds_mode else
                                   "No action found. Reply with JSON: {\"commands\": [...]}, {\"run\": [...]}, "
                                   "{\"fetch\": \"...\"} or {\"report\": \"...\"}.")
        key = tuple(cmds) + tuple(wanted_runs) + ((url,) if url else ())
        repeat = repeat + 1 if key and key == prev else (1 if key else 0)   # look mode: key == tuple(cmds)
        prev = key
        stall = stall + 1 if obs in seen else 0
        seen.add(obs)
        if repeat >= REPEAT_END or stall >= STALL_END:
            nudged_answer = True
            obs += "\n\n" + ANSWER_NOW
        msgs.append({"role": "user", "content": obs})


def reports_block(done, pending):
    """The text appended to the lead's next observation."""
    if not done and not pending:
        return ""
    lines = ["", "Errand reports:"]
    for r in done:
        tag = {"answered": "", "unlooked": " (helper answered without looking)",
               "no_answer": " (helper gave no answer)"}.get(r["status"], "")
        lines.append("- %s%s\n  -> %s" % (r["brief"], tag, r["report"] or "(no report)"))
    for b in pending:
        lines.append("- %s\n  -> still running" % b)
    return "\n".join(lines)
