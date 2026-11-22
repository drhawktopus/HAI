"""The medical helper uses the standard errand delegation loop (rules, placement, turns, delivery) without hard-coded models. It is tool-less to prevent runaway behavior and does not issue errands. Deliverables must include today's date and pass strict length/ID checks; reviewer-generated fixes are discarded to avoid hallucinated doses."""
import json
import re
import threading
import time

import cancellation
import config
import notes
import ollama_client
import pubmed
import scheduler

KIND = "medical_review"
WHO = "the medical helper"
CHARS_PER_TOKEN = 3.15   # ollama_client.COMPACT_CHARS_PER_TOKEN's measured rate
REPLY_TOKENS = 4096      # ~9,979 chars (~3,200 tokens) observed in practice; 4096 leaves headroom for thinking tokens
NO_PROGRESS_STALL = 8    # a1_harness.NO_PROGRESS_STALL
READONLY = ("search_evidence", "search_docs", "get_document")

READER_BRIEF = (
    "You are an independent clinical reader giving a second perspective on another model's work on a nursing or "
    "evidence-based-practice question. You cannot look anything up, so do NOT judge whether a cited source exists, or its ID or "
    "date. Judge the text for clinical safety and accuracy: drug, dose, route, indication, contraindication, lab value, "
    "assessment finding, advice that could harm. Omissions and style are not problems. Reply with JSON: verdict ('pass' or "
    "'changes_requested'), summary, and findings, each with severity and explanation.")
HELPER_SYSTEM = (
    "You do one bounded clinical job for another model working on a nursing or evidence-based-practice task. You have seen only "
    "what is below: find the answer with the tools before you reply, never from memory. You can only look: search_evidence "
    "searches PubMed, search_docs and get_document read the user's own library. When you have the answer, reply in plain text "
    "with no tool call: your conclusion, in a few lines, and the PMID or document each point rests on. Return the conclusion, not "
    "the material. Do not judge whether a cited ID exists: a separate check does that.")
LOOK_FIRST = "You have not looked anything up yet, so you cannot know the answer. Use the tools, then answer."
ANSWER_NOW = "Reply now with your conclusion in plain text, using what the tools showed you."
_CAVEAT = ("A helper's work, not an authority: it can be confidently wrong, including about doses and conversions. Check what it "
           "raises before changing anything, and do not present it to the user as confirmed.")
_PMID = re.compile(r"(?:PMID[:\s#]*|pubmed\.ncbi\.nlm\.nih\.gov/|pubmed/)(\d{6,9})", re.I)
_PMC = re.compile(r"PMC\s?(\d{5,9})", re.I)

_LOCK = threading.Lock()
_OPEN = {}               # run id -> {errand number: its thread}
_SEQ = [0]


def today():
    return "Today's date is %s. " % time.strftime("%B %d, %Y").replace(" 0", " ")


def find_ids(text):
    """{"pubmed": [ids], "pmc": [ids]} named in the text, each once, in order."""
    return {"pubmed": list(dict.fromkeys(_PMID.findall(text or ""))), "pmc": list(dict.fromkeys(_PMC.findall(text or "")))}


def check_ids(text):
    """Do the IDs in this text exist? Asked of NCBI by plain code -> a line per ID, or "" when it names none. One that
    could not be asked says so: could not tell is never "exists" and never "fake"."""
    lines = []
    for db, ids in find_ids(text).items():
        if not ids:
            continue
        try:
            res = pubmed._get(pubmed._eutils() + "/esummary.fcgi",
                              {"db": db, "id": ",".join(ids), "retmode": "json", **pubmed._key_params()}).json().get("result") or {}
        except Exception as exc:                                    # noqa: BLE001 - part of a report, never a raise
            lines.append("%s IDs could not be checked just now (%s): %s" % (db, type(exc).__name__, ", ".join(ids)))
            continue
        for i in ids:
            r = res.get(i) or {}
            label = ("PMC%s" if db == "pmc" else "PMID %s") % i
            if r.get("title") and not r.get("error"):
                who = r.get("sortfirstauthor") or ((r.get("authors") or [{}])[0].get("name") or "")
                lines.append("%s exists: %s, %s, %s" % (label, who, str(r.get("pubdate") or "")[:4], str(r["title"])[:110]))
            else:
                lines.append("%s NOT FOUND at NCBI" % label)
    return ("ID check (a plain lookup at NCBI, not the helper):\n" + "\n".join(lines)) if lines else ""


