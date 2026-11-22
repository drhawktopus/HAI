"""Deterministic triage extraction for library sections.

Reads newline-delimited Base64-encoded JSON items from `items.txt` and writes
triaged results to `out.json`. Base64 encoding prevents framing issues from
multiline text or pipe characters.

Rules settle only credential, record, or narrative sections. The manual-vs-doctrine
boundary is never settled here; ambiguous sections remain in the residual list
for model-based processing to ensure no section is wrongly skipped from the
model lane.

Verification mining extracts undated present-tense system state claims.
Dated lines are excluded as they represent historical records, not current state."""
import base64
import json
import os
import re
import sys

RULES_VERSION = "triage.extract.v1"

# ── section splitting (head-side use) ────────────────────────────────────────────────────────────
_HEADING = re.compile(r"^(#{1,4})\s+\S")


def split_sections(text):
    """[{heading, start, end, text}] split at ATX headings (levels 1-4). Preamble before the
    first heading is its own section; a doc with no headings is ONE whole-doc section -- never
    zero for a non-empty doc (a zero-section split is a broken instrument, asserted by the
    caller, not here)."""
    lines = text.splitlines()
    marks = [i for i, l in enumerate(lines) if _HEADING.match(l)]
    if not marks:
        return [{"heading": "(whole doc)", "start": 1, "end": len(lines),
                 "text": text}] if text.strip() else []
    out = []
    if marks[0] > 0 and "".join(lines[:marks[0]]).strip():
        out.append({"heading": "(preamble)", "start": 1, "end": marks[0],
                    "text": "\n".join(lines[:marks[0]])})
    for n, i in enumerate(marks):
        j = marks[n + 1] if n + 1 < len(marks) else len(lines)
        out.append({"heading": lines[i].lstrip("#").strip()[:120] or "(untitled)",
                    "start": i + 1, "end": j, "text": "\n".join(lines[i:j])})
    return out


# ── the rules ────────────────────────────────────────────────────────────────────────────────────
_DATE = re.compile(r"20\d\d-\d\d")
_CRED_KV = re.compile(
    r"\b(password|passphrase|api[-_ ]?key|secret|token)\b\s*(is|=|:)\s*`?"
    r"(?P<val>[^`\s]{6,})`?", re.I)
_CRED_KEYBLOCK = re.compile(r"BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY")
# values that are POINTERS or NAMES, not leaked secrets
_CRED_NOT_A_VALUE = re.compile(r"^[<$%/]|^[A-Z0-9_]+$|[()]|environ|_FILE\b|\.\.\.$|^\*+$")

_REC_STRUCK = re.compile(r"~~.*~~.*\b(CLOSED|DONE|RESOLVED|FIXED)\b|\b(CLOSED|DONE|RESOLVED|FIXED)\b.*~~", re.S)
_REC_DATELINE = re.compile(r"^\W{0,4}20\d\d-\d\d-\d\d")
_REC_MARKER = re.compile(
    r"\b(CLOSED|DONE|RESOLVED|FIXED|measured|verified|re-baselined|benchmarked)\b"
    r"[^.\n]{0,40}20\d\d-\d\d(-\d\d)?")

# the owner's name (OWNER_USER), when set, is a narrative signal like the role word
_OWNER_ALT = ("|" + re.escape(os.environ.get("OWNER_USER", "").strip())) if os.environ.get("OWNER_USER", "").strip() else ""
_NARR_SIGNALS = (
    re.compile(r"\b(We|I) [a-z]+ed\b"),
    re.compile(r"\bgot [a-z]+ed( out)?\b"),
    re.compile(r"\btook the (evening|night|day|afternoon|morning)\b"),
    re.compile(r"\bturned out\b"),
    re.compile(r"\b(?:the owner%s) (asked|said|caught|called|ruled|corrected)\b" % _OWNER_ALT),
    re.compile(r"\b(before|after) (anyone|we|I) [a-z]+", re.I),
)
_DOCTRINE_MARKERS = re.compile(r"\b(never|always|must|refused|rejected|rule)\b|⚠|🛑|```", re.I)

_CLAIM_PATTERNS = (
    re.compile(r"\b(all|every|both) (the )?\w{0,12}\s?(nodes?|tablets?|hosts?|aliases?|boxes|fellows?)\b", re.I),
    re.compile(r"\b(the )?(two|three|four|five|six|seven|eight|nine|ten|\d+) (nodes?|tablets?|hosts?|aliases?|boxes|fellows?)\b", re.I),
    re.compile(r"\b\d+ of \d+\b"),
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b(runs? on|listens? on|wired into|deployed on|serves?|now (have|has|carry|carries))\b", re.I),
)


def _credential(text):
    if _CRED_KEYBLOCK.search(text):
        return "a private key block"
    for m in _CRED_KV.finditer(text):
        if not _CRED_NOT_A_VALUE.search(m.group("val")):
            # never repeat the secret: quote the keyword context only
            return "%s %s <value>" % (m.group(1), m.group(2))
    return None


def _record(text):
    first = text.strip().splitlines()[0] if text.strip() else ""
    if _REC_STRUCK.search(text):
        return "struck-through closure: %s" % first[:120]
    if _REC_DATELINE.match(first) or _REC_MARKER.search(first):
        return "dated marker in the headline: %s" % first[:120]
    return None


def _narrative(text):
    if _DOCTRINE_MARKERS.search(text):
        return None
    hits = [p.search(text).group(0) for p in _NARR_SIGNALS if p.search(text)]
    if len(hits) >= 2:
        return " / ".join(h[:40] for h in hits[:3])
    return None


def mine_claims(text):
    """Undated present-tense lines matching a live-state pattern, quoted for verification."""
    claims = []
    for line in text.splitlines():
        s = line.strip()
        if not s or _DATE.search(s):
            continue
        if any(p.search(s) for p in _CLAIM_PATTERNS):
            claims.append(s[:160])
    return claims


def classify_section(text):
    """{settled, tier, evidence, verify, claims}. Settle order: credential > record > narrative
    (a leaked secret inside a dated record still needs redacting first)."""
    ev = _credential(text)
    tier = "credential" if ev else None
    if not tier:
        ev = _record(text)
        tier = "record" if ev else None
    if not tier:
        ev = _narrative(text)
        tier = "narrative" if ev else None
    claims = mine_claims(text)
    return {"settled": bool(tier), "tier": tier, "evidence": ev,
            "verify": bool(claims), "claims": claims}


# ── the map-payload entrypoint ───────────────────────────────────────────────────────────────────
def main(items_path, out_path):
    rows = []
    with open(items_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    for line in lines:
        row = {"item": line, "rules": RULES_VERSION}
        try:
            sec = json.loads(base64.b64decode(line).decode("utf-8"))
            row.update({"doc": sec.get("doc"), "heading": sec.get("heading"),
                        "lines": sec.get("lines")})
            row.update(classify_section(sec.get("text") or ""))
        except Exception as e:                                       # noqa: BLE001
            # the item is still ECHOED so coverage counts it; the error is named, never silent
            row["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        rows.append(row)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
