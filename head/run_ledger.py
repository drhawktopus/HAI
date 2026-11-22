"""Checkpoint and resume for runs to avoid blocking orchestrator deploys.

Layout: State/hai-runs/<run>/run.json (restart config) and per-chat-call files containing pre-turn state and post-turn answers.

Replay logic: Completed calls return saved answers; the in-flight call resumes from its last saved state; later calls execute live. The in-flight model turn is re-executed.

Invariants:
- NEVER re-execute tools: Side effects (writes, submissions) persist in the workspace, so replaying them would apply them twice. Saved conversation results are the truth.
- Keys are positional: "0", "1"... for top-level; "K.tT.N" for children. On resume, children of the in-flight turn are dropped as the turn may request different work."""
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import threading

import config

ROOT = pathlib.Path(config.STATE_DIR) / "hai-runs"


def _safe(run_id):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(run_id or ""))[:80]


def _write_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


class Slot:
    """One chat() call's checkpoint."""

    def __init__(self, ledger, key, sig=None):
        self.ledger, self.key, self.sig = ledger, key, sig
        self.path = ledger.dir / "calls" / ("%s.json" % key)
        self.turn = 0
        self._children = 0
        self.tool_names = None   # the tool list this call's conversation is sent; goes with every checkpoint
        self.served = None       # {"host", "model", "num_ctx"} this call runs on; goes with every checkpoint
        self.stopped = None      # why a GUARD stopped this call (the tool loop sets it); a stopped call stays resumable

    def saved_tool_names(self):
        """The tool list the last checkpoint was sent with, or None.
        Read before the call's first turn: the list renders before the conversation, so a resume re-reads it."""
        rec = _read(self.path)
        return rec.get("tool_names") if rec and not rec.get("done") else None

    def recorded(self):
        """The call's (answer, escalation) when it already finished, else None."""
        rec = _read(self.path)
        if rec and rec.get("done"):
            out = rec.get("out") or [None, None]
            esc = out[1]
            return out[0], (tuple(esc) if isinstance(esc, list) else esc)
        return None

    def resume_point(self):
        """{messages, step, tools_done, in_tool} saved at the last natural point, or None.

        tools_done None: saved before model turn `step` - that turn is re-asked, and the children recorded under it
        are dropped (it may request different work). tools_done = i: saved inside turn `step` before its tool i -
        the model's answer and tools 0..i-1's results are in `messages`, so the resume runs the remaining tools
        without asking the model again; in_tool = tool i had STARTED and its result was lost (never re-run - its
        effect on the lab is unknown)."""
        rec = _read(self.path)
        if not rec or rec.get("done") or not rec.get("messages"):
            return None
        step = int(rec.get("step") or 0)
        tools_done = rec.get("tools_done")
        mark = _read(self._tool_mark_path())
        in_tool = bool(tools_done is not None and mark and mark.get("step") == step and mark.get("i") == tools_done)
        prefix = "%s.t%s." % (self.key, step)
        calls = self.ledger.dir / "calls"
        for f in calls.glob(prefix + "*.json") if calls.exists() else []:
            try:
                f.unlink()
            except OSError:
                pass
        return {"messages": rec["messages"], "step": step, "tools_done": tools_done, "in_tool": in_tool,
                "compact": bool(rec.get("compact")), "cut": rec.get("cut"), "loop": rec.get("loop"),
                "pieces": rec.get("pieces") or {}, "handle": (mark or {}).get("handle") if in_tool else None}

    def _tool_mark_path(self):
        return self.path.with_name("%s.tool" % self.key)

    def save(self, messages, step, tools_done=None, compact=False, cut=None, loop=None, pieces=None):
        """Checkpoint at a natural point: before model turn `step` (`tools_done` None), or mid-turn before tool
        `tools_done`. `compact` saves just before a compaction with the cut turn (`cut`) so a restart
        redoes it."""
        if tools_done is None:
            self.turn, self._children = step, 0
        else:
            self.turn = step   # mid-turn: children already made in this turn keep their keys
        rec = {"key": self.key, "step": step, "messages": messages, "done": False}
        if self.sig:
            rec["sig"] = self.sig   # which call this is, so a resume can tell it apart from another call
        if self.tool_names is not None:
            rec["tool_names"] = self.tool_names
        if self.served is not None:
            rec["served"] = self.served
        if tools_done is not None:
            rec["tools_done"] = tools_done
        if loop is not None:
            rec["loop"] = loop   # the tool loop's own state (finish gate, counters), restored with the conversation
        if compact:
            rec["compact"] = True
            if cut:
                rec["cut"] = cut
            if pieces:
                rec["pieces"] = pieces   # the summaries of the pieces already done, by the piece's hash
        try:
            _write_atomic(self.path, rec)
            self._tool_mark_path().unlink(missing_ok=True)
        except Exception:
            pass   # a checkpoint that cannot be written costs resumability, never the run
        hook = self.ledger.on_checkpoint
        if hook is not None:
            try:
                hook()   # the lane's own state that must line up with this checkpoint (the live feed)
            except Exception:
                pass

    def tool_handle(self, step, i, handle):
        """What a tool still running is waiting on (an order), so a run back after a restart waits for the same work."""
        try:
            _write_atomic(self._tool_mark_path(), {"step": step, "i": i, "handle": handle})
        except Exception:
            pass

    def tool_started(self, step, i):
        """A small mark that tool `i` of turn `step` has started; the next save clears it. Present at a resume = the
        tool was cut off and its result never reached the conversation."""
        try:
            _write_atomic(self._tool_mark_path(), {"step": step, "i": i})
        except Exception:
            pass

    def done(self, out):
        """Record the call's answer.

        Persists the conversation context from the last save alongside the result so the run can be inspected at any point.
        If a guard stopped the call, preserves the full resume state (step, loop, tools) to allow `restore_save` continuation."""
        prev = _read(self.path) or {}
        rec = {"key": self.key, "done": True, "out": list(out)}
        if self.sig:
            rec["sig"] = self.sig
        if self.stopped and prev.get("messages") and not prev.get("done"):
            rec = dict(prev, done=True, out=list(out), stopped=self.stopped)
        if prev.get("messages"):
            rec["messages"] = prev["messages"]
        try:
            _write_atomic(self.path, rec)
            self._tool_mark_path().unlink(missing_ok=True)
        except Exception:
            pass

    def child_key(self):
        key = "%s.t%s.%d" % (self.key, self.turn, self._children)
        self._children += 1
        return key


