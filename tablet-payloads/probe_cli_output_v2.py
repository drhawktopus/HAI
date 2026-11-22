"""Measurement payload: capture the COMPLETE stdout and stderr of the two inference binaries the Hard Bench
payloads parse, so the answer boundary is read from real bytes instead of guessed.

Items:  probe~tool   -> llama-cli with the exact argv shape map_tool_swarm*.py uses (grammar, -st, --no-display-prompt)
        probe~vlm    -> llama-mtmd-cli with the exact command shape map_vlm_swarm*.py uses (n_number.png)
        control      -> emits PROBE_CONTROL_PASS (no inference)
Each row ships stdout/stderr in full as text (errors="replace") plus a repr of the first and last 300 characters
so control characters (spinner backspaces) are visible. Nothing is scored. Runs only with files already staged
on the node; a missing file is recorded, never fetched.
"""
import json
import os
import subprocess
import sys
import time

LLM = os.path.expanduser("~/llm")
TOOL_MODEL = "Qwen2.5-1.5B-Instruct-Q4_K_M"
VLM_MODEL = "SmolVLM2-500M-Video-Instruct-Q8_0"
VLM_MMPROJ = "mmproj-SmolVLM2-500M-Video-Instruct-Q8_0"
GBNF = 'root ::= "{" ws "\\"tool\\"" ws ":" ws "\\"restart_service\\"" ws "," ws "\\"arguments\\"" ws ":" ws "{" ws "\\"service\\"" ws ":" ws "\\"redis\\"" ws "}" ws "}"\nws ::= [ \\t\\n]*\n'
PROMPT = 'Restart the redis service. Respond with ONLY a JSON object {"tool": "<name>", "arguments": {...}}.'


def run(argv, cwd, env=None, timeout=400):
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, cwd=cwd, env=env, timeout=timeout)
        out, err, rc = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), r.returncode
    except subprocess.TimeoutExpired:
        out, err, rc = "", "", "timeout"
    return {"rc": rc, "wall_s": round(time.time() - t0, 1), "stdout": out, "stderr": err,
            "stdout_len": len(out), "stderr_len": len(err),
            "stdout_head_repr": repr(out[:300]), "stdout_tail_repr": repr(out[-300:]),
            "stderr_tail_repr": repr(err[-300:])}


def probe_tool():
    need = [os.path.join(LLM, "llama-cli"), os.path.join(LLM, TOOL_MODEL + ".gguf")]
    missing = [p for p in need if not os.path.exists(p)]
    if missing:
        return {"ran": False, "reason": "missing %s" % missing}
    with open(os.path.join(LLM, "_probe_prompt.txt"), "w", encoding="utf-8") as f:
        f.write(PROMPT)
    with open(os.path.join(LLM, "_probe.gbnf"), "w", encoding="utf-8") as f:
        f.write(GBNF)
    env = dict(os.environ, LD_LIBRARY_PATH=LLM)
    argv = [os.path.join(LLM, "llama-cli"), "-m", os.path.join(LLM, TOOL_MODEL + ".gguf"), "-f",
            os.path.join(LLM, "_probe_prompt.txt"), "--grammar-file", os.path.join(LLM, "_probe.gbnf"),
            "-st", "--no-display-prompt", "--temp", "0.6", "--seed", "1", "-n", "-1", "-c", "2048",
            "-t", str(min(os.cpu_count() or 4, 8))]
    return dict(run(argv, LLM, env), ran=True, argv=argv)


def probe_vlm():
    img = os.path.expanduser("~/vlm_set/n_number.png")
    need = [os.path.join(LLM, "llama-mtmd-cli"), os.path.join(LLM, VLM_MODEL + ".gguf"),
            os.path.join(LLM, VLM_MMPROJ + ".gguf"), img]
    missing = [p for p in need if not os.path.exists(p)]
    if missing:
        return {"ran": False, "reason": "missing %s" % missing}
    cmd = ("cd %s && LD_LIBRARY_PATH=. ./llama-mtmd-cli -m %s -mm %s --image %s -p %s -n 32 --temp 0.6 --seed 1 -c 2048 -t 8"
           % (LLM, os.path.join(LLM, VLM_MODEL + ".gguf"), os.path.join(LLM, VLM_MMPROJ + ".gguf"), img,
              "'What number is shown? Answer with only the number.'"))
    return dict(run(["sh", "-c", cmd], LLM), ran=True, cmd=cmd)


def main():
    items_file, out_file = sys.argv[1], sys.argv[2]
    with open(items_file, encoding="utf-8") as f:
        items = [l.strip() for l in f if l.strip()]
    rows = []
    for it in items:
        row = {"item": it, "ok": False}
        try:
            if it == "control":
                row.update({"ok": True, "control_result": "PROBE_CONTROL_PASS"})
            elif it.startswith("probe~tool"):
                row.update(probe_tool()); row["ok"] = bool(row.get("ran"))
            elif it.startswith("probe~vlm"):
                row.update(probe_vlm()); row["ok"] = bool(row.get("ran"))
            else:
                row["reason"] = "unknown-item"
        except Exception as e:                      # noqa: BLE001
            row["reason"] = "payload-error:%s:%s" % (type(e).__name__, e)
        rows.append(row)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main()
