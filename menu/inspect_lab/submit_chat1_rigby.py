"""CHAT1 on rigby: the small tier answering a one-line chat turn. One rigby-lane `llm` order per model on hard_tasks_chat1 (HAI's own
simple-chat requests), 5 replicates, the order's own payload grading the reply as data. Priced on the INTERACTIVE
turn: per-row wall, prompt and generation rates (time to the first token = prompt tokens / prompt rate + load).
No price ceiling: a ceiling cuts the answer this arm exists to time; the rigby lane is serial, so these
short orders go to the FRONT (priority) behind the order in flight.
    python submit_chat1_rigby.py [--dry]"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from submit_speed_probe import GPU_ATTN_NOOFF   # noqa: E402  - the speed probe's winner, ONE definition

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the menu folder
import menu_paths as MP  # noqa: E402   the tablet desk: a setting
INBOX = MP.need("INBOX")
PAYLOAD = "/ssdshared/tabfoundry-jobs/payloads/map_swarm_code_hard_chat1.py"
TASKS = ["chat_capital", "chat_day", "chat_eggs", "chat_good_morning", "chat_guess_what", "chat_ping", "chat_pong"]
# (short, model, extra spec fields): the MoE is rigby's agreed lead (thinking off, the GT 730 placement); the 7B and
# 1.5B are rigby's small instruct models - the candidates for a fast simple-chat turn
ARMS = [("moe-off-gt730", "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf", dict(GPU_ATTN_NOOFF, reasoning="off")),
        ("q25-7b", "Qwen2.5-7B-Instruct-Q4_K_M.gguf", {}),
        ("q25-1.5b", "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", {})]
TEMPERATURE = 0.7       # the vendors' non-thinking value for every model here (menu_fill.VENDOR_THINK_OFF / RIGBY_VENDOR)


def main():
    dry = "--dry" in sys.argv
    stamp = time.strftime("%Y%m%d-%H%M")
    for short, model, extra in ARMS:
        name = "chat1-rigby-%s.%s" % (short, stamp)
        spec = {"kind": "llm", "name": name, "model": model, "payload": PAYLOAD, "tasks": TASKS, "replicates": 5,
                "arms": ["rigby"], "ctx": 32768, "temperature": TEMPERATURE, "priority": 50,
                "initiator": "ClaudeSupervisor",
                "why": ("CHAT1 (measurement 3): rigby answering HAI's own one-line chat turns - the simple-chat tier's "
                        "interactive price (the owner 2026-10-07). No price ceiling (it would cut the answer being timed).")}
        spec.update(extra)
        if dry:
            print("DRY", name, json.dumps({k: v for k, v in spec.items() if k != "why"})[:300])
            continue
        tmp = os.path.join(INBOX, name + ".tjob.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=1)
        os.replace(tmp, tmp[:-4])
        print("submitted", name)


if __name__ == "__main__":
    main()
