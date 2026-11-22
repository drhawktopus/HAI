"""#39 Phase 1 compute order: census the Steam install of Jade Empire, then build plaught-armor's static
recompile against it, unmodified, and record what happened. Nothing is played.

usage: build_recomp.py <game_dir> <out_dir> <commit> <build_root> [order name, appended by the factory]

The repo is unpacked under <build_root>\\<commit> and KEPT there (re-running the installer is how it updates); it is
not shipped with the order, since its build tree holds translated game code.

Deliverables in <out_dir>:
  census.csv           every file in the game folder BEFORE the build: path, bytes, sha256
  census_summary.json  counts by extension, totals, the exe hash, whether it matches the 10-30 fingerprint
  install.log          the recompile installer's own log (port\\build\\install.log), plus its console output
  after.json           files the build added or changed in the game folder, and jade.exe --version
  report.json          one verdict per step: ok / failed, with the reason
Progress: one <out_dir>\\progress\\<step>.done file per finished step (5 steps).
"""
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STOCK_EXE_SHA = "0d25f18c028a7dffff9dd110b7be677f7e9b63e1cad86c1eb8e4e8e189c54fe5"
STOCK_FILES = 36139
REPO_ZIP = "https://github.com/plaught-armor/jade-empire-recomp/archive/%s.zip"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def walk(game):
    rows = {}
    for root, _, files in os.walk(game):
        for n in files:
            p = os.path.join(root, n)
            rel = os.path.relpath(p, game)
            try:
                rows[rel] = (os.path.getsize(p), sha256(p))
            except OSError as e:
                rows[rel] = (-1, "unreadable: %s" % e)
    return rows


def main():
    game, out, commit, build_root = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    os.makedirs(os.path.join(out, "progress"), exist_ok=True)
    report = {"commit": commit, "game": game, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}

    def step(name, ok, **detail):
        report["steps"][name] = dict(ok=ok, **detail)
        open(os.path.join(out, "progress", name + ".done"), "w").close()
        json.dump(report, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=1)
        print("STEP %s %s %s" % (name, "ok" if ok else "FAILED", detail), flush=True)

    # 1. census, before anything touches the folder
    before = walk(game)
    assert before, "census read zero files - the walk is broken, not the folder"
    with open(os.path.join(out, "census.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "bytes", "sha256"])
        for rel in sorted(before):
            w.writerow([rel, before[rel][0], before[rel][1]])
    by_ext = {}
    for rel, (size, _) in before.items():
        e = os.path.splitext(rel)[1].lower() or "(none)"
        c = by_ext.setdefault(e, [0, 0])
        c[0] += 1
        c[1] += max(size, 0)
    exe = before.get("JadeEmpire.exe", (0, ""))[1]
    summary = {"files": len(before), "bytes": sum(max(s, 0) for s, _ in before.values()),
               "stock_files_10_30": STOCK_FILES, "exe_sha256": exe, "exe_is_stock": exe == STOCK_EXE_SHA,
               "by_extension": dict(sorted(by_ext.items(), key=lambda kv: -kv[1][1]))}
    json.dump(summary, open(os.path.join(out, "census_summary.json"), "w", encoding="utf-8"), indent=1)
    step("1_census", exe == STOCK_EXE_SHA, files=len(before), exe_is_stock=exe == STOCK_EXE_SHA)
    if exe != STOCK_EXE_SHA:
        print("ALERT: JadeEmpire.exe is not the stock Steam build; build not attempted")
        return 1

    # 2. fetch the recompile at the pinned commit, into the order's own folder
    src_root = os.path.join(build_root, commit)
    try:
        data = urllib.request.urlopen(REPO_ZIP % commit, timeout=120).read()
        shutil.rmtree(src_root, ignore_errors=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(src_root)
        repo = os.path.join(src_root, os.listdir(src_root)[0])
        assert os.path.exists(os.path.join(repo, "install.bat")), "install.bat missing from the download"
        step("2_fetch", True, bytes=len(data), repo=repo)
    except Exception as e:
        step("2_fetch", False, error=repr(e))
        return 1

    # 3. tools the installer needs
    py = shutil.which("python") or shutil.which("py")
    step("3_tools", bool(py), python=py, path_head=os.environ.get("PATH", "")[:400])

    # 4. run the installer, unmodified; stdin closed so its closing `pause` returns
    t0 = time.time()
    p = subprocess.run(["cmd", "/c", os.path.join(repo, "install.bat"), game], cwd=repo,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    console = p.stdout.decode("utf-8", "replace")
    log = os.path.join(repo, "port", "build", "install.log")
    with open(os.path.join(out, "install.log"), "w", encoding="utf-8") as f:
        f.write("==== console (rc %d, %.0f s)\n%s\n==== port\\build\\install.log\n" % (p.returncode, time.time() - t0,
                                                                                   console))
        if os.path.exists(log):
            f.write(open(log, encoding="utf-8", errors="replace").read())
    built = os.path.exists(os.path.join(game, "jade.exe"))
    step("4_install", p.returncode == 0 and built, rc=p.returncode, seconds=round(time.time() - t0),
         jade_exe_in_game_folder=built, console_tail=console[-1500:])

    # 5. what changed in the game folder, and the build ID; the game itself is not launched
    after = walk(game)
    added = sorted(set(after) - set(before))
    changed = sorted(r for r in set(after) & set(before) if after[r][1] != before[r][1])
    version = None
    if built:
        try:
            v = subprocess.run([os.path.join(game, "jade.exe"), "--version"], cwd=game, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            version = {"rc": v.returncode, "out": v.stdout.decode("utf-8", "replace")[-800:]}
        except subprocess.TimeoutExpired:
            version = {"timeout_s": 30}
    json.dump({"added": added, "changed": changed, "version": version,
               "exe_still_stock": after.get("JadeEmpire.exe", (0, ""))[1] == STOCK_EXE_SHA},
              open(os.path.join(out, "after.json"), "w", encoding="utf-8"), indent=1)
    step("5_after", built, added=len(added), changed=changed, version=version)
    return 0 if built else 1


if __name__ == "__main__":
    sys.exit(main())
