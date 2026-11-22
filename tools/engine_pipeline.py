"""LAB ENGINE pipeline: rebuilds the lab's llama.cpp engine when ollama moves its pin.
Advances one phase per nightly run; state is persisted on disk.

Phases:
  detect    Find newest ollama release (7+ days old) and corresponding llama.cpp commit.
  prepare   Apply lab patches and ollama compat layer to the source tree.
  build     Trigger build order on Mothership.
  delivered Wait for build completion and engine.json delivery.
  canary    Deploy to bighonker and run lossless-resume probe.
  fleet     Roll out to remaining nodes; rollback on failure.
  verify    Confirm all factories are on the new version.

Usage:
  python engine_pipeline.py            # advance one phase
  python engine_pipeline.py status     # print state file
  python engine_pipeline.py reset      # restart from detect

The head is HAI_URL and the staging share is SSD_SHARED_UNC; without them the pipeline has
no desk to order from and no share to write to, so each phase says so instead of guessing."""
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

import lab_settings as S

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("LAB_UPDATES_STATE") or os.path.join(S.setting("DOCS_STATE_DIR", ""), "lab-updates")   # tests point it at a tempdir
STATE = os.path.join(STATE_DIR, "engine.json")
# the lab's engine source tree (a setting); unset, the engine tree beside this one: engine/ in the published tree,
# lab-engine/ beside the lab's own folder
LAB_ENGINE = S.setting("LAB_ENGINE_DIR", "") or next(
    (os.path.normpath(p) for p in (os.path.join(HERE, "..", "engine"), os.path.join(HERE, "..", "..", "lab-engine"))
     if os.path.isdir(os.path.join(p, "patches"))), "")
LOCAL_FOUNDRY_ROOT = S.setting("LOCAL_FOUNDRY_ROOT", "")   # this box's own foundry root (its etc holds the worker credential)
SHARE = S.setting("SSD_SHARED_UNC", "")              # the shared SSD staging root, named the same as the factory uses it
LAB_ENGINE_UNC = SHARE + r"\lab-engine" if SHARE else ""
PATCHES = S.setting("ENGINE_PATCHES_DIR", "") or (os.path.join(LAB_ENGINE, "patches") if LAB_ENGINE else "")   # the lab's engine patches: the engine repo's patches/ unless the setting names another folder
TABLET_HEAD_PATCH = "cuda_graphs_pre_volta_patch.py"   # the one patch only the tablet head's CUDA 11.4 build applies (env-gated)
PATCH_ORDER = ("slot_pending_token_patch.py", "slot_resume_patch.py", "slot_rng_patch.py",
               "slot_continue_patch.py",
               "vk_fa_dead_cells_patch.py")
FLEET_DEPLOY = os.path.join((S.setting_json("CODE_MIRRORS", {}) or {}).get("foundry", ""), "fleet_deploy.py")
RUN_REMOTE = os.path.join(HERE, "run_remote.py")
KIRK = S.head_url()
USER = "ClaudeSupervisor"
CAMPAIGNS = S.setting("CAMPAIGNS_DIR", "")               # the campaigns folder on this box (a setting)
CAMPAIGNS_UNC = SHARE + r"\tabfoundry-jobs\campaigns" if SHARE else ""
MIN_AGE_DAYS = 7
PROBES = {k: tuple(v) for k, v in (S.setting_json("ENGINE_PROBES", {}) or {}).items()}   # box -> (model, window) for the engine proofs
ENGINE_BUILD_BOX = S.setting("ENGINE_BUILD_BOX", "")     # the factory whose compute order builds the Windows engine
# the GPU factories the engine goes to, in order, the canary FIRST (a comma-separated list of fleet names)
ROLLOUT = [b.strip() for b in S.setting("ENGINE_ROLLOUT", "").split(",") if b.strip()]
TABLET_HEAD = S.setting("TABLET_HEAD_NAME", "")
TABLET_HEAD_ENGINE_TREE = S.setting("TABLET_HEAD_ENGINE_TREE", "")   # where the tablet head builds its engine (its lab-engine folder)
TABLET_HEAD_ARCHIVE_PREFIX = S.setting("TABLET_HEAD_ENGINE_ARCHIVE_PREFIX", "")
DEFAULT_PIN = {"build": "b11669", "short": "620d222"}