def place_helper():
    """The errand's row, as A1's --team-errands: the menu's `tool` family on the 8 GB tier, first row free of live work. Its
    turns run on a card, so the Fellowship's bare rows are out -> (host, url, model, setting) or None. Never waits, never
    evicts: no helper now beats holding up the person."""
    band, _rejected = scheduler.weigh_rows("tool", "8gb", interactive=False, window_cost=True, exclude=(scheduler.FELLOWSHIP_HOST,))
    for c in band:
        host, model = c["host"], (c.get("entry") or {}).get("model")
        url = (scheduler.CARDS.get(host) or {}).get("url")
        if url and model and ollama_client.reachable(url) and not scheduler.occupied_for(url, model, takes_window=True):
            return host, url, model, (c.get("entry") or {}).get("think")
    return None


def place_reader():
    """A box with the specialist's model, free of live work, by Spock -> (host, url) or None."""
    model = config.MEDICAL_REVIEW_MODEL
    hosts = tuple(h for h in scheduler.CARDS if "model." + model in scheduler.factory_capabilities(h))
    if not hosts:
        return None
    for host, _score, _why in scheduler.place(KIND, only=hosts):
        url = (scheduler.CARDS.get(host) or {}).get("url")
        if url and ollama_client.reachable(url) and not scheduler.occupied_for(url, model, takes_window=True):
            return host, url
    return None


