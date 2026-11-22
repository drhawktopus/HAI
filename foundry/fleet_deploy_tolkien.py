"""Deploy and verify the Tolkien factory (the tablet head, lane name rigby) through the head box that builds its images.

`check()` diffs the local mirror against Sundae's build directory and the running container's bytes to detect drift.
`deploy()` synchronizes the mirror to Sundae, builds images, and recreates containers.

Note: Tablet images embed shared modules (`scheduler.py`, `config.py`) from the orchestrator build context (`COPY --from=orch`).
These modules are sourced from the orchestrator mirror, not the local Tolkien mirror. Therefore, deploying shared modules via this branch (`deploy(["scheduler.py"])`) will rebuild and ship tablet images only after the orchestrator has already synced its mirror to Sundae; otherwise the deployed images will contain stale shared code."""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import foundry_paths as _P      # the foundry's ONE settings reader (etc\lab.env beside FOUNDRY_ROOT, or the environment)

_HERE = os.path.dirname(os.path.abspath(__file__))
_MIRRORS = _P.setting_json("CODE_MIRRORS", {}) or {}
_FLEET_TABLE = _P.setting_json("FLEET_TABLE", {}) or {}
HEAD = _P.setting("HAI_BOX_NAME", "")                       # the head's ssh name: it builds Tolkien's images
HEAD_STACK = _P.setting("HEAD_STACK_DIR", "").rstrip("/")   # the compose stack folder on the head
TABLET_HEAD = _P.setting("TABLET_HEAD_NAME", "")   # the tablet head's ssh name (the lane keeps the name rigby); empty = none
# the mirrors: CODE_MIRRORS names them; a checkout keeps head, foundry, tablet-foundry and tools side by side
MIRROR = _MIRRORS.get("tolkien") or os.path.normpath(os.path.join(_HERE, "..", "tablet-foundry"))
LIVE = HEAD_STACK + "/tolkien"                       # the build context on the head
CONTAINERS = ("tablet-foundry", "tablet-watchdog")   # BOTH run the image - verify each (the two-consumers lesson)
# what runs on the tablet head: the built services plus tolkien-telemetry (Tolkien's logger, the tablet-foundry image
# with another command - built with it, so it is recreated and byte-verified with it but has no build of its own)
RUN_ON_RIGBY = CONTAINERS + ("tolkien-telemetry",)
IMAGES = ("arrstack-tablet-foundry:latest", "arrstack-tablet-watchdog:latest")
INBOX = _P.setting("TABLET_INBOX_HOST", "")          # the order inbox as this head sees it (a share folder)
DEPLOY_HOLD = os.path.join(INBOX, "DEPLOY_HOLD") if INBOX else ""   # tablet_foundry.DEPLOY_HOLD, the same file on the tablet head's mount
CURRENT = ("/tabfoundry/state/CURRENT_ORDER.json", "/tabfoundry/state/CURRENT_ORDER.rigby.json")
LAB_TOOLS = _P.setting("LAB_TOOLS_DIR", "") or os.path.normpath(os.path.join(_HERE, "..", "tools"))
CLAIM = os.path.join(LAB_TOOLS, "deploy_claim.py")
RUN_REMOTE = os.path.join(LAB_TOOLS, "run_remote.py")
ORCH_MIRROR = _MIRRORS.get("orchestrator") or os.path.normpath(os.path.join(_HERE, "..", "head"))
ORCH_LIVE = HEAD_STACK + "/orchestrator"
SHARED = ("scheduler.py", "config.py", "model_fit.py")
# the ONE answer grader, shared with the GPU foundries (Dockerfile `COPY --from=foundry`; additional build context
# ./foundry-shared): shipped from the GPU mirror only when it is byte-identical to what the source GPU factory RUNS
FOUNDRY_MIRROR = _MIRRORS.get("foundry") or _HERE
FOUNDRY_LIVE = HEAD_STACK + "/foundry-shared"
FOUNDRY_SHARED = ("grade_jobs.py", "render.py", "netguard.py",
                  "helper_window.py", "infer_ollama.py", "gpu_spill.py",
                  "gpu_load_reporter.py")
# the GPU factory whose RUNNING copy a shared file must equal (FOUNDRY_SHARED_SOURCE_BOX; the fleet table's first box
# otherwise; none = no fleet, the mirror's bytes ship unchecked)
SOURCE_BOX = _P.setting("FOUNDRY_SHARED_SOURCE_BOX", "") or next(iter(_FLEET_TABLE), "")
SOURCE_BIN = ((_FLEET_TABLE.get(SOURCE_BOX) or {}).get("root", "") + "\\bin") if SOURCE_BOX else ""
# A shared file that is not a foundry bin file: (its source tree, where the source box runs it) - fleet_deploy's own
# MIRROR_SUBDIR / FLEET_ELSEWHERE for it (test_fleet_deploy pins they agree)
FOUNDRY_ELSEWHERE = {"gpu_load_reporter.py": (LAB_TOOLS, ((_P.setting_json("FLEET_ELSEWHERE", {}) or {})
                                                          .get("gpu_load_reporter.py") or {}).get(SOURCE_BOX, ""))}
