@echo off
REM BigHonker: the order taker, the same p20_foundry.py every factory runs; these settings make it this factory, and
REM settings set here win over machine-level variables. Started by a scheduled task, never over ssh (a network logon
REM cannot see mapped drives). bh_worker.cmd carries the same render settings: change both together.
REM No ai-toolkit here, so `train` is not declared. Render capability follows FOUNDRY_COMFY_LAUNCH; renders use the
REM 4B klein base that fits this 8 GB card (a klein adapter renders only against it), and the PNGs are read from this
REM box's own ComfyUI output folder.
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
"C:\Program Files\Python312\python.exe" -u F:\foundry\bin\p20_foundry.py --queue >> F:\foundry\var\log\foundry_queue.log 2>&1
endlocal
