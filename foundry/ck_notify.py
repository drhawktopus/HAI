"""CorridorKey notifier.

Configuration is read from notify.json (dm_token, user_id, dm_base, dm_host, webhook_url).
The dm_token and user_id must match values in dm_relay.env (OVERNIGHT_DM_*).
The relay address is not in this file: it comes from the foundry settings (OVERNIGHT_DM_URL
and OVERNIGHT_DM_HOST), or notify.json if it names one. With no relay configured, nothing is sent.

Network constraints:
- Attachments use /dm-file with an 8MB limit.
- The relay's own port may be unpublished; a reverse proxy may front it.

Behavior:
- Uses only stdlib and curl.exe.
- All notifications are prefixed with the local hostname.
- Notification failures are logged but never raise exceptions (fail-closed).
- Watch mode tails telemetry/STATUS.json, notifying on stage changes or 25% progress increments,
  and exits if status is stale for --stale-min minutes."""
import argparse, json, os, socket, subprocess, sys, time, urllib.request

CK_DIR = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(CK_DIR, "notify.json")
STATUS = os.path.join(CK_DIR, "telemetry", "STATUS.json")
HOST = socket.gethostname()

try:
    import foundry_paths as _P          # the workspace's ONE settings reader
except ImportError:
    _P = None


def _dm_base(c):
    """The DM relay's base URL: settings first, notify.json next, nothing at all if unlinked."""
    return c.get("dm_base") or (_P.setting("OVERNIGHT_DM_URL", "") if _P else "")


def _dm_host(c):
    return c.get("dm_host") or (_P.setting("OVERNIGHT_DM_HOST", "") if _P else "")


def _conf():
    with open(CONF) as f:
        return json.load(f)


def _dm_text(c, msg):
    base = _dm_base(c)
    if not base:
        return False
    req = urllib.request.Request(
        base.rstrip("/") + "/dm",
        data=json.dumps({"user_id": c["user_id"], "message": msg}).encode(),
        headers={"Content-Type": "application/json", "X-DM-Token": c["dm_token"],
                 "Host": _dm_host(c)})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("ok", False)


def _dm_file(c, msg, filepath):
    base = _dm_base(c)
    if not base:
        return False
    r = subprocess.run(
        ["curl.exe", "-s", "-H", "Host: " + _dm_host(c),
         "-H", "X-DM-Token: " + c["dm_token"],
         "-F", "user_id=" + c["user_id"], "-F", "message=" + msg,
         "-F", "file=@" + filepath,
         base.rstrip("/") + "/dm-file"],
        capture_output=True, text=True, timeout=120)
    try:
        return json.loads(r.stdout).get("ok", False)
    except Exception:
        return False


def _webhook(c, msg):
    if not c.get("webhook_url"):
        return False
    req = urllib.request.Request(
        c["webhook_url"], data=json.dumps({"content": msg}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status in (200, 204)


def post(content, filepath=None):
    msg = "[%s] %s" % (HOST, content)
    try:
        c = _conf()
        ok = _dm_file(c, msg, filepath) if filepath else _dm_text(c, msg)
        if not ok:
            ok = _webhook(c, msg + " (DM failed)")
        return ok
    except Exception as e:
        print("notify FAILED: %r" % e, file=sys.stderr)
        return False


def watch(interval, stale_min):
    """Poll STATUS.json; DM stage changes + 25% progress marks; exit on staleness."""
    last_stage, last_quarter = None, -1
    post("watch: started (interval %ss, stale-exit %sm)" % (interval, stale_min))
    while True:
        try:
            age = time.time() - os.path.getmtime(STATUS)
            if age > stale_min * 60:
                post("watch: STATUS stale %dm — run finished or died; watcher exiting." % (age // 60))
                return
            with open(STATUS) as f:
                s = json.load(f)
            stage, frame, total = s.get("stage"), s.get("frame", 0), s.get("total", 0)
            quarter = int(4 * frame / total) if total else 0
            if stage != last_stage:
                post("stage → %s (%s frames)" % (stage, total))
                last_stage, last_quarter = stage, 0
            elif quarter > last_quarter and quarter < 4:
                post("%s %d/%d (%d%%)  %.2f s/f  ETA %dm" % (
                    stage, frame, total, 100 * frame // max(total, 1),
                    s.get("spf", 0), s.get("eta_secs", 0) // 60))
                last_quarter = quarter
        except FileNotFoundError:
            pass  # run not started yet — keep waiting until the stale window passes
        except Exception as e:
            print("watch glitch: %r" % e, file=sys.stderr)
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--stale-min", type=int, default=15)
    a = ap.parse_args()
    if a.watch:
        watch(a.interval, a.stale_min)
    elif a.message:
        sys.exit(0 if post(a.message, a.file) else 1)
    else:
        ap.error("need a message or --watch")


if __name__ == "__main__":
    main()