SKIP = ("__pycache__", ".git", "backups")


def foundry_src(r):
    """The mirror copy of a shared foundry file."""
    return os.path.join(FOUNDRY_ELSEWHERE[r][0], r) if r in FOUNDRY_ELSEWHERE else os.path.join(FOUNDRY_MIRROR, r)
HANDOFF_WAIT_S = 300    # 300 s bound covers the worst-case unclean singleton handoff (90 s heartbeat silence + beat); clean stops release the lock faster, but this limit must stay to avoid race conditions.
# Git Bash is resolved by path because a bare `bash` on Windows can resolve to WSL's bash.
GIT_BASH = os.environ.get("FLEET_GIT_BASH", r"C:\Program Files\Git\bin\bash.exe")


DEPLOY_STOP_TAIL = ": stop for a deploy (answered rows are checkpointed; requeued after the recreate)"


def stop_why(stamp):
    """The cause on a deploy's stop slip. Every such stop is followed by a requeue slip (deploy()'s `finally`), so an
    order carrying it in its stopped results is coming back, not finished (menu_fill.rigby_outstanding)."""
    return "fleet_deploy %s%s" % (stamp, DEPLOY_STOP_TAIL)


def is_deploy_stop(why):
    why = str(why or "")
    return why.startswith("fleet_deploy ") and why.endswith(DEPLOY_STOP_TAIL)


def _ssh(host, cmd, timeout=120, data=None):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, cmd], input=data, capture_output=True,
                       timeout=timeout)
    out = r.stdout.decode("utf-8", "replace")
    return r.returncode, "\n".join(ln for ln in out.splitlines() if not ln.startswith("**")).strip()


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


BUILD_FILES = ("Dockerfile", "requirements.txt")    # they shape the image as much as the code does


def mirror_tree():
    out = {}
    for d, dirs, fs in os.walk(MIRROR):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for f in fs:
            if (f.endswith(".py") or (d == MIRROR and f in BUILD_FILES)) and ".bak" not in f:
                full = os.path.join(d, f)
                out[os.path.relpath(full, MIRROR).replace("\\", "/")] = _sha(full)
    return out


def _parse_sums(text, strip=""):
    out = {}
    for ln in text.splitlines():
        parts = ln.split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            rel = parts[1].lstrip("*").strip()
            rel = rel[len(strip):] if strip and rel.startswith(strip) else rel
            out[rel.lstrip("./")] = parts[0]
    return out


def live_tree():
    rc, out = _ssh(HEAD, "cd %s && { find . -name '*.py' -not -path '*/__pycache__/*' -not -name '*.bak*'; "
                             "ls %s 2>/dev/null; } | tr '\\n' '\\0' | xargs -0 sha256sum"
                   % (LIVE, " ".join(BUILD_FILES)))
    return _parse_sums(out)


def container_tree(c, rels):
    if not rels:
        return {}
    rc, out = _ssh(TABLET_HEAD, "docker exec %s sha256sum %s 2>/dev/null" % (c, " ".join("/app/" + r for r in rels)))
    return _parse_sums(out, strip="/app/")


def shared_state():
    """{name: (orchestrator-mirror sha, Sundae orchestrator-dir sha)} for the files the tablet image bakes in."""
    want = {r: _sha(os.path.join(ORCH_MIRROR, r)) for r in SHARED}
    rc, out = _ssh(HEAD, "cd %s && sha256sum %s" % (ORCH_LIVE, " ".join(SHARED)))
    have = _parse_sums(out)
    assert have, "zero shared files read from Sundae's orchestrator dir - the read is broken, not the tree"
    return {r: (want[r], have.get(r)) for r in SHARED}


def foundry_deployed_sha(r):
    """sha256 of `r` as the source GPU factory RUNS it (its <root>\\bin), or None if it cannot be read."""
    where = FOUNDRY_ELSEWHERE[r][1] if r in FOUNDRY_ELSEWHERE else SOURCE_BIN
    if not SOURCE_BOX:
        return None
    rc, out = _ssh(SOURCE_BOX, "certutil -hashfile %s\\%s SHA256" % (where, r))
    hit = [ln.strip().replace(" ", "").lower() for ln in out.splitlines()
           if len(ln.strip().replace(" ", "")) == 64 and all(c in "0123456789abcdefABCDEF " for c in ln.strip())]
    return hit[0] if hit else None


def foundry_shared_ship(stamp):
    """Put the GPU foundries' grader into the tablet image's `foundry` build context. -> an error string, or None."""
    _ssh(HEAD, "sudo mkdir -p %s" % FOUNDRY_LIVE)
    for r in FOUNDRY_SHARED:
        want = _sha(foundry_src(r))
        runs = foundry_deployed_sha(r)
        if SOURCE_BOX and runs != want:
            return ("REFUSED - %s in the GPU mirror (%s) is not what %s runs (%s): deploy it to the GPU "
                    "factories first, so Tolkien never gets a grader they do not have" % (r, want[:12], SOURCE_BOX, (runs or "?")[:12]))
        tmp = "/tmp/%s.foundry.%s" % (r, stamp)
        if subprocess.run(["scp", "-q", foundry_src(r), HEAD + ":" + tmp]).returncode != 0:
            return "FAILED - scp of %s to the foundry context" % r
        rc, got = _ssh(HEAD, "sudo cp %s %s/%s && sha256sum %s/%s" % (tmp, FOUNDRY_LIVE, r, FOUNDRY_LIVE, r))
        if not got.startswith(want):
            return "FAILED - %s did not land byte-identical in %s" % (r, FOUNDRY_LIVE)
    return None


