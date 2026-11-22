"""Tier-2 secondary check on claims written to the documentation library.

Unlike `action_review.py` (which validates HAI's own actions), this module checks assertions written by external agents.

Contract:
  * One bounded, tool-less completion per changed document; this module makes no model call.
  * Fail-soft: `_parse` returns None on an unparseable reply; never raises.
  * Advisory only: output is a human worklist, not an automated verdict or gate.
  * Input is a diff, not bare text, to prevent hallucination on removed lines.
  * The box, model, and settings are the desk's; do not pin models here."""
import difflib
import json

import config

_SYSTEM = (
    "You are a documentation reviewer for a home-lab knowledge library. You are shown a DIFF of "
    "changes just made to a reference document. Your ONLY job is to list assertions about LIVE "
    "SYSTEM STATE that are not backed by cited evidence.\n"
    "\n"
    "An assertion about live system state is a factual claim about what hardware, a service, or a "
    "running system currently IS, CAN DO, or IS CONFIGURED AS -- e.g. 'node X has decoder Y', "
    "'3 of 5 nodes support Z', 'the drive has N GB free', 'service S is enabled'.\n"
    "\n"
    "It is BACKED if the same passage cites where the fact came from: a file path holding a dump, a "
    "named command that was run, a dated measurement, or a specific source file and symbol.\n"
    "\n"
    "Do NOT flag: opinions, plans, decisions, history, instructions, rationale, or anything the "
    "text already marks as uncertain, superseded, or corrected. Do NOT flag a claim merely because "
    "you personally do not know if it is true -- only whether the text says where it came from. "
    "When in doubt, do not flag: a reviewer that flags everything gets ignored.\n"
    "\n"
    'Reply with ONLY compact JSON, no prose: {"unbacked":[{"claim":"<= 25 words","why":"<= 15 '
    'words"}],"note":"<one short sentence, empty if nothing found>"}'
)


# SCHEMA (structured output) only constrains the `unbacked` list; never include `note` in the schema. Including it causes alphabetical key ordering, which prompts the model to write the verdict before checking claims, resulting in empty lists.
# Use the `_parse` path for engines without structured output support, where `note` remains optional.
SCHEMA = {"type": "object",
          "properties": {"unbacked": {"type": "array",
                                      "items": {"type": "object",
                                                "properties": {"claim": {"type": "string"}, "why": {"type": "string"}},
                                                "required": ["claim", "why"]}}},
          "required": ["unbacked"]}


def _parse(content):
    """Return {'unbacked': [...], 'note': str} or None. Never raises."""
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        data = json.loads(content[start:end])
    except (ValueError, json.JSONDecodeError, AttributeError):
        return None
    items = data.get("unbacked")
    if not isinstance(items, list):
        return None
    out = []
    for it in items[:12]:                      # bounded: a wall of findings is a muted reviewer
        if not isinstance(it, dict):
            continue
        claim = str(it.get("claim", "")).strip()[:220]
        if claim:
            out.append({"claim": claim, "why": str(it.get("why", "")).strip()[:120]})
    return {"unbacked": out, "note": str(data.get("note", "")).strip()[:200]}


def build_diff(old_text, new_text, path, max_chars=None):
    """Bounded unified diff. Empty string when nothing meaningful changed."""
    max_chars = max_chars or config.CLAIM_REVIEW_MAX_DIFF
    diff = difflib.unified_diff(
        (old_text or "").splitlines(), (new_text or "").splitlines(),
        fromfile=f"{path} (previous)", tofile=f"{path} (now)", lineterm="", n=2)
    # ADDED lines are what a new claim arrives in. Removals matter far less here and would eat the
    # budget, so keep context + additions and drop pure deletions.
    kept = [l for l in diff if not (l.startswith("-") and not l.startswith("---"))]
    text = "\n".join(kept)
    return text[:max_chars]


def worth_review(diff_text):
    """A diff below CLAIM_REVIEW_MIN_DIFF is a typo fix, not a claim - not worth a model call."""
    return config.CLAIM_REVIEW_ENABLED and len((diff_text or "").strip()) >= config.CLAIM_REVIEW_MIN_DIFF


def prompt_for(path, diff_text):
    return f"DOCUMENT: {path}\n\nDIFF OF WHAT JUST CHANGED:\n{(diff_text or '').strip()}"


def parse_answer(answer, thinking=""):
    """A delivered row's answer -> {'unbacked','note'} or None. A thinking model can file its answer under `thinking`
    and leave the answer empty; this is an EXTRACTION path, so take the payload wherever it landed (action_review)."""
    return _parse(answer or thinking or "")
