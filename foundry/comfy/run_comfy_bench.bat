@echo off
cd /d F:\comfy-bench\ComfyUI
..\venv\Scripts\python.exe -u main.py --port 8189 >> F:\comfy-bench\comfy_bench.log 2>&1