def parse_read(answer):
    """The specialist's reply -> [(severity, explanation)], [] for a clean read, or None when it is not the JSON asked for. Its
    thinking and any suggested fixes stop here."""
    text = re.sub(r"(?s)<think>.*?</think>", "", answer or "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(j, dict) or str(j.get("verdict") or "").lower() not in ("pass", "changes_requested"):
        return None
    return [(str(f.get("severity") or "unrated"), str(f.get("explanation") or "").strip())
            for f in (j.get("findings") or []) if isinstance(f, dict) and str(f.get("explanation") or "").strip()]


def too_long(text, num_ctx):
    return len(text) / CHARS_PER_TOKEN + REPLY_TOKENS > num_ctx


def read_draft(placed, task, draft, sources, user):
    """The specialist's read of the lead's draft -> the report for the lead, who checks what it raises with its own evidence
    search. Never raises except a Stop."""
    host, url = placed
    model = config.MEDICAL_REVIEW_MODEL
    theirs = check_ids(draft + "\n" + sources)
    num_ctx = config.ctx_for(url, model, config.MEDICAL_REVIEW_NUM_CTX)
    prompt = "QUESTION:\n%s\n\nTEXT TO READ:\n%s" % (task, draft)
    if sources:
        prompt += "\n\nSOURCES THE OTHER MODEL RETRIEVED:\n%s" % sources
    if too_long(prompt + READER_BRIEF, num_ctx):
        return "\n\n".join(x for x in (theirs, "That is too long for the medical reader's %d-token window, and it judges nothing it "
                                               "cannot read whole. Send only the part in doubt." % num_ctx) if x)
    t0 = time.time()
    try:
        answer = ollama_client.chat(url, model, [{"role": "system", "content": today() + READER_BRIEF},
                                                 {"role": "user", "content": prompt}], [], num_ctx, user=user)[0]
    except cancellation.Cancelled:
        raise
    except Exception as exc:                                        # noqa: BLE001 - a report, never a raise
        notes.log("medical reader failed on %s: %r" % (host, exc))
        return "\n\n".join(x for x in (theirs, "The medical reader could not finish (%s). Carry on without it and do not imply it "
                                               "reported." % type(exc).__name__) if x)
    wall = time.time() - t0
    found = parse_read(answer)
    notes.log("medical reader user=%r on %s %s: %s, %.0f s" % (user, host, model, "unparsed" if found is None else "%d concerns" % len(found), wall))
    if found is None:
        return "\n\n".join(x for x in (theirs, "The medical reader's reply could not be read, so there is nothing from it this time.") if x)
    head = "SECOND PERSPECTIVE (%s on %s, %.0f s). %s" % (model, host, wall, _CAVEAT)
    body = "\n".join(["Concerns to check with search_evidence:"] + ["- [%s] %s" % f for f in found]) if found else "It raised no clinical concern."
    return "\n".join(x for x in (theirs + "\n" if theirs else "", head, body) if x)


def run_errand(job, placed, user, persona):
    """The errand loop, a1_harness._Errands._run's rules -> {"status", "report", "turns", "calls"}. Ends on a report, the
    no-progress rule, a helper error or a Stop, never a clock."""
    import tools
    host, url, model, setting = placed
    offered = [t for t in (tools._SEARCH_EVIDENCE, tools._SEARCH, tools._GET_DOCUMENT) if t["function"]["name"] in READONLY]
    num_ctx = config.ctx_for(url, model, config.NUM_CTX)
    think = ollama_client.placed_effort(url, model, setting)
    keep_alive = config.KEEP_ALIVE_OVERRIDES.get(url, config.KEEP_ALIVE)
    messages = [{"role": "system", "content": today() + HELPER_SYSTEM}, {"role": "user", "content": job}]
    rec = {"status": "running", "report": "", "turns": 0, "calls": 0, "model": model, "host": host}
    seen, stall, nudged, asked = set(), 0, False, False
    while True:
        cancellation.check()
        reply = ollama_client._chat_once(url, model, messages, offered, num_ctx, keep_alive, think=think)
        rec["turns"] += 1
        msg = reply.get("message") or {}
        calls, content = msg.get("tool_calls") or [], (msg.get("content") or "").strip()
        if not calls and not rec["calls"]:
            if not nudged:
                nudged = True
                messages += [{"role": "assistant", "content": content}, {"role": "user", "content": LOOK_FIRST}]
                continue
            return dict(rec, status="unlooked", report=content)
        if not calls and not content:
            if not asked:
                asked = True
                messages += [{"role": "assistant", "content": ""}, {"role": "user", "content": ANSWER_NOW}]
                continue
            return dict(rec, status="no_answer")
        if not calls:
            return dict(rec, status="done", report=content)
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        new = False
        for c in calls:
            fn = (c or {}).get("function") or {}
            name, args = str(fn.get("name") or ""), fn.get("arguments") or {}
            key = name + json.dumps(args, sort_keys=True, default=str)
            new = new or key not in seen
            seen.add(key)
            rec["calls"] += 1
            result = (tools.run_tool(name, args, job, user=user, persona=persona) if name in READONLY
                      else "ERROR: %s is not available on an errand (look-only tools)" % name)
            messages.append({"role": "tool", "content": str(result)})
        stall = 0 if new else stall + 1
        if stall >= NO_PROGRESS_STALL:
            return dict(rec, status="no_progress", report=content)


def _say(rec, wall):
    """The errand's record -> what the lead reads. Every way it can end says so."""
    how = {"unlooked": "It answered WITHOUT looking anything up: treat this as a guess, not a finding.",
           "no_progress": "It repeated itself and was stopped, so this may be incomplete.",
           "no_answer": "It looked but gave no answer."}.get(rec["status"], "")
    head = "REPORT (%s on %s, %.0f s, %d lookups). %s%s" % (rec["model"], rec["host"], wall, rec["calls"], _CAVEAT, " " + how if how else "")
    body = rec.get("report") or ""
    ids = check_ids(body)
    return "\n".join(x for x in (head, body, ids) if x)


def look_up(placed, task, sources, user, persona):
    """A lookup job with no draft to read, on the errand's own thread -> the report. Never raises except a Stop."""
    _host, url, model, _setting = placed
    num_ctx = config.ctx_for(url, model, config.NUM_CTX)
    job = "JOB: %s" % task
    if sources:
        job += "\n\nSOURCES THE OTHER MODEL RETRIEVED:\n%s" % sources
    if too_long(job + HELPER_SYSTEM, num_ctx):
        return ("That is too long for the helper's %d-token window, and it judges nothing it cannot read whole. Send only the part "
                "in doubt." % num_ctx)
    theirs = check_ids(sources)
    ollama_client._CALL_CONTEXT.chat_user = user or ""        # its turns go through the factory window, as any of this user's
    t0 = time.time()
    try:
        rec = run_errand(job, placed, user, persona)
    except cancellation.Cancelled:
        raise
    except Exception as exc:                                        # noqa: BLE001 - a report, never a raise
        notes.log("medical helper failed: %r" % (exc,))
        return "\n".join(x for x in (theirs, "The helper could not finish (%s). Carry on without it and do not imply it reported." % type(exc).__name__) if x)
    wall = time.time() - t0
    notes.log("medical helper user=%r on %s %s: %s, %d turns, %d lookups%s, %.0f s" % (
        user, rec["host"], rec["model"], rec["status"], rec["turns"], rec["calls"], " (one_call)" if rec["calls"] == 1 else "", wall))
    return "\n\n".join(x for x in (theirs, _say(rec, wall)) if x)


def _run_id():
    """The lead's run, when it has one a later turn can read from -> id or None. A sub-call's slot key carries a "."."""
    slot = getattr(ollama_client._CALL_CONTEXT, "ckpt_slot", None)
    if slot is None or "." in str(getattr(slot, "key", "")):
        return None
    return slot.ledger.run_id


def errand(task, draft="", sources="", user=None, persona=None):
    """Asynchronous medical review. Posts the report to the active run when available, otherwise returns it directly. Never raises except Stop."""
    task, draft, sources = str(task or "").strip(), str(draft or "").strip(), str(sources or "").strip()
    if not task:
        return "medical_review needs `task`: the job, and the short answer you want back."
    placed = place_reader() if draft else place_helper()
    if not placed:
        notes.log("medical helper: none free for %s user=%r" % ("a read" if draft else "a lookup", user))
        return "No medical helper is available right now. Carry on without it and do not imply one reported."

    def work():
        report = read_draft(placed, task, draft, sources, user) if draft else look_up(placed, task, sources, user, persona)
        # This log is the sole record of the helper's response; the report is discarded and the run ledger is ephemeral.
        notes.diag({"event": "medical_review", "user": user, "host": placed[0], "job": "read" if draft else "lookup",
                    "task": task, "draft": draft, "report": report})
        return report
    run = _run_id()
    if run is None:
        return work()
    with _LOCK:
        _SEQ[0] += 1
        n = _SEQ[0]

    def job():
        try:
            report = work()
        except Exception as exc:                                    # noqa: BLE001 - the lead is told, the thread ends
            report = "The helper could not finish (%s). Carry on without it." % type(exc).__name__
        ollama_client.post_to_run(run, "Errand %d: %s\n%s" % (n, task[:200], report), WHO, internal=True)
        with _LOCK:
            (_OPEN.get(run) or {}).pop(n, None)
            if not _OPEN.get(run):
                _OPEN.pop(run, None)
    t = threading.Thread(target=job, name="medical-errand-%d" % n, daemon=True)
    with _LOCK:
        _OPEN.setdefault(run, {})[n] = t
    t.start()
    notes.log("medical helper errand %d started on %s (%s) run=%s user=%r" % (n, placed[0], "read" if draft else "lookup", run, user))
    return ("Errand %d started on %s. Keep working: the report arrives as a message on a later turn, and you need not wait or repeat "
            "the job. Your answer will not end until it has reported. If a restart loses it, carry on without it." % (n, placed[0]))


def wait_for_run(run_id):
    """Blocks until all errands for the run are complete. Exits immediately if all are done, or if cancellation is requested. Does not use a timeout (wait indefinitely)."""
    while True:
        with _LOCK:
            open_now = list((_OPEN.get(str(run_id)) or {}).values())
        if not open_now:
            return
        open_now[0].join(0.5)
        cancellation.check()


ollama_client.OPEN_ERRANDS_HOOK = wait_for_run
