@echo off
rem One-time Krea 2 trainer install (krea2_trainer_setup.py, run from <P20_KREA2_TRAIN_DIR>\setup). Verdict: RESULT.json
rem in that folder.   run_krea2_setup.cmd [python.exe]   (default: python on PATH)
set "PY=%~1"
if "%PY%"=="" set "PY=python"
"%PY%" -I "%~dp0krea2_trainer_setup.py" > "%~dp0setup.stdout.log" 2>&1
echo EXIT=%ERRORLEVEL% >> "%~dp0setup.stdout.log"
