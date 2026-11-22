"""Whole-tree hash diff between orchestrator mirror and live system.

Step 0 of any orchestrator deploy. Ensures the comparison actually compared files
(by walking recursively rather than relying on caller-specified globs that might miss directories)
and asserts per-subtree coverage to prevent silent matches of zero pairs."""
import hashlib
import pathlib
import subprocess
import sys
import lab_settings as S  # noqa: E402  the lab's settings reader

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = S.setting("HEAD_NAME", "")   # the head box (an ssh name): its live copies are what the mirrors are audited against
if not HOST:
    raise SystemExit("mirror_audit: HEAD_NAME is not set (the head box's ssh name, in the lab's settings)")

# Add every mirror↔live pair here to prevent blind spots in audit coverage.
_M = S.setting_json("CODE_MIRRORS", {}) or {}
_STACK = S.setting("HEAD_STACK_DIR", "").rstrip("/")
PAIRS = (
    ("orchestrator", pathlib.Path(_M.get("orchestrator", "")), _STACK + "/orchestrator", ("(root)", "tests")),
    ("tolkien", pathlib.Path(_M.get("tolkien", "")), _STACK + "/tolkien", ("(root)", "tests")),
)

# Declared, not silent. Every run PRINTS this -- a skip nobody sees is a truncation.
#   backups/, *.bak*        historical copies; both sides keep their own, drift there is meaningless
#   __pycache__, .pytest_cache  build artefacts
#   coding-runner/workspaces/   agent scratch checkouts, deliberately divergent
SKIP_DIRS = ("backups", "__pycache__", ".pytest_cache", "workspaces", ".git", "node_modules")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def skipped(rel):
    return any(part in SKIP_DIRS for part in rel.parts[:-1]) or ".bak" in rel.name


def group(name):
    """Top-level bucket for the coverage report -- 'tests/x.py' -> 'tests', 'app.py' -> '(root)'."""
    head, _, tail = name.partition("/")
    return head if tail else "(root)"


def audit(name, mirror, remote, required_subtrees):
    """One mirror↔live pair. Returns drift count (0 = clean)."""
    print("\n================ PAIR: %s   %s <-> %s:%s" % (name, mirror, HOST, remote))
    local = {}
    for p in sorted(mirror.rglob("*.py")):
        rel = p.relative_to(mirror)
        if skipped(rel):
            continue
        local[rel.as_posix()] = sha256(p)

    # Prune must match local `skipped()` logic at any depth.
    # `-name ... -type d` ensures deep pruning, avoiding mismatches
    # if the remote find only pruned top-level directories.
    prune = " ".join("-name '%s' -type d -prune -o" % d for d in SKIP_DIRS)
    cmd = ("cd %s && find . %s -name '*.py' ! -name '*.bak*' -print0 | xargs -0 sha256sum"
           % (remote, prune))
    r = subprocess.run(["ssh", HOST, cmd], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 and not (r.stdout or "").strip():
        print("FATAL: remote hashing failed rc=%s\n%s" % (r.returncode, (r.stderr or "")[:500]))
        sys.exit(2)

    live = {}
    for line in (r.stdout or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        nm = parts[1].strip().lstrip("*")
        if nm.startswith("./"):
            nm = nm[2:]
        live[nm] = parts[0].strip()

    both = sorted(set(local) & set(live))
    print("mirror=%d  live=%d  COMPARED=%d" % (len(local), len(live), len(both)))
    assert both, "FATAL: zero files compared in pair %s - the join is broken, not the tree" % name

    # Coverage by subtree, printed so a shrinking scope is VISIBLE rather than inferred.
    groups = {}
    for n in both:
        groups[group(n)] = groups.get(group(n), 0) + 1
    print("coverage: %s" % ("  ".join("%s=%d" % kv for kv in sorted(groups.items()))))
    missing = [g for g in required_subtrees if not groups.get(g)]
    assert not missing, (
        "FATAL: required subtree(s) %s contributed ZERO compared files in pair %s. The audit's "
        "scope has silently shrunk -- fix the walk before trusting any 'clean' verdict."
        % (", ".join(missing), name))

    drift = [n for n in both if local[n] != live[n]]
    only_mirror = sorted(set(local) - set(live))
    only_live = sorted(set(live) - set(local))

    print("DRIFT (same path, different content): %d" % len(drift))
    for n in drift:
        print("  DRIFT  %-45s mirror=%s  live=%s" % (n, local[n][:12], live[n][:12]))
    print("only in mirror (new / not deployed): %d" % len(only_mirror))
    for n in only_mirror:
        print("  +  %s" % n)
    print("only on live (deployed but not mirrored): %d" % len(only_live))
    for n in only_live:
        print("  -  %s" % n)
    return len(drift)


print("skipping directories: %s   (and any *.bak*)" % ", ".join(SKIP_DIRS))
total_drift = 0
for _name, _mirror, _remote, _req in PAIRS:
    total_drift += audit(_name, _mirror, _remote, _req)

print("\n" + ("ALL PAIRS CLEAN - safe to edit and deploy" if not total_drift else
              "⚠ DRIFT PRESENT - pull live down as the baseline BEFORE editing, or a deploy "
              "silently reverts someone's work"))
sys.exit(1 if total_drift else 0)
