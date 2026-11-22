"""Move the orchestrator gate's pinned test count. argv: <old> <new> <note>.
The note is one plain sentence saying what the new tests pin. It goes to the pin's log beside the deploy logs, never
into the pin file: the pin file states the count and nothing else."""
import datetime
import io
import os
import re
import sys

old, new, note = sys.argv[1], sys.argv[2], sys.argv[3]
import lab_settings as S  # noqa: E402  the head mirror is CODE_MIRRORS.orchestrator
p = os.path.join((S.setting_json("CODE_MIRRORS", {}) or {}).get("orchestrator", ""), "tests", "gate_pin.py")
if not os.path.isfile(p):
    raise SystemExit("bump_pin: no gate_pin.py at %s (CODE_MIRRORS.orchestrator)" % p)
s = io.open(p, encoding="utf-8", newline="").read()
pat = re.compile(r"^EXPECT_TESTS = %s\b.*$" % re.escape(old), re.M)
assert len(pat.findall(s)) == 1, "the pin is not at %s" % old
io.open(p, "w", encoding="utf-8", newline="").write(pat.sub("EXPECT_TESTS = %s" % new, s))
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy-logs", "gate_pin_history.log")
os.makedirs(os.path.dirname(log), exist_ok=True)
with io.open(log, "a", encoding="utf-8") as f:
    f.write("%s  %s -> %s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), old, new, note))
print("pin %s -> %s" % (old, new))
