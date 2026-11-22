"""Calibrate HAI on the library-section triage task against the gold set.

Sends each goldset section through POST /query twice (routing=auto and routing=force_27b),
as user "test-homelab" (gated into force_routing_users AND test-scoped -- HAI-Control-Surface
gotcha: an ungated user's force_27b is SILENTLY dropped). Every row records the SERVED
route/model/host from the response; rows are bucketed by served model, never by what was
requested, and a force_27b row served by a non-27B is REFUSED (recorded, not graded) -- a row
served by the wrong tier is a different experiment, not a noisy one.

The rubric is read at runtime from the live Reference doc so the prompt and the doc cannot
drift apart. Raw answers are persisted to results/ BEFORE any printing (cp1252 lesson).

Usage: python calibrate.py            (both arms, 36 calls, sequential)
       python calibrate.py auto      (one arm only)
"""
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os as _os
import sys as _sys2
_sys2.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "bin"))
_sys2.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "bin"))
import lab_settings as _S  # noqa: E402
URL = _S.head_url() + "/query"
HERE = os.path.dirname(os.path.abspath(__file__))
RUBRIC_PATH = _os.path.join(_S.setting("LIBRARY_ROOT", ""), "Reference", "Library-Section-Triage-Rubric.md")
GOLD_PATH = os.path.join(HERE, "goldset.jsonl")
TIERS = ("manual", "doctrine", "narrative", "record", "credential", "unsure")
# TIER-only grading for these gold ids: credential (secret handling) and the unsure fragment.
TIER_ONLY = {"g17", "g18"}

with open(RUBRIC_PATH, encoding="utf-8") as f:
    RUBRIC = f.read()
gold = []
with open(GOLD_PATH, encoding="utf-8") as f:
    for line in f:
        if line.strip():
            gold.append(json.loads(line))
assert len(gold) == 18, "goldset changed size (%d) - update the assert deliberately" % len(gold)

arms = sys.argv[1:] or ["auto", "force_27b"]
stamp = time.strftime("%Y%m%d-%H%M%S")
os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
out_path = os.path.join(HERE, "results", "calibrate-%s.jsonl" % stamp)


def ask(text, routing):
    body = json.dumps({"text": text, "user": "test-homelab", "routing": routing,
                       "ephemeral": True}).encode("utf-8")
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.loads(r.read().decode("utf-8"))
    data["_elapsed_s"] = round(time.time() - t0, 1)
    return data


def parse(answer):
    """Parse the three-line contract. Returns (tier, verify, quote, action, format_ok)."""
    tier = verify = quote = action = None
    m = re.search(r"^\s*TIER:\s*(\w+)", answer, re.M | re.I)
    if m and m.group(1).lower() in TIERS:
        tier = m.group(1).lower()
    m = re.search(r"^\s*VERIFY:\s*(yes|no)\b(.*)$", answer, re.M | re.I)
    if m:
        verify = m.group(1).lower()
        q = re.search(r'"([^"]+)"', m.group(2))
        quote = q.group(1) if q else None
    m = re.search(r"^\s*ACTION:\s*(.+)$", answer, re.M | re.I)
    if m:
        action = m.group(1).strip()
    return tier, verify, quote, action, (tier is not None and verify is not None)


rows = []
with open(out_path, "w", encoding="utf-8") as out:
    for routing in arms:
        for g in gold:
            prompt = (RUBRIC + "\n\n---\nClassify this section:\n<<<\n" + g["text"]
                      + "\n>>>\nAnswer with exactly the three-line output contract.")
            try:
                resp = ask(prompt, routing)
            except Exception as e:  # noqa: BLE001
                row = {"id": g["id"], "routing": routing, "error": repr(e)}
                rows.append(row)
                out.write(json.dumps(row) + "\n")
                out.flush()
                print("%s %-9s ERROR %r" % (g["id"], routing, e))
                continue
            answer = resp.get("answer") or resp.get("message") or ""
            model = str(resp.get("model") or "?")
            tier, verify, quote, action, fmt_ok = parse(answer)
            refused = routing == "force_27b" and "27b" not in model.lower()
            row = {"id": g["id"], "routing": routing, "model": model,
                   "route": resp.get("route"), "host": resp.get("host"),
                   "elapsed_s": resp.get("_elapsed_s"), "refused_wrong_tier": refused,
                   "tier": tier, "verify": verify, "quote": quote, "action": action,
                   "format_ok": fmt_ok, "gold_tier": g["tier"], "gold_verify": g["verify"],
                   "raw": answer[:2000]}
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print("%s %-9s model=%-22s tier=%s/%s verify=%s/%s %s"
                  % (g["id"], routing, model, tier, g["tier"], verify, g["verify"],
                     "REFUSED" if refused else ("fmt-FAIL" if not fmt_ok else "")))

expected = len(arms) * len(gold)
assert len(rows) == expected, "processed %d rows, expected %d" % (len(rows), expected)

# ── summary, bucketed by SERVED model ───────────────────────────────────────
print("\n=== SUMMARY (%d rows -> %s) ===" % (len(rows), out_path))
buckets = {}
for r in rows:
    if "error" in r:
        buckets.setdefault("ERROR", []).append(r)
    elif r["refused_wrong_tier"]:
        buckets.setdefault("REFUSED(force_27b served by %s)" % r["model"], []).append(r)
    else:
        buckets.setdefault(r["model"], []).append(r)
for name in sorted(buckets):
    rs = buckets[name]
    if name.startswith(("ERROR", "REFUSED")):
        print("%-40s n=%d" % (name, len(rs)))
        continue
    graded = [r for r in rs if r["format_ok"]]
    tier_ok = sum(1 for r in graded if r["tier"] == r["gold_tier"])
    unsure = sum(1 for r in graded if r["tier"] == "unsure" and r["gold_tier"] != "unsure")
    v_rows = [r for r in graded if r["id"] not in TIER_ONLY]
    v_ok = sum(1 for r in v_rows if r["verify"] == r["gold_verify"])
    print("%-40s n=%d  fmt-ok=%d  tier %d/%d  honest-unsure=%d  verify %d/%d"
          % (name, len(rs), len(graded), tier_ok, len(graded), unsure, v_ok, len(v_rows)))
print("\nPer-row misses (graded rows only):")
for r in rows:
    if "error" not in r and not r["refused_wrong_tier"] and r["format_ok"] \
            and (r["tier"] != r["gold_tier"]):
        print("  %s %-9s %s: said %s, gold %s" % (r["id"], r["routing"], r["model"],
                                                  r["tier"], r["gold_tier"]))