def drift(names):
    m, lv = mirror_tree(), live_tree()
    both = set(m) & set(lv)
    assert both, "zero files compared between mirror and Sundae - the join is broken, not the tree"
    differ = sorted(r for r in both if m[r] != lv[r])
    return m, lv, len(both), [r for r in differ if r not in names], [r for r in differ if r in names]


def _settings_missing():
    """The names this door cannot work without, when unset; the doors say so instead of ssh-ing to nowhere."""
    return [k for k, v in (("HAI_BOX_NAME", HEAD), ("HEAD_STACK_DIR", HEAD_STACK), ("TABLET_INBOX_HOST", INBOX),
                           ("TABLET_HEAD_NAME", TABLET_HEAD)) if not v]


def check(names=()):
    if _settings_missing():
        print("REFUSED: %s unset - no Tolkien deploy from this head" % " / ".join(_settings_missing()))
        return 2
    m, lv, n, foreign, named = drift(set(names))
    print("-- tolkien  mirror %s <-> %s:%s   compared %d" % (MIRROR, HEAD, LIVE, n))
    print("   build dir drift (not in this deploy): %s" % (", ".join(foreign) or "none"))
    sh = shared_state()
    sundae_stale = [r for r, (w, h) in sh.items() if w != h]
    print("   shared from the orchestrator mirror (%s): Sundae's copy %s" % (
        ", ".join(SHARED), ("DIFFERS " + ", ".join(sundae_stale)) if sundae_stale else "matches"))
    want = dict(m, **{r: w for r, (w, h) in sh.items()})
    fw = {r: _sha(foundry_src(r)) for r in FOUNDRY_SHARED}
    runs = {r: foundry_deployed_sha(r) for r in FOUNDRY_SHARED}
    off_gpu = [r for r in FOUNDRY_SHARED if runs[r] != fw[r]]
    print("   shared from the GPU foundries (%s): Mothership runs %s" % (
        ", ".join(FOUNDRY_SHARED), ("a DIFFERENT copy of " + ", ".join(off_gpu)) if off_gpu else "the mirror's copy"))
    want.update(fw)
    rels = sorted(((set(names) - set(SHARED) - set(FOUNDRY_SHARED)) or set(m)) - set(BUILD_FILES)) \
        + list(SHARED) + list(FOUNDRY_SHARED)
    stale_any = False
    for c in RUN_ON_RIGBY:
        ct = container_tree(c, rels)
        assert ct, "zero files read from %s - the read is broken, not the container" % c
        stale = [r for r in rels if r in ct and ct[r] != want.get(r)]
        # a runtime file the image should carry but does not is drift too (tests are gate-mounted, never baked)
        missing = [r for r in rels if r not in ct and not r.startswith("tests/")]
        stale_any = stale_any or bool(stale) or bool(missing)
        print("   %-16s compared %d files to the mirrors: %s" % (c, len(ct), "; ".join(
            x for x in (("STALE " + ", ".join(stale)) if stale else "", ("MISSING " + ", ".join(missing)) if missing
                        else "") if x) or "in sync"))
    return 0 if not (foreign or sundae_stale or stale_any or off_gpu) else 1


def _current_orders():
    names = []
    for p in CURRENT:
        rc, out = _ssh(TABLET_HEAD, "docker exec tablet-foundry cat %s 2>/dev/null" % p)
        try:
            d = json.loads(out) if out else {}
        except ValueError:
            raise RuntimeError("unreadable %s - fail closed, nothing shipped" % p)
        if not isinstance(d, dict) or str(d.get("state", "running")).lower() in ("idle", "done", ""):
            continue
        # Claim lists EVERY order under `orders` (see tablet_foundry.claim_orders) - stop/requeue each one, not just the oldest
        rows = d.get("orders") if isinstance(d.get("orders"), list) else [d]
        for r in rows:
            n = (r.get("name") or r.get("order") or "").strip() if isinstance(r, dict) else ""
            if n and n not in names:
                names.append(n)
    return names


def rigby_since(ssh=None):
    """Return the `docker logs --since` timestamp using RIGBY's local clock to ensure log line alignment.

    Raises an exception if the timestamp cannot be read to fail closed, preventing the deploy from
    shipping incomplete or mismatched log data."""
    rc, out = (ssh or _ssh)(TABLET_HEAD, "date -u +%Y-%m-%dT%H:%M:%SZ")
    stamp = (out or "").strip().splitlines()[-1].strip() if (out or "").strip() else ""
    if rc != 0 or not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", stamp):
        raise RuntimeError("rigby's clock unreadable (rc %s, %r) - fail closed, nothing shipped" % (rc, stamp[:60]))
    return stamp


