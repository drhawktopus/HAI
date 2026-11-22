r"""Is a new lab engine revision as fast as the one before it?  Same box, same model, one variable: the engine tree.

    engine_speed_pair.py <model> <num_ctx> {ORDER_OUT} <old tag> <new tag>      e.g. ... 620d222-r4 620d222-r5

Serves each tree in turn from the lab launcher's settings on this order's own port (the lab's serve is never touched;
engine_switch_search.serve), in the order old, new, new, old so a drift over the order's minutes cannot pose as a
difference. Per serve: the model is loaded fresh, then the long read (about 25,000 tokens of prose, prompt cache off,
96 tokens written) runs three times; the first is the warm-up and is reported apart. Delivers speed_pair.json."""
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_resume_probe as E  # noqa: E402
import engine_switch_search as S  # noqa: E402


class Ask:
    temperature, seed = 0.6, 7


def main(argv):
    model, num_ctx, out, old, new = argv[0], int(argv[1]), argv[2], argv[3], argv[4]
    os.makedirs(out, exist_ok=True)
    exe, base_env = S.launcher_settings(open(S.LAUNCHER, encoding="utf-8", errors="replace").read())
    home = os.path.dirname(exe)
    stem = home[:home.rindex("-lab") + 4]                 # ...\Ollama-0.40.5-lab
    trees = {t: os.path.join(stem + t, "ollama.exe") for t in (old, new)}
    for t, p in trees.items():
        assert os.path.isfile(p), "no engine tree for %s at %s" % (t, p)
    rep = {"box": socket.gethostname(), "model": model, "num_ctx": num_ctx, "serving_tree": home, "trees": trees, "runs": []}
    E.OLLAMA = S.LAB_OLLAMA
    E.evict(model)
    E.OLLAMA = "http://127.0.0.1:%d" % S.PORT
    a = Ask()
    for i, tag in enumerate((old, new, new, old)):
        log_path = os.path.join(out, "serve-%d-%s.log" % (i, tag))
        p, log = S.serve(trees[tag], base_env, {}, log_path)
        row = {"i": i, "tag": tag}
        try:
            port, _lines = E.load(model, num_ctx)
            reads = [S.long_read(a, port) for _ in range(3)]
            row["warm_up"], row["reads"] = reads[0], reads[1:]
        except Exception as e:
            row["died"] = repr(e)[:300]
        finally:
            S.stop(p, log)
        rep["runs"].append(row)
        with open(os.path.join(out, "speed_pair.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print(tag, row.get("died") or [(round(r["read_tok_s"], 1), round(r["write_tok_s"], 1)) for r in row["reads"]], flush=True)

    def band(tag, key):
        v = [r[key] for run in rep["runs"] if run["tag"] == tag and not run.get("died") for r in run["reads"]]
        return [round(min(v), 1), round(max(v), 1), len(v)] if v else None
    rep["read_tok_s"] = {t: band(t, "read_tok_s") for t in (old, new)}
    rep["write_tok_s"] = {t: band(t, "write_tok_s") for t in (old, new)}
    rep["verdict"] = "read tok/s [min, max, n]: %s | write tok/s [min, max, n]: %s" % (rep["read_tok_s"], rep["write_tok_s"])
    with open(os.path.join(out, "speed_pair.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    print("VERDICT:", rep["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:6]))
