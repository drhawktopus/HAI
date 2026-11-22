@echo off
:: Wrapper used by Windows Task Scheduler to run the Plex monitor.
:: Logs are appended to plex_monitor.log in the same folder.

cd /d "%~dp0"
node plex_monitor.js >> plex_monitor.log 2>&1