def slip_answers(names, log_text):
    """Determine if a stop slip was stopped by the deploy ('stopped') or already finished ('finished').

    Relies on the log, not the claim file: claims can outlive the order and cause false resume."""
    out = {}
    for ln in log_text.splitlines():
        for n in names:
            if "[DESK] STOPPED %s " % n in ln:
                out[n] = "stopped"
            elif ("STOP %s: already finished" % n) in ln:
                out.setdefault(n, "finished")
    return out


def customer_stopped(names, log_text):
    """Identify running orders stopped by a customer, not a deploy.

    These orders must be excluded from requeueing; a deploy's requeue would
    override the customer's stop intent."""
    out = set()
    for ln in (log_text or "").splitlines():
        for n in names:
            head = "STOP requested for RUNNING %s by " % n
            if head in ln and "): no new work" in ln:
                why = ln.split(head, 1)[1].split(" (", 1)[1].rsplit("): no new work", 1)[0] if " (" in ln.split(
                    head, 1)[1] else ""
                if not is_deploy_stop(why):
                    out.add(n)
    return out


def hold_intake(stamp, log=print):
    """Set the intake hold flag. Non-fatal: on failure the deploy proceeds but risks a race with parked orders."""
    try:
        with open(DEPLOY_HOLD, "w", encoding="utf-8") as f:
            f.write("fleet_deploy %s\n" % stamp)
    except OSError as e:
        log("tolkien: could not hold intake (%r) - an order started before the swap would be lost to it" % (e,))


def release_intake(log=print):
    """Never raises (it runs in a finally). The taker also ignores a flag older than DEPLOY_HOLD_MAX_S."""
    try:
        os.remove(DEPLOY_HOLD)
    except FileNotFoundError:
        pass
    except OSError as e:
        log("tolkien: could not release intake (%r) - it ends by itself at the flag's age limit" % (e,))


def comes_back(name, customer):
    """Determines if a deploy requeues `name`. Excludes customer-stopped and chat windows (`hai-win-`). `pause` marks stop as incomplete, not failed."""
    return name not in customer and not name.startswith("hai-win-")


def to_requeue(asked, answers, customer):
    """The stopped orders that come back after the deploy (comes_back), of those Tolkien says it stopped."""
    return [n for n in asked if answers.get(n) == "stopped" and comes_back(n, customer)]


def _slip(name, ext, body):
    p = os.path.join(INBOX, name + ext)
    with open(p + ".incoming", "w", encoding="utf-8") as f:
        json.dump(body, f)
    os.replace(p + ".incoming", p)


