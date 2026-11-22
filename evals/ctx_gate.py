"""PHASE 2 GATE — prove the 27B loads at the CONFIGURED context before any row is recorded.

Sequence: stand-down check -> ask Spock -> ONE placement-wired heavy call -> /api/ps readback.
REQUIRES context_length == 65536. Anything else STOPS Phase 2.

 THE CALL IS effort=normal, DELIBERATELY, AND THIS IS THE WHOLE TRICK.
`think_hard` alternates the requested context inside a single turn — lead 65536 (app.py:3553),
critics 16384 (3605/3635), refine 65536 (3693), recheck 16384 (3728) — and ollama keeps ONE resident
instance per model, so a think-hard turn ENDS by leaving the model at 16384. Gating with a think-hard
call would therefore fail for a structural reason and read as a fleet problem. A plain heavy call
(force_27b at HEAVY_NUM_CTX) is the only shape whose LAST request is 65536.

 AND WHAT THIS GATE DOES **NOT** PROVE: that think-hard measurement rows run wholly at 65536. They
cannot today — the critic passes are configured at CODING_REVIEW_NUM_CTX=16384 by design. That is the
open CODING_REVIEW_NUM_CTX decision, and it is the owner's; the gate only establishes that the model CAN
hold the configured window, so a 16384 readback afterwards means the fleet, not the code.
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import urllib.request

import placement

import os as _os
import sys as _sys2
_sys2.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "bin"))
_sys2.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "bin"))
import lab_settings as _S  # noqa: E402
HAI = _S.head_url()
HEAVY = _S.setting("HEAVY_BOX_NAME", "")   # the heavy box (its name in the fleet table)
if not HEAVY:
    raise SystemExit("ctx_gate: HEAVY_BOX_NAME is not set (the heavy box's name in FLEET_HOSTS, in the lab's settings)")
HEAVY_PS = "http://%s:11434/api/ps" % _S.fleet_hosts().get(HEAVY, HEAVY)   # the heavy box's model server
WANT_CTX = 65536
WANT_MODEL = "qwen3.6:27b"


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ps():
    models = _get(HEAVY_PS, 8).get("models") or []
    if not models:
        return None
    m = models[0]
    return {"name": m.get("name"), "ctx": m.get("context_length"),
            "resident": m.get("size_vram") == m.get("size")}


print("=" * 70)
print("1. STAND-DOWN CHECK — the factory owns this card and gets priority")
print("=" * 70)
tile = _get(f"{HAI}/p20/factory/{HEAVY}")
print(f"   {HEAVY}: state={tile.get('state')!r} summary={tile.get('summary')!r} "
      f"health={tile.get('health')!r} stale={tile.get('stale')}")
if tile.get("state") not in ("free", "idle", ""):
    print(f"\nSTOP: the card is {tile.get('state')!r}. Standing down — factory work has priority.")
    raise SystemExit(3)

print("\n" + "=" * 70)
print("2. ASK SPOCK (never pick)")
print("=" * 70)
plc = placement.intended_placement("chat")
print(f"   best={plc['best']!r}  ranked={[r['host'] for r in plc['ranked']]}")
print(f"   heavy: {plc['heavy_model']} @ {plc['heavy_host']} num_ctx={plc['heavy_num_ctx']}")

print("\n" + "=" * 70)
print("3. BEFORE — resident state")
print("=" * 70)
print(f"   {ps()}")

print("\n" + "=" * 70)
print("4. ONE placement-wired heavy call (effort=normal, force_27b — see module docstring)")
print("=" * 70)
body = json.dumps({"text": "Reply with the single word: ready.",
                   "user": "test-homelab", "effort_mode": "normal",
                   "routing": "force_27b", "ephemeral": True}).encode("utf-8")
req = urllib.request.Request(f"{HAI}/query", data=body,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=900) as r:
    resp = json.loads(r.read().decode("utf-8"))
served = placement.served_identity(resp)
print(f"   route={served['route']} reported_model={served['reported_model']} "
      f"answer={(resp.get('answer') or '')[:60]!r}")

ok_place, why_place = placement.assert_placement("heavy", resp, plc)
print(f"   placement assert: ok={ok_place} — {why_place}")

ctx_verdict, why_ctx = placement.void_if_degraded(0)
print(f"   ctx downgrade check: {ctx_verdict or 'clean'} — {why_ctx}")

print("\n" + "=" * 70)
print("5. AFTER — readback (THE GATE)")
print("=" * 70)
after = ps()
print(f"   {after}")

print("\n" + "=" * 70)
fail = []
if not ok_place:
    fail.append(f"placement: {why_place}")
if ctx_verdict:
    fail.append(f"context degraded during the gate call: {why_ctx}")
if after is None:
    fail.append("no model resident after a heavy call — cannot confirm the load context")
else:
    if after["name"] != WANT_MODEL:
        fail.append(f"resident model is {after['name']!r}, expected {WANT_MODEL!r}")
    if after["ctx"] != WANT_CTX:
        fail.append(f"resident context is {after['ctx']}, REQUIRED {WANT_CTX}")
    if not after["resident"]:
        fail.append("model is not fully resident (spilling)")

if fail:
    print("GATE: FAIL — Phase 2 measurement must NOT proceed")
    for f in fail:
        print(f"   * {f}")
    raise SystemExit(2)
print(f"GATE: PASS — {after['name']} resident at ctx={after['ctx']}, fully on-card.")
print("⚠ Scope: this proves the model CAN hold the configured window. It does NOT make think-hard")
print("  rows wholly 65536 — critic passes are configured at 16384 (an open decision of the owner).")
