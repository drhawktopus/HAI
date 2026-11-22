r"""Factory status board: each component declares its read sources and their freshness.

Design constraints:
- One file per component to avoid write races between independent processes.
- Atomic writes (tmp + os.replace) prevent corruption.
- `age_s` is computed at publish time; do not rely on filesystem mtimes or implied freshness.
- `ok` reflects a real freshness verdict. Unverifiable sources return False with a reason.
- Fail-soft: publishing errors are wrapped and suppressed so the board never breaks its publishers.

Imported by publishers (foundry, stall daemon, render worker, telemetry logger).
Read by consumers needing a system health picture (homepage, fleet push, operators).
"""
import datetime
import json
import os

# Derive the root from foundry_paths, never a literal: factories differ by drive letter
# (Softserve is E:, not F:), and publish() fails soft, so a hardcoded path becomes a silent
# no-op on the mismatched box. The literal fallback exists only so import works outside a factory.
try:
    import foundry_paths as _P
    STATUS_DIR = os.path.join(_P.STATE_DIR, "status")
except Exception:
    STATUS_DIR = os.path.join(os.environ.get("FOUNDRY_ROOT",
                                             os.path.join("F:", os.sep, "foundry")),
                              "var", "state", "status")


def source(name, path, stale_after=120.0, note="", absent_ok=False):
    """Describe ONE input a component reads. Returns a dict for `publish(sources=[...])`.

    `stale_after` is per-source because acceptable idle times vary: telemetry
    ticks every ~11 s, while render progress files may be quiet for minutes
    during model loading.

    `absent_ok` indicates the input only exists during active processing.
    If True, absence does not mark the source as failed.
    """
    d = {"name": name, "path": path, "age_s": None, "ok": False, "note": note,
         "absent_ok": bool(absent_ok)}
    try:
        if not os.path.exists(path):
            d["ok"] = bool(absent_ok)
            d["note"] = (note + (" | absent (normal between activity)" if absent_ok
                                 else " | ABSENT")).strip(" |")
            return d
        age = max(0.0, __import__("time").time() - os.path.getmtime(path))
        d["age_s"] = round(age, 1)
        d["ok"] = age <= stale_after
        if not d["ok"]:
            d["note"] = (note + " | STALE > %.0fs" % stale_after).strip(" |")
    except OSError as e:
        d["note"] = (note + " | UNREADABLE %r" % (e,)).strip(" |")
    return d


def publish(component, state, sources=None, notes="", extra=None):
    """Write this component's status card. Never raises."""
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        srcs = list(sources or [])
        payload = {
            "component": component,
            "pid": os.getpid(),
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "state": state,
            "sources": srcs,
            # the one-glance verdict: is every input this component depends on actually good?
            "sources_ok": all(s.get("ok") for s in srcs) if srcs else None,
            "notes": notes,
        }
        if extra:
            payload.update(extra)
        path = os.path.join(STATUS_DIR, "%s.json" % component)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        os.replace(tmp, path)
        return payload
    except Exception:
        return None


def read_all(stale_after=180.0):
    """Every component's card, plus a freshness verdict on the CARD ITSELF.

    A component that stopped publishing is exactly the "worker is blind" case, so a stale card is
    reported as such rather than served as current — the same rule the dashboard needed when it
    showed a three-day-old run as live.
    """
    out = {}
    try:
        import time
        now = time.time()
        for fn in sorted(os.listdir(STATUS_DIR)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(STATUS_DIR, fn)
            try:
                with open(p, encoding="utf-8") as f:
                    rec = json.load(f)
                rec["card_age_s"] = round(max(0.0, now - os.path.getmtime(p)), 1)
                rec["reporting"] = rec["card_age_s"] <= stale_after
                out[rec.get("component", fn[:-5])] = rec
            except Exception as e:
                out[fn[:-5]] = {"component": fn[:-5], "error": repr(e), "reporting": False}
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return out


def summary_line(cards=None):
    """One human line for a log or a DM: who is reporting, and whose inputs are bad."""
    cards = read_all() if cards is None else cards
    if not cards:
        return "factory status: no components reporting"
    bits = []
    for name, c in sorted(cards.items()):
        if not c.get("reporting"):
            bits.append("%s=SILENT(%ss)" % (name, c.get("card_age_s", "?")))
        elif c.get("sources_ok") is False:
            bad = [s["name"] for s in c.get("sources", []) if not s.get("ok")]
            bits.append("%s=BAD-INPUT(%s)" % (name, ",".join(bad)))
        else:
            bits.append("%s=%s" % (name, c.get("state", "?")))
    return "factory status: " + " · ".join(bits)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps(read_all(), indent=1))
    else:
        print(summary_line())
        for name, c in sorted(read_all().items()):
            print("\n[%s] %s  pid=%s  card_age=%ss  reporting=%s"
                  % (name, c.get("state"), c.get("pid"), c.get("card_age_s"), c.get("reporting")))
            for s in c.get("sources", []):
                print("    %-18s age=%-7s ok=%-5s %s\n        %s"
                      % (s.get("name"), s.get("age_s"), s.get("ok"), s.get("note", ""), s.get("path")))