def deploy(names, log=print, wait_stop_s=1800):
    if _settings_missing():
        return "REFUSED - %s unset: no Tolkien deploy from this head" % " / ".join(_settings_missing())
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    t_start = datetime.datetime.now(datetime.timezone.utc)
    # Log output is wrapped to prepend elapsed time; t_mono must remain the pre-wrapped baseline.
    t_mono, _log = time.time(), log
    log = lambda m: _log("[+%5.1fs] %s" % (time.time() - t_mono, m))  # noqa: E731
    for r in names:
        if r not in SHARED + FOUNDRY_SHARED and not os.path.exists(os.path.join(MIRROR, r)):
            return "FAILED - no such file in the Tolkien mirror: %s" % r
    shared = [r for r in names if r in SHARED]
    names = [r for r in names if r not in SHARED + FOUNDRY_SHARED]
    if subprocess.run(["python", CLAIM, "acquire", "fleet_deploy", "tolkien: " + " ".join(names)]).returncode != 0:
        return "REFUSED - the deploy lane is held (deploy_claim.py status)"
    stopped, requeued = [], set()
    try:
        m, lv, n, foreign, named = drift(set(names))
        log("tolkien: compared %d files mirror<->Sundae; foreign drift: %s" % (n, foreign or "none"))
        if foreign:
            return "REFUSED - Sundae's build dir differs from the mirror outside this deploy (%s): a deploy would " \
                   "publish or revert someone else's work" % ", ".join(foreign)
        sh = shared_state()
        off = [r for r, (w, h) in sh.items() if w != h]
        if off:
            return "REFUSED - Sundae's orchestrator copy of %s differs from the orchestrator mirror: deploy it through " \
                   "the orchestrator procedure first (the tablet image bakes in Sundae's copy)" % ", ".join(off)
        m = dict(m, **{r: w for r, (w, h) in sh.items()})
        err = foundry_shared_ship(stamp)
        if err:
            return err
        m.update({r: _sha(foundry_src(r)) for r in FOUNDRY_SHARED})
        for r in names:
            dst = "%s/%s" % (LIVE, r)
            _ssh(HEAD, "sudo cp %s %s.bak.%s 2>/dev/null || true" % (dst, dst, stamp))
            tmp = "/tmp/%s.new.%s" % (os.path.basename(r), stamp)
            if subprocess.run(["scp", "-q", os.path.join(MIRROR, r), HEAD + ":" + tmp]).returncode != 0:
                return "FAILED - scp of %s" % r
            rc, got = _ssh(HEAD, "sudo cp %s %s && sudo chmod 0644 %s && sha256sum %s" % (tmp, dst, dst, dst))
            # the expected bytes are the mirror file's own (the tree map holds .py only; a test fixture is .json)
            if not got.startswith(m.get(r) or _sha(os.path.join(MIRROR, r))):
                return "FAILED - %s did not land byte-identical on Sundae" % r
        log("tolkien: %d file(s) copied and byte-verified; building both images" % len(names))
        # the BUILD's exit code, not tail's - a failed build must never read as built
        rc, out = _ssh(HEAD, "cd %s && sudo docker compose build %s > /tmp/tolkien_build.%s.log 2>&1; "
                                 "r=$?; tail -3 /tmp/tolkien_build.%s.log; exit $r" % (HEAD_STACK, " ".join(CONTAINERS), stamp, stamp),
                       timeout=1800)
        if rc != 0:
            return "FAILED - image build: %s" % out[-300:]
        rc, gate = _ssh(HEAD, "sudo bash %s/run-tolkien-suite.sh 2>&1 | tail -8" % HEAD_STACK, timeout=1800)
        log("tolkien gate: %s" % gate.replace("\n", " | ")[-300:])
        gate_ok = any(ln.strip().startswith("OK") for ln in gate.splitlines())
        if not gate_ok or "FAILED" in gate or "MISMATCH" in gate:
            for r in names:
                dst = "%s/%s" % (LIVE, r)
                _ssh(HEAD, "sudo cp %s.bak.%s %s 2>/dev/null || true" % (dst, stamp, dst))
            return "FAILED - the Tolkien gate is not green; Sundae's files restored, rigby untouched"
        log("tolkien: shipping both images Sundae -> rigby%s" % ("" if os.path.exists(GIT_BASH) else
                                                                  " (Git Bash not found - the slow Windows pipe)"))
        if os.path.exists(GIT_BASH):
            # shell pipe is required to avoid the Windows Python stdio bottleneck (30x slower); pipefail ensures a failed save does not silently succeed.
            load = subprocess.run([GIT_BASH, "-c", "set -o pipefail; ssh %s 'sudo docker save %s' | ssh %s "
                                   "'docker load'" % (HEAD, " ".join(IMAGES), TABLET_HEAD)], capture_output=True, timeout=1800)
            shipped = load.returncode == 0
        else:
            save = subprocess.Popen(["ssh", HEAD, "sudo docker save %s" % " ".join(IMAGES)], stdout=subprocess.PIPE)
            load = subprocess.run(["ssh", TABLET_HEAD, "docker load"], stdin=save.stdout, capture_output=True, timeout=1800)
            save.stdout.close()
            shipped = save.wait() == 0 and load.returncode == 0
        if not shipped:
            return "FAILED - image ship: %s" % load.stderr.decode("utf-8", "replace")[-300:]
        log("tolkien: images loaded on rigby")
        hold_intake(stamp, log)      # before the claim is read, so nothing starts between the read and the swap
        asked = sorted(set(_current_orders()))
        customer = set()
        if asked:
            rc, text = _ssh(TABLET_HEAD, "docker logs tablet-foundry 2>&1 | grep 'STOP requested for RUNNING'", timeout=120)
            customer = customer_stopped(asked, text)
        since = rigby_since() if asked else None
        for name in asked:
            _slip(name, ".stop.json", {"why": stop_why(stamp), "pause": comes_back(name, customer)})
            log("tolkien: stop slip for running order %s" % name)
        t0 = time.time()
        answers = {}
        while asked and len(answers) < len(asked):
            rc, text = _ssh(TABLET_HEAD, "docker logs --since %s tablet-foundry 2>&1" % since, timeout=120)
            answers = slip_answers(asked, text)
            if len(answers) == len(asked):
                break
            if time.time() - t0 > wait_stop_s:
                return "STOPPED SHORT - no stop answer from Tolkien for %s within %ds; images built, gated and loaded " \
                       "on rigby, but the containers NOT recreated" % (sorted(set(asked) - set(answers)), wait_stop_s)
            time.sleep(10)
        for name in asked:
            log("tolkien: %s - %s" % (name, "already finished per Tolkien (not ours to requeue)" if answers.get(name) != "stopped"
                                     else "stopped by this deploy; Kirk reopens its window" if name.startswith("hai-win-")
                                     else "stopped by this deploy (requeued after it)"))
        for name in sorted(customer):
            log("tolkien: %s - its customer had already asked it to stop: NOT requeued after the deploy" % name)
        stopped.extend(to_requeue(asked, answers, customer))
        log("tolkien: recreating")
        script = os.path.join(tempfile.gettempdir(), "tolkien_recreate_%s.sh" % stamp)
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write("cd %s && docker compose up -d --force-recreate %s 2>&1\necho DONE\n"
                    % (HEAD_STACK, " ".join(RUN_ON_RIGBY)))
        r = subprocess.run(["python", RUN_REMOTE, TABLET_HEAD, script, "300"], capture_output=True, timeout=600)
        log("tolkien recreate: %s" % r.stdout.decode("utf-8", "replace").strip().replace("\n", " | ")[-300:])
        # Requeue before release: the new taker processes staged slips before picking queued orders. Requeuing after 'up' causes the old taker to miss its turn.
        requeue_stopped(stopped, requeued, stamp, log)
        release_intake(log)          # after the requeue: the new taker's first pass reads its slips, then picks
        bad = []
        runtime = ([rr for rr in names if not rr.startswith("tests/") and rr not in BUILD_FILES]
                   + list(SHARED) + list(FOUNDRY_SHARED))    # tests are gate-mounted; build files shape, never ship
        for c in RUN_ON_RIGBY:
            rc, started = _ssh(TABLET_HEAD, "docker inspect -f '{{.State.StartedAt}}' %s" % c)
            ct = container_tree(c, runtime)
            fresh = started[:19] >= t_start.strftime("%Y-%m-%dT%H:%M:%S")
            same = all(ct.get(rr) == m[rr] for rr in runtime)
            log("   %-16s started %s (%s) | bytes %s" % (c, started[:19], "new" if fresh else "OLD",
                                                        "match" if same else "STALE"))
            if not (fresh and same):
                bad.append(c)
        if bad:
            return "FAILED - %s not running the new code after the recreate" % bad
        # the new order taker takes the singleton lock once the old instance's heartbeat has been quiet for 90 s
        t1 = time.time()
        while True:
            rc, logs = _ssh(TABLET_HEAD, "docker logs --since 10m tablet-foundry 2>&1 | grep -c 'order taker up'")
            if logs.strip().isdigit() and int(logs.strip()) > 0:
                log("tolkien: order taker up after %.0f s of handover wait" % (time.time() - t1))
                break
            if time.time() - t1 > HANDOFF_WAIT_S:
                return "INCONCLUSIVE - new code verified in both containers, but no 'order taker up' within %ds" \
                       % HANDOFF_WAIT_S
            time.sleep(10)
        return "OK - deployed, gated, shipped, both containers recreated and verified, order taker up"
    finally:
        requeue_stopped(stopped, requeued, stamp, log)       # any route that has not requeued yet (failures included)
        release_intake(log)
        subprocess.run(["python", CLAIM, "release", "fleet_deploy"])


