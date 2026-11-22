"""Verify the local mirror matches the live orchestrator deployment before deploying.

python orch_mirror_audit.py                 report; exit 0 = same, 1 = drift, 2 = could not compare
python orch_mirror_audit.py --adopt-live    pull live changes into the mirror only for files with no uncommitted mirror edits

This prevents manual deploys from reverting changes made directly to the live environment by other deploy paths."""
import hashlib
import os
import subprocess
import sys
import lab_settings as S  # noqa: E402  the lab's settings reader

MIRROR = (S.setting_json("CODE_MIRRORS", {}) or {}).get("orchestrator", "")
LIVE = S.setting("HEAD_STACK_DIR", "").rstrip("/") + "/orchestrator"
HEAD = S.setting("HEAD_NAME", "")
KEEP = (".py", ".js", ".json", ".sh", ".txt", ".html", ".css")
SKIP_DIRS = {".git", "__pycache__", "backups", ".pytest_cache", "node_modules"}
REMOTE = ("cd %s && find . \\( -name .git -o -name __pycache__ -o -name backups -o -name .pytest_cache \\) -prune -o "
          "-type f \\( %s \\) -print0 | xargs -0 sha256sum" % (LIVE, " -o ".join("-name '*%s'" % e for e in KEEP)))


def local_hashes():
    out = {}
    for dp, dns, fns in os.walk(MIRROR):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn.endswith(KEEP):
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, MIRROR).replace(os.sep, "/")
                with open(p, "rb") as f:
                    out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def live_hashes():
    r = subprocess.run(["ssh", HEAD, REMOTE], capture_output=True, timeout=180)
    if r.returncode not in (0, 123):
        raise SystemExit("could not read the live tree: %s" % r.stderr.decode("utf-8", "replace")[-300:])
    out = {}
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        digest, _, name = line.partition("  ")
        name = name.strip()
        if name.startswith("./"):
            name = name[2:]
        if len(digest) == 64 and name:
            out[name] = digest
    return out


def mirror_dirty():
    r = subprocess.run(["git", "-C", MIRROR, "status", "--porcelain"], capture_output=True, text=True, timeout=60)
    return {line[3:].strip().strip('"') for line in r.stdout.splitlines() if line.strip()}


def adopt(names, live, run=subprocess.run):
    """Pull each live file into the mirror and check its bytes -> the names that arrived intact (a failed one is said)."""
    ok_names = []
    for n in names:
        dest = os.path.join(MIRROR, n.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        r = run(["scp", "-q", "%s:%s/%s" % (HEAD, LIVE, n), dest], capture_output=True, timeout=120)
        try:
            with open(dest, "rb") as f:
                ok = r.returncode == 0 and hashlib.sha256(f.read()).hexdigest() == live[n]
        except OSError:
            ok = False
        print("  %s %s" % ("PULLED " if ok else "FAILED ", n))
        if ok:
            ok_names.append(n)
    return ok_names


def main(argv):
    mine, live = local_hashes(), live_hashes()
    both = sorted(set(mine) & set(live))
    if len(both) < 100:
        print("compared only %d files: the join is broken or a tree is missing; this is NOT a clean result" % len(both))
        return 2
    differ = [n for n in both if mine[n] != live[n]]
    only_live = sorted(n for n in set(live) - set(mine) if "/" not in n or n.startswith(("tests/", "ui/", "coding-runner/")))
    only_mirror = sorted(n for n in set(mine) - set(live))
    dirty = mirror_dirty()
    # COMMITTED in the mirror and absent from live = finished work that was never deployed: drift, not a harmless extra
    # (it can be a helper that live's own scripts call, or a tool the image is documented to carry). Uncommitted
    # mirror-only files are someone's work in progress: reported, never refused.
    undeployed = [n for n in only_mirror if n not in dirty]
    print("compared %d files; %d differ; %d only on live; %d only in the mirror" % (len(both), len(differ), len(only_live), len(only_mirror)))
    for n in differ:
        print("  DIFFERS   %s%s" % (n, "   (mirror has uncommitted edits)" if n in dirty else "   (mirror is at its last commit: LIVE moved)"))
    for n in only_live[:40]:
        print("  LIVE ONLY %s" % n)
    for n in only_mirror[:40]:
        print("  MIRROR ONLY %s%s" % (n, "   (uncommitted)" if n in dirty else ""))
    if "--adopt-live" in argv:
        take = [n for n in differ if n not in dirty] + only_live
        adopt(take, live)
        print("pulled %d file(s); nothing committed: review `git -C %s diff` and commit them as the deploy they came from" % (len(take), MIRROR))
    if "--deploying" in argv:
        # the deploy tool's step 0: the ONLY differences allowed are the files about to be copied up
        going = {a.replace("\\", "/") for a in argv[argv.index("--deploying") + 1:] if not a.startswith("--")}
        # If live moved and the mirror is unchanged, another writer (e.g. self_deploy) deployed first. Adopt those files into the mirror
        # so this deploy carries them instead of refusing. A file the mirror also changed is a real conflict and refuses below.
        moved = [n for n in differ if n not in dirty and n not in going] + [n for n in only_live if n not in going]
        if moved:
            pulled = adopt(moved, live)
            if pulled:
                subprocess.run(["git", "-C", MIRROR, "add", "--"] + pulled, capture_output=True, timeout=60)
                subprocess.run(["git", "-C", MIRROR, "commit", "-q", "-m",
                                "Adopt live: %d file(s) another writer deployed first (Doc's self_deploy)\n\n%s" % (
                                    len(pulled), "\n".join(pulled)), "--"] + pulled, capture_output=True, timeout=60)
                print("ADOPTED %d file(s) live had moved to (mirror was clean): %s" % (len(pulled), ", ".join(pulled[:12])))
            differ = [n for n in differ if n not in pulled]
            only_live = [n for n in only_live if n not in pulled]
        stray = [n for n in differ if n not in going] + only_live
        if stray:
            print("REFUSED: live differs from the mirror in file(s) this deploy does not carry: %s" % ", ".join(stray[:12]))
            print("         run --adopt-live (live moved) or add them to the deploy (the mirror moved), then deploy")
            return 1
        never = [n for n in undeployed if n not in going]
        if never:
            print("REFUSED: committed in the mirror but never deployed: %s" % ", ".join(never[:12]))
            print("         carry them in this deploy if they belong live, or retire them from the mirror (archive, then git rm)")
            return 1
        print("STEP 0 OK: live matches the mirror everywhere except the %d file(s) being deployed" % len([n for n in going if n in differ or n in only_mirror]))
        return 0
    return 1 if (differ or only_live or undeployed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
