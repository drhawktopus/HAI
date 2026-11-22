"""Generate probe_cli_multiline.py: capture llama-cli's full stdout for the REAL multi-line prompts the hard payloads
send (tool: PREAMBLE.format(req) + the payload's GBNF; code: task prompt + SUFFIX), so the answer boundary is
measured on the prompt shape actually used. Prompts and grammar are copied from the payload modules with repr(),
never retyped."""
import hashlib, importlib.util, pathlib

import menu_paths as MP  # the payload folder is a setting

PAY = pathlib.Path(MP.need("PAYLOADS"))


def load(name):
    spec = importlib.util.spec_from_file_location(name[:-3], PAY / name)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tool = load("map_tool_swarm_hard.py")
code = load("map_swarm_code_hard.py")
TOOL_PROMPT = tool.PREAMBLE.format(req=tool.TASKS["update_paid"]["req"])
ctask = sorted(code.TASKS)[0]
CODE_PROMPT = code.TASKS[ctask]["prompt"] + code.SUFFIX
src = (PAY / "probe_cli_output.py").read_text(encoding="utf-8")
head, _, _ = src.partition("\ndef main():")
body = '''

TOOL_PROMPT = %r
TOOL_GBNF = %r
CODE_PROMPT = %r
CODE_MODEL = "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M"


def probe_cli(prompt, model, grammar):
    need = [os.path.join(LLM, "llama-cli"), os.path.join(LLM, model + ".gguf")]
    missing = [p for p in need if not os.path.exists(p)]
    if missing:
        return {"ran": False, "reason": "missing %%s" %% missing}
    pp = os.path.join(LLM, "_probe_prompt.txt")
    with open(pp, "w", encoding="utf-8") as f:
        f.write(prompt)
    argv = [os.path.join(LLM, "llama-cli"), "-m", os.path.join(LLM, model + ".gguf"), "-f", pp]
    if grammar:
        gp = os.path.join(LLM, "_probe.gbnf")
        with open(gp, "w", encoding="utf-8") as f:
            f.write(grammar)
        argv += ["--grammar-file", gp]
    argv += ["-st", "--no-display-prompt", "--temp", "0.6", "--seed", "1", "-n", "-1", "-c", "2048",
             "-t", str(min(os.cpu_count() or 4, 8))]
    return dict(run(argv, LLM, dict(os.environ, LD_LIBRARY_PATH=LLM)), ran=True, prompt=prompt)


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
            elif it.startswith("probe~toolml"):
                row.update(probe_cli(TOOL_PROMPT, TOOL_MODEL, TOOL_GBNF)); row["ok"] = bool(row.get("ran"))
            elif it.startswith("probe~codeml"):
                row.update(probe_cli(CODE_PROMPT, CODE_MODEL, None)); row["ok"] = bool(row.get("ran"))
            else:
                row["reason"] = "unknown-item"
        except Exception as e:                      # noqa: BLE001
            row["reason"] = "payload-error:%%s:%%s" %% (type(e).__name__, e)
        rows.append(row)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(rows, f)


if __name__ == "__main__":
    main()
''' % (TOOL_PROMPT, tool.GBNF, CODE_PROMPT)
out = head + body
dst = PAY / "probe_cli_multiline.py"
dst.write_text(out, encoding="utf-8", newline="\n")
m = load("probe_cli_multiline.py")
assert m.TOOL_PROMPT == TOOL_PROMPT and m.TOOL_GBNF == tool.GBNF and m.CODE_PROMPT == CODE_PROMPT
print("wrote", dst.name, hashlib.sha256(out.encode()).hexdigest()[:16], "| tool prompt lines", TOOL_PROMPT.count("\n") + 1,
      "| code task", ctask, "lines", CODE_PROMPT.count("\n") + 1)