# Rigby A1: share path is the live deployed payload (Tolkien reads it for every order); dev tree is the source (tests live there). Keep them in sync manually; the container has no network or live mount to reconcile them. The test suites run inside the tablet-foundry image on rigby.
HB = _P.setting("HARD_BENCH_DIR", "")                     # the menu machinery's folder on this head (the A1 dev trees)
RIGBY_A1_LIVE = _P.setting("TABLET_A1_PAYLOAD_HOST", "")  # the live payload share as this head sees it
RIGBY_A1_SEEN = _P.setting("TABLET_A1_PAYLOAD_SEEN", "")  # the same folder through Tolkien's own mount
RIGBY_A1 = {"a1_payload.py": "inspect_lab", "a1_task.py": "inspect_lab", "lab_provider.py": "inspect_lab",
            "a1_fellowship.py": "inspect_lab", "a1_harness.py": "a1", "a1_validity.py": "a1", "a1_ollama.py": "a1",
            "a1_stop.py": "a1", "a1_branch.py": "a1", "a1_chat.py": "a1"}
RIGBY_A1_PIN = os.path.join(HB, "inspect_lab", "gate_pin.py")
RIGBY_A1_MANIFEST = os.path.join(RIGBY_A1_LIVE, ".deployed.json")   # {file: sha256} as this door last shipped it
STAGE_SKIP = ("__pycache__", "logs", "orders")


def rigby_a1_src(name):
    return os.path.join(HB, RIGBY_A1[name], name)


def read_pin(path):
    """(EXPECT_TESTS, EXPECT_SKIPS) from a suite's gate_pin.py, or None when it cannot be read - never a default."""
    try:
        txt = open(path, encoding="utf-8").read()
    except OSError:
        return None
    got = {}
    for ln in txt.splitlines():
        k, _, v = ln.partition("=")
        if k.strip() in ("EXPECT_TESTS", "EXPECT_SKIPS") and v.strip().split("#")[0].strip().isdigit():
            got[k.strip()] = int(v.strip().split("#")[0].strip())
    return (got["EXPECT_TESTS"], got.get("EXPECT_SKIPS", 0)) if "EXPECT_TESTS" in got else None


def suite_verdict(out, pin):
    """'ok' / a reason, from unittest's output and the pin. A run that printed no count is a failure, never a pass."""
    if pin is None:
        return "no readable gate pin"
    ran = [int(x.split()[1]) for x in out.splitlines() if x.startswith("Ran ") and x.split()[1].isdigit()]
    if not ran:
        return "no 'Ran N tests' line"
    tail = [x.strip() for x in out.splitlines() if x.strip()][-1:]
    if not tail or not tail[0].startswith("OK"):
        return "suite not green: %s" % (tail[0] if tail else "no output")
    skips = int(tail[0].split("skipped=")[1].rstrip(")").split(",")[0]) if "skipped=" in tail[0] else 0
    if (ran[-1], skips) != pin:
        return "PIN MISMATCH - ran %d skipped %d, pinned %d/%d" % (ran[-1], skips, pin[0], pin[1])
    return "ok"


