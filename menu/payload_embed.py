"""Copy cli_answer's constants and functions into a generated payload (a payload runs as one file on a tablet),
and prove the copy is the same derivation as the module."""
import inspect

import cli_answer

MARK = "# ---------------------- Answer boundary (generated from cli_answer.py - measured; see its docstring)\n"


def block():
    consts = "".join("%s = %r\n" % (k, getattr(cli_answer, k))
                     for k in ("LLAMA_CLI_ECHO_LIMIT", "LLAMA_CLI_TRUNCATED", "LLAMA_CLI_STATS"))
    funcs = "\n\n".join(inspect.getsource(f) for f in (cli_answer.llama_cli_answer, cli_answer.mtmd_answer))
    return "\n" + MARK + consts + "\n\n" + funcs + "\n\n"


def embed(src, anchor):
    assert src.count(anchor) == 1, "anchor %r found %d times" % (anchor, src.count(anchor))
    assert MARK not in src, "already embedded"
    i = src.index(anchor)
    return src[:i] + block() + src[i:]


def check(mod):
    """The payload's copies must answer exactly like the module on the real captures and on a refusal."""
    import test_cli_answer as T
    short = next(x for x in T.rows("probe-cli-output.20260927-192910") if x["item"].startswith("probe~tool"))
    long_ = next(x for x in T.rows("probe-cli-multiline.20260927-193433") if x["item"].startswith("probe~toolml"))
    vlm = next(x for x in T.rows("probe-cli-output.20260927-192910") if x["item"].startswith("probe~vlm"))
    cases = [(short["stdout"], T.SHORT_PROMPT), (long_["stdout"], long_["prompt"]), (long_["stdout"], "nope")]
    for out, p in cases:
        assert mod.llama_cli_answer(out, p) == cli_answer.llama_cli_answer(out, p), p[:40]
    assert mod.mtmd_answer(vlm["stdout"]) == cli_answer.mtmd_answer(vlm["stdout"]) == ("42.", "")
    return len(cases) + 1
