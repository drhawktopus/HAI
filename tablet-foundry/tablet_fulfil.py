"""
Fan-out engine for tablet order fulfillment.

Separated from `tablet_foundry.py` to decouple intake/health management from execution.

Design constraints:
- DYNAMIC PULL: Node latency is volatile; static splitting causes severe tail latencies.
- SPECULATIVE EXECUTION: First-to-finish wins; duplicates are deduplicated.
- REQUEUE ON FAILURE: Dropped nodes return work to the pool; no tasks are lost.
- CHECKPOINT EVERY BATCH: Long sweeps must survive driver hiccups without restarting.
- POSITIVE CONTROL: Each order includes a control task; failure invalidates the run.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from queue import Empty, Queue

from tablet_foundry import (BATCH, REMOTE_STAGE, RESULTS_DIR, STATE_DIR, _scp, _ssh, log, sh)

# Speculative winners must release the order even while a losing transport cleans up.
SPECULATIVE_FACTOR = float(os.environ.get("TABLET_SPECULATIVE_FACTOR", "3"))
SPECULATIVE_MIN_S = float(os.environ.get("TABLET_SPECULATIVE_MIN_S", "120"))
SPECULATIVE_CLEANUP_GRACE_S = float(
    os.environ.get("TABLET_SPECULATIVE_CLEANUP_GRACE_S", "2"))

# A loser keeps cleaning up on its daemon thread.  Its host is withheld from later orders until
# that thread exits, so a possibly-live remote process never overlaps new work on the same tablet.
_ORPHAN_LOCK = threading.Lock()
_ORPHAN_THREADS = {}
REJOIN_CHECK_S = 10      # how often a running order looks for a withheld host whose loser has exited
# matches the watchdog's read cadence (TABLET_WATCH_POLL); probing faster only adds ssh load to busy nodes
WORK_PROBE_S = float(os.environ.get("TABLET_WORK_PROBE_S", os.environ.get("TABLET_WATCH_POLL", "60")))

# The tablet head's model stage server (a payload fetches a model it lacks from it), as the tablets reach it: a lab
# setting, handed to every payload as FELLOW_STAGE_URL. A tablet whose own environment sets FELLOW_STAGE_URL keeps its
# own (a wired tablet reaches the head on its USB address). Only a plain http(s)://host[:port] address is passed on.
STAGE_URL = os.environ.get("TABLET_STAGE_URL", "").strip()
_STAGE_URL_RX = re.compile(r"https?://[A-Za-z0-9.\-]+(?::\d{1,5})?/?")


def _stage_env():
    """The shell prefix that hands a payload the stage server's address; empty when no usable address is set."""
    if not STAGE_URL:
        return ""
    if not _STAGE_URL_RX.fullmatch(STAGE_URL):
        log("TABLET_STAGE_URL is not a plain http(s)://host[:port] address (%r): payloads get no stage server"
            % STAGE_URL)
        return ""
    return 'FELLOW_STAGE_URL="${FELLOW_STAGE_URL:-%s}" ' % STAGE_URL.rstrip("/")


def _available_hosts(hosts):
    """Return (available, quarantined), reaping finished speculative losers first."""
    with _ORPHAN_LOCK:
        for host, thread in list(_ORPHAN_THREADS.items()):
            if not thread.is_alive():
                del _ORPHAN_THREADS[host]
        quarantined = [host for host in hosts if host in _ORPHAN_THREADS]
    return [host for host in hosts if host not in quarantined], quarantined


def kill_attempt_cmd(stage):
    """Shell for the tablet: kill the processes whose working directory is this attempt's stage (the payload) and their
    children (llama-cli). Matched on cwd, never the command line - this command's own line names the stage."""
    return ('S="$HOME/%s"; n=0; for p in /proc/[0-9]*; do [ "$(readlink $p/cwd 2>/dev/null)" = "$S" ] || continue; '
            'pid=${p#/proc/}; pkill -9 -P $pid 2>/dev/null; kill -9 $pid 2>/dev/null && n=$((n+1)); done; '
            'echo "killed $n"' % stage)


