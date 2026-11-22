"""Measures tool-selection precision and recall on real requests.

Run inside the orchestrator container:  python /app/retrieval_probe.py [user] [pool] [num_ctx]

This is a live diagnostic, not a gate pin; the production gate runs with embeddings off.

Precision replays saved conversations turn-by-turn using live state. Note that the tool block
accumulates (sticky tools); if a tool is added on many unrelated turns, it is a "magnet." Recall
uses positive and negative controls to ensure specific tools are retrieved and irrelevant ones are not."""
import collections
import glob
import json
import os
import sys

sys.path.insert(1, "/app")   # after the script's own folder: a candidate module beside it is measured before deploy
import config  # noqa: E402
import tool_selection  # noqa: E402
import tools  # noqa: E402

USER = sys.argv[1] if len(sys.argv) > 1 else (config.OWNER_USER or "admin")
POOL = sys.argv[2] if len(sys.argv) > 2 else "BRAIN_TOOLS"
NUM_CTX = int(sys.argv[3]) if len(sys.argv) > 3 else 19968

RECALL = [
    ("Can you render me a picture of a lighthouse at dusk?", {"render_person", "render_image", "render_status"}),
    ("Propose a cleanup of the dead code in scheduler.py", {"code_propose_cleanup"}),
    ("Carve the H1 headings out of the Runbook domain", {"library_carve_h1"}),
    ("Review the dataset candidates for the concept set", {"dataset_review", "dataset_candidates"}),
    ("Restart the caddy container", {"restart_container", "docker_restart", "container_restart"}),
]
NEGATIVE = ("thanks, that's all for now", {"code_propose_cleanup", "code_apply_cleanup", "library_carve_h1",
                                           "library_apply_restructure", "dataset_review"})


def nm(t):
    return (t.get("function") or {}).get("name")


def conversations(user):
    for f in sorted(glob.glob(os.path.join(config.STATE_DIR, "history", user, "*.json"))):
        if f.endswith(".summary.json"):
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        msgs = d if isinstance(d, list) else (d.get("messages") or [])
        msgs = [{"role": m.get("role"), "content": m.get("sent") or m.get("content") or ""}
                for m in msgs if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        if sum(m["role"] == "user" for m in msgs) >= 2:
            yield os.path.basename(f), msgs


pool = tools.tools_for(getattr(tools, POOL), USER)
pool_names = {nm(t) for t in pool}
state = set(tools.live_state_tools(USER) or ())
print("user %s pool %s (%d tools) num_ctx %d live_state %s" % (USER, POOL, len(pool), NUM_CTX, sorted(state)[:6]))


def pick(text, msgs):
    """-> (tool names, the selector's info: unsure / barely / ranked)."""
    info = {}
    got, _ = tool_selection.select(pool, USER, text, NUM_CTX, state_tools=state,
                                   history=tool_selection.recent_context(msgs), info=info)
    return [nm(t) for t in got], info


def may_add(names, info, policy):
    """The picks a turn may ADD to a held block under `policy` - the selector's own verdicts: sure / barely (nothing
    scored confidently) / bunched (unsure, but the turn pointed somewhere)."""
    if not info.get("unsure") or policy == "grow on every pick":
        return names
    if policy == "grow only when sure":
        return []
    if policy == "unsure: the raw reading's top, if sure":
        return [info["raw_top"]] if info.get("raw_sure") else []
    return [] if info.get("barely") else (info.get("ranked") or [])[:1]


POLICIES = ("grow on every pick", "grow only when sure", "barely: none, bunched: the strongest",
            "unsure: the raw reading's top, if sure")
follow = 0
grew = {p: 0 for p in POLICIES}
adds = {p: collections.Counter() for p in POLICIES}
base_chat = None
for name, msgs in conversations(USER):
    base_chat = base_chat or msgs
    held = {p: None for p in POLICIES}
    for i, m in enumerate(msgs):
        if m["role"] != "user":
            continue
        picked, info = pick(m["content"], msgs[:i + 1])
        if held[POLICIES[0]] is None:
            held = {p: list(picked) for p in POLICIES}
            continue
        follow += 1
        for p in POLICIES:
            new = [n for n in may_add(picked, info, p) if n not in held[p]]
            if new:
                grew[p] += 1
                adds[p].update(new)
                held[p] += new
assert follow, "zero follow-up turns replayed - the history walk is broken, not the selector"
for p in POLICIES:
    print("PRECISION [%s]: %d follow-up turns in %s's chats, %d grew the tool block (%.0f %%)" % (
        p, follow, USER, grew[p], 100.0 * grew[p] / follow))
    for n, c in adds[p].most_common(8):
        print("   %-32s added on %3d turns (%.0f %% of follow-ups)" % (n, c, 100.0 * c / follow))

# RECALL, twice: as a fresh request, and as a FOLLOW-UP inside a real conversation (a new need mid-chat must still grow
# the block under either policy - otherwise "keep the set when unsure" froze the conversation out of a capability)
convo = base_chat[:2] if base_chat else []
held0, _ = pick(convo[0]["content"], convo[:1]) if convo else ([], {})
for p in POLICIES:
    misses = 0
    for text, want in RECALL:
        want = want & pool_names
        if not want:
            continue
        got, _i = pick(text, [{"role": "user", "content": text}])
        fresh = sorted(set(got) & want)
        got2, info2 = pick(text, convo + [{"role": "user", "content": text}])
        have = sorted((set(may_add(got2, info2, p)) | set(held0)) & want)
        misses += (not fresh) + (not have)
        if not (fresh and have):
            print("RECALL [%s] MISS %s: fresh %s follow-up %s" % (p, text[:50], fresh or "-", have or "-"))
    text, bad = NEGATIVE
    got, info = pick(text, convo + [{"role": "user", "content": text}])
    grows = sorted(set(may_add(got, info, p)) & bad - set(held0))
    print("RECALL [%s]: %d misses of %d (fresh + follow-up) | NEGATIVE follow-up %s %s" % (
        p, misses, 2 * len(RECALL), "ok" if not grows else "NOISY", grows or ""))
