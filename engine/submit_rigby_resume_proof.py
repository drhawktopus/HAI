"""The tablet head's lossless-resume proof on the engine-parity build: the earlier resume proof's closing arms re-run
unchanged on llama.cpp 620d222 + lab patches - r10b (MTP on, temp 0.6, the production shape) and r9c (MTP on, temp 0).
The one variable is the engine. Written as .incoming, then renamed into the Fellowship inbox."""
import datetime
import json
import os
import sys

import engine_settings as E   # the tablet desk, from the lab's settings
INBOX = E.inbox()
DAY = datetime.datetime.now().strftime("%Y%m%d")
# one name = one engine build: argv[1] tags the build (e.g. "compat" = + ollama's llama/compat layer)
BUILD = "620d222" + ("-" + sys.argv[1] if len(sys.argv) > 1 else "")
BASE = {"kind": "llm", "initiator": "ClaudeSupervisor", "model": "Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf",
        "arms": ["rigby"], "ctx": 65536, "reasoning": "on", "gpu": "gtx1060", "fit_target": 1024, "build": "cuda",
        "parallel": 1, "ubatch": 2048, "engine_env": {"LAB_CUDA_GRAPHS_PRE_VOLTA": 1}, "op_offload_min_batch": 256,
        "spec_type": "draft-mtp", "draft_max": 2}
ORDERS = {
    "dumpprobe-chat-mtp-t06-%s.%s" % (BUILD, DAY): dict(BASE, dump_probe={
        "p": 4000, "cut": 200, "n": 512, "mode": "chat", "doc": "LLM-Project/34-CHARTER.md", "seed": 1,
        "temperature": 0.6}),
    "dumpprobe-chat-mtp-t0-%s.%s" % (BUILD, DAY): dict(BASE, dump_probe={
        "p": 4000, "cut": 128, "n": 384, "mode": "chat", "doc": "Runbook/RUNBOOK.md", "seed": 1, "temperature": 0}),
}
for name, spec in ORDERS.items():
    spec = dict(spec, name=name)
    p = os.path.join(INBOX, name + ".tjob.json")
    with open(p + ".incoming", "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    os.replace(p + ".incoming", p)
    print("submitted", name)
