r"""Dataset agent for Softserve.

Runs queued dataset jobs by polling a local directory synced via CIFS from the
Sundae orchestrator. This avoids inbound SSH/API calls to Softserve (which has no
sshd and strict firewall rules) and reuses existing byte-verified spec patterns.

Workflow:
  1. Orchestrator drops <name>.spec.json into /docs/State/dataset-queue/
     (the dataset queue folder in the library's State folder).
  2. This agent claims the spec, copies it to a local jobs directory,
     and executes p20_dataset.py.
  3. Progress state and logs are mirrored back to the queue directory for
     the orchestrator to read.

Note: State is copied (not atomically renamed via os.replace) to avoid
WinError 5 races on CIFS shares when files are open by other hosts."""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# the three places this agent works between: settings, no lab defaults (an agent with none set refuses to start)
QUEUE_DIR = os.environ.get("DATASET_QUEUE_DIR", "")
RUNNER = os.environ.get("DATASET_RUNNER", "")
LOCAL_JOBS = os.environ.get("DATASET_LOCAL_JOBS", "")
if not (QUEUE_DIR and RUNNER and LOCAL_JOBS):
    raise SystemExit("dataset agent: set DATASET_QUEUE_DIR, DATASET_RUNNER and DATASET_LOCAL_JOBS")
POLL_S = float(os.environ.get("DATASET_POLL_S", "10"))
MIRROR_S = float(os.environ.get("DATASET_MIRROR_S", "15"))
AGENT_LOG = os.path.join(LOCAL_JOBS, "_agent.log")


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOCAL_JOBS, exist_ok=True)
        with open(AGENT_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _heartbeat():
    """So HAI can tell 'the agent is down' from 'the job is slow'. Those need opposite responses,
    and without this they look identical — the same conflation that cost a whole run on the Met."""
    try:
        os.makedirs(QUEUE_DIR, exist_ok=True)
        tmp = os.path.join(QUEUE_DIR, "_agent.seen.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"seen": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "host": os.environ.get("FOUNDRY_FLEET_HOST") or __import__("socket").gethostname().split(".")[0].lower(),
                       "running": CURRENT[0]}, fh)
        os.replace(tmp, os.path.join(QUEUE_DIR, "_agent.seen"))
    except OSError:
        pass


def _mirror(name):
    """Copy this job's state + log into the shared queue dir so the orchestrator can read them."""
    for ext in (".state.json", ".log"):
        src = os.path.join(LOCAL_JOBS, name + ext)
        if not os.path.exists(src):
            continue
        try:
            shutil.copyfile(src, os.path.join(QUEUE_DIR, name + ext))
        except OSError as exc:
            log("  mirror failed for %s%s: %s" % (name, ext, str(exc)[:70]))


CURRENT = [None]


def _claim(spec_path):
    """Claim a spec by writing a marker. First writer wins; a second agent (or a restart mid-job)
    will not double-run the same spec."""
    name = os.path.basename(spec_path)[:-len(".spec.json")]
    marker = os.path.join(QUEUE_DIR, name + ".claimed")
    if os.path.exists(marker):
        return None
    try:
        # 'x' fails if it already exists — the claim is the atomic bit.
        with open(marker, "x", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S") + " softserve\n")
    except (FileExistsError, OSError):
        return None
    return name


def run_job(name, spec_path):
    """Copy the spec local, run the runner to completion, mirroring progress as it goes."""
    os.makedirs(LOCAL_JOBS, exist_ok=True)
    local_spec = os.path.join(LOCAL_JOBS, name + ".spec.json")
    try:
        shutil.copyfile(spec_path, local_spec)
    except OSError as exc:
        log("cannot copy spec %s: %s" % (name, exc))
        return
    try:
        with open(local_spec, encoding="utf-8") as fh:
            spec = json.load(fh)
        log("claimed %r mode=%s source=%s" % (name, spec.get("mode"), spec.get("source")))
    except Exception as exc:
        log("spec %s is unreadable (%s) — refusing to run it" % (name, str(exc)[:80]))
        return

    CURRENT[0] = name
    # LIST argv, never a shell string: the house rule for anything crossing a process boundary.
    proc = subprocess.Popen([sys.executable, RUNNER, local_spec],
                            cwd=os.path.dirname(RUNNER) or None,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("running %r as pid %d" % (name, proc.pid))
    last = 0.0
    while proc.poll() is None:
        if time.time() - last >= MIRROR_S:
            _mirror(name)
            _heartbeat()
            last = time.time()
        time.sleep(1.0)
    rc = proc.returncode
    _mirror(name)
    CURRENT[0] = None
    log("finished %r rc=%s" % (name, rc))
    try:
        with open(os.path.join(QUEUE_DIR, name + ".done"), "w", encoding="utf-8") as fh:
            fh.write("%s rc=%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), rc))
    except OSError:
        pass


def _preflight():
    """Return list of missing modules required to run the runner.

    The runner is launched via sys.executable, so this interpreter must have
    requests/PIL/pyarrow installed. Fail fast here: a healthy-looking agent
    that lacks these deps will fail on its first real job."""
    missing = []
    for mod in ("requests", "PIL", "pyarrow"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def main():
    log("dataset agent starting — queue=%s runner=%s local=%s" % (QUEUE_DIR, RUNNER, LOCAL_JOBS))
    log("interpreter: %s (%s)" % (sys.executable, sys.version.split()[0]))
    if not os.path.exists(RUNNER):
        log("FATAL: runner not found at %s" % RUNNER)
        return 2
    missing = _preflight()
    if missing:
        log("FATAL: this interpreter is missing %s — the runner would crash on the first job. "
            "Point the launcher at the interpreter that has them." % ", ".join(missing))
        return 3
    while True:
        try:
            os.makedirs(QUEUE_DIR, exist_ok=True)
            _heartbeat()
            # Oldest first, so a queue is served in the order it was filled.
            specs = sorted(glob.glob(os.path.join(QUEUE_DIR, "*.spec.json")), key=os.path.getmtime)
            for sp in specs:
                name = os.path.basename(sp)[:-len(".spec.json")]
                if os.path.exists(os.path.join(QUEUE_DIR, name + ".done")):
                    continue
                claimed = _claim(sp)
                if claimed:
                    run_job(claimed, sp)
                    break          # one job at a time: they share bandwidth and the cache tier
        except Exception as exc:
            # A crash loop that dies silently is worse than one that says why every cycle.
            log("agent cycle error: %s: %s" % (type(exc).__name__, str(exc)[:120]))
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main() or 0)
