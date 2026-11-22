"""A1-team: the lead's `delegate(brief)` becomes a DESK ORDER on the helper box (34-A1 spec "Sub-agents": a piece is
placed like an order; work on a box is that box's foundry order, so the helper's foundry claims its card
and the deck shows it - the lead never calls another box's ollama directly).

Each piece: one infer_ollama item (the brief as the prompt) ordered on the plan's helper (one layer at a time: the
helper box runs nothing else during a team run, so the run names it - `only` - rather than letting placement pick a
busy box). The lead WAITS ON THE DESK'S STATUS for that order (ask the worker, never a clock), keeps its
own model loaded meanwhile (a cold reload plus a full re-read would otherwise hide inside the lead's price), and gets
the helper's answer back as the tool result. Every piece is logged with its timings so waiting is priced apart from
the lead's own turns.

    team = {"helper": "bighonker", "helper_model": "qwen3.5:9b", "helper_ctx": 7168, "helper_think": false,
            "helper_temperature": 0.6}"""
import json
import os
import sys
import time
import urllib.request


def _foundry_settings():
    """The foundry's one settings reader (foundry_paths), in <FOUNDRY_ROOT>\\bin on a factory, where this file runs from
    <FOUNDRY_ROOT>\\workspace\\a1; None where there is none (a dev tree reads the environment only). Appended to the
    path, so nothing in bin can shadow this package's own modules."""
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bin")
    if not os.path.isfile(os.path.join(d, "foundry_paths.py")):
        return None
    if d not in sys.path:
        sys.path.append(d)
    import foundry_paths
    return foundry_paths


_FP = _foundry_settings()
KIRK = (_FP.head_url() if _FP else os.environ.get("HAI_URL", "")).rstrip("/")   # the head's desk
TERMINAL = ("done", "failed", "cancelled", "expired")
POLL_S = 2.0             # how often the desk is asked (0.10 s per ask since the desk cache, gate 2559); not a deadline
WARM_EVERY_S = 240.0     # well inside the lead's 10 min keep_alive (infer_ollama._chat)
DESK_FAILS = 12          # consecutive unreadable status replies before the lead is told the helper is unreachable


def work_beat(clock=time.time):
    """The foundry's work beat (`[beat] N`, p20_foundry.COMPUTE_BEAT_RE - the line infer_ollama prints while tokens
    stream): the stall demon reads a moving beat as work whatever the unit count says. A lead waiting on a desk piece
    streams nothing, so without this the wait reads as a wedge: the demon respawned the rigby team
    arm 15 min into a 14-min rigby piece. The value is the clock (monotonic, so every beat is a change)."""
    print("[beat] %d" % int(clock()), flush=True)


