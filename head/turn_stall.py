"""Detects turn stalls in supervised coding jobs.

A stall is identified when a turn ends with:
- A short work announcement (e.g., "Creating the module now:")
- done_reason='stop'
- Minimal eval tokens
- Zero write-shaped tool calls (the work never actually executes)

This module handles detection only. The actuator that feeds nudge_text() back
to a stalled worker is wired separately.

Key constraint: Never report 'stalled' for missing data. If eval_tokens=None,
the state is treated as "ok"; lack of measurement is not a verdict."""


# Read-only coding tools. A turn that only ATTACHED a task and stopped is the exact measured
# signature, so these do not count as work.
_READ_TOOLS = frozenset([
    "coding_read", "coding_list", "coding_search",
    "coding_workspace", "coding_project", "coding_task",
])

# Announce patterns, matched against the last 120 chars of the answer.
_ANNOUNCE_PATTERNS = ["let me", "i'll now", "creating", "now:"]

# Module-level state: consecutive stalled verdicts, reset by any ok.
_consecutive_stalls = 0


def _reset_for_tests():
    """Reset module-level dedup state. For test isolation only."""
    global _consecutive_stalls
    _consecutive_stalls = 0


def _count_write_tools(tool_names):
    """Count write-shaped coding tool calls."""
    count = 0
    for name in tool_names:
        if name.startswith("coding_") and name not in _READ_TOOLS:
            count += 1
    return count


def _answer_announces(answer):
    """True if the answer ends with ':' or announces work in its last 120 characters."""
    stripped = answer.rstrip()
    if not stripped:
        return False
    if stripped[-1] == ":":
        return True
    tail = stripped[-120:].lower()
    for pat in _ANNOUNCE_PATTERNS:
        if pat in tail:
            return True
    return False


def _on_ok():
    """Reset the consecutive stall counter on a normal turn."""
    global _consecutive_stalls
    _consecutive_stalls = 0


def observe(answer, tool_names, step, done_reason, eval_tokens, supervised):
    """Observe a completed turn and return a verdict dict.

    Args:
        answer: the final answer text
        tool_names: tool names called during this turn
        step: step/turn count
        done_reason: the ollama done_reason string
        eval_tokens: eval token count, or None when the metadata is missing
        supervised: True for a SUPERVISED LOCAL CODING JOB turn

    Returns:
        {"verdict": "ok"} for normal turns, or
        {"verdict": "stalled", "why": <the measured evidence, with numbers>}.
    """
    global _consecutive_stalls

    # Could-not-measure is never "stalled" — a guard may not narrate a state it did not measure.
    if eval_tokens is None:
        _on_ok()
        return {"verdict": "ok"}

    if not supervised:            # never police ordinary chat
        _on_ok()
        return {"verdict": "ok"}

    if done_reason != "stop":
        _on_ok()
        return {"verdict": "ok"}

    if eval_tokens >= 60:         # the token bound is load-bearing
        _on_ok()
        return {"verdict": "ok"}

    write_count = _count_write_tools(tool_names)
    if write_count > 0:           # it actually did something
        _on_ok()
        return {"verdict": "ok"}

    if not _answer_announces(answer):
        _on_ok()
        return {"verdict": "ok"}

    _consecutive_stalls += 1
    total_tools = len(tool_names) if tool_names else 0
    why = (
        f"supervised stall: eval_tokens={eval_tokens}, step={step}, "
        f"write_tools=0/{total_tools}, announce=true"
    )
    return {"verdict": "stalled", "why": why}


def consecutive_stalls():
    """Consecutive stalled verdicts observed (reset by any ok)."""
    return _consecutive_stalls


def nudge_text(attempt):
    """The nudge for a given attempt: firm, firmer-with-an-exit, then silence.

    attempt >= 3 returns "" deliberately — an unbounded nudge is a loop, and a retry that changes
    nothing is not a retry. At that point the escalation feed carries it to a human.
    """
    if attempt == 1:
        return (
            "Your last turn announced work and stopped without doing it. "
            "Do the work NOW in this turn - call the tools; "
            "a reply without tool calls is a failed turn."
        )
    elif attempt == 2:
        return (
            "Your last turn announced work and stopped without doing it. "
            "Do the work NOW in this turn - call the tools; "
            "a reply without tool calls is a failed turn. "
            "If you are blocked, use coding_escalation instead of stopping."
        )
    else:
        return ""
