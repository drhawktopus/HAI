"""Is any ClaudeSupervisor run really WORKING on Kirk? /query/live lists suspended feeds too (restored for display), so
the live ids are compared with /runs/parked's suspended set. Exit 0 = nothing working (safe to recreate), 1 = working."""
import json, sys, urllib.request
import lab_settings as S
BASE = S.head_url()                       # the head's address is a setting (HAI_URL)
def get(p):
    with urllib.request.urlopen(BASE + p, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
live = get("/query/live?user=ClaudeSupervisor").get("runs", [])
susp = set(get("/runs/parked?user=ClaudeSupervisor").get("suspended", []))
ids = [r.get("cancel_id") for r in live]
working = [r for r in live if r.get("cancel_id") not in susp]
print("live:", [i[:8] for i in ids], "| suspended:", [i[:8] for i in susp])
for r in working:
    print("WORKING:", r.get("cancel_id", "")[:8], "chat", r.get("chat_id"), "|", (r.get("text") or "")[:80].replace("\n", " "))
print("verdict:", "WORKING" if working else "nothing working")
sys.exit(1 if working else 0)