def attempt_cpu_cmd(stage):
    """Shell for the tablet: CPU ticks (utime+stime) used so far by this attempt's payload and its children - the
    worker's own beat. Found exactly as kill_attempt_cmd finds them (cwd match), so the probe and the stop agree on
    what the attempt is. Prints `cpu <ticks> procs <n>`."""
    return ('S="$HOME/%s"; m=" "; for p in /proc/[0-9]*; do [ "$(readlink $p/cwd 2>/dev/null)" = "$S" ] && '
            'm="$m${p#/proc/} "; done; t=0; n=0; for p in /proc/[0-9]*; do l=$(cat $p/stat 2>/dev/null); '
            'set -- ${l##*)}; [ $# -ge 13 ] || continue; case "$m" in *" ${p#/proc/} "*|*" $2 "*) '
            't=$((t+${12}+${13})); n=$((n+1));; esac; done; echo "cpu $t procs $n"' % stage)


def _quarantine_losers(host_threads):
    """Remember still-running attempts so their hosts cannot receive new work."""
    alive = [(host, thread) for host, thread in host_threads if thread.is_alive()]
    if not alive:
        return []
    with _ORPHAN_LOCK:
        for host, thread in alive:
            _ORPHAN_THREADS[host] = thread
    return [host for host, _thread in alive]


def staging_failure(rows):
    """The staging error when EVERY row of a batch failed to stage (the node cannot hold the order's model - a node
    property), else None. One staged row means the node can do the work and its rows stand."""
    errs = [str(r.get("error") or "") for r in rows or []]
    if errs and all(e.startswith("staging_failed:") for e in errs):
        return errs[0][:120]
    return None


def _safe_stage_name(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:80]


def clear_stage_dirs(state_dir, order_name):
    """Remove only THIS order's local staging dirs (stage_<its name>-...). Two lanes run orders at once, so an order
    finishing must never clear another order's staging mid-run. A window's arms are named "<window>.arm", so the
    window's name covers them. Never raises. Returns the number removed."""
    s = _safe_stage_name(order_name)
    # "<name>-<hash>..." for the order's own batches, "<name>.arm-..." for a window's arm; the separator keeps win.1
    # from matching win.10
    prefixes = ("stage_%s-" % s, "stage_%s." % s)
    n = 0
    try:
        names = os.listdir(state_dir)
    except OSError:
        return 0
    for d in names:
        if d.startswith(prefixes):
            shutil.rmtree(os.path.join(state_dir, d), ignore_errors=True)
            n += 1
    return n


def _stage_local(spec, names, workdir):
    """Pull this batch's items to the driver, then push to the node (a 3-way scp is not a thing).

    `src_host` of LOCAL/empty means the inputs are already visible to the foundry -- normally via a
    mounted share. That is the PREFERRED shape: the container reaches the tablets with the cluster
    key, and shared storage covers everything else, so no second credential has to exist just to
    move files around."""
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)
    ctl = spec.get("control") or {}
    host = (spec.get("src_host") or "").strip()
    local = host in ("", "LOCAL", "local")
    for nm in names:
        src = ctl["src"] if nm == ctl.get("file") else "%s/%s" % (spec["src_dir"], nm)
        dst = os.path.join(workdir, nm)
        if local:
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                return False, "copy %s: %s" % (nm, type(e).__name__)
        else:
            rc, out = _scp_from(host, src, dst)
            if rc != 0:
                return False, "fetch %s: %s" % (nm, out[:120])
    return True, ""



def _scp_from(host, remote, local, timeout=300):
    """scp FROM a tablet. IP + key, never an alias -- aliases live in Softserve's ~/.ssh/config."""
    from tablet_foundry import _ssh_base, sh_argv
    opts, ip = _ssh_base(host)
    opts = ["-P" if o == "-p" else o for o in opts]        # scp uses uppercase -P
    return sh_argv(["scp"] + opts + ["%s:%s" % (ip, remote), local], timeout=timeout)


def _scp_many(host, localdir, remotedir, timeout=1200):
    """Push every file in localdir to the node. argv list, so paths with spaces survive."""
    import glob as _glob
    from tablet_foundry import _ssh_base, sh_argv
    opts, ip = _ssh_base(host)
    opts = ["-P" if o == "-p" else o for o in opts]
    files = sorted(_glob.glob(os.path.join(localdir, "*")))
    if not files:
        return 1, "nothing staged locally"
    return sh_argv(["scp"] + opts + files + ["%s:%s/" % (ip, remotedir)], timeout=timeout)


def order_kind(spec):
    """ONE derivation of an order's kind — producer and every consumer must agree (the seam rule)."""
    return (spec.get("kind") or "sweep").strip().lower()


