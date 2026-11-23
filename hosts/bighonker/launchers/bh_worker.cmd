@echo off
REM BigHonker: the render worker, the same p20_render_worker.py every factory runs. It publishes the heartbeat, pushes
REM the card's state and reports progress; render capability follows the ComfyUI launcher (WORKER_CAN_RENDER overrides).
REM bh_runner.cmd carries the same render settings: the runner needs them for probe orders, the worker for render jobs.
setlocal
set FOUNDRY_ROOT=F:\foundry
set FOUNDRY_FLEET_HOST=bighonker
set FOUNDRY_PY=C:\Program Files\Python312\python.exe
set FOUNDRY_LORAS=F:\comfy-bench\ComfyUI\models\loras
set FOUNDRY_COMFY_LAUNCH=F:\comfy-bench\run_comfy_bench.bat
set FOUNDRY_RENDER_UNET=flux-2-klein-base-4b-Q8_0.gguf
set FOUNDRY_COMFY_OUTPUT=F:\comfy-bench\ComfyUI\output
set PYTHONPATH=F:\foundry\bin
cd /d F:\foundry\bin
"C:\Program Files\Python312\python.exe" -u F:\foundry\bin\p20_render_worker.py >> F:\foundry\var\log\p20_render_worker_console.log 2>&1
endlocal