def hand_edited(live_sha, shipped_sha, src_sha):
    """True when the share copy is neither what this door last shipped nor the source: someone saved it by hand, and
    shipping the source would silently revert their work."""
    return live_sha is not None and live_sha != src_sha and (shipped_sha is None or live_sha != shipped_sha)


def share_edits(st, manifest):
    """Files whose share copy was saved by hand (one derivation for --check and the deploy). With no manifest yet
    (before the first deploy through this door), only a share copy NEWER than its source counts."""
    edited = [n for n, (s, lv, sh) in st.items() if hand_edited(lv, sh, s)]
    if manifest:
        return edited
    return [n for n in edited
            if os.path.getmtime(os.path.join(RIGBY_A1_LIVE, n)) > os.path.getmtime(rigby_a1_src(n))]


def _stage_tar(names):
    """The dev layout the suites expect (inspect_lab beside a1, map_brief.py one level up), as tar bytes."""
    import io as _io
    import tarfile
    buf = _io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for sub in ("inspect_lab", "a1"):
            root = os.path.join(HB, sub)
            for d, dirs, fs in os.walk(root):
                dirs[:] = [x for x in dirs if x not in STAGE_SKIP and x != "tests"]
                for f in fs:
                    if ".bak" in f or f.endswith(".pyc") or f.endswith(".log"):
                        continue
                    if d == root and sub == "a1" and not (f.endswith(".py") and f in names):
                        continue
                    full = os.path.join(d, f)
                    tar.add(full, arcname=os.path.relpath(full, HB).replace("\\", "/"))
        for f in ("map_brief.py", "payload_embed.py", "cli_answer.py", "test_cli_answer.py",   # test_map_brief's
                  "fixtures/cli_captures"):                                                # one-derivation check
            tar.add(os.path.join(HB, f), arcname=f)
    return buf.getvalue()


def rigby_a1_gate(stamp, log=print):
    """Run the payload's suites from the dev trees inside the tablet-foundry image on rigby. -> 'ok' or a reason."""
    pin = read_pin(RIGBY_A1_PIN)
    suites = sorted(f[:-3] for f in os.listdir(os.path.join(HB, "inspect_lab"))
                    if f.startswith("test_") and f.endswith(".py"))
    stage = "/tmp/rigby-a1-gate-%s" % stamp
    r = subprocess.run(["ssh", TABLET_HEAD, "mkdir -p %s && tar x -C %s" % (stage, stage)],
                       input=_stage_tar(set(RIGBY_A1) | {"a1_order.py"}), capture_output=True, timeout=300)
    if r.returncode != 0:
        return "staging failed: %s" % r.stderr.decode("utf-8", "replace")[-200:]
    # offline by design; Inspect's mock model needs the o200k tokenizer, so it comes from a staged fixture cache
    rc, out = _ssh(TABLET_HEAD, "docker run --rm --network none -v %s:/gate -w /gate/inspect_lab -e PYTHONPATH=/app "
                            "-e HAI_STATE_DIR=/tmp/gate-state -e PYTHONDONTWRITEBYTECODE=1 "
                            "-e TIKTOKEN_CACHE_DIR=/gate/inspect_lab/fixtures/tiktoken_cache "
                            "%s python -m unittest %s 2>&1; rm -rf %s"
                   % (stage, IMAGES[0], " ".join(suites), stage), timeout=1800)
    v = suite_verdict(out, pin)
    log("rigby-a1 gate (%d suites in %s): %s" % (len(suites), IMAGES[0], v if v == "ok" else v + "\n" + out[-1500:]))
    return v


