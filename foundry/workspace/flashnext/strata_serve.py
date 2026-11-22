"""On-demand Strata serving via llama-swap. Lifecycle stays with strata_arm (single implementation for all callers).

    python strata_serve.py install <plan>          verify shards (SHA-256), run Strata setup, record fingerprint
    python strata_serve.py run <plan>              check fingerprint -> unload ollama -> start Strata. No frontier mode.
    python strata_serve.py stop <plan> <pid>       unload Strata, end process tree (never raises)

`run` auto-installs if fingerprint is missing or stale."""
import datetime, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strata_arm as S  # noqa: E402


def _plan(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _stamp_path(plan):
    return os.path.join(plan["data_dir"], "serve", "installed-%s.json" % str(plan["model"]).lower())


def _src(plan):
    import glob, zipfile
    srczip = glob.glob(os.path.join(plan["strata"], "Strata-v*-source.zip"))[0]
    with zipfile.ZipFile(srczip) as zf:
        return os.path.join(plan["strata"], zf.namelist()[0].split("/", 1)[0])


def _shards(plan):
    out = {}
    for name in plan["shards"]:
        st = os.stat(os.path.join(plan["gguf_dir"], name))
        out[name] = [st.st_size, st.st_mtime_ns]
    return out


def _out(plan, what):
    d = os.path.join(plan["data_dir"], "serve", "%s-%s" % (what, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    os.makedirs(d, exist_ok=True)
    return d


def install(plan_path):
    plan = _plan(plan_path)
    res = {}
    S.prepare(plan, _out(plan, "install"), res, trust_verified=True)   # daily serving: a shard already proven and unchanged is not re-hashed
    rec = {"plan": os.path.abspath(plan_path), "shards": _shards(plan), "engine_build": res.get("engine_build"),
           "setup_s": res.get("setup_s"), "at": datetime.datetime.now().isoformat(timespec="seconds")}
    with open(_stamp_path(plan), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    print("installed:", json.dumps(rec)[:400], flush=True)


def check_installed(plan, plan_path):
    """None when this plan was installed and every shard is as it was then; else the reason it is not."""
    try:
        with open(_stamp_path(plan), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return "not installed - run: strata_serve.py install %s" % plan_path
    if os.path.abspath(rec.get("plan", "")) != os.path.abspath(plan_path):
        return "installed for another plan (%s)" % rec.get("plan")
    if rec.get("shards") != _shards(plan):
        return "a shard changed since install - re-run install (it re-verifies the hashes)"
    return None


# The factory's own claim on the card. enter() unloads every ollama model, so a live request must never start Strata
# under a factory order: the same "free" set the desk reads run-state by (orchestrator app.RELEASING_STATES).
# the factory's root: FOUNDRY_ROOT, else the folder this workspace sits in (<root>\workspace\flashnext)
_FOUNDRY_ROOT = os.environ.get("FOUNDRY_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CURRENT_RUN = os.path.join(_FOUNDRY_ROOT, "var", "state", "CURRENT_RUN.json")
RELEASING_STATES = ("paused", "done", "idle", "finished", "released", "")


def factory_holds_card(path=CURRENT_RUN):
    """None when no factory order holds the card; else why it does. Unreadable = held (cannot-tell is never free)."""
    try:
        with open(path, encoding="utf-8") as f:
            cur = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        return "CURRENT_RUN unreadable (%r)" % (e,)
    state = (cur.get("state") or "").strip().lower()
    if cur.get("name") and state not in RELEASING_STATES:
        return "factory order %r is on the card (state %r)" % (cur.get("name"), state)
    return None


def run(plan_path):
    plan = _plan(plan_path)
    why = factory_holds_card()
    if not why and check_installed(plan, plan_path):
        print("installing first:", check_installed(plan, plan_path), flush=True)
        install(plan_path)                    # once per plan (or after a shard changed): hashes + Strata's setup
        why = check_installed(plan, plan_path)
    if why:
        print("REFUSED:", why, flush=True)
        return 2
    port = int(plan.get("port", 8080))
    if not S.port_free(port):
        print("REFUSED: port %d is already in use (a factory order's Strata?)" % port, flush=True)
        return 2
    src = _src(plan)
    print("serving:", json.dumps(S.apply_serving(plan, src)), flush=True)   # env + engine flags, every start
    S.enter(frontier=False)          # Unload Ollama to free VRAM; do not terminate user processes.
    proc = None
    try:
        proc, ok, load_s = S.launch(os.path.join(src, "START-HERE.bat"), src, port, _out(plan, "run"))
        print("strata %s in %.0f s on port %d" % ("up" if ok else "DIED", load_s, port), flush=True)
        if ok:
            proc.wait()
        return 0 if ok else 1
    finally:
        if proc is not None and proc.poll() is None:
            S.stop_pid(proc.pid, port, restore=False)


def stop(plan_path, pid):
    try:
        plan = _plan(plan_path)
        port = int(plan.get("port", 8080))
    except Exception as e:                                                   # noqa: BLE001
        print("strata_serve stop: plan unreadable (%r) - ending the tree anyway" % (e,), flush=True)
        port = 8080
    S.stop_pid(pid, port, restore=False)    # nothing to restore: daily serving never entered frontier mode


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install" and len(sys.argv) == 3:
        install(sys.argv[2])
    elif cmd == "run" and len(sys.argv) == 3:
        sys.exit(run(sys.argv[2]))
    elif cmd == "stop" and len(sys.argv) == 4:
        stop(sys.argv[2], int(sys.argv[3]))
    else:
        print(__doc__)
        sys.exit(2)
