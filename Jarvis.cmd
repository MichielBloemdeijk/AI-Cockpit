@echo off
rem Process-local script execution; no machine policy or account changes.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\jarvis.ps1" %*
if "%~1"=="" pause
