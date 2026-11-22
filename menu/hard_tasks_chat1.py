"""CHAT1 - the small tier answering a one-line chat turn.

The prompts are HAI's OWN requests that the advisor's labelled set (advisor_tasks.py, Claude's v1 labels, the owner may
relabel) puts on the `fellowship` tier - simple chat - verbatim, one per request shape. Nothing is added to them: no
answer format, no instructions, because a chat turn carries none. C1 already prices the generalist QUALITY per class
(documents of ~2,000 words, ANSWER blocks); this pool prices the INTERACTIVE TURN: time and tokens for a reply someone
is waiting on, plus the light checks a chat reply can be held to.

Grading: the reply itself is the data - the payload's extract_code returns `REPLY = <the reply>` - and each task's
checks read REPLY. Every task: a non-empty reply with no leaked thinking. The two with an objective answer also check
it (the capital is Trenton; PONG is exactly PONG). The open ones (small talk, a playful estimate) are read by eye."""
NEG_THINK = "lambda: '<think>' not in REPLY and '</think>' not in REPLY"
NONEMPTY = "lambda: len(REPLY.strip()) > 0"

TASKS = {
    "chat_day": {"kind": "chat", "prompt": "hows your day going", "checks": [NONEMPTY, NEG_THINK]},
    "chat_capital": {"kind": "chat", "prompt": "whats the capital of new jersey",
                     "checks": [NONEMPTY, NEG_THINK, "lambda: 'trenton' in REPLY.lower()"]},
    "chat_eggs": {"kind": "chat", "prompt": "how may eggs could fit in a 2026 nissan altima",
                  "checks": [NONEMPTY, NEG_THINK]},
    "chat_pong": {"kind": "chat", "prompt": "Reply with exactly the word PONG and nothing else.",
                  "checks": [NONEMPTY, NEG_THINK, "lambda: REPLY.strip().strip('.!').upper() == 'PONG'"]},
    "chat_ping": {"kind": "chat",
                  "prompt": "Connectivity ping: first packet through the new machine thread. Reply in one short sentence.",
                  "checks": [NONEMPTY, NEG_THINK, "lambda: len(REPLY.split()) <= 40"]},
    "chat_guess_what": {"kind": "chat", "prompt": "yo guess what", "checks": [NONEMPTY, NEG_THINK]},
    "chat_good_morning": {"kind": "chat", "prompt": "Good morning!", "checks": [NONEMPTY, NEG_THINK]},
}

# sample replies (the grader's positive control) and planted wrong ones (its negative control)
TEXT = {
    "chat_day": "Pretty good, thanks for asking! How about yours?",
    "chat_capital": "The capital of New Jersey is Trenton.",
    "chat_eggs": "Very roughly, around 100,000 eggs if you filled the whole cabin and trunk.",
    "chat_pong": "PONG",
    "chat_ping": "Ping received loud and clear.",
    "chat_guess_what": "What? Tell me!",
    "chat_good_morning": "Good morning! Hope your day is off to a great start.",
}
NEGATIVE_TEXT = {
    "chat_day": "",
    "chat_capital": "The capital of New Jersey is Newark.",
    "chat_eggs": "<think>let me estimate</think>",
    "chat_pong": "PONG! Happy to help with anything else.",
    "chat_ping": " ".join(["word"] * 60),
    "chat_guess_what": "   ",
    "chat_good_morning": "",
}


def reply_program(text):
    """The graded program for a chat reply: the reply as data (the payload's extract_code does exactly this)."""
    return "REPLY = %r\n" % (text or "").strip()


REFERENCE = {k: reply_program(v) for k, v in TEXT.items()}
NEGATIVE = {k: reply_program(v) for k, v in NEGATIVE_TEXT.items()}


def selftest(score, work):
    """References pass every check and planted wrong replies fail at least one, through the payload's own score()."""
    bad = []
    for name, t in TASKS.items():
        p, tot, err = score(REFERENCE[name], t["checks"], work)
        if tot == 0 or p != tot:
            bad.append("reference %s %d/%d %s" % (name, p, tot, err))
        p, tot, err = score(NEGATIVE[name], t["checks"], work)
        if tot and p == tot:
            bad.append("negative %s passed" % name)
    return "; ".join(bad)
