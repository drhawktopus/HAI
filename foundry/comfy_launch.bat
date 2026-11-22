@echo off
rem ComfyUI lives beside this launcher (the launcher's own folder + comfyui-rocm): no drive is named.
cd /d "%~dp0comfyui-rocm"
set "PYTHON_DIR=%~dp0comfyui-rocm\python_env"
set "PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%"
"%PYTHON_DIR%\scripts\rocm-sdk" init >nul 2>&1
for /f "delims=" %%i in ('rocm-sdk path --root') do set "HIP_PATH=%%i"
set "ROCM_PATH=%HIP_PATH%"
set TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1
set "PYTORCH_TUNABLEOP_CACHE_DIR=%~dp0tunableop-cache"
set "TRITON_CACHE_DIR=%~dp0triton-cache"
set COMFYUI_ENABLE_MIOPEN=0
set FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE
rem Absolute entrypoints preserve factory identity in Win32_Process.CommandLine.
"%PYTHON_DIR%\python.exe" -u "%~dp0comfyui-rocm\main.py" --extra-model-paths-config "%~dp0comfyui-rocm\extra_model_paths.yaml" --disable-api-nodes --disable-pinned-memory --disable-triton-backend --use-sage-attention --listen 0.0.0.0 --port 8189
