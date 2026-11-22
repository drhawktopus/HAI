r"""#39: build-check patches to the recompile without running the game. Each patch is applied ALONE to a clean copy of
the pinned source, built with the repo's own port\build.bat, and the result copied into the game folder as its own
exe (jade.exe stays the upstream build). Afterwards the source tree and the tree's build\win\jade.exe are restored
exactly (verified by hash).

Usage: patch_build.py <build_root> <commit> <game_dir> <patch_dir> <out_dir> <name=exe> ... [order]
  e.g. 00-diag-backbuffer=jade-diag.exe 01-drawbuffers-trim=jade-p01.exe

Deliverables in out_dir: build-<name>.log per patch, patch_build.json, progress\*.done.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tree_hash(root):
    h = hashlib.sha256()
    for d, _dirs, files in sorted(os.walk(root)):
        _dirs.sort()
        for fn in sorted(files):
            p = os.path.join(d, fn)
            h.update(os.path.relpath(p, root).replace("\\", "/").encode())
            h.update(sha256(p).encode())
    return h.hexdigest()


def done(out, step, data=None):
    os.makedirs(os.path.join(out, "progress"), exist_ok=True)
    with open(os.path.join(out, "progress", step + ".done"), "w") as f:
        json.dump(data or {}, f)
    print("STEP", step, "done", flush=True)


def main():
    build_root, commit, game, patch_dir, out = sys.argv[1:6]
    pairs = [a.split("=", 1) for a in sys.argv[6:] if "=" in a]
    os.makedirs(out, exist_ok=True)
    src_root = os.path.join(build_root, commit)
    repo = os.path.join(src_root, os.listdir(src_root)[0])
    port = os.path.join(repo, "port")
    src = os.path.join(port, "src")
    built = os.path.join(port, "build", "win", "jade.exe")
    keep_src = os.path.join(build_root, "src-clean-" + commit[:8])
    keep_exe = built + ".upstream"
    report = {"repo": repo, "patches": []}
    src_hash0, exe_hash0 = tree_hash(src), sha256(built)
    game_exe0 = sha256(os.path.join(game, "jade.exe"))
    report.update(src_hash=src_hash0, upstream_exe=exe_hash0)
    if os.path.exists(keep_src):
        shutil.rmtree(keep_src)
    shutil.copytree(src, keep_src)
    shutil.copy2(built, keep_exe)
    try:
        for name, exe in pairs:
            row = {"patch": name, "exe": exe}
            shutil.rmtree(src)
            shutil.copytree(keep_src, src)
            patch = os.path.join(patch_dir, name + ".patch")
            a = subprocess.run(["git", "apply", "--whitespace=nowarn", patch], cwd=repo, capture_output=True,
                               text=True, errors="replace")
            row["apply_rc"], row["apply_err"] = a.returncode, a.stderr.strip()[-400:]
            if a.returncode == 0:
                with open(os.path.join(out, "build-%s.log" % name), "wb") as log:
                    b = subprocess.run(["cmd", "/c", "build.bat"], cwd=port, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL)
                row["build_rc"] = b.returncode
                if b.returncode == 0:
                    dst = os.path.join(game, exe)
                    shutil.copy2(built, dst)
                    row["sha"] = sha256(dst)
                    v = subprocess.run([dst, "--version"], capture_output=True, text=True, errors="replace")
                    row["version"] = (v.stdout + v.stderr).strip()
                else:
                    with open(os.path.join(out, "build-%s.log" % name), errors="replace") as f:
                        row["build_tail"] = f.read().splitlines()[-25:]
            report["patches"].append(row)
            print(json.dumps(row)[:600], flush=True)
            done(out, name, {k: row.get(k) for k in ("apply_rc", "build_rc")})
    finally:
        shutil.rmtree(src)
        shutil.copytree(keep_src, src)
        shutil.copy2(keep_exe, built)
        os.remove(keep_exe)
        shutil.rmtree(keep_src)
    report["src_restored"] = tree_hash(src) == src_hash0
    report["tree_exe_restored"] = sha256(built) == exe_hash0
    report["game_jade_exe_unchanged"] = sha256(os.path.join(game, "jade.exe")) == game_exe0
    with open(os.path.join(out, "patch_build.json"), "w") as f:
        json.dump(report, f, indent=1)
    ok = all(r.get("build_rc") == 0 for r in report["patches"]) and report["src_restored"] \
        and report["tree_exe_restored"] and report["game_jade_exe_unchanged"]
    print("VERDICT", "ALL BUILT" if ok else "SOME FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
