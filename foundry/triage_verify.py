"""triage_verify -- the library VERIFY lane's instrument.

Reads a typed-claims file from the share, answers ONLY the rows addressed to this box, and
ships verdicts back to the share. The verdict grades the DOC LINE, not the resource:

    CURRENT      the line is still true (path exists / port answers / task present)
    STALE        the line is no longer true (path absent / task gone)
    UNREACHABLE  the target did not answer -- could be down, could be stale; human decides
    CANNOT-TELL  the instrument could not measure -- never rendered as any definite state
    ERROR        a row this payload has no handler for, named per-row, never silent

READ-ONLY BY CONSTRUCTION -- this payload runs under the lab's blanket read-only probe
class, which requires it to be provable: the only subprocess invocations are
`schtasks /query` and `tasklist` (both read-only queries), the only file written is the
verdicts deliverable, and there is no kill, no config write, no state mutation anywhere.
test_triage_verify.py pins this behaviourally (subprocess argv allowlist +
only-artifact-created sweep), not by grepping source.

Invocation (compute order): python triage_verify.py <claims.json> <out.json> <box> [jobname]
Claims rows not addressed to <box> (and all `judgment` rows) are skipped and counted.
Zero rows addressed to this box exits rc 2 -- a zero-match join is a broken join, not a
clean sweep (every box always has gold rows, so zero-mine can only mean breakage).

Gold rows (id `gold-*`) are positive/negative controls seeded in the claims file; this
payload treats them exactly like real rows and never reads their `expect` field -- grading
is the reviewer's job, so the instrument cannot teach itself the answers.
"""
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

VERSION = "verify-v1 2026-08-22"

# An embedded-python interpreter (._pth beside the exe) builds sys.path without the script
# dir, so sibling imports need this insert; PYTHONPATH is ignored in that mode.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import p20_status  # beside this file in bin; source() is the path primitive
except Exception:      # noqa: BLE001 -- payload must still run if bin layout shifts
    p20_status = None


def _log(msg):
    print("[verify] %s" % msg, flush=True)


# ---------------------------------------------------------------- handlers ----
def h_port_open(params):
    ip, port = str(params.get("ip") or ""), params.get("port")
    if not ip or not isinstance(port, int):
        return "CANNOT-TELL", "malformed params %r" % (params,)
    t0 = time.time()
    try:
        with socket.create_connection((ip, port), timeout=5):
            return "CURRENT", "tcp connect ok in %d ms" % int((time.time() - t0) * 1000)
    except OSError as e:
        return "UNREACHABLE", "tcp connect failed after %d ms: %r" % (
            int((time.time() - t0) * 1000), e)


def h_http_reach(params):
    url = str(params.get("url") or "")
    if not url.startswith("http"):
        return "CANNOT-TELL", "malformed params %r" % (params,)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read(4096)
            return "CURRENT", "HTTP %s, %d bytes, %d ms" % (
                r.status, len(body), int((time.time() - t0) * 1000))
    except urllib.error.HTTPError as e:
        # the service ANSWERED -- a 4xx is still a live endpoint; 5xx is a live-but-sick one
        v = "CURRENT" if e.code < 500 else "UNREACHABLE"
        return v, "HTTP %d after %d ms" % (e.code, int((time.time() - t0) * 1000))
    except OSError as e:
        return "UNREACHABLE", "no answer after %d ms: %r" % (
            int((time.time() - t0) * 1000), e)


def h_path_fresh(params):
    path = str(params.get("path") or "")
    if not path:
        return "CANNOT-TELL", "malformed params %r" % (params,)
    if p20_status is not None:
        s = p20_status.source("verify", path, stale_after=10 ** 9)
        note = s.get("note") or ""
        if "UNREADABLE" in note:
            return "CANNOT-TELL", note
        if "ABSENT" in note:
            return "STALE", "path absent"
        return "CURRENT", "exists, mtime age %.1f days" % ((s.get("age_s") or 0) / 86400.0)
    try:
        st = os.stat(path)
        return "CURRENT", "exists, mtime age %.1f days" % (
            (time.time() - st.st_mtime) / 86400.0)
    except FileNotFoundError:
        return "STALE", "path absent"
    except OSError as e:
        return "CANNOT-TELL", "unreadable: %r" % (e,)


