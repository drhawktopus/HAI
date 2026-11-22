"""Holds a compute order open for a fixed duration to allow deployment pauses. Writes 'started.txt' immediately and 'finished.txt' upon completion. Does not maintain checkpoints; a requeued run restarts from zero. Usage: sleep_decoy.py <ORDER_OUT> <seconds> [order name]"""
import os
import sys
import time

out, secs = sys.argv[1], float(sys.argv[2])
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "started.txt"), "a", encoding="utf-8") as f:
    f.write("%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
time.sleep(secs)
with open(os.path.join(out, "finished.txt"), "w", encoding="utf-8") as f:
    f.write("%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
