"""A1-team on the Fellowship (S10c, 34-S10 design: "one order that holds rigby and deals tablet pieces - Tolkien
orchestrating both lanes"). The lead on rigby calls `delegate(brief)`; each brief becomes a Tolkien MAP order on the
brief payload (map_brief.py), submitted through Tolkien's own front door (the INBOX) and run by the fleet lane beside
the rigby lane (the two lanes run side by side, Fellowship-Order-Taker-Design §13). The piece's answer is the tool
result. Same call protocol as the GPU helper's a1_delegate.Delegator: `__call__(brief) -> text`, `.pieces`, `.exercise`.

The lead waits on the ORDER'S OWN STATUS, never a clock: the delivered result file, the HELD marker, or
the runner's own retirement note (the delegator runs inside the order taker's process, in the rigby lane thread).
Every piece is recorded with its timings so waiting is priced apart from the lead's turns.

    team = {"helper": "fellowship", "helper_model": "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M", "helper_ctx": 2048,
            "delegated_dir": "<the desk's share>/campaigns/<campaign>/delegated"}   # delegated_dir optional"""
import importlib.util
import json
import os
import posixpath
import time

POLL_S = 5.0                                      # how often the order's status is read; not a deadline


def desk_share(tf):
    """The tablet desk's share as Tolkien sees it: the folder its INBOX is in (TABLET_INBOX, one setting). The payload
    and the delivered results live under it, in the container's own path form."""
    return posixpath.dirname(str(tf.INBOX).replace("\\", "/").rstrip("/"))


def _tablet_foundry():
    import tablet_foundry
    return tablet_foundry


