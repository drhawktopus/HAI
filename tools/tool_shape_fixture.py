"""Runs inside the orchestrator container; read-only: prints tool-shape test data (workspace\\toolshape\\tool_shape_probe.py) to stdout as JSON.

Usage: docker exec -i orchestrator python - < this.py

Env args:
  POOL (BRAIN_TOOLS): The tool pool to test.
  NUM_CTX (19968): Context length.
  SHAPE_USER: The user for real turns.
  REQUESTS_FILE: Path to JSON list of {"text", "expected": [...], "kind"}.
  REAL_TURNS (20): Number of real user turns to include.

REQUESTS are hand-labelled per tool family in plain words (do not name the tool).
REAL turns are first messages from saved chats; they are unlabelled and scored by agreement with today's selection."""
import glob
import json
import os
import sys

sys.path.insert(1, "/app")
import config  # noqa: E402
import tool_selection  # noqa: E402
import tools  # noqa: E402

POOL = os.environ.get("POOL", "BRAIN_TOOLS")
NUM_CTX = int(os.environ.get("NUM_CTX", "19968"))
USER = os.environ.get("SHAPE_USER") or config.OWNER_USER or "owner"
with open(os.environ["REQUESTS_FILE"], encoding="utf-8") as _f:
    REQUESTS = json.load(_f)
REAL = int(os.environ.get("REAL_TURNS", "20"))


def nm(t):
    return (t.get("function") or {}).get("name") or ""


pool = tools.tools_for(getattr(tools, POOL), USER)
names = {nm(t) for t in pool}
state = set(tools.live_state_tools(USER) or ())
core = [nm(t) for t in pool if nm(t) in tool_selection.CORE_TOOL_NAMES]
assert len(core) > 5, "no shared tools in the pool: the fixture would be empty"
bad = sorted({x for r in REQUESTS for x in r.get("expected") or [] if x not in names})
print("labels naming a tool not in the pool: %s" % bad, file=sys.stderr)


def real_turns():
    seen, out = set(), []
    files = sorted(glob.glob(os.path.join(config.STATE_DIR, "history", USER, "*.json")), key=os.path.getmtime, reverse=True)
    for f in files:
        if f.endswith(".summary.json"):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        msgs = d if isinstance(d, list) else (d.get("messages") or [])
        first = next((m for m in msgs if isinstance(m, dict) and m.get("role") == "user"), None)
        text = ((first or {}).get("sent") or (first or {}).get("content") or "").strip()
        if 12 <= len(text) <= 600 and text not in seen:
            seen.add(text)
            out.append({"text": text, "expected": None, "kind": "real"})
        if len(out) >= REAL:
            break
    return out


rows = []
for r in REQUESTS + real_turns():
    info = {}
    got, note = tool_selection.select(pool, USER, r["text"], NUM_CTX, state_tools=state, info=info)
    rows.append(dict(r, selected=[nm(t) for t in got], note=note, unsure=bool(info.get("unsure"))))
json.dump({"pool": POOL, "num_ctx": NUM_CTX, "user": USER, "core": core, "tools": {nm(t): t for t in pool}, "requests": rows}, sys.stdout, ensure_ascii=False)