def row_key(spec):
    """The field that identifies a row's item: sweeps key rows by 'file', maps by 'item'.
    ONE derivation, shared by fulfilment and both validators, so they cannot drift."""
    return "item" if order_kind(spec) == "map" else "file"


def order_items(spec):
    """The order's experiment items (the control not included) - ONE derivation for run_order and useful_nodes."""
    if order_kind(spec) == "map" and spec.get("items") is not None:
        return [str(x).strip() for x in spec["items"] if str(x).strip()]
    with open(spec["items_list"], encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]


def useful_nodes(spec):
    """How many tablets an elastic order can keep busy: one per batch it deals (its control rides as an item), or None
    when its items cannot be read here - then it takes every free tablet, as before (the run then reports the read
    error itself). The node pool caps an order at this, so a one-item order holds one tablet, not the fleet."""
    try:
        items = order_items(spec)
    except Exception:                                                # noqa: BLE001
        return None
    ctl = spec.get("control") or {}
    n = len(items) + (1 if (ctl.get("item") if order_kind(spec) == "map" else ctl.get("file")) else 0)
    batch_n = int(spec.get("batch") or BATCH)
    return max(1, -(-n // batch_n))


def run_order(spec, hosts, on_rows=None, host_sku=None, stop_event=None, on_work=None):
    """Fan one order across `hosts`. Returns (rows, errors, wall_s, n_items, unclaimed).

    `stop_event`: When set, no new batches are dispatched, in-flight work is terminated, and
    all undealt work is returned in `unclaimed` to ensure stopped work is reported, never lost.

    `on_work(ticks)`: Reports summed CPU ticks of in-flight attempts if no rows have landed for
    WORK_PROBE_S, allowing the watchdog to distinguish grinding from dead work.

    `host_sku` (opt-in): Map of `{host: sku}` provided by the runner to enforce SKU affinity.
    Items are assigned to lanes by their first '~'-field and pulled only by hosts matching that
    SKU. If None/empty, a single shared lane is used (original behavior). Items whose lane has no
    healthy host are left UNCLAIMED, never silently dropped."""
    name = spec["name"]
    hosts, quarantined = _available_hosts(hosts)
    if quarantined:
        log("%s: withholding speculative-cleanup hosts: %s"
            % (name, ", ".join(sorted(quarantined))))
    kind = order_kind(spec)
    batch_n = int(spec.get("batch") or BATCH)
    items = order_items(spec)
    ctl = spec.get("control") or {}
    ctl_item = ctl.get("item") if kind == "map" else ctl.get("file")
    experiment_items = list(items)
    if ctl_item:
        items = items + [ctl_item]              # the control rides along, placed like any item

    distinct_n = spec.get("distinct_workers")
    distinct_n = int(distinct_n) if distinct_n is not None else None
    initial_by_host = {}
    queued_items = list(items)
    if distinct_n is not None:
        # The runner already selected exactly N eligible hosts.  Pin one experimental item to each
        # host before opening the shared queue; only the control and recovery attempts may float.
        initial_by_host = dict(zip(hosts, experiment_items))
        queued_items = [ctl_item] if ctl_item else []

    host_sku = host_sku or {}

    def _hlane(h):
        return host_sku.get(h, "") if host_sku else ""

    def _ilane(cell):
        return cell.split("~", 1)[0] if host_sku else ""

    lane_items = {}
    for it in queued_items:
        lane_items.setdefault(_ilane(it), []).append(it)
    lane_q = {}
    for lane, its in lane_items.items():
        q = Queue()
        for i in range(0, len(its), batch_n):
            q.put(its[i:i + batch_n])
        lane_q[lane] = q
    total_batches = len(initial_by_host) + sum(q.qsize() for q in lane_q.values())
    if host_sku:
        log("%s: %d items -> %d batches of <=%d across %d nodes (SKU lanes: %s)"
            % (name, len(items), total_batches, batch_n, len(hosts),
               ", ".join("%s=%d" % (k, len(v)) for k, v in sorted(lane_items.items()))))
    else:
        log("%s: %d items -> %d batches of <=%d across %d nodes"
            % (name, len(items), total_batches, batch_n, len(hosts)))

    rows, errors, lk = [], {}, threading.Lock()
    running = {}                 # (host, remote_stage) -> True while that attempt's payload runs on the tablet
    inflight, durations, finished = {}, [], set()
    logical_done = threading.Event()
    ckpt = os.path.join(STATE_DIR, "%s.checkpoint.jsonl" % name)
    t_all = time.time()

    def bkey(ns):
        # A HASH, never a join of the item strings themselves: map items are opaque and may
        # contain the join character, and the steal path used to reconstruct a batch by
        # SPLITTING this key -- an item with a '|' in it would have been shredded into wrong
        # items on a steal. Keys only need equality; the real items live in inflight["names"].
        import hashlib
        return hashlib.sha1("\x00".join(ns).encode("utf-8")).hexdigest()

    # Register every pinned first attempt before starting workers.  Fast workers therefore wait
    # for slow selected peers instead of exiting before those peers have entered the inflight map.
    for host, item in initial_by_host.items():
        inflight[bkey([item])] = {"host": host, "start": t_all, "spec": False,
                                  "names": [item], "lane": _hlane(host)}
    # A pinned sample re-run on another host fails validate_distinct_workers, so stealing one from a
    # live straggler can only VOID the window.  A pinned host that FAILS still requeues its item.
    pinned = {bkey([item]) for item in initial_by_host.values()}

    def steal(host):
        """An idle node reclaims a batch a straggler is sitting on. Never kills the straggler --
        it may still finish first, and killing a node you cannot reach invents a new failure.
        Scoped to the host's own SKU lane: a straggler in another lane is not this host's to take."""
        if not durations:
            return None
        med = sorted(durations)[len(durations) // 2]
        cutoff = max(SPECULATIVE_FACTOR * med, SPECULATIVE_MIN_S)
        lane = _hlane(host)
        with lk:
            for k, rec in inflight.items():
                if k in finished or k in pinned or rec["spec"] or rec["host"] == host \
                        or rec.get("lane") != lane:
                    continue
                if time.time() - rec["start"] > cutoff:
                    rec["spec"] = True
                    log("%s SPECULATIVE: taking %d items held by %s for %.0fs (median %.0fs)"
                        % (host, len(rec["names"]), rec["host"], time.time() - rec["start"], med))
                    return list(rec["names"])
        return None

    def do_batch(host, names):
        k = bkey(names)
        t0 = time.time()
        safe_name = _safe_stage_name(name)
        safe_host = "".join(c if c.isalnum() or c in "._-" else "_" for c in host)[:40]
        attempt = "%s-%s-%s-%s" % (safe_name, bkey([name])[:8], k[:12], safe_host)
        remote_stage = "%s/%s" % (REMOTE_STAGE, attempt)
        wd = os.path.join(STATE_DIR, "stage_%s" % attempt)

        def failed(detail):
            """A loser failing after another attempt won is not an error or a requeue."""
            with lk:
                if k in finished:
                    log("%s lost the speculative race after failure, ignoring: %s"
                        % (host, detail[:120]))
                    won_elsewhere = True
                else:
                    errors[host] = detail
                    won_elsewhere = False
            if won_elsewhere:
                _ssh(host, "rm -rf %s" % remote_stage, timeout=60)
                shutil.rmtree(wd, ignore_errors=True)
                return True
            return False

        with lk:
            if k in finished:
                return True
            inflight.setdefault(k, {"host": host, "start": t0, "spec": False,
                                    "names": list(names), "lane": _hlane(host)})
        # Remove only this uniquely named attempt.  A remote process can outlive a broken SSH
        # connection, so clearing the shared root could damage its cleanup or a later attempt.
        rc, out = _ssh(host, "rm -rf %s; mkdir -p %s" % (remote_stage, remote_stage),
                       timeout=90)
        if rc != 0:
            return failed("stage setup: %s" % out[:120])
        if kind == "map":
            # MAP: nothing to stage — the items ARE the work. Write this batch's strings as the
            # items file; the payload contract is unchanged (`python payload items.txt out.json`),
            # the lines are opaque strings instead of staged filenames.
            os.makedirs(wd, exist_ok=True)
            with open(os.path.join(wd, "items.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(names) + "\n")
            rc, out = _scp(host, os.path.join(wd, "items.txt"),
                           "%s/items.txt" % remote_stage, timeout=90)
            if rc != 0:
                return failed("push items: %s" % out[:120])
        else:
            ok, why = _stage_local(spec, names, wd)
            if not ok:
                return failed(why)
            rc, out = _scp_many(host, wd, remote_stage)
            if rc != 0:
                return failed("push: %s" % out[:120])
        rc, out = _scp(host, spec["payload"],
                       "%s/%s" % (remote_stage, os.path.basename(spec["payload"])), timeout=180)
        if rc != 0:
            return failed("push payload: %s" % out[:120])
        for sf in spec.get("support_files") or []:
            rc, out = _scp(host, sf, "%s/%s" % (remote_stage, os.path.basename(sf)), timeout=180)
            if rc != 0:
                return failed("push support file %s: %s" % (os.path.basename(sf), out[:120]))
        pay = os.path.basename(spec["payload"])
        if kind == "map":
            cmd = "cd %s && %spython %s items.txt out.json" % (remote_stage, _stage_env(), pay)
        else:
            cmd = ("cd %s && ls -1 | grep -vE '%s|listing.txt' > listing.txt && %spython %s listing.txt out.json"
                   % (remote_stage, pay, _stage_env(), pay))
        # Do not set a default timeout; a flat limit (e.g., 3600s) voids valid long-running generations. SSH keepalives and the payload handle actual stalls.
        _bt = spec.get("batch_timeout")
        with lk:
            running[(host, remote_stage)] = True
        try:
            rc, out = _ssh(host, cmd, timeout=int(_bt) if _bt else None)
        finally:
            with lk:
                running.pop((host, remote_stage), None)
        if rc != 0:
            return failed("run: %s" % out[:160])
        rc, out = _scp_from(host, "%s/out.json" % remote_stage,
                            os.path.join(wd, "out.json"), timeout=180)
        if rc != 0:
            return failed("fetch result: %s" % out[:120])
        try:
            with open(os.path.join(wd, "out.json"), encoding="utf-8") as f:
                got = json.load(f)
        except Exception as e:
            return failed("unreadable result: %s" % type(e).__name__)
        for r in got:
            r["_host"] = host
        unstaged = staging_failure(got)
        if unstaged:
            # A staging failure is a node incompatibility (not an item error). Retire the node and requeue the items.
            shutil.rmtree(wd, ignore_errors=True)
            _ssh(host, "rm -rf %s" % remote_stage, timeout=60)
            return failed("node cannot stage the model: %s" % unstaged)
        with lk:
            if k in finished:
                log("%s lost the speculative race on %d items, discarding" % (host, len(got)))
            else:
                finished.add(k)
                durations.append(time.time() - t0)
                rows.extend(got)
                try:
                    with open(ckpt, "a", encoding="utf-8") as cf:
                        for r in got:
                            cf.write(json.dumps(r) + "\n")
                        cf.flush()
                except Exception as e:
                    log("checkpoint write failed (%s) - rows still in memory" % type(e).__name__)
                done = len(rows)
                rate = done / max(1e-9, time.time() - t_all)
                log("%s finished %d (%.1fs/item) | %d/%d, ~%.0f min left"
                    % (host, len(got), (time.time() - t0) / max(1, len(got)), done, len(items),
                       (len(items) - done) / rate / 60.0 if rate else 0))
                if on_rows:
                    on_rows(done, len(items))
        _ssh(host, "rm -rf %s" % remote_stage, timeout=60)
        return True

    def stopped():
        return stop_event is not None and stop_event.is_set()

    def worker(host):
        lane = _hlane(host)
        q = lane_q.get(lane)
        initial = initial_by_host.get(host)
        if q is None and initial is None:  # no items for this host's lane -- nothing to pull
            return
        did = 0
        if initial is not None and stopped():
            with lk:
                if q is None:
                    q = lane_q.setdefault(lane, Queue())
            q.put([initial])
            return
        if initial is not None:
            try:
                ok = do_batch(host, [initial])
            except Exception as e:                  # a thread must never die holding work
                with lk:
                    errors[host] = "unhandled %s: %s" % (type(e).__name__, str(e)[:160])
                ok = False
            if not ok:
                with lk:
                    won_elsewhere = bkey([initial]) in finished
                if not won_elsewhere:
                    if q is None:
                        q = lane_q.setdefault(lane, Queue())
                    q.put([initial])
                    log("%s requeued 1 pinned item after failure; retiring this node" % host)
                return
            did += 1
        if q is None:
            return
        while True:
            if stopped():
                return
            try:
                names = q.get_nowait()
            except Empty:
                names = steal(host)
                while names is None:
                    with lk:
                        outstanding = [k for k in inflight
                                       if k not in finished and inflight[k].get("lane") == lane]
                    if not outstanding and q.empty():
                        return
                    if logical_done.wait(5) or stopped():
                        return
                    try:
                        names = q.get_nowait()
                    except Empty:
                        names = steal(host)
            try:
                ok = do_batch(host, names)
            except Exception as e:                  # a thread must never die holding work
                with lk:
                    errors[host] = "unhandled %s: %s" % (type(e).__name__, str(e)[:160])
                ok = False
            if not ok:
                with lk:
                    won_elsewhere = bkey(names) in finished
                if not won_elsewhere:
                    q.put(names)
                    log("%s requeued %d items after failure; retiring this node"
                        % (host, len(names)))
                return
            did += len(names)

    host_threads = [(h, threading.Thread(target=worker, args=(h,), daemon=True,
                                         name="tablet-order-%s" % h)) for h in hosts]
    for _host, t in host_threads:
        t.start()

    # Completion belongs to batches, not transports.  Once every batch has a winning result, a
    # losing SSH attempt may finish cleanup in the background instead of pinning the whole order.
    # If completion is impossible, all workers still retire and coverage reports the remainder.
    reaped = set()
    rejoin, rejoin_at = list(quarantined), 0.0
    work_seen, beat = {}, {"rows": 0, "row_t": time.time(), "probe_t": 0.0, "thread": None}

    def probe_work(attempts):
        for host, stage in attempts:
            try:
                rc, out = _ssh(host, attempt_cpu_cmd(stage), timeout=30)
            except Exception:
                continue
            words = (out or "").split()
            if rc == 0 and len(words) >= 2 and words[0] == "cpu" and words[1].isdigit():
                with lk:
                    work_seen[(host, stage)] = int(words[1])
        with lk:
            seen = [work_seen[a] for a in attempts if a in work_seen]
        if seen:
            try:
                on_work(sum(seen))
            except Exception as e:
                log("%s: work beat failed (%s)" % (name, type(e).__name__))

    while True:
        if rejoin and not stopped() and time.time() >= rejoin_at:
            # a host withheld for an earlier order's loser joins this order once that loser has exited
            rejoin_at = time.time() + REJOIN_CHECK_S
            free, _held = _available_hosts(rejoin)
            for h in free:
                rejoin.remove(h)
                t = threading.Thread(target=worker, args=(h,), daemon=True, name="tablet-order-%s" % h)
                host_threads.append((h, t))
                t.start()
                log("%s: %s joins - its speculative cleanup from an earlier order has finished" % (name, h))
        with lk:
            logically_complete = len(finished) >= total_batches
            in_flight = [hs for hs in running if hs not in reaped]
            n_rows = len(rows)
        t_now = time.time()
        if n_rows != beat["rows"]:
            beat["rows"], beat["row_t"] = n_rows, t_now
        if (on_work is not None and in_flight and not stopped() and not logically_complete
                and t_now - beat["row_t"] >= WORK_PROBE_S and t_now - beat["probe_t"] >= WORK_PROBE_S
                and not (beat["thread"] and beat["thread"].is_alive())):
            beat["probe_t"] = t_now
            beat["thread"] = threading.Thread(target=probe_work, args=(list(in_flight),), daemon=True,
                                              name="tablet-work-probe")
            beat["thread"].start()
        if stopped() and in_flight:
            # a stop must terminate in-flight work, not just the pending queue (the batch clock removal makes this the sole cleanup path)
            for host, stage in in_flight:
                reaped.add((host, stage))
                rc, out = _ssh(host, kill_attempt_cmd(stage), timeout=60)
                log("%s STOP: ended the batch in flight in %s (%s)" % (host, stage, (out or "rc %s" % rc)[:80]))
        if logically_complete or not any(t.is_alive() for _host, t in host_threads):
            break
        for _host, t in host_threads:
            t.join(timeout=0.05)

    if logically_complete:
        logical_done.set()       # wake idle workers; only blocked transports remain alive
        with lk:
            losing = [hs for hs in running if hs not in reaped]
        for host, stage in losing:          # every batch already has a winner, so a loser's answer is never read
            reaped.add((host, stage))
            rc, out = _ssh(host, kill_attempt_cmd(stage), timeout=60)
            log("%s: ended the losing attempt in %s - its batch was won elsewhere (%s)"
                % (host, stage, (out or "rc %s" % rc)[:80]))
        deadline = time.time() + SPECULATIVE_CLEANUP_GRACE_S
        for _host, t in host_threads:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            t.join(timeout=remaining)
        losers = _quarantine_losers(host_threads)
        if losers:
            log("%s: logical work complete; continuing while cleanup finishes on %s"
                % (name, ", ".join(sorted(losers))))

    # Completeness is reported, not assumed: the return value must indicate how many items were
    # measured vs expected. If all nodes retire on error -- or a lane has no healthy host of its
    # SKU -- remaining work is silently dropped from the queue; without this metric a partial
    # sweep looks clean.
    unclaimed = []
    for q in lane_q.values():
        try:
            while True:
                unclaimed.extend(q.get_nowait())
        except Empty:
            pass
    if stopped():
        log("%s: STOPPED by a customer slip -- %d row(s) finished, %d item(s) never dealt"
            % (name, len(rows), len(unclaimed)))
    elif unclaimed:
        log("*** %d items NEVER MEASURED -- every node retired before they ran ***"
            % len(unclaimed))
    return rows, errors, time.time() - t_all, len(items), unclaimed


def validate_control(spec, rows):
    """(passed, detail). A failing control makes the run VOID -- never a clean result."""
    ctl = spec.get("control") or {}
    key = row_key(spec)
    want = ctl.get("item") if order_kind(spec) == "map" else ctl.get("file")
    if not want:
        return False, "no control declared"
    hit = next((r for r in rows if r.get(key) == want), None)
    if hit is None:
        return False, "control item was never measured"
    txt = json.dumps(hit).lower()
    return (str(ctl["expect"]).lower() in txt), "control read: %s" % (
        hit.get("best") or hit.get("result") or hit.get("error"))


def validate_complete(rows, expected, unclaimed, key="file", stopped=False):
    """(complete, detail). Two DIFFERENT questions, deliberately not merged with the control.

    The control asks "does the detector work"; this asks "did every item go through it". Both can
    fail independently and a run needs both to be a clean result -- a working detector that only
    saw half the corpus is the more dangerous of the two, because it looks perfect.

    `key` comes from row_key(spec): sweeps count distinct 'file' rows, maps distinct 'item' rows.
    A map payload that forgets to echo the input line into 'item' therefore FAILS coverage rather
    than silently passing -- the contract is enforced, not assumed."""
    measured = len({r.get(key) for r in rows if r.get(key)})
    if unclaimed:
        return False, ("%d/%d measured; %d items never ran (%s): %s"
                       % (measured, expected, len(unclaimed),
                          "order STOPPED by a customer slip" if stopped else "nodes retired",
                          ", ".join(unclaimed[:5]) + ("..." if len(unclaimed) > 5 else "")))
    if measured < expected:
        return False, "%d/%d measured; %d unaccounted for" % (measured, expected,
                                                              expected - measured)
    return True, "%d/%d measured" % (measured, expected)


def validate_distinct_workers(spec, rows, selected_hosts):
    """Validate the scientific cohort separately from ordinary coverage and control."""
    requested = spec.get("distinct_workers")
    if requested is None:
        return True, "distinct-worker cohort not requested", []
    requested = int(requested)
    key = row_key(spec)
    control = spec.get("control") or {}
    control_item = control.get("item") if order_kind(spec) == "map" else control.get("file")
    experiment_rows = [row for row in rows if row.get(key) != control_item]
    counts = Counter(row.get(key) for row in experiment_rows)
    expected_items = [str(item).strip() for item in spec.get("items", [])]
    actual_hosts = sorted({row.get("_host") for row in experiment_rows if row.get("_host")})

    if len(experiment_rows) != requested:
        return False, ("distinct-worker FAIL: %d experiment rows for requested N=%d"
                       % (len(experiment_rows), requested)), actual_hosts
    if set(counts) != set(expected_items) or any(count != 1 for count in counts.values()):
        return False, "distinct-worker FAIL: experiment identities are missing or duplicated", actual_hosts
    if len(actual_hosts) != requested:
        return False, ("distinct-worker FAIL: requested N=%d but observed %d hosts (%s)"
                       % (requested, len(actual_hosts), ", ".join(actual_hosts))), actual_hosts
    if set(actual_hosts) != set(selected_hosts):
        return False, ("distinct-worker FAIL: selected hosts [%s], observed hosts [%s]"
                       % (", ".join(sorted(selected_hosts)), ", ".join(actual_hosts))), actual_hosts
    return True, ("distinct-worker PASS: %d/%d selected hosts observed"
                  % (len(actual_hosts), requested)), actual_hosts
