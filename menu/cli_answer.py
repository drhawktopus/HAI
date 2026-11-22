"""Where the model's answer sits in the tablet inference binaries' stdout - MEASURED, not guessed.

Source: probe orders probe-cli-output.20260927-192910 and probe-cli-multiline.20260927-193433 (full stdout and
stderr captured on real tablets; results in tabfoundry-jobs\\outbox).

llama-cli -st --no-display-prompt, stdout:
    spinner (backspaces) + banner + help block, then "\\n> " + the prompt echo, then the answer, then
    "\\n\\n[ Prompt: <n> t/s | Generation: <n> t/s ]" and "Exiting...". The echo is the WHOLE prompt followed by
    "\\n" when the prompt is at most 500 characters; otherwise its first 500 characters followed by
    " ... (truncated)\\n". Exactly one echo and one stats line per run.
llama-mtmd-cli, stdout:
    the answer only (all logging is on stderr).

Both functions return (answer, "") or (None, reason). They never guess: an unrecognised shape is an
instrument failure, reported as such, and must not be scored as a model failure.

The generators copy these function sources into each payload (a payload runs as one file on the tablet), and
check the copy equals this module - one derivation.
"""

LLAMA_CLI_ECHO_LIMIT = 500
LLAMA_CLI_TRUNCATED = " ... (truncated)\n"
LLAMA_CLI_STATS = "\n\n[ Prompt:"


def llama_cli_answer(stdout, prompt):
    end = stdout.rfind(LLAMA_CLI_STATS)
    if end < 0:
        return None, "answer_boundary:stats_line_missing"
    echo = "\n> " + prompt[:LLAMA_CLI_ECHO_LIMIT]
    n = stdout.count(echo, 0, end)
    if n != 1:
        return None, "answer_boundary:prompt_echo_found_%d_times" % n
    i = stdout.find(echo) + len(echo)
    tail = LLAMA_CLI_TRUNCATED if len(prompt) > LLAMA_CLI_ECHO_LIMIT else "\n"
    if not stdout.startswith(tail, i):
        return None, "answer_boundary:prompt_echo_end_unrecognised"
    return stdout[i + len(tail):end].strip("\n"), ""


def mtmd_answer(stdout):
    answer = stdout.strip()
    if not answer:
        return None, "answer_boundary:empty_stdout"
    return answer, ""
