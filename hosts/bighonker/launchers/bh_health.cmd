@echo off
REM BigHonker: the factory health daemon, the fleet's own p20_health_daemon.py configured by these settings.
REM FOUNDRY_TARGET_CLOCK=0: no clock target on this card. Settings set here win over machine-level variables.
setlocal
set FOUNDRY_ROOT=F:\foundry
set FOUNDRY_FLEET_HOST=bighonker
set FOUNDRY_TARGET_CLOCK=0
set PYTHONPATH=F:\foundry\bin
cd /d F:\foundry\bin
"C:\Program Files\Python312\python.exe" -u F:\foundry\bin\p20_health_daemon.py 20 >> F:\foundry\var\state\telemetry\health_console.log 2>&1
endlocal
