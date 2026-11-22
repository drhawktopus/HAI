@echo off
REM The foundry's deploy gate. Safe to run AT ANY TIME -- no GPU, no ComfyUI, no live-state writes,
REM so unlike the orchestrator gate it cannot go red merely because the lab is busy.
REM
REM Run BEFORE deploying anything under the foundry's bin, and AFTER restarting the runner. FOUNDRY_PY names the
REM interpreter (the box's python otherwise); the tests sit beside this folder.
REM Exit code 0 = green.
setlocal
if "%FOUNDRY_PY%"=="" (set PY=python) else (set PY=%FOUNDRY_PY%)
echo === FOUNDRY SUITE ===
"%PY%" -W error::SyntaxWarning "%~dp0..\tests\test_foundry.py"
set RC=%ERRORLEVEL%
if %RC%==0 (
  echo.
  echo FOUNDRY SUITE GREEN
) else (
  echo.
  echo *** FOUNDRY SUITE RED ^(exit %RC%^) -- DO NOT DEPLOY ***
)
exit /b %RC%
