"""Live decoy for the factory's own tok/s (p20_foundry.EngineRates): generate a few long answers through the box's
ollama and print NOTHING, so any rate on the tile can only have come from the factory reading the engine's log.

    silent_generate.py <ORDER_OUT> <model> <answers> [order name, appended by the factory]"""
import json
import sys
import time
import urllib.request

out, model, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
for i in range(n):
    body = {"model": model, "stream": False, "think": False,
            "prompt": "Write a detailed, multi-paragraph explanation of how a hash map works, with examples. (%d)" % i,
            "options": {"num_predict": 600, "temperature": 0.6}}
    rq = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    urllib.request.urlopen(rq, timeout=600).read()
    time.sleep(20)          # hold the order open between answers so the tile is read while the run is live
