@echo off
REM Softserve: the order taker, the same p20_foundry.py every factory runs; these settings make it this factory.
REM FOUNDRY_ROOT is E: (this box has no F: drive). Ollama starts on demand: its autostart is off on this box, which
REM also serves media. OLLAMA_CONTEXT_LENGTH keeps a model's default window resident on the 8 GB card. There is no
REM ai-toolkit and no ComfyUI here: the capability check finds neither, so train and probe are not declared.
setlocal
set FOUNDRY_ROOT=E:\foundry
set FOUNDRY_FLEET_HOST=softserve
set FOUNDRY_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
set FOUNDRY_FRAMING_PY=E:\foundry\venv\Scripts\python.exe
set FOUNDRY_LORAS=E:\foundry\var\cache\loras
set FOUNDRY_COMFY_LAUNCH=E:\foundry\etc\no_comfy_here.bat
set FOUNDRY_AITK=E:\foundry\etc\no_aitoolkit_here
set OLLAMA_CONTEXT_LENGTH=4096
set PYTHONPATH=E:\foundry\bin

curl.exe -s --max-time 3 http://127.0.0.1:11434/api/version >nul 2>&1
if errorlevel 1 (
  echo starting ollama on demand...
  start "" /b "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
  timeout /t 8 /nobreak >nul
)

cd /d E:\foundry\bin
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -u E:\foundry\bin\p20_foundry.py --queue >> E:\foundry\var\log\foundry_queue.log 2>&1
endlocal
