"""A1 on Inspect AI, without a second implementation: each Inspect sample runs a1_harness.run_task (the same brief,
tools, no-progress rule and hidden-test grade the factory orders run) and only the MODEL CALL goes through Inspect's
model (the `lab` provider on our boxes). Inspect then owns what we adopted it for: model access, full logs of every
model call, eval orchestration, scoring and later arms - one measured loop, the one HAI's tools describe.

    inspect eval a1_task.py --model lab/<tag> -M num_ctx=<tested> -M think=<on|off> [-T mode=agentic-hai]
        [-T exercises=a,b] [-M backend=tolkien]

Each sample's score carries the harness row (failing tests, turns, tool counts, wall) as metadata.
"""
import asyncio
import json
import os
import sys
import time

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import get_model
from inspect_ai.scorer import CORRECT, INCORRECT, Score, accuracy, scorer
from inspect_ai.solver import solver

A1_DIR = os.environ.get("A1_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "a1")
sys.path.insert(0, A1_DIR)
import a1_harness as H  # noqa: E402
from a1_chat import (CompactingChat, from_output, is_context_full, is_runaway,  # noqa: E402,F401
                     to_inspect, to_tool_infos)

EXERCISES = os.path.join(A1_DIR, "polyglot-benchmark", "python", "exercises", "practice")


def _save(path, obj):
    """Whole-file write (tmp + replace): a stop mid-write can never leave a half checkpoint."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


# A1-team: the order sets a delegate(brief) -> text callable here for the run (a1_payload.run_agentic), the solver hands
# it to the harness, which then offers the lead the `delegate` tool. A module hook rather than a task argument because
# Inspect records task arguments in the eval log and a callable does not serialise.
DELEGATE = {"fn": None, "brief": "", "finish_gate": False, "review_fn": None}
# Per-turn checkpoint (the fleet's a1_order standard, one shape on every factory): written before each model call, and a
# stopped exercise resumes by replaying it (a1_branch.ReplayChat, point "end") - a stop costs one turn, not the item.
CHECKPOINT = {"path": None}


@solver
def a1_harness_run(mode="agentic-hai", turn_cap=0):
    """`turn_cap`: the order's per-call output cap (0 = none), sent as max_tokens; a reply cut there without a tool
    call reaches the harness as a runaway."""
    turn_cap = int(turn_cap or 0)

    async def solve(state, generate):
        model, loop = get_model(), asyncio.get_running_loop()
        extra = {"delegate_fn": DELEGATE["fn"]} if DELEGATE.get("fn") else {}
        if extra and DELEGATE.get("brief"):                 # the DIRECTED arm: the lead is told to use the helper
            extra["delegate_brief"] = DELEGATE["brief"]
        if DELEGATE.get("finish_gate"):                      # finish refused until the lead's own tests pass
            extra["finish_gate"] = True
        if DELEGATE.get("review_fn"):                        # A1-review: a tablet reads the artifact at finish
            extra["review_fn"] = DELEGATE["review_fn"]

        ck_path, turns = CHECKPOINT.get("path"), []
        cc = CompactingChat(model, turn_cap,
                            lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result(), calibrate=True)

        def chat_fn(messages, tools):
            if ck_path:                      # before every call: a stop then costs only the turn in flight
                _save(ck_path, {"exercise": state.metadata["ex_dir"], "turns": turns, "conversation": messages})
            t0 = time.time()
            out, why = cc.call(messages, tools)
            turns.append({"wall_s": round(time.time() - t0, 2), "done_reason": why})
            if out is None:
                return {"content": "", "tool_calls": [], "context_full": True}
            return from_output(out, turn_cap)

        chat = chat_fn
        if ck_path and os.path.exists(ck_path):
            import a1_branch
            with open(ck_path, encoding="utf-8") as f:
                ck = json.load(f)
            turns.extend(ck.get("turns") or [])
            chat = a1_branch.ReplayChat(ck, "end", chat_fn)
            state.metadata["resumed_turns"] = chat.k
        row = await asyncio.to_thread(H.run_task, state.metadata["ex_dir"], chat, mode, **extra)
        row["compactions"] = cc.compactions
        state.metadata["a1_row"] = {k: v for k, v in row.items() if k != "grade_output"}
        state.metadata["a1_grade_output"] = row.get("grade_output", "")
        return state
    return solve


@scorer(metrics=[accuracy()])
def a1_hidden_tests():
    async def score(state, target):
        row = state.metadata.get("a1_row") or {}
        return Score(value=CORRECT if row.get("passed") else INCORRECT,
                     answer="%s/%s" % (row.get("tests_passed"), row.get("tests_total")),
                     explanation=json.dumps(row.get("failing") or [])[:2000], metadata=row)
    return score


@task
def a1(mode="agentic-hai", exercises="all", ex_dir="", turn_cap=0):
    """`ex_dir`: one exercise folder by path instead (rigby's agentic payload runs one item per eval, and its
    control exercise lives outside the archived task set). `turn_cap`: the runaway guard's per-call cap (0 = none),
    recorded in the eval log as a task argument."""
    if ex_dir:
        samples = [Sample(id=os.path.basename(ex_dir.rstrip("/\\")), input="A1 exercise",
                          metadata={"ex_dir": ex_dir})]
        return Task(dataset=samples, solver=a1_harness_run(mode, turn_cap), scorer=a1_hidden_tests())
    names = sorted(os.listdir(EXERCISES)) if exercises == "all" else exercises.split(",")
    samples = [Sample(id=n, input="A1 exercise %s" % n, metadata={"ex_dir": os.path.join(EXERCISES, n)})
               for n in names]
    return Task(dataset=samples, solver=a1_harness_run(mode, turn_cap), scorer=a1_hidden_tests())
