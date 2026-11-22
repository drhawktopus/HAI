"""Factory payload shim for third-party tools: the runner appends the order name to argv, and tools that parse their
own arguments strictly (convert_hf_to_gguf.py, llama-quantize.exe) reject it.
usage (as an order's payload + args): run_tool.py <script.py | tool.exe> [tool args...]  (+ the runner's order name)"""
import os
import runpy
import subprocess
import sys

target, args = sys.argv[1], sys.argv[2:-1]
print("[run_tool] %s %s" % (target, " ".join(args)), flush=True)
if target.lower().endswith(".exe"):
    sys.exit(subprocess.call([target] + args))
sys.argv = [target] + args
sys.path.insert(0, os.path.dirname(os.path.abspath(target)))
runpy.run_path(target, run_name="__main__")