class Delegator:
    def __init__(self, team, order_name, unc_dir, token_fn, keep_warm, log=print, http=None, sleep=time.sleep,
                 clock=time.time, prefix="a1team-", beat=None):
        self.team, self.order_name, self.unc_dir = dict(team), order_name, unc_dir
        self.token_fn, self.keep_warm, self.log = token_fn, keep_warm, log
        self.http, self.sleep, self.clock = http or self._http, sleep, clock
        self.beat = beat or (lambda: work_beat(self.clock))
        self.prefix = prefix                          # "a1team-" for a helper piece, "a1review-" for a reviewer
        self.pieces, self.exercise = [], ""

    def _http(self, method, path, body=None, timeout=30):
        if not KIRK:
            raise RuntimeError("no head linked (HAI_URL in the foundry's settings): a piece has no desk to go to")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        tok = self.token_fn()
        if tok:
            headers["X-P20-Token"] = tok
        req = urllib.request.Request(KIRK + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")

    def _order_body(self, brief, name):
        """The desk body for one piece, and where its answer will be delivered (read back by _read_answer)."""
        items_dir = os.path.join(self.unc_dir, "delegations")
        os.makedirs(items_dir, exist_ok=True)
        items = os.path.join(items_dir, name + ".json")
        with open(items, "w", encoding="utf-8") as f:
            json.dump({"items": [{"id": "brief", "prompt": brief}]}, f)
        t = self.team
        spec = {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
                "args": [items, "{ORDER_OUT}\\out.json", "--model", t["helper_model"], "--rounds", "1",
                         "--num-ctx", str(int(t["helper_ctx"])), "--temperature", str(t.get("helper_temperature", 0.6)),
                         "--seed-base", "1"] + (["--think"] if t.get("helper_think") else []),
                "progress_glob": "{ORDER_OUT}\\out.progress\\*.done", "progress_total": 1,
                "deliver_to": os.path.join(self.unc_dir, "delegated"), "notify": "failures",
                "priority": GPU_PIECE_PRIORITY}
        body = {"spec": spec, "requires": ["order.compute", "model.%s" % t["helper_model"]], "only": [t["helper"]],
                "user": "ClaudeSupervisor"}
        return body, spec["deliver_to"]

    def _read_answer(self, rec, deliver, step):
        """The helper's answer from its delivery (infer_ollama's out.json); fills rec; returns the tool result."""
        out = os.path.join(deliver, step, "out.json")
        try:
            with open(out, encoding="utf-8") as f:
                row = json.load(f)["rows"][0]
        except (OSError, ValueError, KeyError, IndexError) as e:
            rec.update(status="no_delivery", error=repr(e)[:200])
            return "ERROR: the helper's answer was not delivered"
        ans = row.get("answer") or row.get("thinking") or ""
        rec.update(helper_wall_s=row.get("wall_s"), helper_tokens=row.get("eval_count"),
                   done_reason=row.get("done_reason"), answer_chars=len(ans))
        return ans if ans.strip() else "ERROR: the helper returned an empty answer"

    def __call__(self, brief):
        k = len(self.pieces) + 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "%s%s-%s-d%d.%s" % (self.prefix, self.order_name.rsplit(".", 1)[0][-40:], self.exercise[:24], k, stamp)
        body, deliver = self._order_body(brief, name)
        rec = {"k": k, "exercise": self.exercise, "order": name, "brief_chars": len(brief), "submitted": self.clock()}
        self.pieces.append(rec)
        try:
            reply = self.http("POST", "/p20/order", body)
        except Exception as e:                                              # noqa: BLE001
            rec.update(status="submit_failed", error=repr(e)[:200], done=self.clock())
            return "ERROR: the helper order could not be placed (%s)" % type(e).__name__
        # the desk's reply: {"ok": true, "order": <name>, "kind":..., "requires": [...],
        # "parallel": false, "steps": [{"host": <box>, "id": <record id>, "order": <name>_<box>}]} - the id that
        # GET /p20/order/{id} answers to is the STEP's record id; there is no top-level id. The first deploy read
        # reply["id"], told the lead every placed piece had failed, and the 3.8 re-briefed four times in 45 s.
        steps_full = [s for s in (reply.get("steps") or []) if isinstance(s, dict)]
        steps = [s.get("order") for s in steps_full]
        oid = reply.get("id") or (steps_full[0].get("id") if steps_full else None)
        if not reply.get("ok", True) or not oid or len(steps) != 1 or not steps[0]:
            rec.update(status="refused", error=json.dumps(reply)[:200], done=self.clock())
            return "ERROR: the desk did not place the helper order (%s)" % (reply.get("error") or "no step")
        rec.update(id=oid, step=steps[0], host=steps_full[0].get("host"))
        status, fails, warmed = "", 0, self.clock()
        while True:
            try:
                st = self.http("GET", "/p20/order/%s" % oid)
                status, fails = str(st.get("status") or ""), 0
            except Exception:                                               # noqa: BLE001
                fails += 1
                if fails >= DESK_FAILS:
                    rec.update(status="desk_unreachable", done=self.clock())
                    return "ERROR: the desk stopped answering about the helper order"
                st = {}
            if status in TERMINAL:
                break
            if status == "held":
                rec.update(status="held", error=str(st.get("hold_reason") or "")[:200], done=self.clock())
                return "ERROR: the helper order is held (%s)" % (st.get("hold_reason") or "no reason given")
            if self.clock() - warmed >= WARM_EVERY_S:
                self.keep_warm()
                warmed = self.clock()
            self.beat()                               # waiting on a piece is work, not a wedge
            self.sleep(POLL_S)
        # where it actually ENDED: a factory may hand an order back and the desk re-place it under another host and
        # name
        if st.get("host"):
            rec["host"] = st["host"]
        step = st.get("order") or steps[0]
        rec.update(status=status, step=step, done=self.clock())
        if status != "done":
            return "ERROR: the helper order ended %s (%s)" % (status, st.get("reason") or "")
        return self._read_answer(rec, deliver, step)


def desk_id_for(out, root):
    """The running order's desk record id from its spec in <root>\\jobs\\queue; {ORDER_OUT}'s basename is the order's
    `name`. The spec FILE does not always carry that name (10-11: a late-routed order was filed as `<x>.job.json` while
    its name and ORDER_OUT were `<x>_softserve`), so a spec is matched by its `name` field when the file name misses.
    None when no spec names this order."""
    import glob
    name = os.path.basename(out.rstrip("\\/"))
    qdir = os.path.join(root, "jobs", "queue")
    exact = os.path.join(qdir, name + ".job.json")
    for path in [exact] + sorted(p for p in glob.glob(os.path.join(qdir, "*.job.json")) if p != exact):
        try:
            with open(path, encoding="utf-8") as f:
                spec = json.load(f)
        except (OSError, ValueError):
            continue
        if path == exact or spec.get("name") == name:
            return str(spec.get("fleet_order_id") or "").strip() or None
    return None


def lead_box():
    """[the box this lead runs on]: a placed helper never lands on the lead's own card (one model per card - it would
    evict the lead, or queue behind the lead's own order and never open)."""
    import socket
    return [socket.gethostname().lower()]


class PlacedDelegator(Delegator):
    """The PLACED delegate (#34 Phase 7, register 10-10 01:4x): a piece names its job family + tier + urgency and NO
    host and NO model. Spock orders the eligible hosts by policy.json and the desk fills the model, effort, temperature
    and tested context from the row that placed the chosen host (scheduler.resolve_policy_tokens). Pinned team arms
    keep using Delegator: they price one helper; this is the production shape.

        team = {"placed": True, "family": "coding-f2", "tier": "8gb", "urgency": "background"}"""

    def lens(self):
        t = self.team
        return t.get("lens") or ("fastest" if t.get("urgency") == "interactive" else "least_energy")

    def placement(self):
        """The order's placement fields. A piece naming its URGENCY (and no lens) is placed by the desk's per-request
        weighing - the rule chat turns use; a team that names a lens is a
        pricing arm and keeps that lens. The lens rides along either way (the desk's records name it)."""
        t = self.team
        out = {"family": t["family"], "tier": t.get("tier", "8gb"), "lens": self.lens()}
        if t.get("urgency") in ("interactive", "background") and not t.get("lens"):
            out["urgency"] = t["urgency"]
        return out

    def _order_body(self, brief, name):
        items_dir = os.path.join(self.unc_dir, "delegations")
        os.makedirs(items_dir, exist_ok=True)
        items = os.path.join(items_dir, name + ".json")
        with open(items, "w", encoding="utf-8") as f:
            json.dump({"items": [{"id": "brief", "prompt": brief}]}, f)
        spec = {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                "payload": "{FOUNDRY_ROOT}\\bin\\infer_ollama.py",
                "args": [items, "{ORDER_OUT}\\out.json", "--model", "{POLICY_MODEL}", "--rounds", "1",
                         "--num-ctx", "{POLICY_NUM_CTX}", "{POLICY_TEMPERATURE_ARGS}", "--seed-base", "1",
                         "{POLICY_THINK_ARGS}"],
                "progress_glob": "{ORDER_OUT}\\out.progress\\*.done", "progress_total": 1,
                "deliver_to": os.path.join(self.unc_dir, "delegated"), "notify": "failures",
                "priority": GPU_PIECE_PRIORITY}
        body = dict({"spec": spec, "requires": ["order.compute"], "exclude": lead_box(), "user": "ClaudeSupervisor"},
                    **self.placement())
        fel_spec, fel_result = self._fellowship_form(brief, name)
        body["fellowship"] = {"spec": fel_spec, "requires": ["fellowship.placed"]}
        return body, {"gpu": spec["deliver_to"], "fellowship": fel_result}

    def _fellowship_form(self, brief, name):
        """The FELLOWSHIP FORM of the same piece (design § D): a Tolkien `llm` order naming no worker - the desk adds
        the placement key if the table picks the Fellowship, and Tolkien fills arms, model and settings from the row
        it picks (rigby, or one warm tablet). The brief is one task in a generated payload on the share, graded and
        controlled by the f2 payload, as RigbyDelegator's pieces are."""
        rel = "tabfoundry-jobs/campaigns/a1team-placed/%s" % self.order_name.split(".")[0][-60:]
        unc, cont = os.path.join(SHARE_UNC, *rel.split("/")), "%s/%s" % (SHARE_CONTAINER, rel)
        payload, result = name + ".payload.py", name + "_fellowship.results.json"
        os.makedirs(unc, exist_ok=True)
        old = os.path.join(unc, result)
        if os.path.exists(old):          # one name = one artifact (archived, never read as this piece's answer)
            os.replace(old, old + ".superseded-%d" % int(self.clock() * 1000))
        tmp = os.path.join(unc, payload + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(PLACED_PIECE_PAYLOAD % {"f2": F2_PAYLOAD, "brief": brief})
        os.replace(tmp, os.path.join(unc, payload))
        spec = {"kind": "llm", "name": name, "payload": cont + "/" + payload, "tasks": ["brief"], "replicates": 1,
                "priority": RIGBY_PIECE_PRIORITY, "notify": "failures", "deliver_to": cont + "/" + result,
                "initiator": "ClaudeSupervisor"}
        return spec, os.path.join(unc, result)

    def _read_answer(self, rec, deliver, step):
        """Read the answer where the desk sent the piece: a GPU factory's infer_ollama delivery, or the Fellowship's
        results file (rigby's or a warm tablet's row - the same reader RigbyDelegator uses)."""
        if rec.get("host") == "fellowship":
            return RigbyDelegator._read_answer(self, rec, deliver["fellowship"], step)
        return Delegator._read_answer(self, rec, deliver["gpu"], step)


SHARE_UNC = (_FP.setting("SSD_SHARED_UNC", "") if _FP else os.environ.get("SSD_SHARED_UNC", ""))   # the share Tolkien mounts at /ssdshared
SHARE_CONTAINER = "/ssdshared"
F2_PAYLOAD = SHARE_CONTAINER + "/tabfoundry-jobs/payloads/map_swarm_code_hard_f2.py"   # the one grader + control
RIGBY_PIECE_PRIORITY = 100     # a piece runs next on rigby's lane (Tolkien `priority`: higher first)
# The same front-load rule on the GPU factories (p20_foundry.order_priority, higher first): a helper piece, a reviewer
# or a helper window is a short SERVICE order a live lead waits on, so it runs next after the current order, never
# behind a queue of filler.
GPU_PIECE_PRIORITY = 100
PIECE_PAYLOAD = '''"""A1-team piece for rigby, generated by a1_delegate.RigbyDelegator: the lead's brief as ONE task. The
answer is the deliverable (the lead reads it); the f2 payload's grader and known-positive control validate the run."""
import importlib.util

_s = importlib.util.spec_from_file_location("_f2", %(f2)r)
_f2 = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_f2)
# the f2 payload's whole surface (scorer, control, extraction - whatever the one grader reads), then the piece's own
globals().update({_k: _v for _k, _v in vars(_f2).items() if not _k.startswith("__")})
TEMPERATURE = %(temperature)r
CONTEXT = %(ctx)r
SUFFIX = ""
TASKS = {"brief": {"kind": "a1team_piece", "prompt": %(brief)r, "checks": []}}
'''
# A PLACED piece's payload (PlacedDelegator's Fellowship form): the worker is not known when it is written - Tolkien
# picks it from the table row, whose order fields set the context and sampling. A row that leaves the temperature
# blank was measured at the payload's own default, so the defaults here are the f2 payload's, never chosen here.
PLACED_PIECE_PAYLOAD = '''"""A1-team PLACED piece, generated by a1_delegate.PlacedDelegator: the lead's brief as ONE task. The Fellowship
worker (rigby or a warm tablet) is Tolkien's pick from the placement table; the f2 payload grades and controls it."""
import importlib.util

_s = importlib.util.spec_from_file_location("_f2", %(f2)r)
_f2 = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_f2)
# the f2 payload's whole surface (scorer, control, extraction, its TEMPERATURE / CONTEXT defaults - whatever the one
# grader reads); only the task is the piece's own. Re-exporting score + CONTROL_TASKS alone would leave
# extract_code out, and the one grader (grade_jobs.answer_code) reads it for an answer with no fence: a correct
# unfenced answer would fail grading and the piece would end "failed".
globals().update({_k: _v for _k, _v in vars(_f2).items() if not _k.startswith("__")})
SUFFIX = ""
TASKS = {"brief": {"kind": "a1team_piece", "prompt": %(brief)r, "checks": []}}
'''


class RigbyDelegator(Delegator):
    """A1-team with RIGBY as the helper (34-A1 spec decision 3a, "sub-agent to the 3.8"): each brief becomes a Tolkien
    `llm` order placed through KIRK'S DESK (`only: fellowship`, `requires: rigby.llm` - a capability Tolkien detects),
    so the same desk status the GPU helper path waits on says when it is done (Tolkien reports every collected order's
    verdict). The piece is one task in a generated payload on the share; rigby answers it with its own model and
    server settings (the GT 730 placement when the team block names it) and DELIVERS the result file to a stated path.
    `priority` puts it ahead of rigby's queued arms; one layer at a time still holds rigby to this team run.

        team = {"helper": "rigby", "helper_model": "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf", "helper_ctx": 32768,
                "helper_think": False, "helper_temperature": 0.6, "gpu_layers": 99, "override_tensor": [...],
                "op_offload": False}
    """

    reserved = False

    def _lane_slip(self, action, owner_id=None):
        """`<owner>.<action>.lane.json` in Tolkien's inbox (tablet_foundry LANE_SUFFIX). Never raises: a slip that
        cannot be written is logged, and Tolkien still lifts a reservation once the owner's desk record ends."""
        inbox = os.path.join(SHARE_UNC, "tabfoundry-jobs", "inbox")
        name = "%s.%s.lane.json" % (self.order_name.split(".")[0][-60:], action)
        body = {"action": action, "lane": "rigby", "by": self.order_name, "owner_id": owner_id,
                "why": "A1 team run %s %s rigby (one layer at a time)" % (self.order_name, action + "s")}
        try:
            tmp = os.path.join(inbox, name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(body, f)
            os.replace(tmp, os.path.join(inbox, name))
            return True
        except Exception as e:                                              # noqa: BLE001
            self.log("rigby %s slip not written (%r)" % (action, e))
            return False

    def reserve(self, owner_id=None):
        """Reserve rigby's lane for this team run: Tolkien stops what rigby is running and starts only this run's pieces."""
        self.reserved = self._lane_slip("reserve", owner_id)
        return self.reserved

    def close(self):
        """Release rigby at the end of the run, by ANY route (a1_order calls it in its finally)."""
        if self.reserved:
            self._lane_slip("release")
            self.reserved = False
        return {"rigby_reservation": "released"}

    def _paths(self, name):
        rel = "tabfoundry-jobs/campaigns/a1team-rigby/%s" % self.order_name.split(".")[0][-60:]
        return (os.path.join(SHARE_UNC, *rel.split("/")), "%s/%s" % (SHARE_CONTAINER, rel),
                name + ".payload.py", name + ".results.json")

    def _order_body(self, brief, name):
        t = self.team
        unc, cont, payload, result = self._paths(name)
        os.makedirs(unc, exist_ok=True)
        old = os.path.join(unc, result)
        if os.path.exists(old):          # one name = one artifact: a same-named earlier result must never be read as
            os.replace(old, old + ".superseded-%d" % int(self.clock() * 1000))   # this piece's answer (archived)
        tmp = os.path.join(unc, payload + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(PIECE_PAYLOAD % {"f2": F2_PAYLOAD, "temperature": float(t.get("helper_temperature", 0.6)),
                                     "ctx": int(t["helper_ctx"]), "brief": brief})
        os.replace(tmp, os.path.join(unc, payload))
        spec = {"kind": "llm", "name": name, "model": t["helper_model"], "payload": cont + "/" + payload,
                "tasks": ["brief"], "replicates": 1, "arms": ["rigby"], "ctx": int(t["helper_ctx"]),
                "reasoning": "on" if t.get("helper_think") else "off",
                "temperature": float(t.get("helper_temperature", 0.6)), "priority": RIGBY_PIECE_PRIORITY,
                "notify": "failures", "deliver_to": cont + "/" + result,
                "reservation": self.order_name}              # admitted while this run holds rigby's lane
        requires = ["rigby.llm"]
        for k in ("gpu_layers", "override_tensor", "op_offload"):
            if t.get(k) is not None:
                spec[k] = t[k]
        if t.get("gpu_layers") is not None:
            requires.append("rigby.gt730")
        body = {"spec": spec, "requires": requires, "only": ["fellowship"], "user": "ClaudeSupervisor"}
        return body, os.path.join(unc, result)

    def _read_answer(self, rec, deliver, step):
        try:
            with open(deliver, encoding="utf-8") as f:
                res = json.load(f)
        except (OSError, ValueError) as e:
            rec.update(status="no_delivery", error=repr(e)[:200])
            return "ERROR: rigby's answer was not delivered"
        row = next((r for r in res.get("rows") or [] if r.get("task") == "brief"), None)
        if row is None:
            rec.update(status="no_answer_row", error="no brief row in %s" % os.path.basename(deliver))
            return "ERROR: rigby's result has no answer row"
        ans = row.get("answer") or row.get("thinking") or ""
        rec.update(helper_wall_s=row.get("wall_s"), helper_tokens=row.get("gen_tokens"),
                   helper_tok_s=row.get("gen_tps"), done_reason=row.get("finish_reason"), answer_chars=len(ans),
                   control_passed=res.get("control_passed"))
        return ans if ans.strip() else "ERROR: rigby returned an empty answer"


DESK_CHECK_POLLS = 10    # while waiting on an item: ask the desk about the WINDOW order every 10 polls (20 s)
CLOSE_WAIT_POLLS = 60    # after CLOSE: how many polls to wait for the window order to report done (a bound, logged)


class WindowDelegator(Delegator):
    """A1-team through a helper WINDOW: ONE desk order per lead run
    on the helper box (`bin\\helper_window.py`, which holds the model resident), opened at the first piece; each piece
    is an item file in the window's share dir, answered beside it by the worker; CLOSE at the end of the run. The
    desk's per-piece fixed cost (~12 s) is paid once. The window is still a desk order: the helper's foundry claims
    the card, the deck shows it, the police see it (12e). While waiting on an item the lead asks the desk about the
    window's STATUS (held / ended -> an ERROR the lead can act on), never a clock; the lead stays warm as before."""

    def __init__(self, team, order_name, unc_dir, token_fn, keep_warm, log=print, http=None, sleep=time.sleep,
                 clock=time.time, lead_order_id=None, prefix="a1win-", beat=None):
        super().__init__(team, order_name, unc_dir, token_fn, keep_warm, log=log, http=http, sleep=sleep, clock=clock,
                         prefix=prefix, beat=beat)
        self.lead_order_id = lead_order_id
        self.window = None                      # the window order's record once placed (or its refusal)
        self.items_dir = None

    def open(self):
        """Place the window order on the helper. Refusal is recorded and every piece then fails fast."""
        name = "%s%s.%s" % (self.prefix, self.order_name.rsplit(".", 1)[0][-40:], time.strftime("%Y%m%d-%H%M%S"))
        self.items_dir = os.path.join(self.unc_dir, "windows", name)
        os.makedirs(self.items_dir, exist_ok=True)
        body = self._window_body(name)
        self.window = {"order": name, "items_dir": self.items_dir, "submitted": self.clock(), "lead_order": self.lead_order_id}
        try:
            reply = self.http("POST", "/p20/order", body)
        except Exception as e:                                              # noqa: BLE001
            self.window.update(status="submit_failed", error=repr(e)[:200])
            self.log("helper window could not be placed: %r" % (e,))
            return self.window
        steps_full = [s for s in (reply.get("steps") or []) if isinstance(s, dict)]
        oid = reply.get("id") or (steps_full[0].get("id") if steps_full else None)
        if not reply.get("ok", True) or not oid or len(steps_full) != 1 or not steps_full[0].get("order"):
            self.window.update(status="refused", error=json.dumps(reply)[:200])
            self.log("helper window refused by the desk: %s" % (reply.get("error") or "no step"))
            return self.window
        self.window.update(id=oid, step=steps_full[0]["order"], status="placed")
        self.log("helper window placed: %s (%s)" % (name, oid))
        return self.window

    def _window_spec(self, name, model_args):
        lead = ["--lead-order", str(self.lead_order_id)] if self.lead_order_id else []
        return {"name": name, "mode": "compute", "load": "gpu", "initiator": "ClaudeSupervisor",
                "payload": "{FOUNDRY_ROOT}\\bin\\helper_window.py",
                "args": [self.items_dir, "{ORDER_OUT}\\out.json"] + model_args + ["--seed-base", "1"] + lead,
                "progress_glob": "{ORDER_OUT}\\out.progress\\*.done",
                "deliver_to": os.path.join(self.unc_dir, "delegated"), "notify": "failures",
                "priority": GPU_PIECE_PRIORITY}

    def _window_body(self, name):
        """The window order pinned to the team's helper and model (a pricing arm)."""
        t = self.team
        args = ["--model", t["helper_model"], "--num-ctx", str(int(t["helper_ctx"])),
                "--temperature", str(t.get("helper_temperature", 0.6))]
        # an effort level, when the team names one: a bare --think is MEDIUM on qwen3.8 (10-07 effort sandbag)
        if t.get("helper_think_level"):
            args += ["--think-level", t["helper_think_level"]]
        elif t.get("helper_think"):
            args += ["--think"]
        return {"spec": self._window_spec(name, args), "requires": ["order.compute", "model.%s" % t["helper_model"]],
                "only": [t["helper"]], "user": "ClaudeSupervisor"}

    def _window_status(self):
        try:
            st = self.http("GET", "/p20/order/%s" % self.window["id"])
            return str(st.get("status") or ""), st
        except Exception:                                                   # noqa: BLE001
            return "", {}

    def _new_rec(self, prefix, **extra):
        import threading
        lock = self.__dict__.setdefault("_rec_lock", threading.Lock())   # errands ask from their own threads
        with lock:
            if self.window is None:
                self.open()
            k = len(self.pieces) + 1
            rec = dict({"k": k, "exercise": self.exercise, "order": self.window["order"], "item": "%s%d" % (prefix, k),
                        "submitted": self.clock(), "step": self.window.get("step")}, **extra)
            self.pieces.append(rec)
        return rec

    def _ask(self, rec, body):
        """Write ONE item into the window, wait on its row. -> (row, None), or (None, the error text for the lead)."""
        if self.window.get("status") in ("refused", "submit_failed"):
            rec.update(status=self.window["status"], error=self.window.get("error", "")[:200], done=self.clock())
            return None, "ERROR: the helper window could not be placed (%s)" % self.window.get("error", "")[:120]
        if self.window.get("status") in TERMINAL:
            rec.update(status="window_%s" % self.window["status"], done=self.clock())
            return None, "ERROR: the helper window has ended (%s)" % self.window["status"]
        item = os.path.join(self.items_dir, rec["item"] + ".item.json")
        tmp = item + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(body, id=rec["item"]), f, ensure_ascii=False)
        os.replace(tmp, item)
        out = os.path.join(self.items_dir, rec["item"] + ".out.json")
        polls, warmed = 0, self.clock()
        while True:
            if os.path.exists(out):
                try:
                    with open(out, encoding="utf-8") as f:
                        row = json.load(f)
                except (OSError, ValueError):
                    row = None                          # being written: whole on the next poll
                if row is not None:
                    rec.update(status="done", done=self.clock(), helper_wall_s=row.get("wall_s"),
                               helper_tokens=row.get("eval_count"), done_reason=row.get("done_reason"),
                               helper_gpu_j=row.get("gpu_j"), error=(row.get("error") or "")[:200])
                    if row.get("error"):
                        return None, "ERROR: the helper failed on this piece (%s)" % row["error"][:120]
                    return row, None
            polls += 1
            if polls % DESK_CHECK_POLLS == 0:
                status, st = self._window_status()
                if status == "held":
                    rec.update(status="held", error=str(st.get("hold_reason") or "")[:200], done=self.clock())
                    return None, "ERROR: the helper window is held (%s)" % (st.get("hold_reason") or "no reason given")
                if status in TERMINAL:
                    self.window["status"] = status
                    rec.update(status="window_%s" % status, done=self.clock())
                    return None, "ERROR: the helper window ended %s before answering (%s)" % (
                        status, st.get("reason") or "")
            if self.clock() - warmed >= WARM_EVERY_S:
                self.keep_warm()
                warmed = self.clock()
            self.beat()                               # waiting on a piece is work, not a wedge
            self.sleep(POLL_S)

    def __call__(self, brief):
        rec = self._new_rec("d", brief_chars=len(brief))
        row, err = self._ask(rec, {"prompt": brief})
        if err:
            return err
        ans = row.get("answer") or row.get("thinking") or ""
        rec["answer_chars"] = len(ans)
        return ans if ans.strip() else "ERROR: the helper returned an empty answer"

    def turn(self, messages, tools):
        """One ERRAND turn (a1_harness._Errands): the conversation + the look-only tools go to the window; the reply
        comes back in the harness's chat shape {"content", "tool_calls"} (or {"error"})."""
        import a1_ollama as A
        rec = self._new_rec("e", kind="errand", messages=len(messages))
        row, err = self._ask(rec, {"messages": messages, "tools": A.to_ollama_tools(tools)})
        if err:
            return {"error": err}
        mapped = A.map_response({"message": {"content": row.get("answer") or "",
                                              "tool_calls": row.get("tool_calls") or []}})
        rec["tool_calls"] = len(mapped["tool_calls"])
        return mapped

    def close(self):
        """End the window: CLOSE in its dir (the worker exits within a poll, the foundry ships its rows), then wait on
        the desk's status for it to report done - bounded, and the bound is logged, never silent. Never raises."""
        if not self.window or not self.items_dir or self.window.get("status") in ("refused", "submit_failed"):
            return self.window
        try:
            open(os.path.join(self.items_dir, "CLOSE"), "w").close()
        except OSError as e:
            self.window.update(close_error=repr(e)[:200])
            self.log("helper window CLOSE could not be written: %r" % (e,))
            return self.window
        self.window["closed"] = self.clock()
        for _ in range(CLOSE_WAIT_POLLS):
            status, _st = self._window_status()
            if status in TERMINAL:
                self.window["status"] = status
                self.window["ended"] = self.clock()
                self.log("helper window %s: %s" % (self.window["order"], status))
                return self.window
            self.sleep(POLL_S)
        self.window["status"] = "closing"
        self.log("helper window %s: CLOSE written, not yet reported done after %d polls (the foundry finishes it)"
                 % (self.window["order"], CLOSE_WAIT_POLLS))
        return self.window


class PlacedWindowDelegator(WindowDelegator):
    """The PLACED window: ONE helper window per lead run, placed by Spock from policy.json
    the window names family + tier + lens and no host and no model; the desk fills model, effort, temperature and the
    tested context from the row that placed the host (scheduler.resolve_policy_tokens). Placement is paid once, the
    model stays resident, so a piece costs its answer + ~2 s instead of a per-piece order's cold load.

        team = {"placed": True, "window": True, "family": "coding-f2", "tier": "8gb", "urgency": "background"}"""

    lens = PlacedDelegator.lens
    placement = PlacedDelegator.placement

    def _window_body(self, name):
        args = ["--model", "{POLICY_MODEL}", "--num-ctx", "{POLICY_NUM_CTX}", "{POLICY_TEMPERATURE_ARGS}",
                "{POLICY_THINK_ARGS}"]
        return dict({"spec": self._window_spec(name, args), "requires": ["order.compute"], "exclude": lead_box(),
                     "user": "ClaudeSupervisor"}, **self.placement())
