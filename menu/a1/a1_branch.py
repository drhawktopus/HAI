"""A1 BRANCHING: start an arm from a saved run instead of regenerating the part it shares.

An arm may reuse a parent run only up to where its OWN difference begins:
    point "finish" gate+review from a gate run: the reviewer acts only after the lead's accepted finish
    point "first_test" b-recover / b-correct from a solo run: the injected event is at / after the first test call

How: the harness is NOT changed (its sha stays the parent's). ReplayChat is the chat function the harness already takes:
it returns the parent's recorded assistant turns, one per call, up to and including the branch turn, then hands every
later call to the live model. The harness executes each replayed tool call for real, so the workspace, the gate state
and the last test output rebuild exactly as the parent had them; each replayed FILE EDIT's result must equal the parent's
recorded result (same new sha256) or the branch is marked invalid and the exercise never counts.

The comparison becomes PAIRED - same exercise, same history to the branch, one variable after it - and the parent's
model cost up to the branch is recorded (`prefix_wall_s`, `prefix_eval_tokens`) so prices stay comparable."""
import json
import os
import re

POINTS = ("finish", "first_test", "end")    # "end": every saved turn - resuming a stopped exercise from its checkpoint
TEST_TOOLS = ("run_tests", "coding_test")
EDIT_RESULT = ("OK patched", "OK created", "OK deleted", "OK moved", "OK wrote")
PARSE_ERROR = "Your last tool call could not be parsed"
RUNAWAY_RE = r"Your last reply ran past (\d+) tokens without calling a tool"      # a1_harness.RUNAWAY_TEXT
MUST_MATCH = ("model", "num_ctx", "think", "mode", "turn_cap")   # a parent on other settings is a different experiment


def load_parent(parent_dir):
    """(results, {exercise: transcript}) of a delivered parent run."""
    with open(os.path.join(parent_dir, "results.json"), encoding="utf-8") as f:
        res = json.load(f)
    trans = {}
    tdir = os.path.join(parent_dir, "transcripts")
    for name in os.listdir(tdir):
        if name.endswith(".json"):
            with open(os.path.join(tdir, name), encoding="utf-8") as f:
                trans[name[:-5]] = json.load(f)
    return res, trans


def compatible(parent_res, plan, harness_sha):
    """'' when the parent can be branched for this plan, else the reason it cannot."""
    for k in MUST_MATCH:
        a, b = parent_res.get(k), plan.get(k)
        if k in ("num_ctx", "turn_cap"):
            a, b = int(a or 0), int(b or 0)
        if a != b:
            return "parent %s=%r, this plan %r" % (k, a, b)
    if parent_res.get("harness_sha256") != harness_sha:
        return "parent ran harness %s, this run loads %s" % (parent_res.get("harness_sha256"), harness_sha)
    return ""


def assistant_turns(conversation):
    """The parent's model turns in order: [(assistant_msg, following_tool_msgs, note)]; note is "parse" (the call could
    not be parsed), the turn cap (the runaway guard cut the reply) or None."""
    out = []
    for i, m in enumerate(conversation):
        if m.get("role") != "assistant":
            continue
        follow, j = [], i + 1
        while j < len(conversation) and conversation[j].get("role") == "tool":
            follow.append(conversation[j])
            j += 1
        nxt = conversation[j] if j < len(conversation) else {}
        text = str(nxt.get("content") or "") if nxt.get("role") == "user" else ""
        cut = re.match(RUNAWAY_RE, text)
        out.append((m, follow, "parse" if text.startswith(PARSE_ERROR) else int(cut.group(1)) if cut else None))
    return out


def _calls(m):
    return [((tc.get("function") or tc).get("name") or "") for tc in (m.get("tool_calls") or [])]


def branch_index(turns, point):
    """How many parent turns to replay (the branch turn included); None when the parent never reached the point."""
    if point == "finish":
        hit = None
        for k, (m, follow, _p) in enumerate(turns):
            if "finish" in _calls(m) and any(str(t.get("content")) == "Task finished." for t in follow):
                hit = k + 1                       # the ACCEPTED finish (a refused one is answered otherwise)
        return hit
    if point == "first_test":
        for k, (m, _f, _p) in enumerate(turns):
            if any(c in TEST_TOOLS for c in _calls(m)):
                return k + 1
        return None
    if point == "end":
        return len(turns)
    raise ValueError("branch point must be one of %s" % (POINTS,))


class ReplayChat:
    """chat_fn(messages, tools): the parent's turns 1..k, then `live`. `record` (the order's own per-turn log) gets a
    zero-cost row per replayed turn so turn counts line up; `rec` collects the branch's own facts."""

    def __init__(self, transcript, point, live, record=None):
        self.turns = assistant_turns(transcript.get("conversation") or [])
        k = branch_index(self.turns, point)
        # a parent that never reached the point is replayed WHOLE: the arm's own event never fires on that history, so
        # the paired answer is the parent's own ending, at no model cost (a fresh run would break the pairing)
        self.k = k if k is not None else len(self.turns)
        self.live, self.record = live, record
        self.served = 0
        self._checked = 0
        pt = transcript.get("turns") or []
        upto = pt[:self.k]
        self.rec = {"point": point, "replayed_turns": self.k, "point_reached": k is not None, "mismatches": [],
                    "available": bool(self.turns),
                    "prefix_wall_s": round(sum(float(t.get("wall_s") or 0) for t in upto), 2),
                    "prefix_eval_tokens": sum(int(t.get("eval_count") or 0) for t in upto)}

    def _check(self, messages):
        """The tool results the harness just produced for the previous replayed turn, against the parent's."""
        if self.served == 0 or self.served == self._checked:     # each replayed turn once; live turns are not the parent's
            return
        self._checked = self.served
        _m, parent_tools, _p = self.turns[self.served - 1]
        tail = []
        for m in reversed(messages):
            if m.get("role") == "assistant":
                break
            if m.get("role") == "tool":
                tail.append(m)
        tail.reverse()
        for mine, theirs in zip(tail, parent_tools):
            a, b = str(mine.get("content") or ""), str(theirs.get("content") or "")
            if (a.startswith(EDIT_RESULT) or b.startswith(EDIT_RESULT)) and a != b:
                self.rec["mismatches"].append({"turn": self.served, "got": a[:160], "parent": b[:160]})

    def __call__(self, messages, tools):
        self._check(messages)
        if self.served >= self.k:
            return self.live(messages, tools)
        m, _f, note = self.turns[self.served]
        self.served += 1
        if self.record is not None:
            self.record.append({"wall_s": 0.0, "done_reason": "replayed", "eval_count": 0, "replayed": True,
                                "tool_calls": [{"name": n} for n in _calls(m)]})
        if note == "parse":
            return {"content": m.get("content") or "", "tool_calls": [], "invalid_tool_call": "replayed parse error"}
        if isinstance(note, int):
            return {"content": "", "tool_calls": [], "runaway": note}
        return {"content": m.get("content") or "",
                "tool_calls": [{"name": (tc.get("function") or tc).get("name") or "",
                                "arguments": (tc.get("function") or tc).get("arguments") or {}}
                               for tc in (m.get("tool_calls") or [])]}

    def valid(self):
        return self.rec["available"] and not self.rec["mismatches"] and self.served >= self.k