def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "engine.log"), "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), msg))
    print(msg)


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"phase": "detect", "live": dict(DEFAULT_PIN), "steps": []}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(STATE + ".tmp", STATE)


def say(st, msg):
    st.setdefault("steps", []).append("%s %s" % (datetime.datetime.now().strftime("%m-%d %H:%M"), msg))
    log(msg)


# ---- the outside world, each in one small function (tests replace them) -------------------------------------------------
def gh_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hai-lab-engine-pipeline", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def gh_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hai-lab-engine-pipeline"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8").strip()


def download(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "hai-lab-engine-pipeline"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest + ".part", "wb") as f:
        while True:
            b = r.read(1 << 22)
            if not b:
                break
            f.write(b)
    os.replace(dest + ".part", dest)
    return dest


def run(argv, timeout=3600, **kw):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kw)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def desk_post(path, body):
    req = urllib.request.Request(KIRK + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def desk_get(path):
    token = os.environ.get("P20_WORKER_TOKEN")
    if not token:
        # the same local credential store p20_foundry._worker_token reads (the root resolved at import: one read of the
        # settings file, none inside this function's own file read)
        env_path = os.path.join(LOCAL_FOUNDRY_ROOT, "etc", "dm_relay.env")
        try:
            with open(env_path, encoding="ascii") as env:
                for line in env:
                    key, sep, value = line.strip().partition("=")
                    if sep and key == "P20_WORKER_TOKEN":
                        token = value
                        break
        except OSError:
            pass
    if not token:
        raise RuntimeError("No registered Softserve worker credential; protected desk read not sent")
    request = urllib.request.Request(KIRK + path, headers={"X-P20-Token": token})
    with urllib.request.urlopen(request, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def live_tag(fleet_check=None):
    """The build the fleet SERVES, from fleet_deploy --check (`tag=<short>+compat` per factory); None when it cannot be
    read or the factories disagree (drift is its own problem, said)."""
    rc, out = fleet_check() if fleet_check else run([sys.executable, FLEET_DEPLOY, "--check"], timeout=600)
    tags = set(re.findall(r"tag=([0-9a-f]{7,})\+compat", out or ""))
    return tags.pop() if len(tags) == 1 else None


# ---- decisions (pure) -----------------------------------------------------------------------------------------------------
def detect_decision(release, pin_build, pin_sha, live_short, now=None, min_age_days=MIN_AGE_DAYS):
    """release = {tag, published_at}; -> (start: bool, why, target{tag, build, sha, short})."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not release or not release.get("tag") or not pin_build or not pin_sha:
        return False, "WAIT - ollama's release or its llama.cpp pin could not be read", None
    short = pin_sha[:7]
    if live_short and short.startswith(live_short[:7]):
        return False, "OK - the fleet already serves llama.cpp %s (ollama %s pins %s)" % (short, release["tag"], pin_build), None
    pub = datetime.datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
    age = (now - pub).total_seconds() / 86400
    if age < min_age_days:
        return False, "WAIT - ollama %s is %.1f days old; the lab rebuilds from releases %d+ days old" % (
            release["tag"], age, min_age_days), None
    return True, "ollama %s (%.0f days old) pins llama.cpp %s = %s; the fleet serves %s - rebuild" % (
        release["tag"], age, pin_build, short, live_short), {"tag": release["tag"], "build": pin_build, "sha": pin_sha, "short": short}


# ---- phases ---------------------------------------------------------------------------------------------------------------
def phase_detect(st, io=None):
    io = io or sys.modules[__name__]
    rel = io.gh_json("https://api.github.com/repos/ollama/ollama/releases/latest")
    release = {"tag": rel.get("tag_name"), "published_at": rel.get("published_at")}
    build = io.gh_text("https://raw.githubusercontent.com/ollama/ollama/%s/LLAMA_CPP_VERSION" % release["tag"]) if release["tag"] else None
    sha = (io.gh_json("https://api.github.com/repos/ggml-org/llama.cpp/git/ref/tags/%s" % build).get("object") or {}).get("sha") if build else None
    live = io.live_tag() or st.get("live", {}).get("short")
    start, why, target = detect_decision(release, build, sha, live)
    say(st, "detect: " + why)
    st["live"] = {"short": live, "build": st.get("live", {}).get("build")}
    if start:
        st.update(target=target, phase="prepare", started=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    else:
        st["verdict"] = why


def phase_prepare(st, io=None):
    io = io or sys.modules[__name__]
    t = st["target"]
    tree = os.path.join(LAB_ENGINE, "llama.cpp-" + t["short"])
    osrc = os.path.join(LAB_ENGINE, "ollama-%s-src" % t["tag"].lstrip("v"))
    dl = os.path.join(LAB_ENGINE, "_downloads")
    if not os.path.isdir(tree):
        z = io.download("https://github.com/ggml-org/llama.cpp/archive/refs/tags/%s.zip" % t["build"], os.path.join(dl, "llama.cpp-%s.zip" % t["build"]))
        top = _extract(z, LAB_ENGINE)
        os.rename(os.path.join(LAB_ENGINE, top), tree)
        say(st, "prepare: llama.cpp %s (%s) -> %s" % (t["build"], t["short"], tree))
    if not os.path.isdir(osrc):
        z = io.download("https://github.com/ollama/ollama/archive/refs/tags/%s.zip" % t["tag"], os.path.join(dl, "ollama-%s.zip" % t["tag"]))
        os.makedirs(osrc, exist_ok=True)
        _extract(z, osrc)
        say(st, "prepare: ollama %s source -> %s" % (t["tag"], osrc))
    compat = glob.glob(os.path.join(osrc, "*", "llama", "compat"))
    if not compat:
        st.update(phase="hold", verdict="HOLD - ollama %s source has no llama/compat dir; a person looks" % t["tag"])
        return
    need = PATCH_ORDER + ((TABLET_HEAD_PATCH,) if TABLET_HEAD else ())
    missing = [n for n in need if not (PATCHES and os.path.isfile(os.path.join(PATCHES, n)))]
    if missing:   # a missing patch is not a patch that does not apply: say which, before any patch touches the tree
        st.update(phase="hold", verdict="HOLD - lab patch(es) missing from the patches folder %r: %s" % (PATCHES, ", ".join(missing)))
        say(st, st["verdict"])
        return
    for name in PATCH_ORDER:
        rc, out = io.run([sys.executable, os.path.join(PATCHES, name), tree], timeout=600)
        tail = " ".join(out.split())[-300:]
        if rc != 0:
            st.update(phase="hold", verdict="HOLD - lab patch %s does not apply to llama.cpp %s (a rebase, a person's): %s" % (
                name, t["short"], tail))
            say(st, st["verdict"])
            return
        say(st, "prepare: %s -> %s" % (name, tail[-80:]))
    rc, out = io.run([sys.executable, os.path.join(LAB_ENGINE, "apply_ollama_compat.py"), tree, compat[0]], timeout=600)
    if rc != 0:
        st.update(phase="hold", verdict="HOLD - ollama's compat layer did not apply to %s: %s" % (t["short"], " ".join(out.split())[-300:]))
        say(st, st["verdict"])
        return
    say(st, "prepare: compat layer applied (%s)" % " ".join(out.split())[-120:])
    io.archive("lab-engine-tree", t["short"], "lab", location=tree, stage="engine",
               rollback="the previous tree %s stays in lab-engine\\; fleet_deploy --engine-rollback restores the fleet" % st.get("live", {}).get("short"),
               note="llama.cpp %s (%s) + lab patches #0/#3/#2 + ollama %s compat" % (t["build"], t["short"], t["tag"]))
    st["tree"] = tree
    st["phase"] = "build"


def _extract(zpath, into):
    with zipfile.ZipFile(zpath) as z:
        top = z.namelist()[0].split("/")[0]
        z.extractall(into)
    return top


def phase_build(st, io=None):
    io = io or sys.modules[__name__]
    t = st["target"]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    day = stamp[:8]
    name = "labengine-%s-compat.%s" % (t["short"], stamp)              # the 10-22 name shape: fleet_engine reads the tag from it
    spec = {"name": name, "mode": "compute", "load": "cpu", "initiator": USER,
            "payload": "{FOUNDRY_ROOT}\\workspace\\labengine\\build_lab_engine.py",
            "args": [LAB_ENGINE_UNC + "\\llama.cpp-" + t["short"], "{ORDER_OUT}"],
            "deliver_to": CAMPAIGNS_UNC + "\\labengine-" + day, "notify": "failures"}
    if not (ENGINE_BUILD_BOX and ROLLOUT):
        st.update(phase="hold", verdict="HOLD - ENGINE_BUILD_BOX / ENGINE_ROLLOUT unset: no box to build on or deploy to")
        say(st, st["verdict"])
        return
    rep = io.desk_post("/p20/order", {"spec": spec, "requires": ["order.compute"], "only": [ENGINE_BUILD_BOX],
                                      "user": USER})
    if not rep.get("ok"):
        st.update(phase="hold", verdict="HOLD - the desk refused the build order: %s" % (rep.get("why") or rep.get("error") or rep))
        say(st, st["verdict"])
        return
    # the desk answers {"ok", "order": <name>, "steps": [{"host", "id", ...}]} (a held order carries "id" at the top)
    oid = (rep.get("steps") or [{}])[0].get("id") or rep.get("id")
    # the factory delivers to deliver_to\<name>_<host> (the 10-22 builds: labengine-620d222.<stamp>_mothership)
    st.update(order={"id": oid, "name": name, "dir_glob": os.path.join(CAMPAIGNS, "labengine-" + day, name + "*")}, phase="delivered")
    say(st, "build: order %s placed on Mothership (%s)" % (name, st["order"]["id"]))


def phase_delivered(st, io=None):
    io = io or sys.modules[__name__]
    o = st["order"]
    rec = io.desk_get("/p20/order/%s" % o["id"]) if o.get("id") else {}
    status = str(rec.get("status") or rec.get("state") or "")
    hits = glob.glob(os.path.join(o.get("dir_glob") or o.get("dir", ""), "engine.json"))
    if hits:
        o["dir"] = os.path.dirname(sorted(hits, key=os.path.getmtime)[-1])
        meta = json.load(open(os.path.join(o["dir"], "engine.json"), encoding="utf-8"))
        failed = [n for n, s in (meta.get("sets") or {}).items() if "failed" in s]
        if failed:
            st.update(phase="hold", verdict="HOLD - the build delivered with FAILED set(s) %s (see %s\\build.log)" % (failed, o["dir"]))
        else:
            say(st, "delivered: %s (sets %s)" % (o["dir"], sorted((meta.get("sets") or {}))))
            st["phase"] = "canary"
        return
    if status in ("failed", "cancelled", "error"):
        st.update(phase="hold", verdict="HOLD - the build order %s ended %s: %s" % (o["name"], status, rec.get("error")))
    else:
        st["verdict"] = "WAIT - build %s is %s on Mothership; not delivered yet" % (o["name"], status or "unknown")
    say(st, st.get("verdict") or "delivered")


def resume_probe_verdict(report):
    """The gate asked for whole-turn continuation, not just raw token continuation.

    Existing 27B reports can say LOSSLESS at top level while lab_continue says
    NOT LOSSLESS. The narrower success must never override that failure.
    """
    continuation = (report.get("lab_continue") or {}).get("verdict")
    if continuation:
        return "LOSSLESS" if continuation == "CONTINUES LOSSLESS" else str(continuation)
    raw = report.get("verdict")
    return "NO CONTINUATION RESULT" if raw in ("LOSSLESS", "CONTINUES LOSSLESS") else str(raw or "?")


def _probe(st, io, host):
    """Submit the lossless-resume probe to `host`, wait on its order (status), read resume.json -> verdict string."""
    model, ctx = PROBES[host]
    rc, out = io.run([sys.executable, os.path.join(LAB_ENGINE, "submit_resume_probe.py"), host, model, ctx, "--continue", "--hang-up", "--fresh-load"], timeout=120)
    m = re.search(r"\b([0-9a-f]{12})\b", out)
    if rc != 0 or not m:
        return "COULD NOT SUBMIT (%s)" % " ".join(out.split())[-160:]
    oid = m.group(1)
    named = re.search(r"\bTrue\s+(resume(?:dist)?probe-[^\s]+)", out)
    if not named:
        return "COULD NOT IDENTIFY SUBMITTED PROBE NAME"
    submitted_name = named.group(1)
    t0 = time.time()
    for _ in range(180):                               # status, asked once a minute; a probe is ~20 min, 3 h is "something is wrong"
        rec = io.desk_get("/p20/order/%s" % oid)
        status = str(rec.get("status") or rec.get("state") or "")
        if status in ("done", "delivered"):
            break
        if status in ("failed", "cancelled", "error"):
            return "PROBE ORDER %s: %s" % (status, rec.get("error"))
        io.sleep(60)
    files = glob.glob(os.path.join(CAMPAIGNS, "labengine-*", submitted_name + "_" + host + "*", "resume.json"))
    if len(files) != 1 or os.path.getmtime(files[0]) < t0 - 60:
        return "NO RESULT FILE"
    report = json.load(open(files[0], encoding="utf-8"))
    return resume_probe_verdict(report)


def _deploy(io, order_dir, host):
    rc, out = io.run([sys.executable, FLEET_DEPLOY, "--engine", order_dir, "--only", host], timeout=3600)
    return rc == 0, " ".join(out.split())[-300:]


def _rollback(io, host):
    rc, out = io.run([sys.executable, FLEET_DEPLOY, "--engine-rollback", "--only", host], timeout=1800)
    return rc == 0, " ".join(out.split())[-200:]


def phase_canary(st, io=None):
    io = io or sys.modules[__name__]
    if not ROLLOUT:
        st.update(phase="hold", verdict="HOLD - ENGINE_ROLLOUT unset: no canary to deploy to")
        return
    canary = ROLLOUT[0]
    ok, tail = _deploy(io, st["order"]["dir"], canary)
    say(st, "canary: fleet_deploy --engine --only %s -> %s (%s)" % (canary, "OK" if ok else "FAILED", tail[-120:]))
    if not ok:
        st.update(phase="hold", verdict="HOLD - the canary deploy on %s failed: %s" % (canary, tail))
        return
    io.archive("lab-engine/" + canary, st["live"].get("short") or "stock", "lab", stage="engine",
               location="%s launcher backup *.bak-*-pre-lab%s" % (canary, st["target"]["short"]),
               rollback="python fleet_deploy.py --engine-rollback --only " + canary,
               note="replaced by %s+compat" % st["target"]["short"])
    v = _probe(st, io, canary)
    say(st, "canary: resume probe on %s -> %s" % (canary, v))
    if v == "LOSSLESS":
        st["phase"] = "fleet"
        return
    rb_ok, rb = _rollback(io, canary)
    st.update(phase="hold", verdict="HOLD - %s probe %s on %s+compat; rollback %s (%s)" % (
        canary, v, st["target"]["short"], "OK" if rb_ok else "FAILED", rb[-120:]))


def phase_fleet(st, io=None):
    io = io or sys.modules[__name__]
    short = st["target"]["short"]
    done = st.setdefault("fleet_done", [])
    for host in ROLLOUT[1:]:
        if host in done:
            continue
        ok, tail = _deploy(io, st["order"]["dir"], host)
        say(st, "fleet: --engine --only %s -> %s" % (host, "OK" if ok else "FAILED " + tail[-120:]))
        if not ok:
            st.update(phase="hold", verdict="HOLD - deploy on %s failed: %s" % (host, tail))
            return
        io.archive("lab-engine/" + host, st["live"].get("short") or "stock", "lab", stage="engine",
                   location="%s launcher backup *.bak-*-pre-lab%s" % (host, short),
                   rollback="python fleet_deploy.py --engine-rollback --only " + host, note="replaced by %s+compat" % short)
        if host in PROBES:
            v = _probe(st, io, host)
            say(st, "fleet: resume probe on %s -> %s" % (host, v))
            if v != "LOSSLESS":
                _rollback(io, host)
                st.update(phase="hold", verdict="HOLD - %s probe %s on %s+compat; rolled back" % (host, v, short))
                return
        done.append(host)
    if TABLET_HEAD and TABLET_HEAD not in done:
        # the tablet head builds its own CUDA 11.4 engine from the same patched tree: the tree and its one extra patch over
        # (tar pipes), build, then the door
        rc, out = io.run(["bash", "-c", "tar -C '%s' -cf - 'llama.cpp-%s' | ssh %s 'mkdir -p %s && tar -C %s -xf -' && "
                          "tar -C '%s' -cf - '%s' | ssh %s 'mkdir -p %s/patches && tar -C %s/patches -xf -'" % (
            S._posix(LAB_ENGINE), short, TABLET_HEAD, TABLET_HEAD_ENGINE_TREE, TABLET_HEAD_ENGINE_TREE,
            S._posix(PATCHES), TABLET_HEAD_PATCH, TABLET_HEAD, TABLET_HEAD_ENGINE_TREE, TABLET_HEAD_ENGINE_TREE)], timeout=1800)
        if rc != 0:
            st.update(phase="hold", verdict="HOLD - copying the tree to %s failed: %s" % (TABLET_HEAD, " ".join(out.split())[-200:]))
            return
        rc, out = io.run([sys.executable, RUN_REMOTE, TABLET_HEAD, os.path.join(LAB_ENGINE, "build_cuda114.sh"), "7200",
                          "--args", "%s:%s" % (short, TABLET_HEAD_ENGINE_TREE)], timeout=7500)
        if "BUILD_OK=" not in out:
            st.update(phase="hold", verdict="HOLD - %s's engine build did not finish OK: %s" % (TABLET_HEAD, " ".join(out.split())[-240:]))
            return
        say(st, "fleet: %s build OK" % TABLET_HEAD)
        ok, tail = _deploy(io, st["order"]["dir"], TABLET_HEAD)
        say(st, "fleet: --engine --only %s -> %s" % (TABLET_HEAD, "OK" if ok else "FAILED " + tail[-120:]))
        if not ok:
            st.update(phase="hold", verdict="HOLD - %s engine swap failed: %s" % (TABLET_HEAD, tail))
            return
        io.archive("lab-engine/" + TABLET_HEAD, st["live"].get("short") or "?", "lab", stage="engine",
                   location="%s %s<stamp>" % (TABLET_HEAD, TABLET_HEAD_ARCHIVE_PREFIX), rollback="python fleet_deploy.py --engine-rollback --only %s" % TABLET_HEAD)
        done.append(TABLET_HEAD)
    st["phase"] = "verify"


def phase_verify(st, io=None):
    io = io or sys.modules[__name__]
    short = st["target"]["short"]
    live = io.live_tag()
    if live and live.startswith(short):
        st.update(phase="done", live={"short": short, "build": st["target"]["build"]},
                  verdict="OK - every factory serves llama.cpp %s+compat (ollama %s's pin)" % (short, st["target"]["tag"]))
    else:
        st.update(phase="hold", verdict="HOLD - fleet_deploy --check does not read %s+compat everywhere (tag=%s)" % (short, live))
    say(st, st["verdict"])


PHASES = {"detect": phase_detect, "prepare": phase_prepare, "build": phase_build, "delivered": phase_delivered,
          "canary": phase_canary, "fleet": phase_fleet, "verify": phase_verify}


def advance(st=None, io=None):
    """One phase per call. `done` restarts at detect (the next pin); `hold` stays until `reset`."""
    st = st if st is not None else load_state()
    if st.get("phase") == "done":
        st = {"phase": "detect", "live": st.get("live", DEFAULT_PIN), "steps": []}
    if st.get("phase") == "hold":
        say(st, "hold: %s (python engine_pipeline.py reset after a person dealt with it)" % st.get("verdict"))
        return st
    PHASES[st["phase"]](st, io)
    return st


def archive(component, version, klass, rollback, **kw):
    try:
        import lab_archive
        return lab_archive.archive(component, version, klass, rollback, store=os.environ.get("LAB_ARCHIVE_DIR"), **kw)
    except Exception as e:                                                         # noqa: BLE001
        return {"error": str(e)[:120]}


sleep = time.sleep


def main(argv):
    if argv[:1] == ["status"]:
        print(json.dumps(load_state(), indent=1))
        return 0
    if argv[:1] == ["reset"]:
        st = load_state()
        st = {"phase": "detect", "live": st.get("live", DEFAULT_PIN), "steps": ["reset by hand"]}
        save_state(st)
        print("reset to detect")
        return 0
    st = advance()
    save_state(st)
    print("phase now: %s | %s" % (st["phase"], st.get("verdict", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
