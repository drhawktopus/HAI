"""P20 create-intent detection.

This module determines whether a chat turn should be routed to the render queue. It is kept
separate from `app.py` to ensure the logic is directly testable without heavy dependencies,
avoiding brittle static checks.

Design rule: prefer false negatives over false positives. Answering a genuine render request
with text is cheaper than wasting GPU cycles on an unwanted image, especially since queued jobs
cannot be cancelled from this layer. When in doubt, return False; the chat path retains the
capability to emit `render_person` or `generate_image` tool calls directly if needed.
"""
import re

# --- the intent vocabulary (moved verbatim from app.py; do not re-word without a test) ---------

CREATE_INTENT = re.compile(
    r"\b(?:make|generate|render|create|draw up)\b.{0,30}\b(?:photo|picture|image|render)\b.{0,30}\bof\b",
    re.IGNORECASE,
)
CREATE_VERB = re.compile(r"\b(?:make|generate|render|create|draw up|draw)\b", re.IGNORECASE)
# NOTE: "render" is deliberately ABSENT from IMAGE_NOUN — it is already a create VERB, and
# including it made verb+noun self-satisfy on one word ("how do I render a video in premiere").
IMAGE_NOUN = re.compile(
    r"\b(?:photos?|pictures?|pics?|images?|shots?|portraits?|selfies?)\b",
    re.IGNORECASE,
)
SUBJECT_PRONOUN = re.compile(r"\b(?:her|him|them|she|he|they)\b", re.IGNORECASE)
REPEAT_CUE = re.compile(r"\b(?:another|one more|again|different|other one)\b", re.IGNORECASE)
CONTEXT_SCENE_IMPERATIVE = re.compile(
    r"^(?:(?:please|can you|could you)\s+)?(?:put|show|make|depict|draw|render)\s+"
    r"(?:her|him|them|the same (?:woman|man|character)|the fictional (?:woman|character))\s+"
    r"(?:in|at|on|by|with|wearing|standing|seated|sitting|walking|holding|beside|under)\b", re.IGNORECASE)
NOUN_OF_NAME = re.compile(r"(?i)\b(?:photo|picture|image|render)s?\s+of\s+[A-Za-z]+")

# --- the three new guards ----------------------------------------------------------------------

# (1) PROXIMITY. The verb and the image noun must appear near each other, in either order, on the
# same line. Uses the same `.{0,30}` idiom CREATE_INTENT already used -- `.` does not match a
# newline, which is deliberate: it keeps a match inside one sentence-ish span rather than letting
# a verb on line 3 pair with a noun on line 900.
_NEAR = r".{0,30}"
VERB_NEAR_NOUN = re.compile(
    r"(?:\b(?:make|generate|render|create|draw up|draw)\b" + _NEAR +
    r"\b(?:photos?|pictures?|pics?|images?|shots?|portraits?|selfies?)\b)"
    r"|(?:\b(?:photos?|pictures?|pics?|images?|shots?|portraits?|selfies?)\b" + _NEAR +
    r"\b(?:make|generate|render|create|draw up|draw)\b)",
    re.IGNORECASE,
)

# (2) EXPLICIT NEGATION. "Do not generate any image" must never queue one. Kept tight on purpose:
# the negation has to sit directly in front of a create verb AND an image noun within the same
# clause, so ordinary qualifiers ("make a picture of Alex, but don't make it blurry" -- no image
# noun after the negation) are untouched.
NEGATED_IMAGE = re.compile(
    r"\b(?:do\s*n[o']?t|don'?t|never|no\s+need\s+to)\s+"
    r"(?:generate|make|render|create|draw)\b[^.!?]{0,40}?"
    r"\b(?:photos?|pictures?|pics?|images?|renders?|shots?|portraits?)\b",
    re.IGNORECASE,
)

# (3) LENGTH. A chat render request is a sentence, not a document. 2000 chars is deliberately
# generous -- the most elaborate scene prompt on record is far under it -- and the failure mode of
# tripping it is only that the model emits the tool call itself instead of the server short-
# circuiting. Anything longer is a paste (a log, a manual, a transcript) being handed over for
# reading, which is exactly the case that broke.
MAX_PROMPT_CHARS = 2000


def create_intent(text, subj_tok=None, subject_context=False):
    """Return (wants_create: bool, reason: str).

    `reason` names the clause that fired, or why it did not. It exists so the routing decision is
    visible in the log -- a routing choice nobody can see is how this went unnoticed until it had
    queued two junk jobs. Callers that only need the boolean should use `wants_create`.
    """
    t = text or ""

    if len(t) > MAX_PROMPT_CHARS:
        return False, "no: %d chars > MAX_PROMPT_CHARS=%d (a paste, not a request)" % (
            len(t), MAX_PROMPT_CHARS)
    if NEGATED_IMAGE.search(t):
        return False, "no: explicit negation of image generation"
    if subject_context and CONTEXT_SCENE_IMPERATIVE.search(t):
        return True, "scene instruction for a known image subject"

    if CREATE_INTENT.search(t):
        return True, "create-intent phrase"
    if NOUN_OF_NAME.search(t):
        return True, "'<image noun> of <name>'"
    if VERB_NEAR_NOUN.search(t):
        return True, "create verb adjacent to image noun"
    if CREATE_VERB.search(t) and subj_tok:
        return True, "create verb + named library subject %r" % (subj_tok,)
    if CREATE_VERB.search(t) and SUBJECT_PRONOUN.search(t) and REPEAT_CUE.search(t):
        return True, "create verb + pronoun + repeat cue"
    return False, "no create intent"


def wants_create(text, subj_tok=None):
    return create_intent(text, subj_tok)[0]
