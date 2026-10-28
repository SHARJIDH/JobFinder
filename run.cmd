@echo off
rem AI Job Hunter (JobFinder) for Windows: double-click this file, or run  run.cmd  (options: -Port 8080 -NoBrowser -SetupOnly -SetupAI)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
