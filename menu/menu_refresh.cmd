@echo off
rem The placement table refresh (a scheduled task, every 15 min). Runs from this folder; MENU_PYTHON names the
rem interpreter (default: python on PATH). Log: menu_refresh_task.log beside this file.
cd /d "%~dp0"
if "%MENU_PYTHON%"=="" set MENU_PYTHON=python
"%MENU_PYTHON%" menu_refresh.py >> menu_refresh_task.log 2>&1
echo %DATE% %TIME% exit %ERRORLEVEL% >> menu_refresh_task.log
