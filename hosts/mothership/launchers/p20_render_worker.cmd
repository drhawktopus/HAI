@echo off
REM Mothership: the render worker. It imports foundry_paths, so the root and the import path are set before it runs.
setlocal
set FOUNDRY_ROOT=F:\foundry
set FOUNDRY_FLEET_HOST=mothership
set PYTHONPATH=F:\foundry\bin

cd /d F:\foundry\bin
"F:\ai-toolkit\venv\python.exe" -u F:\foundry\bin\p20_render_worker.py >> F:\foundry\var\log\p20_render_worker_console.log 2>&1
endlocal
