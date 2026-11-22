@echo off
REM Demo-day pre-flight check: read-only, changes nothing.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (.venv\Scripts\python.exe scripts\preflight.py) else (python scripts\preflight.py)
pause