def h_task_exists(params):
    task = str(params.get("task") or "")
    if not task:
        return "CANNOT-TELL", "malformed params %r" % (params,)
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", task],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "CANNOT-TELL", "schtasks query failed: %r" % (e,)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    if r.returncode == 0:
        status = ""
        for ln in out.splitlines():
            if task in ln:
                status = " ".join(ln.split()[-2:])
        return "CURRENT", ("task present, tail: %s" % status).strip()
    if "ERROR:" in out and ("cannot find" in out.lower() or "does not exist" in out.lower()):
        return "STALE", "task not found (rc %d)" % r.returncode
    return "CANNOT-TELL", "schtasks rc %d: %s" % (r.returncode, out.strip()[:200])


def h_proc_running(params):
    image = str(params.get("image") or "")
    if not image:
        return "CANNOT-TELL", "malformed params %r" % (params,)
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % image, "/FO", "CSV",
                            "/NH"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "CANNOT-TELL", "tasklist failed: %r" % (e,)
    out = r.stdout.decode("utf-8", "replace")
    pids = []
    for ln in out.splitlines():
        cells = [c.strip('"') for c in ln.split('","')]
        if len(cells) >= 2 and cells[0].lower() == image.lower():
            try:
                # this payload's own interpreter must never count as evidence that the
                # claimed process runs
                if int(cells[1]) != os.getpid():
                    pids.append(cells[1])
            except ValueError:
                pass
    if pids:
        return "CURRENT", "%d instance(s), pid %s" % (len(pids), ",".join(pids[:5]))
    return "STALE", "no %s process (observer pid %d excluded)" % (image, os.getpid())


HANDLERS = {"port_open": h_port_open, "http_reach": h_http_reach,
            "path_fresh": h_path_fresh, "task_exists": h_task_exists,
            "proc_running": h_proc_running}


# ------------------------------------------------------------------- main ----
def run(claims_path, out_path, box):
    data = json.load(io.open(claims_path, encoding="utf-8"))
    rows = data.get("claims") or []
    mine, judgment, other = [], 0, 0
    for c in rows:
        if c.get("type") == "judgment":
            judgment += 1
        elif str(c.get("target_box") or "").lower() in (box, "any"):
            mine.append(c)
        else:
            other += 1
    _log("claims %d total: %d mine (%s), %d judgment, %d other-box"
         % (len(rows), len(mine), box, judgment, other))
    if not mine:
        _log("ZERO rows addressed to %r - a zero-match join is a broken join, not a clean "
             "sweep (gold rows exist for every box)" % box)
        return 2

    cache, verdicts = {}, []
    for c in mine:
        h = HANDLERS.get(str(c.get("type") or ""))
        if h is None:
            verdicts.append({"id": c.get("id"), "type": c.get("type"),
                             "verdict": "ERROR",
                             "evidence": "unknown claim type %r - no handler; named, never "
                                         "silent" % c.get("type"),
                             "doc": c.get("doc"), "lines": c.get("lines"),
                             "claim_text": c.get("claim_text"), "params": c.get("params")})
            _log("%s ERROR unknown type %r" % (c.get("id"), c.get("type")))
            continue
        key = (c["type"], json.dumps(c.get("params") or {}, sort_keys=True))
        if key in cache:
            v, ev = cache[key]
            ev = ev + " (cached)"
        else:
            v, ev = h(c.get("params") or {})
            cache[key] = (v, ev)
        verdicts.append({"id": c.get("id"), "type": c["type"], "verdict": v,
                         "evidence": ev, "doc": c.get("doc"), "lines": c.get("lines"),
                         "claim_text": c.get("claim_text"), "params": c.get("params")})
        _log("%s %s %s | %s" % (c.get("id"), c["type"], v, ev))

    counts = {}
    for v in verdicts:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    payload = {"version": VERSION, "box": box, "claims_file": claims_path,
               "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "rows_total": len(rows), "rows_mine": len(mine),
               "rows_judgment": judgment, "rows_other_box": other,
               "verdict_counts": counts, "verdicts": verdicts}
    tmp = out_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, out_path)
    _log("shipped %s: %s" % (out_path, counts))
    return 0


def main(argv):
    args = [a for a in argv[1:]]
    # the runner may append --items-file <p> and always appends the job name; strip both
    if "--items-file" in args:
        i = args.index("--items-file")
        del args[i:i + 2]
    if len(args) < 3:
        _log("usage: triage_verify.py <claims.json> <out.json> <box> [jobname]")
        return 2
    claims_path, out_path, box = args[0], args[1], args[2].lower()
    _log("%s | box=%s" % (VERSION, box))
    return run(claims_path, out_path, box)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