def _map_brief():
    """The brief payload module, from the path the spec ships to the tablets (beside this package: payloads/a1/.. ):
    its item_line is the ONE spelling of an item, so the lead's lines and the tablet's parser cannot drift."""
    path = os.environ.get("MAP_BRIEF_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "map_brief.py")
    spec = importlib.util.spec_from_file_location("map_brief", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FellowshipDelegator:
    def __init__(self, team, order_name, tf=None, log=None, sleep=time.sleep, clock=time.time, stop_event=None,
                 brief_mod=None, prefix="a1team-"):
        self.team, self.order_name = dict(team), order_name
        self.tf = tf or _tablet_foundry()
        self.log = log or self.tf.log
        self.sleep, self.clock, self.stop_event = sleep, clock, stop_event
        self.mb = brief_mod or _map_brief()
        self.payload = posixpath.join(desk_share(self.tf), "payloads", "map_brief.py")
        self.delegated_dir = self.team.get("delegated_dir") or posixpath.join(
            desk_share(self.tf), "campaigns", "s10c", order_name, "delegated")
        self.prefix = prefix          # "a1team-" = a drafted piece; "a1review-" = A1-review, a tablet reads the lead's
        self.pieces, self.exercise = [], ""    # work (the same shape one layer down: the reviewer is a tablet)

    # ---- the order -------------------------------------------------------------------------------------------
    def spec(self, brief, name):
        t = self.team
        model, ctx = t["helper_model"], int(t["helper_ctx"])
        deliver = os.path.join(self.delegated_dir, name + ".results.json")
        return {"kind": "map", "name": name, "payload": self.payload,
                "items": [self.mb.item_line(self.mb.ARM_BRIEF, "brief", 1, model, ctx, brief)],
                "batch": 1,                                        # the brief and the control on two tablets at once
                "control": {"item": self.mb.control_line(model, ctx), "expect": self.mb.CONTROL_SENTINEL},
                "deliver_to": deliver, "notify": "failures",
                "why": ("A1-review of %s (%s): one tablet reads the lead's brief, files and test output against the brief"
                        if self.prefix == "a1review-" else
                        "A1-team piece of %s (%s): the lead's delegate() brief, drafted by one tablet")
                       % (self.order_name, self.exercise)}

    def submit(self, spec):
        """Through the INBOX, the customer's door: a tmp name the intake ignores, made whole by one rename."""
        inbox = self.tf.INBOX
        os.makedirs(inbox, exist_ok=True)
        final = os.path.join(inbox, spec["name"] + ".tjob.json")
        tmp = os.path.join(inbox, "." + spec["name"] + ".tjob.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1)
        os.replace(tmp, final)
        return final

    # ---- the status ------------------------------------------------------------------------------------------
    def status(self, name, deliver):
        """One reading of where the piece is: ("done", results path) | ("held", why) | ("waiting", "")."""
        if os.path.exists(deliver):
            return "done", deliver
        for d, suffix in ((self.tf.QUEUE_DIR, ".tjob.json.HELD"), (self.tf.INBOX, ".tjob.json.HELD")):
            if os.path.exists(os.path.join(d, name + suffix)):
                return "held", "the piece order was HELD (%s) - see Tolkien's log for the defect" % d
        note = getattr(self.tf, "_RECENT_RETIRED", {}).get(name)
        if note and note[1]:
            res = os.path.join(self.tf.RESULTS_DIR, note[1])
            if os.path.exists(res):
                return "done", res                             # retired but deliver_to failed: the local copy stands
        return "waiting", ""

    def __call__(self, brief):
        k = len(self.pieces) + 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "%s%s-%s-d%d.%s" % (self.prefix, self.order_name.split(".")[0][-40:], self.exercise[:24], k, stamp)
        rec = {"k": k, "exercise": self.exercise, "order": name, "brief_chars": len(brief), "submitted": self.clock(),
               "helper": self.team.get("helper", "fellowship"), "helper_model": self.team["helper_model"]}
        self.pieces.append(rec)
        try:
            spec = self.spec(brief, name)
            os.makedirs(self.delegated_dir, exist_ok=True)
            self.submit(spec)
        except Exception as e:                                              # noqa: BLE001
            rec.update(status="submit_failed", error=repr(e)[:200], done=self.clock())
            return "ERROR: the helper order could not be placed (%s)" % type(e).__name__
        self.log("delegate: piece %d of %s (%s) -> %s on the Fellowship" % (k, self.order_name, self.exercise, name))
        deliver = spec["deliver_to"]
        while True:
            state, detail = self.status(name, deliver)
            if state != "waiting":
                break
            if self.stop_event is not None and self.stop_event.is_set():
                rec.update(status="stopped", done=self.clock())
                return "ERROR: the run was stopped while waiting for the helper"
            self.sleep(POLL_S)
        rec["done"] = self.clock()
        if state == "held":
            rec.update(status="held", error=detail[:200])
            return "ERROR: the helper order is held (%s)" % detail
        try:
            with open(detail, encoding="utf-8") as f:
                out = json.load(f)
            rows = out.get("rows") or []
            row = next(r for r in rows if r.get("arm") == self.mb.ARM_BRIEF)
        except (OSError, ValueError, StopIteration) as e:
            rec.update(status="no_delivery", error=repr(e)[:200])
            return "ERROR: the helper's answer was not delivered"
        rec.update(status="done" if out.get("usable") else "void", host=row.get("_host"),
                   helper_wall_s=row.get("wall_s"), answer_chars=len(row.get("answer") or ""),
                   control_passed=out.get("control_passed"), error=(row.get("error") or "")[:200])
        self.log("delegate: piece %d back in %ds (%s, %s chars)" % (k, rec["done"] - rec["submitted"], rec["status"],
                                                                  rec["answer_chars"]))
        if not out.get("usable"):
            return "ERROR: the helper order was void (%s)" % (out.get("control") or out.get("coverage") or "")
        ans = row.get("answer") or ""
        if row.get("error"):
            return "ERROR: the helper could not answer (%s)" % row["error"]
        return ans if ans.strip() else "ERROR: the helper returned an empty answer"
