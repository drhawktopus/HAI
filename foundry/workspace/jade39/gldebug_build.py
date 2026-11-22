r"""#39: build the recompile's own GL-debug variant (every GL call followed by a glGetError check, the port's
documented JE_GL_DEBUG=true build switch, docs/recompiler.md "GL debug wrapper") as jade-gldebug.exe beside the
real jade.exe. The first launch drew black on the 7900 XTX with no error anywhere; this build names the GL call
the driver rejects.

Usage: gldebug_build.py <build_root> <commit> <game_dir> <out_dir> [order]

The build tree's own build\win\jade.exe is backed up and restored, so the tree and the game folder's jade.exe stay
the upstream build; only jade-gldebug.exe is new in the game folder. Deliverables: build.log, gldebug.json.
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


def main():
    build_root, commit, game, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    src_root = os.path.join(build_root, commit)
    repo = os.path.join(src_root, os.listdir(src_root)[0])
    port = os.path.join(repo, "port")
    built = os.path.join(port, "build", "win", "jade.exe")
    keep = built + ".upstream"
    report = {"repo": repo, "upstream_sha": sha256(built)}
    game_exe_before = sha256(os.path.join(game, "jade.exe"))
    shutil.copy2(built, keep)
    try:
        env = dict(os.environ, JE_GL_DEBUG="true")
        with open(os.path.join(out, "build.log"), "wb") as log:
            p = subprocess.run(["cmd", "/c", "build.bat"], cwd=port, env=env, stdout=log, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL)
        report["build_rc"] = p.returncode
        if p.returncode == 0:
            dst = os.path.join(game, "jade-gldebug.exe")
            shutil.copy2(built, dst)
            report["gldebug_sha"] = sha256(dst)
            report["differs_from_upstream"] = report["gldebug_sha"] != report["upstream_sha"]
            v = subprocess.run([dst, "--version"], capture_output=True, text=True, errors="replace")
            report["version"] = (v.stdout + v.stderr).strip()
    finally:
        shutil.copy2(keep, built)
        os.remove(keep)
    report["tree_restored"] = sha256(built) == report["upstream_sha"]
    report["game_jade_exe_unchanged"] = sha256(os.path.join(game, "jade.exe")) == game_exe_before
    with open(os.path.join(out, "gldebug.json"), "w") as f:
        json.dump(report, f, indent=1)
    print(json.dumps(report), flush=True)
    ok = report.get("build_rc") == 0 and report.get("differs_from_upstream") and report["tree_restored"] \
        and report["game_jade_exe_unchanged"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
