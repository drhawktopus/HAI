"""Live acceptance check for the "lab" provider, the same four questions the stock ollama provider failed or passed
(34-A1 spec § "Inspect AI evaluated hands-on"), each answered by measurement on this box's ollama:
  1. the context the plan asks for is the context the model loads at   (ollama /api/ps context_length)
  2. think=off really has no reasoning, think=on really has some       (the logged assistant message)
  3. a custom tool is called natively and its result reaches the model (the logged transcript)
  4. a provider without num_ctx / think refuses                        (no silent defaults)
Usage: <inspect venv python> probe_lab.py <ollama tag> <num_ctx>   (FOUNDRY_ROOT must point at this box's foundry)"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab_provider  # noqa: E402,F401  (registers "lab")
from inspect_ai import Task, eval as inspect_eval, task  # noqa: E402
from inspect_ai.agent import react  # noqa: E402
from inspect_ai.dataset import Sample  # noqa: E402
from inspect_ai.log import read_eval_log  # noqa: E402
from inspect_ai.scorer import includes  # noqa: E402
from inspect_ai.solver import generate  # noqa: E402
from inspect_ai.tool import tool  # noqa: E402

TAG, CTX = sys.argv[1], int(sys.argv[2])
OLLAMA = "http://127.0.0.1:11434"
LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
fails = []


@tool
def add():
    async def execute(a: int, b: int):
        """Add two integers.

        Args:
            a: first integer
            b: second integer
        """
        return str(a + b + 1000)
    return execute


@task
def arith():
    return Task(dataset=[Sample(input="What is 17 * 23? Answer with the number only.", target="391")],
                solver=generate(), scorer=includes())


@task
def tooluse():
    return Task(dataset=[Sample(input="Use the add tool to add 2 and 3, then submit exactly the number the tool "
                                      "returned.", target="1005")], solver=react(tools=[add()]), scorer=includes())


def ps_ctx():
    m = [x for x in json.load(urllib.request.urlopen(OLLAMA + "/api/ps", timeout=20)).get("models") or []
         if x.get("name", "").split(":")[0] == TAG.split(":")[0]]
    return (m[0].get("context_length"), m[0].get("size_vram") == m[0].get("size")) if m else (None, None)


def sample(t, think):
    log = inspect_eval(t, model="lab/" + TAG, model_args={"num_ctx": CTX, "think": think}, log_dir=LOGS,
                       display="none")[0]
    assert log.status == "success", (log.status, log.error)
    return read_eval_log(log.location).samples[0]


def reasoning_chars(s):
    return sum(len(p.reasoning or "") for m in s.messages if m.role == "assistant" and isinstance(m.content, list)
               for p in m.content if getattr(p, "type", "") == "reasoning")


for think in ("off", "on"):
    s = sample(arith(), think)
    ctx, on_card = ps_ctx()
    rc = reasoning_chars(s)
    ok_ctx, ok_think = ctx == CTX, (rc == 0) if think == "off" else (rc > 0)
    print("[1+2] think=%s: loaded ctx %s (want %d) on card %s | reasoning chars %d -> ctx %s, thinking %s"
          % (think, ctx, CTX, on_card, rc, "OK" if ok_ctx else "WRONG", "OK" if ok_think else "WRONG"))
    fails += [] if ok_ctx else ["ctx think=%s" % think]
    fails += [] if ok_think else ["thinking=%s" % think]

s = sample(tooluse(), "off")
calls = [tc.function for m in s.messages if m.role == "assistant" for tc in (m.tool_calls or [])]
results = [str(m.content) for m in s.messages if m.role == "tool"]
ok_tool = "add" in calls and any("1005" in r for r in results)
print("[3] tool calls %s | tool results %s | roles %s -> %s" % (calls, results, [m.role for m in s.messages],
                                                               "OK" if ok_tool else "WRONG"))
fails += [] if ok_tool else ["tool"]

try:
    inspect_eval(arith(), model="lab/" + TAG, model_args={"think": "off"}, log_dir=LOGS, display="none")
    refused = False
except Exception as e:                                               # noqa: BLE001
    refused = "num_ctx" in str(e)
if not refused:
    # Inspect may catch provider-init errors into a failed log rather than raising; read it back
    import glob
    newest = max(glob.glob(os.path.join(LOGS, "*.eval")), key=os.path.getmtime)
    lg = read_eval_log(newest)
    refused = lg.status == "error" and "num_ctx" in str(lg.error)
print("[4] missing num_ctx refused -> %s" % ("OK" if refused else "WRONG"))
fails += [] if refused else ["no-default refusal"]
print("VERDICT:", "PASS" if not fails else "FAIL %s" % fails)
