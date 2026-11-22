"""Run ON a box (ssh box "python.exe - <new script path>" < this): the "GPU Telemetry Logger" task keeps every trigger,
principal and setting it has (its own XML, exported) and runs the DEPLOYED copy of its script instead of the hand copy.
The old XML is kept beside the deployed script. Then the task is ended and run, and the action read back."""
import os, re, subprocess, sys, time
new = sys.argv[1]
assert os.path.isfile(new), "deployed copy missing: " + new
TN = "GPU Telemetry Logger"
def run(a, **kw):
    r = subprocess.run(a, capture_output=True, text=True, errors="replace", **kw)
    return r.returncode, (r.stdout + r.stderr).strip()
rc, xml = run(["schtasks", "/query", "/tn", TN, "/xml"])
assert rc == 0 and "<Arguments>" in xml, xml[-200:]
old = re.search(r'-File "([^"]+)"', xml).group(1)
if old.lower() == new.lower():
    print("already runs the deployed copy:", new)
    sys.exit(0)
stamp = time.strftime("%Y%m%d-%H%M%S")
keep = os.path.join(os.path.dirname(new), "gpu_telemetry_logger_task.pre-door-%s.xml" % stamp)
with open(keep, "w", encoding="utf-16") as f:
    f.write(xml)
new_xml = xml.replace('-File "%s"' % old, '-File "%s"' % new)
assert new_xml != xml
tmp = os.path.join(os.environ.get("TEMP", "."), "gpu_telemetry_logger_task.%s.xml" % stamp)
with open(tmp, "w", encoding="utf-16") as f:
    f.write(new_xml)
rc, out = run(["schtasks", "/create", "/f", "/tn", TN, "/xml", tmp])
print("create:", rc, out[:120])
assert rc == 0
run(["schtasks", "/end", "/tn", TN])
time.sleep(3)
rc, out = run(["schtasks", "/run", "/tn", TN])
print("run:", rc, out[:80])
time.sleep(6)
rc, q = run(["schtasks", "/query", "/tn", TN, "/v", "/fo", "LIST"])
act = [l.strip() for l in q.splitlines() if "Task To Run" in l][:1]
st = [l.strip() for l in q.splitlines() if l.strip().startswith("Status")][:1]
print("now:", act, st, "| old xml kept:", keep)