def _manifest():
    try:
        return json.load(open(RIGBY_A1_MANIFEST, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rigby_a1_state():
    """{file: (source sha, share sha or None, last-shipped sha or None)}."""
    shipped = _manifest()
    out = {}
    for n in RIGBY_A1:
        live = os.path.join(RIGBY_A1_LIVE, n)
        out[n] = (_sha(rigby_a1_src(n)), _sha(live) if os.path.exists(live) else None, shipped.get(n))
    return out


def rigby_a1_seen(names):
    """{file: sha} as the tablet-foundry container reads the share (the object actually in force)."""
    rc, out = _ssh(TABLET_HEAD, "docker exec tablet-foundry sha256sum %s" % " ".join(
        "%s/%s" % (RIGBY_A1_SEEN, n) for n in names))
    return _parse_sums(out, strip=RIGBY_A1_SEEN + "/")


def rigby_a1_check():
    st = rigby_a1_state()
    seen = rigby_a1_seen(list(st))
    assert seen, "zero payload files read through the container - the read is broken, not the share"
    bad = 0
    edited = share_edits(st, _manifest())
    print("-- rigby-a1  dev trees %s\\{inspect_lab,a1} <-> %s   compared %d" % (HB, RIGBY_A1_LIVE, len(st)))
    for n, (src, live, shipped) in sorted(st.items()):
        tag = ("in sync" if src == live == seen.get(n) else "HAND-EDITED in the share" if n in edited
               else "differs (source newer - deploy it)" if src != live else "container reads DIFFERENT bytes")
        bad += tag != "in sync"
        print("   %-18s %s" % (n, tag))
    return 1 if bad else 0


def _rigby_a1_order_running():
    """The running rigby order's name if its spec loads the A1 payload, else None. Unreadable = assume it does."""
    rc, out = _ssh(TABLET_HEAD, "docker exec tablet-foundry cat %s 2>/dev/null" % CURRENT[1])
    try:
        cur = json.loads(out) if out else {}
    except ValueError:
        raise RuntimeError("unreadable %s - fail closed, nothing shipped" % CURRENT[1])
    name = (cur.get("name") or "").strip()
    if not name or str(cur.get("state", "running")).lower() in ("idle", "done"):
        return None
    rc, spec = _ssh(TABLET_HEAD, "docker exec tablet-foundry sh -c 'cat /tabfoundry/queue/%s.tjob.json' 2>/dev/null" % name)
    return name if (rc != 0 or "/payloads/a1/" in spec) else None


def deploy_rigby_a1(log=print, wait_stop_s=1800):
    """Gate the payload's suites, stop an A1 order mid-run (its answers are checkpointed), copy + byte-verify into the
    share, confirm the container reads the new bytes, requeue what was stopped. Refuses over a hand-edited share."""
    if not (HB and RIGBY_A1_LIVE and RIGBY_A1_SEEN and TABLET_HEAD):
        return ("REFUSED - HARD_BENCH_DIR / TABLET_A1_PAYLOAD_HOST / TABLET_A1_PAYLOAD_SEEN / TABLET_HEAD_NAME unset: no A1 "
                "payload door on this head")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if subprocess.run(["python", CLAIM, "acquire", "fleet_deploy", "rigby-a1 payload"]).returncode != 0:
        return "REFUSED - the deploy lane is held (deploy_claim.py status)"
    stopped, requeued = [], set()
    try:
        st = rigby_a1_state()
        edited = share_edits(st, _manifest())
        if edited:
            return "REFUSED - the share copy of %s was saved by hand: pull it into the dev tree first, or this " \
                   "deploy would revert it" % ", ".join(edited)
        changed = [n for n, (s, lv, sh) in st.items() if s != lv]
        v = rigby_a1_gate(stamp, log)
        if v != "ok":
            return "FAILED - the rigby-a1 gate: %s; the share is untouched" % v
        if not changed:
            json.dump({n: s for n, (s, lv, sh) in st.items()}, open(RIGBY_A1_MANIFEST, "w", encoding="utf-8"), indent=1)
            return "OK - gated; the share already holds every source file (manifest written)"
        # Do not stop a running A1 order: it loaded the payload modules at start (via a1_payload drop/re-import) and holds that copy; the next order picks up new files. This 'keep-running' behavior is the fleet's --a1 contract.
        name = _rigby_a1_order_running()
        if name:
            log("rigby-a1: %s is running - it keeps the copy it loaded; the next order runs the new files" % name)
        for n in changed:
            dst = os.path.join(RIGBY_A1_LIVE, n)
            if os.path.exists(dst):
                shutil.copy2(dst, "%s.bak-%s" % (dst, stamp))
            shutil.copyfile(rigby_a1_src(n), dst + ".incoming")
            os.replace(dst + ".incoming", dst)
            if _sha(dst) != st[n][0]:
                return "FAILED - %s did not land byte-identical in the share" % n
        seen = rigby_a1_seen(changed)
        stale = [n for n in changed if seen.get(n) != st[n][0]]
        if stale:
            return "FAILED - the tablet-foundry container still reads the old %s" % ", ".join(stale)
        json.dump({n: s for n, (s, lv, sh) in st.items()}, open(RIGBY_A1_MANIFEST, "w", encoding="utf-8"), indent=1)
        log("rigby-a1: shipped %s; the container reads the new bytes" % ", ".join(changed))
        return "OK - gated, shipped %d file(s), verified through the container" % len(changed)
    finally:
        requeue_stopped(stopped, requeued, stamp, log)
        subprocess.run(["python", CLAIM, "release", "fleet_deploy"])


def requeue_stopped(stopped, requeued, stamp, log=print):
    """One requeue slip per order this deploy stopped, at most once each; never raises (it runs in a finally)."""
    for name in stopped:
        if name in requeued:
            continue
        try:
            _slip(name, ".ctl.json", {"action": "requeue", "why": "fleet_deploy %s: resume after the deploy" % stamp})
            requeued.add(name)
            log("tolkien: requeue slip for %s" % name)
        except Exception as e:                                       # noqa: BLE001
            log("tolkien: REQUEUE SLIP FAILED for %s (%r) - requeue by hand" % (name, e))
