r"""Does a new lab engine revision hold more of the card than the one before it?  One variable: the engine tree.

    engine_memory_pair.py <model> <num_ctx> {ORDER_OUT} <old tag> <new tag>

Serves each tree in turn (old, new, new, old) from the lab launcher's settings on this order's own port, loads the model
at the given window, lets one short answer run, and reads Windows' own per-process GPU counters: the runner's dedicated
and shared memory, and what every OTHER process holds at that moment. Delivers memory_pair.json."""
import json
import os
import re
import socket
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_resume_probe as E  # noqa: E402
import engine_history_probe as H  # noqa: E402
import engine_switch_search as S  # noqa: E402

PS = ("$d=(Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples;"
      "$s=(Get-Counter '\\GPU Process Memory(*)\\Shared Usage' -ErrorAction SilentlyContinue).CounterSamples;"
      "foreach($x in $d){'D|'+$x.InstanceName+'|'+[int64]$x.CookedValue};foreach($x in $s){'S|'+$x.InstanceName+'|'+[int64]$x.CookedValue}")


def card():
    out = subprocess.run(["powershell", "-NoProfile", "-Command", PS], capture_output=True, text=True, timeout=60).stdout
    ded, sh = {}, {}
    for line in out.splitlines():
        p = line.split("|")
        m = re.search(r"pid_(\d+)", p[1]) if len(p) == 3 else None
        if m:
            tgt = ded if p[0] == "D" else sh
            tgt[int(m.group(1))] = tgt.get(int(m.group(1)), 0) + int(p[2])
    names = {}
    for line in subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=30).stdout.splitlines():
        q = [x.strip('"') for x in line.split('","')]
        if len(q) > 1 and q[1].isdigit():
            names[int(q[1])] = q[0]
    mb = lambda v: v // 2 ** 20
    runner = [k for k in ded if names.get(k, "").lower() == "llama-server.exe"]
    return {"runner_dedicated_mb": mb(sum(ded.get(k, 0) for k in runner)), "runner_shared_mb": mb(sum(sh.get(k, 0) for k in runner)),
            "others_dedicated_mb": mb(sum(v for k, v in ded.items() if k not in runner)),
            "others_top": sorted(((names.get(k, "?"), mb(v)) for k, v in ded.items() if k not in runner), key=lambda x: -x[1])[:5],
            "runners": len(runner)}


class Ask:
    n, temperature, seed, probs = 8, 0.6, 7, 0


def main(argv):
    model, num_ctx, out, old, new = argv[0], int(argv[1]), argv[2], argv[3], argv[4]
    os.makedirs(out, exist_ok=True)
    exe, base_env = S.launcher_settings(open(S.LAUNCHER, encoding="utf-8", errors="replace").read())
    home = os.path.dirname(exe)
    stem = home[:home.rindex("-lab") + 4]
    trees = {t: os.path.join(stem + t, "ollama.exe") for t in (old, new)}
    for t, p in trees.items():
        assert os.path.isfile(p), "no engine tree for %s at %s" % (t, p)
    rep = {"box": socket.gethostname(), "model": model, "num_ctx": num_ctx, "trees": trees, "runs": []}
    E.OLLAMA = S.LAB_OLLAMA
    E.evict(model)
    E.OLLAMA = "http://127.0.0.1:%d" % S.PORT
    rep["card_before_any_load"] = card()
    for i, tag in enumerate((old, new, new, old)):
        p, log = S.serve(trees[tag], base_env, {}, os.path.join(out, "serve-%d-%s.log" % (i, tag)))
        row = {"i": i, "tag": tag}
        try:
            port, _lines = E.load(model, num_ctx)
            P = E.post("http://127.0.0.1:%d/tokenize" % port, {"content": H.P_TEXT, "add_special": True})["tokens"]
            H.ask(port, P, Ask())
            row.update(card())
        except Exception as e:
            row["died"] = repr(e)[:300]
        finally:
            S.stop(p, log)
        rep["runs"].append(row)
        with open(os.path.join(out, "memory_pair.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print(tag, {k: row.get(k) for k in ("runner_dedicated_mb", "runner_shared_mb", "others_dedicated_mb", "died")}, flush=True)
    rep["verdict"] = "runner dedicated+shared MB per run (old, new, new, old): %s | others dedicated: %s | before any load: %s" % (
        [(r.get("runner_dedicated_mb"), r.get("runner_shared_mb")) for r in rep["runs"]],
        [r.get("others_dedicated_mb") for r in rep["runs"]], rep["card_before_any_load"])
    with open(os.path.join(out, "memory_pair.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    print("VERDICT:", rep["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:6]))
