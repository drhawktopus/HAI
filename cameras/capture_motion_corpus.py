"""Collect a REAL corpus: grab a snapshot every time the NVR reports motion.

Why: measuring a classical person-filter needs frames the cameras actually produce -- at their
angles, their lighting, their night-vision mode -- not two hand-picked stills. This subscribes to
the NVR's own event stream (already proven working) and pulls snapshot.cgi on each VideoMotion
Start, tagging the channel and time.

Deliberately does NOT judge anything. It only collects, so that labelling stays the owner's eye and the
filter can be scored against ground truth rather than against my own guess.

Read-only against the NVR: eventManager attach + snapshot.cgi. Writes only into the corpus dir.
"""
import os
import re
import sys
import time
import urllib.request

from nvr_settings import HOST, login
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")
MAX_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
COOLDOWN = 8          # per-channel seconds; motion re-fires constantly on a busy room
CHANNEL_NAMES = {1: "livingroom", 2: "frontdoor"}

USER, PW = login()
mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
mgr.add_password(None, "http://%s/" % HOST, USER, PW)
opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))
os.makedirs(OUT, exist_ok=True)


def say(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m))
    sys.stdout.flush()


def grab(channel):
    data = opener.open(
        "http://%s/cgi-bin/snapshot.cgi?channel=%d" % (HOST, channel), timeout=20).read()
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("not a JPEG (%d bytes)" % len(data))
    name = "%s_ch%d_%s.jpg" % (time.strftime("%Y%m%d-%H%M%S"), channel,
                               CHANNEL_NAMES.get(channel, "cam"))
    path = os.path.join(OUT, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path, len(data)


url = ("http://%s/cgi-bin/eventManager.cgi?action=attach&codes=%%5BAll%%5D&heartbeat=5" % HOST)
say("connecting to event stream ...")
stream = opener.open(url, timeout=20)
say("HTTP %s -- stream OPEN. Collecting for %d s." % (stream.status, MAX_SECONDS))

start = time.time()
last = {}
grabbed = 0
beats = 0

while time.time() - start < MAX_SECONDS:
    try:
        raw = stream.readline()
    except Exception as e:
        say("stream read error: %r" % e)
        break
    if not raw:
        say("stream closed by NVR")
        break
    line = raw.decode("utf-8", "replace").strip()
    if not line:
        continue
    if "heartbeat" in line.lower():
        beats += 1
        continue

    m = re.match(r"Code=(\w+);action=(\w+)(?:;index=(\d+))?", line)
    if not m or m.group(2) != "Start":
        continue
    code, idx = m.group(1), m.group(3)
    # event index is 0-based; snapshot.cgi / ChannelTitle are 1-based
    ch = int(idx) + 1 if idx is not None else 1
    now = time.time()
    if now - last.get(ch, 0) < COOLDOWN:
        continue
    last[ch] = now
    try:
        path, size = grab(ch)
        grabbed += 1
        say("%s ch%d -> %s (%d bytes)  [%d total]"
            % (code, ch, os.path.basename(path), size, grabbed))
    except Exception as e:
        say("grab failed ch%d: %r" % (ch, e))

say("")
say("=" * 56)
if beats == 0 and grabbed == 0:
    say("VERDICT: INCONCLUSIVE -- no heartbeat and no events. Cannot tell 'quiet house' "
        "from 'stream inert'.")
else:
    say("VERDICT: collected %d snapshots over %d s (heartbeats=%d, so the stream was live)."
        % (grabbed, int(time.time() - start), beats))
    say("corpus: %s" % OUT)
