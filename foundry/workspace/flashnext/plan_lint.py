"""Refuse ladder plans re-running configurations measured as bad on the plan's host (its "host" field: the box's fleet
name; a plan naming no host is held to the rules that bind on every box, and the per-box rules wait for its name).

Usage: python plan_lint.py <plan.json>  (exit 0 = clean, exit 1 = refused)

To intentionally re-test a closed regime (e.g., hardware change), set "known_bad_ok": "<reason>" on the plan arm."""
import json, os, re, sys

# Highest --n-cpu-moe measured as NOT in the RAM cliff on 32 GB, per cook (CPU-side experts must fit in RAM).
MAX_N = {
    "q2k.gguf": (34, "q2k: N=34 ok (cache-repack arm 1, RAM free 3.0 GB); N=35 4.1 tok/s and N=36/38 paging cliffs "
                     "(cache-repack arm 2 13:20, backend-fix R0, cache-ladder3)"),
    "q2kd2.gguf": (33, "q2kd2: N=32/33 fit (q2kd2-ladder, concurrency); N=34 back to 20k pages-in/s (q2kd2-push)"),
}
# These builds include the moecache_repack_path patch; other Q2_K builds lack it and have silently disabled expert caches.
PATCHED_CACHE_BINS = ("llama-mix-9b19d2d00", "llama-mix-46f32e43d", "llama-mix-d02a47ef2")   # 46f32e43d: all three patches


def arm_hits(a, host=""):
    """Rules bind globally unless derived from specific hardware (e.g., threads), which applies only to that box."""
    x = a.get("extra", [])
    s = " ".join(x)
    bin_ = a.get("bin", "")
    gguf = os.path.basename(a.get("gguf", "")).lower()
    hits = []

    def val(flag):
        return x[x.index(flag) + 1] if flag in x and x.index(flag) + 1 < len(x) else None

    for key, (mx, why) in MAX_N.items():
        if gguf.endswith(key) and int(a.get("n_cpu_moe", 0)) > mx:
            hits.append("n_cpu_moe %s > %d on this cook: RAM cliff. %s" % (a.get("n_cpu_moe"), mx, why))
    if host == "mothership":
        if int(a.get("threads", 8)) != 8:
            hits.append("threads %s: 12 and 16 both lost to 8 (vk6 26.5 vs 27.1; validate-mmap-threads 6.8-10.2 vs "
                        "8.9-14.2)" % a.get("threads"))
        if val("-ngl") and val("-ngl") not in ("99", "999"):
            hits.append("-ngl %s whole-layer split: 12.5 vs 28.4 (fullsplit 12:37)" % val("-ngl"))
        if a.get("concurrency") and max(a["concurrency"]) > 1 and "--moe-expert-cache" not in x:
            hits.append("concurrency > 1 without the expert cache: aggregate flat or worse (concurrency 13:02)")
    if host == "bighonker":
        if a.get("threads") is not None and int(a["threads"]) != 4:
            hits.append("threads %s: 4 > 5 > 3 >> 6 with experts in RAM on the 6-core i5-9600K (B10 10-17: opt t4 "
                        "22.6-23.1 vs t5 22.0-23.0 vs t6 18.5-18.8; ollama-knobs t3 18.0)" % a["threads"])
        if val("--spec-type") and "draft-mtp" in val("--spec-type"):
            hits.append("MTP with experts in RAM lost at depth ~3 AND 2 (B10 10-17: 22.2-22.7 vs 25.6-26.3 at 1965 MHz)")
    if val("--spec-draft-n-max") and int(val("--spec-draft-n-max")) > 2:
        hits.append("draft n-max > 2: draft 3 lost to 2 (vk4 VM3)")
    if "--moe-expert-cache" in x and "--no-repack" not in x and not any(p in bin_ for p in PATCHED_CACHE_BINS):
        hits.append("expert cache with repack ON on an engine not listed in PATCHED_CACHE_BINS: unpatched = garbage output "
                    "(cache-ladder 01:30); Q2_K-repacking builds without moecache_repack_path.py = cache silently off, "
                    "hits=0 misses=0 (stack1 14:30)")
    if gguf.endswith("q2kd2.gguf") and "mmap" in a.get("load", []):
        hits.append("q2kd2 with -lm mmap: lost to -lm none (5.9-14.7 vs 10.8-16.1, q2kd2-ladder)")
    if "Q8_0" in (val("-md") or ""):
        hits.append("shared-Q8_0 MTP head: no gain over Q4_K_M, +1 GB VRAM (vk7)")
    if "llama-unsloth-b11160\\" in bin_ + "\\" and "vulkan" not in bin_.lower():
        hits.append("Unsloth ROCm zip: CPU backend has no SIMD, every number void (2026-09-30 22:10)")
    return hits


ARSENAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arsenal.json")


def arsenal_check(plan):
    """Enforce that a plan explicitly names its arsenal levers (plan["levers"]), refusing unknown ids."""
    ars = json.load(open(ARSENAL, encoding="utf-8"))["levers"]
    named = plan.get("levers") or []
    problems = []
    if not named:
        problems.append('plan names no arsenal levers - add "levers": [ids from arsenal.json]')
    unknown = [l for l in named if l not in ars]
    if unknown:
        problems.append("unknown lever ids %s - add them to arsenal.json first (a lever outside the ledger is a lever "
                        "that gets forgotten)" % unknown)
    open_full = [(k, v) for k, v in ars.items() if v["status"] in ("open", "partial", "needs-owner", "testing")]
    print("ARSENAL: this plan exercises %s" % (", ".join(named) or "(nothing named)"))
    # Coverage check: flag resources with no levers (open or done) as arsenal holes.
    resources = json.load(open(ARSENAL, encoding="utf-8")).get("resources", [])
    cov = []
    for r in resources:
        on = [k for k, v in ars.items() if r in v.get("resources", [])]
        op = [k for k in on if ars[k]["status"] in ("open", "partial", "needs-owner", "testing")]
        cov.append("%s %d open/%d" % (r, len(op), len(on)) + ("  <-- NO LEVERS AT ALL" if not on else ""))
    print("ARSENAL: whole-box coverage: " + " | ".join(cov))
    # Couplings: interactions between this plan's levers and the running serving config.
    full = json.load(open(ARSENAL, encoding="utf-8"))
    serving = full.get("serving_config", [])
    scope = set(named) | set(serving)
    hits = [c for c in full.get("couplings", []) if (c["a"] in named or c["b"] in named) and c["a"] in scope | set(named)
            and c["b"] in scope | set(named)]
    near = [c for c in full.get("couplings", []) if (c["a"] in named) != (c["b"] in named) and c not in hits]
    print("ARSENAL: couplings inside this plan + the serving config (%s):" % ", ".join(serving))
    for c in hits:
        print("   %-9s %s x %s: %s" % (c["kind"].upper(), c["a"], c["b"], c["why"]))
    if not hits:
        print("   (none recorded - if this plan's levers truly touch nothing else, say why in its note)")
    uncoupled = [k for k, v in ars.items() if k != "telemetry" and v["status"] != "closed"
                 and not any(k in (c["a"], c["b"]) for c in full.get("couplings", []))]
    if uncoupled:
        problems.append("levers with NO recorded coupling (isolation creeping back): %s - record what each one moves "
                        "in arsenal.json couplings before placing work" % uncoupled)
    print("ARSENAL: what this plan's levers will shift elsewhere if adopted:")
    for c in near:
        print("   %-9s %s x %s: %s" % (c["kind"].upper(), c["a"], c["b"], c["why"]))
    print("ARSENAL: %d levers whose FULL form has not run yet:" % len(open_full))
    for k, v in open_full:
        print("   [%s] %s - %s%s" % (v["status"], k, v["full"], ("  (tested only: %s)" % "; ".join(v["tested"])) if v["tested"] else ""))
    return problems


def main(path):
    plan = json.load(open(path, encoding="utf-8"))
    arms = plan.get("arms", [])
    assert arms, "plan has no arms - nothing was linted"
    refused = 0
    for p in arsenal_check(plan):
        refused += 1
        print("REFUSED plan: %s" % p)
    host = str(plan.get("host") or "").lower()          # the box the plan's arms run on
    print("lint: host %s" % (host or "(none named: only the rules that bind on every box)"))
    for a in arms:
        hits = arm_hits(a, host)
        if hits and a.get("known_bad_ok"):
            print("ALLOWED %s (known_bad_ok: %s): %s" % (a["name"], a["known_bad_ok"], " | ".join(hits)))
        elif hits:
            refused += 1
            print("REFUSED %s:\n  - %s" % (a["name"], "\n  - ".join(hits)))
    print("lint: %d arm(s) checked, %d refused" % (len(arms), refused))
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