class RunLedger:
    def __init__(self, run_id, root=None):
        self.run_id = str(run_id)
        self.dir = pathlib.Path(root or ROOT) / _safe(run_id)
        self._top = 0
        self._handed = set()      # top-level keys this process has already given out, so a resume never
                                  # hands the same record to two live calls
        self._lock = threading.Lock()
        self.on_checkpoint = None   # called after every turn checkpoint (the chat lane snapshots its live feed)

    @classmethod
    def create(cls, run_id, lane, request, root=None):
        """Record a run that can be restarted: its lane and the request that starts it again."""
        led = cls(run_id, root)
        _write_atomic(led.dir / "run.json", {"run_id": led.run_id, "lane": lane, "request": request,
                                             "accepted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                             "resumes": 0})
        return led

    def info(self):
        return _read(self.dir / "run.json") or {}

    def note_working(self, seconds):
        """How long this run has worked, at each checkpoint, so a restart does not hand it a fresh budget. Never raises."""
        try:
            _write_atomic(self.dir / "working.json", {"seconds": round(float(seconds))})
        except Exception:
            pass

    def working_seconds(self, budget_s):
        """Seconds of its budget this run has used. A run from before note_working counts from when it was accepted, unless that is
        longer than its budget: it was held, not working, and keeps a whole budget, as it always did."""
        rec = _read(self.dir / "working.json")
        if isinstance(rec, dict) and isinstance(rec.get("seconds"), (int, float)):
            return rec["seconds"]
        try:
            at = datetime.datetime.fromisoformat(str(self.info().get("accepted_at")))
            since = (datetime.datetime.now(datetime.timezone.utc) - at).total_seconds()
        except (TypeError, ValueError):
            return 0
        return since if 0 <= since <= budget_s else 0

    def served(self):
        """{"host", "model", "num_ctx"}: where the run's unfinished top call was last checkpointed, or None (nothing
        saved yet, every call finished, or saved before this was recorded). A run resumed with no window of its own
        goes back there (app._resumed_home): its box and model were decided when it started."""
        calls = self.dir / "calls"
        try:
            tops = sorted((f for f in calls.glob("*.json") if f.stem.isdigit()), key=lambda f: int(f.stem))
        except OSError:
            return None
        for f in tops:
            rec = _read(f)
            if rec and not rec.get("done"):
                s = rec.get("served")
                return s if isinstance(s, dict) and s.get("host") and s.get("model") else None
        return None

    def progress_mark(self):
        """A fingerprint of every checkpoint written so far: it changes whenever the run saves a turn or
        finishes a call. Equal marks at two resumes = the run saved nothing in between (a crash loop)."""
        calls = self.dir / "calls"
        try:
            rows = sorted((f.name, f.stat().st_size, f.stat().st_mtime_ns) for f in calls.glob("*.json"))
        except OSError:
            rows = []
        # hashlib, not hash(): str hashes are salted per process, and this mark is compared ACROSS restarts
        return "%d:%s" % (len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()[:16])

    def mark_resumed(self, mark):
        info = self.info()
        info["resumes"] = int(info.get("resumes") or 0) + 1
        info["resumed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        info["mark_at_resume"] = mark
        _write_atomic(self.dir / "run.json", info)
        return info["resumes"]

    def open_call(self, parent=None, sig=None):
        if parent is not None:
            return Slot(self, parent.child_key())
        with self._lock:
            key = self._top_key_for(sig)
            self._handed.add(key)
        return Slot(self, key, sig)

    def _top_key_for(self, sig):
        """Which saved record this live top-level call is. With a signature, the first record not yet handed out
        in this process whose sig matches, or which has no sig (written before signatures existed, so it keeps
        today's positional behaviour). A record with a DIFFERENT sig is skipped: handing a call another call's
        answer is the defect this fixes. With no sig, the positional counter, unchanged."""
        if sig is None:
            key = str(self._top)
            self._top += 1
            return key
        calls = self.dir / "calls"
        highest = -1
        try:
            files = [f for f in calls.glob("*.json") if f.stem.isdigit()]
        except OSError:
            files = []
        for f in files:
            highest = max(highest, int(f.stem))
        for f in sorted(files, key=lambda f: int(f.stem)):
            if f.stem in self._handed:
                continue
            rec = _read(f)
            if rec is None or rec.get("sig") is None or rec.get("sig") == sig:
                return f.stem
        key = str(max(highest, self._top - 1) + 1)
        self._top = int(key) + 1
        return key

    def mark_suspended(self, on=True):
        """A suspended run is NOT resumed at startup; POST /runs/resume clears this and starts it again."""
        info = self.info()
        if not info:
            return False
        info["suspended"] = bool(on)
        if on:
            info["suspended_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _write_atomic(self.dir / "run.json", info)
        return True

    def pin(self):
        """SAVE ON REQUEST: this run's ledger is kept when it ends, never rotated away. True when the run exists."""
        info = self.info()
        if not info:
            return False
        info["pinned"] = True
        _write_atomic(self.dir / "run.json", info)
        return True

    def discard(self):
        """The run ended and its lane keeps its own record (an overnight drive: the run-state and activity log), so
        no save is made - the rolling saves stay the runs people read."""
        shutil.rmtree(self.dir, ignore_errors=True)

    def finish(self):
        """Pinned runs are saved indefinitely; other runs are kept only if config.RUN_SAVES_KEEP > 0."""
        info = self.info()
        keep = int(getattr(config, "RUN_SAVES_KEEP", 5))
        pinned = bool(info.get("pinned"))
        if not info or (keep == 0 and not pinned):
            shutil.rmtree(self.dir, ignore_errors=True)
            return
        dest_root = saves_root(self.dir.parent, pinned)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        try:
            info["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            _write_atomic(self.dir / "run.json", info)
            for f in (self.dir / "calls").glob("*.tool") if (self.dir / "calls").exists() else []:
                f.unlink(missing_ok=True)
            dest_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.dir), str(dest_root / ("%s.%s" % (self.dir.name, stamp))))
        except Exception:
            shutil.rmtree(self.dir, ignore_errors=True)   # a save that cannot be made must not leave a "running" run
            return
        if not pinned and keep > 0:
            # Resumable runs are exempt from the count-based rolling window; they are purged only by age (RESUMABLE_KEEP_S).
            # This prevents short-lived runs from rotating out the only checkpoint of a long-running job before it can be resumed.
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            olds = []
            for d in sorted((d for d in dest_root.iterdir() if d.is_dir()), key=lambda d: d.name.rsplit(".", 1)[-1]):
                if resumable(d):
                    try:
                        if now - d.stat().st_mtime > RESUMABLE_KEEP_S:
                            _archive_save(d, dest_root)
                    except OSError:
                        pass
                else:
                    olds.append(d)
            for d in olds[:-keep]:
                _archive_save(d, dest_root)


RESUMABLE_KEEP_S = 7 * 86400     # an unfinished run's save is kept a week. A chosen value, not a measured one: such
                                 # saves are rare and ~8 MB each, and a week outlasts a weekend away


def _archive_save(d, dest_root):
    """Saves leaving the rolling window are MOVED to the archive, never deleted.\n    restore_save looks in the archive for them."""
    arch = archive_root(dest_root)
    try:
        arch.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d), str(arch / d.name))
    except Exception:
        pass       # a save that cannot be archived stays where it is; it is never removed instead


def archive_root(saves_dir):
    """State/hai-runs-saved -> State/hai-runs-archive (one archive for the rolling saves)."""
    saves_dir = pathlib.Path(saves_dir)
    return saves_dir.with_name(saves_dir.name.replace("-saved", "") + "-archive")


def resumable(save_dir):
    """True when a saved run is resumable: it ended unfinished, or was stopped by a guard (a handoff)."""
    top = _read(pathlib.Path(save_dir) / "calls" / "0.json")
    return bool(top and top.get("messages") and (not top.get("done") or top.get("stopped")))


def saves_root(runs_root, pinned=False):
    """Where finished runs are kept: beside the live ledgers' root (State/hai-runs-saved, State/hai-runs-pinned)."""
    runs_root = pathlib.Path(runs_root)
    return runs_root.with_name(runs_root.name + ("-pinned" if pinned else "-saved"))


def saved(root=None):
    """Every kept run, newest first: [{"save", "pinned", "run_id", "lane", "accepted_at", "finished_at", "calls"}]."""
    out = []
    for pinned in (True, False):
        base = saves_root(root or ROOT, pinned)
        for d in base.iterdir() if base.exists() else []:
            info = _read(d / "run.json") if d.is_dir() else None
            if info:
                calls = d / "calls"
                out.append({"save": d.name, "pinned": pinned, "run_id": info.get("run_id"), "lane": info.get("lane"),
                            "accepted_at": info.get("accepted_at"), "finished_at": info.get("finished_at"),
                            "calls": sorted(f.stem for f in calls.glob("*.json")) if calls.exists() else []})
    return sorted(out, key=lambda r: r.get("finished_at") or "", reverse=True)


def read_save(save, root=None):
    """One kept run: its run.json and every call's conversation + answer - the material for starting again from
    any internal turn (truncate a call's messages at that turn). None when there is no such save."""
    name = _safe(save)
    if not name or name.startswith("."):
        return None
    for pinned in (True, False):
        d = saves_root(root or ROOT, pinned) / name
        if d.is_dir():
            calls = d / "calls"
            return {"save": name, "pinned": pinned, "run": _read(d / "run.json") or {},
                    "calls": {f.stem: _read(f) for f in sorted(calls.glob("*.json"))} if calls.exists() else {}}
    return None


def restore_save(run_id, root=None, lanes=("stream",)):
    """Restore a kept run that errored without an answer to a live ledger for resumption. Refuses runs that are already live or have completed an answer.

    Returns:
        (RunLedger, name) on success, (None, reason) on failure."""
    base = pathlib.Path(root or ROOT)
    live = base / _safe(run_id)
    if not run_id or live.exists():
        return None, "that run is live" if run_id else "no run named"
    found = []
    for kept in (saves_root(base, True), saves_root(base, False), archive_root(saves_root(base, False))):
        for d in kept.iterdir() if kept.exists() else []:
            info = _read(d / "run.json") if d.is_dir() else None
            if info and info.get("run_id") == run_id:
                found.append((info.get("finished_at") or "", d, info))
    if not found:
        return None, "no save of that run"
    _at, d, info = max(found, key=lambda r: r[0])
    if info.get("lane") not in lanes:
        return None, "a %s run is not resumed from its save" % info.get("lane")
    if not resumable(d):
        return None, "that run finished its answer or saved no checkpoint: nothing to continue"
    base.mkdir(parents=True, exist_ok=True)
    shutil.move(str(d), str(live))
    top_path = live / "calls" / "0.json"
    top = _read(top_path) or {}
    if top.get("stopped"):
        # a guard-stopped call: its handoff is set aside and the call is live again from its last checkpoint
        _write_atomic(top_path, {k: v for k, v in dict(top, done=False, stopped_before=top["stopped"]).items()
                                 if k not in ("out", "stopped")})
    info = dict(info, restored_from_save=d.name, ended_unfinished_at=info.get("finished_at"))
    for k in ("finished_at", "suspended", "mark_at_resume"):   # an operator's restore is not a crash loop
        info.pop(k, None)
    _write_atomic(live / "run.json", info)
    return RunLedger(run_id, base), d.name


def interrupted(root=None):
    """Every run that started and never finished: [(RunLedger, run.json dict)], oldest first."""
    base = pathlib.Path(root or ROOT)
    out = []
    if not base.exists():
        return out
    for d in base.iterdir():
        info = _read(d / "run.json") if d.is_dir() else None
        if info and info.get("run_id"):
            out.append((RunLedger(info["run_id"], base), info))
    return sorted(out, key=lambda x: x[1].get("accepted_at") or "")
