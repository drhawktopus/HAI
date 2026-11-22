"""CHAT1 WARM: the Fellowship hosting several chats at once. One `llm` order per tablet
model with arms ["warm"]: Tolkien starts a resident llama-server on every healthy tablet that holds the model, each node
answers its own control, then the 7 chat turns x 5 replicates are dealt to whichever node is free. ONE variable against
the cold run (`hardbench-chat1-*-r4`, llama-cli per turn): same models, turns, replicates, context (16,384) and reasoning
setting; only the server is resident. Rows carry the node and the time to the first token.
    python submit_chat1_warm.py [--dry] [--only=c15b,q3508bnt]"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the menu folder
import menu_paths as MP  # noqa: E402   the tablet desk: a setting
INBOX = MP.need("INBOX")
PAYLOAD = "/ssdshared/tabfoundry-jobs/payloads/map_swarm_code_hard_chat1.py"
TASKS = ["chat_capital", "chat_day", "chat_eggs", "chat_good_morning", "chat_guess_what", "chat_ping", "chat_pong"]
# (short, tablet model, reasoning) - the cold run's four, at its settings (map_swarm_code_hard_chat1_<tag>.py)
MODELS = [("c05b", "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M", None),
          ("c15b", "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M", None),
          ("q3508bnt", "Qwen3.5-0.8B-Q4_K_M", "off"),
          ("q352bnt", "Qwen3.5-2B-Q4_K_M", "off")]


def main():
    dry = "--dry" in sys.argv
    stamp = time.strftime("%Y%m%d-%H%M")
    only = [a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--only=")]
    picked = [m for m in MODELS if not only or m[0] in only[0]]
    assert picked, "--only matched no model"
    for short, model, reasoning in picked:
        name = "chat1-warm-%s.%s" % (short, stamp)
        spec = {"kind": "llm", "name": name, "model": model, "payload": PAYLOAD, "tasks": TASKS, "replicates": 5,
                "arms": ["warm"], "ctx": 16384, "temperature": 0.6, "initiator": "ClaudeSupervisor",
                "why": ("CHAT1 WARM (the owner 2026-10-07 option 1): the Fellowship serving chat from resident servers, "
                        "several at once; one variable vs the cold -r4 run.")}
        if reasoning:
            spec["reasoning"] = reasoning
        if dry:
            print("DRY", json.dumps({k: v for k, v in spec.items() if k != "why"}))
            continue
        tmp = os.path.join(INBOX, name + ".tjob.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=1)
        os.replace(tmp, tmp[:-4])
        print("submitted", name)


if __name__ == "__main__":
    main()
