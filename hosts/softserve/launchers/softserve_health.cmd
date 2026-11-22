@echo off
REM Softserve: the factory health daemon, the fleet's own p20_health_daemon.py configured by these settings.
REM FOUNDRY_TARGET_CLOCK=1500: this 3070 Ti's clock target, carried over from the 3070's measured safe point for
REM margin. The daemon reports a lost card; it never reboots this box or stops the order taker.
setlocal
set FOUNDRY_ROOT=E:\foundry
set FOUNDRY_FLEET_HOST=softserve
set FOUNDRY_TARGET_CLOCK=1500
set PYTHONPATH=E:\foundry\bin

cd /d E:\foundry\bin
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -u E:\foundry\bin\p20_health_daemon.py 20 >> E:\foundry\var\state\telemetry\health_console.log 2>&1
endlocal
