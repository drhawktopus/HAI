"""Submit the paired source-quality corpus as a Fellowship map order. Runs in the ORCHESTRATOR
container (that is where `tablet_orders` lives and where the share is mounted at /downloads).

Uses `tablet_orders.submit_map` rather than hand-composing a spec: the counter validates the
payload whitelist, the item characters, MAX_ITEMS and the presence of a positive control, and
drops the order atomically. Hand-composing would skip every one of those checks -- the point of
this whole exercise is to stop building parallel mechanisms beside the factory.

The customer submits and walks away. Results arrive in the OUTBOX as *.results.json.
"""
import os
import sys

sys.path.insert(0, "/app")
import tablet_orders as to  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RUN = sys.argv[1] if len(sys.argv) > 1 else None
if not RUN:
    print("usage: submit_paired_order.py <run-id>")
    sys.exit(1)

SITE = os.environ.get("CAMERAS_SITE_URL", "")   # the cameras site (this runs in the orchestrator container)
if not SITE:
    print("set CAMERAS_SITE_URL (the cameras site)")
    sys.exit(1)
BASE = "%s/bench/%s" % (SITE.rstrip("/"), RUN)
CONTROL = "%s/CONTROL_known_cat_0953.jpg" % BASE

print("payloads available: %s" % ", ".join(to.payload_names()))
if "map_detect" not in to.payload_names():
    print("FAIL: map_detect is not whitelisted - a human must place it in payloads/")
    sys.exit(1)

# The listing is taken from the PUBLISHED directory on the web host, not from a local glob: the
# order must reference what the tablets can actually fetch, and those are different filesystems.
if len(sys.argv) < 3:
    print("FAIL: pass the published file listing (one filename per line) as argv[2]")
    sys.exit(1)
with open(sys.argv[2], encoding="utf-8") as f:
    names = [l.strip() for l in f if l.strip()]
if not names:
    print("FAIL: empty listing")
    sys.exit(1)

# DO NOT put the control in `items`. The factory appends it itself
# (`tablet_fulfil.run_order`: "items = items + [ctl_item] # the control rides along"). Listing it
# too makes expected = N+1 while distinct measured = N, and the run is VOIDed as INCOMPLETE with
# one item "unaccounted for" -- a bookkeeping ghost that looks exactly like a lost item. Cost one
# re-run.
items = ["%s/%s" % (BASE, n) for n in names if n != "CONTROL_known_cat_0953.jpg"]
print("items: %d (+1 control the factory adds itself)" % len(items))

receipt = to.submit_map(
    name="cam-paired-quality",
    payload="map_detect",
    items=items,
    control_item=CONTROL,
    # The control cell scores cat=0.953, verified 25x across all five nodes with spread 0.000.
    # 'cat=0.9' therefore demands a cat at 0.9-something: strict enough to catch a broken payload
    # or a swapped model, loose enough not to VOID on a harmless last-digit change.
    control_expect="cat=0.9",
)
print()
print(receipt)
