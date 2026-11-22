"""Lane claim mechanism for orchestrator deploys. Prevents concurrent
`docker compose` rebuilds on shared image by serializing access.

Semantics (consistent with factory CURRENT_RUN.json):
    MISSING = free to acquire.
    PRESENT = held; do not deploy.
    STALE   = reported loudly; never auto-cleared. Requires manual
              intervention (`release --force`) to distinguish holder
              failure from completion.

Usage:
    python deploy_claim.py status
    python deploy_claim.py acquire "session_id" "description"
    python deploy_claim.py release  "session_id"
"""
import json
import subprocess
import sys
import time
import lab_settings as S  # noqa: E402  the lab's settings reader

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CLAIM = S.setting("HEAD_STACK_DIR", "").rstrip("/") + "/.deploy-claim.json"   # on the head's box, beside its compose stack
STALE_MIN = 30


def _ssh(cmd, timeout=45):
    r = subprocess.run(["ssh", S.setting("HEAD_NAME", ""), cmd], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def read_claim():
    rc, out, _ = _ssh(f"cat {CLAIM} 2>/dev/null || true")
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # Unreadable is NOT free -- same rule as an unreadable CURRENT_RUN.
        return {"who": "<unreadable claim file>", "started": "", "what": "", "_bad": True}


def age_min(rec):
    try:
        return (time.time() - float(rec.get("epoch", 0))) / 60.0
    except (TypeError, ValueError):
        return None


def status(verbose=True):
    rec = read_claim()
    if not rec:
        if verbose:
            print("DEPLOY LANE: FREE (no claim)")
        return None
    a = age_min(rec)
    tag = "HELD"
    if a is not None and a > STALE_MIN:
        tag = f"HELD **STALE** ({a:.0f} min > {STALE_MIN})"
    if verbose:
        print(f"DEPLOY LANE: {tag}")
        print(f"  who    : {rec.get('who')}")
        print(f"  since  : {rec.get('started')}" + (f"  ({a:.0f} min ago)" if a is not None else ""))
        print(f"  what   : {rec.get('what')}")
        if tag.startswith("HELD **STALE"):
            print("\n  A stale claim means a deploy DIED PART-WAY. Do not just take the lane --")
            print("  check what state the image and containers are actually in first, then")
            print("  `release --force` deliberately.")
    return rec


def acquire(who, what):
    rec = read_claim()
    if rec:
        print("REFUSED - the deploy lane is already claimed:\n")
        status()
        return 1
    payload = json.dumps({
        "who": who, "what": what,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "epoch": time.time(),
    })
    # Single-shot write; re-read to confirm, because "reported ok" is not "landed".
    rc, _o, err = _ssh(f"cat > {CLAIM} << 'EOF'\n{payload}\nEOF")
    back = read_claim()
    if not back or back.get("who") != who:
        print(f"FAILED to write the claim ({err or 'no error text'}) - DO NOT DEPLOY")
        return 2
    print(f"CLAIMED the deploy lane for: {who}\n  what: {what}")
    print("  release it when the deploy is verified, even if it FAILED.")
    return 0


def release(who, force=False):
    rec = read_claim()
    if not rec:
        print("nothing to release - the lane is already free")
        return 0
    if rec.get("who") != who and not force:
        print(f"REFUSED - the lane is held by {rec.get('who')!r}, not {who!r}.")
        print("  Use --force only if you know that deploy is finished or dead.")
        return 1
    _ssh(f"rm -f {CLAIM}")
    if read_claim():
        print("FAILED to remove the claim - the lane still reads HELD")
        return 2
    print("released")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    cmd = args[0] if args else "status"
    if cmd == "status":
        sys.exit(0 if status() is None else 1)
    if cmd == "acquire":
        sys.exit(acquire(args[1] if len(args) > 1 else "unknown",
                         args[2] if len(args) > 2 else ""))
    if cmd == "release":
        sys.exit(release(args[1] if len(args) > 1 else "unknown", force))
    print(__doc__)
    sys.exit(2)
