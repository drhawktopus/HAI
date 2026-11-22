"""Tracks run habits via three views: notes, finish gate, and log lines.

Never blocks or raises; state persists via loop checkpoint."""
import json
import posixpath
import re

LIVE = {}   # run id -> line(): what /hai/jobs shows for a run in flight (this process; a resumed run fills it again)

_PICTURES_ON_PAGE = re.compile(r"Pictures on this page \((\d+)\)")


def _is_sheet(path):
    return "-sheet-" in posixpath.basename(str(path or ""))


class Habits:
    def __init__(self, state=None):
        self.s = {"files": {}, "renders": 0, "frames": 0, "last": [], "last_after": {}, "seen": [],
                  "looks": {"load": 0, "after": 0, "reference": 0, "sheet": 0},
                  "changed_since_render": False, "pictures_listed": 0, "fetched": 0, "told_reference": False,
                  # whether the serving model takes pictures, learned from the run's own looks (no box is asked):
                  # None until a look answers, True once a picture was handed over, False once one was refused
                  "sees": None}
        if isinstance(state, dict):
            for k, v in state.items():
                if k in self.s:
                    self.s[k] = v

    def state(self):
        return json.loads(json.dumps(self.s))

    # ------------------------------------------------------------------ counting
    def note(self, fn, args, result_text, snag=False):
        """One tool call that has just run. -> a sentence to add to its result, or ''. Never raises."""
        try:
            return self._note(str(fn or ""), args if isinstance(args, dict) else {}, str(result_text or ""), bool(snag))
        except Exception:
            return ""

    def _note(self, fn, args, text, snag):
        s = self.s
        path = str(args.get("path") or args.get("destination") or "")
        if fn in ("coding_look", "look_at_upload"):
            return self._looked(path, text)          # a look a blind model was refused is a snag, and is how `sees` is learned
        if snag:
            return ""
        if fn == "coding_create":
            rec = s["files"].setdefault(path, {"whole": 0, "patch": 0})
            rec["whole"] += 1
            s["changed_since_render"] = True
            if rec["whole"] >= 2:
                return ("[How this run is working: this is whole-file write %d of %s (edits in place: %d). A rewrite replaces "
                        "everything, including what already worked. To change one thing, coding_patch it.]"
                        % (rec["whole"], posixpath.basename(path), rec["patch"]))
            return ""
        if fn == "coding_patch":
            s["files"].setdefault(path, {"whole": 0, "patch": 0})["patch"] += 1
            s["changed_since_render"] = True
            return ""
        if fn == "coding_file":
            s["changed_since_render"] = True
            return ""
        if fn == "fetch_url":
            m = _PICTURES_ON_PAGE.search(text)
            if m:
                s["pictures_listed"] += int(m.group(1))
            return ""
        if fn == "coding_fetch":
            s["fetched"] += 1
            return ""
        if fn == "coding_render":
            return self._rendered(text)
        return ""

    def _rendered(self, text):
        s = self.s
        sees = s["sees"]
        try:
            d = json.loads(text)
        except ValueError:
            return ""
        if not isinstance(d, dict) or not d.get("rendered"):
            return ""
        shots = [x for x in (d.get("shots") or []) if isinstance(x, dict) and x.get("path")]
        s["renders"] += 1
        s["frames"] += len(shots)
        s["last"] = [x["path"] for x in shots]
        s["last_after"] = {x["path"]: str(x.get("after") or "view") for x in shots}
        sheet_seen = isinstance(d.get("sheet"), dict) and d["sheet"].get("seen_by") == "you"
        s["seen"] = list(s["last"]) if sheet_seen else []
        if sheet_seen:
            s["looks"]["sheet"] += 1
            s["sees"] = sees = True
        s["changed_since_render"] = False
        said = []
        if shots and not sheet_seen and sees is not False:
            said.append("this render made %d picture%s and none has been looked at yet; look at the ones taken after an "
                        "interaction, not only the first" % (len(shots), "" if len(shots) == 1 else "s"))
        if s["pictures_listed"] and not s["looks"]["reference"] and not s["told_reference"] and sees is not False:
            s["told_reference"] = True
            said.append("you opened pages that list %d pictures and have looked at none of them; to match something real, "
                        "coding_fetch its picture and coding_look at it before judging your own" % s["pictures_listed"])
        return ("[How this run is working: " + "; ".join(said) + ".]") if said else ""

    def _looked(self, path, text):
        s = self.s
        if '"seen_by": "you"' not in text.replace('\\"', '"'):
            if "not set up to take pictures" in text:
                s["sees"] = False
            return ""
        s["sees"] = True
        if _is_sheet(path):
            s["seen"] = list(s["last"])
            s["looks"]["sheet"] += 1
            return ""
        if path in s["last"]:
            if path not in s["seen"]:
                s["seen"].append(path)
            first = s["last"][0] == path or s["last_after"].get(path) == "load"
            s["looks"]["load" if first else "after"] += 1
            unseen = [p for p in s["last"] if p not in s["seen"] and s["last_after"].get(p) != "load" and p != s["last"][0]]
            if first and unseen:
                return ("[How this run is working: %d picture%s of that render, taken after an interaction, %s not been "
                        "looked at.]" % (len(unseen), "" if len(unseen) == 1 else "s", "has" if len(unseen) == 1 else "have"))
            return ""
        if "/renders/" in "/" + path or path.startswith("renders/"):
            s["looks"]["load"] += 1          # an older render's picture: a look, at nothing the last render made
        else:
            s["looks"]["reference"] += 1
        return ""

    # ------------------------------------------------------------------ the gate
    def finish_reasons(self):
        """Why a job that has rendered may not hand in yet. [] = nothing to say. A run that never rendered is not held,
        and a model that takes no pictures is never asked to look."""
        s = self.s
        if not s["renders"]:
            return []
        if s["changed_since_render"]:
            return ["the files changed after the last render, so nothing has seen what you are handing in"]
        if s["sees"] is True and s["last"] and not s["seen"]:
            return ["the last render made %d picture%s and none was looked at" % (len(s["last"]), "" if len(s["last"]) == 1 else "s")]
        return []

    # ------------------------------------------------------------------ the deck
    def short(self):
        """The line for a live status: the same numbers as line(), short enough to sit after "Tool step 41: coding_patch"."""
        s = self.s
        lk = s["looks"]
        parts = []
        if s["renders"]:
            seen = lk["load"] + lk["after"] + lk["reference"] + lk["sheet"]
            parts.append("%d renders, %d looks (%d after an interaction)" % (s["renders"], seen, lk["after"] + lk["sheet"]))
        if s["files"]:
            top = max(s["files"].items(), key=lambda kv: kv[1]["whole"] + kv[1]["patch"])
            parts.append("%s: %d rewrites, %d edits" % (posixpath.basename(top[0]), max(0, top[1]["whole"] - 1), top[1]["patch"]))
        return "; ".join(parts)

    def line(self):
        s = self.s
        lk = s["looks"]
        parts = []
        if s["renders"]:
            parts.append("renders %d, looks %d (after an interaction %d, sheets %d, reference %d)"
                         % (s["renders"], lk["load"] + lk["after"] + lk["reference"] + lk["sheet"], lk["after"], lk["sheet"], lk["reference"]))
        if s["files"]:
            top = max(s["files"].items(), key=lambda kv: kv[1]["whole"] + kv[1]["patch"])
            parts.append("%s: %d whole-file writes, %d edits in place" % (posixpath.basename(top[0]), top[1]["whole"], top[1]["patch"]))
        return "; ".join(parts)
