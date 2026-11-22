@echo off
rem llama-swap on :11435 (Flash-Next on demand): its config is beside this file, its folder is LLAMA_SWAP_DIR (lab.env)
for /f "usebackq tokens=1,* delims==" %%a in (`findstr /b "LLAMA_SWAP_DIR=" "%~dp0..\..\etc\lab.env"`) do set "LLAMA_SWAP_DIR=%%b"
if not defined LLAMA_SWAP_DIR exit /b 1
"%LLAMA_SWAP_DIR%\v265\llama-swap.exe" --config "%~dp0llama-swap.yaml" --listen 0.0.0.0:11435 --watch-config >> "%LLAMA_SWAP_DIR%\llama-swap.log" 2>&1
