"""How long is a healthy A1 model turn? Reads every Inspect log under the given dirs (inside tablet-foundry) and, per
model call, its output tokens, stop reason and whether it called a tool. The A1 harness logs a placeholder `messages`;
the turns live in each sample's MODEL events. Prints the distribution for calls that acted (tool call), calls that
replied in text, and every call at or above a threshold - the evidence for a runaway-turn cap (a limit comes from a
measurement, never a round number).

    python - <glob of inspect dirs> [threshold]      (run inside tablet-foundry: inspect_ai is there)"""
import glob
import sys

from inspect_ai.log import read_eval_log

pattern = sys.argv[1]
thr = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
acted, texted, big = [], [], []
files = sorted(glob.glob(pattern + "/*.eval"))
assert files, "no .eval files under %s - the pattern is wrong, not the data" % pattern
for f in files:
    try:
        log = read_eval_log(f, resolve_attachments=False)
    except Exception as e:                                                   # noqa: BLE001
        print("skip", f, type(e).__name__)
        continue
    for s in log.samples or []:
        for e in (s.events or []):
            if getattr(e, "event", "") != "model" or not e.output or not e.output.choices:
                continue
            n = e.output.usage.output_tokens if e.output.usage else 0
            calls = getattr(e.output.choices[0].message, "tool_calls", None) or []
            (acted if calls else texted).append(n)
            if n >= thr:
                big.append((n, bool(calls), e.output.stop_reason, f.split("/")[-2][:48], s.id))


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p / 100.0 * len(v)))] if v else 0


print("logs %d | calls with a tool %d, text-only %d" % (len(files), len(acted), len(texted)))
for name, v in (("acted", acted), ("text-only", texted)):
    print("  %-9s p50 %5d  p90 %5d  p99 %5d  p99.9 %5d  max %6d" % (name, pct(v, 50), pct(v, 90), pct(v, 99),
                                                                    pct(v, 99.9), max(v) if v else 0))
print("calls >= %d tokens: %d" % (thr, len(big)))
for b in sorted(big, reverse=True)[:25]:
    print("  ", b)
