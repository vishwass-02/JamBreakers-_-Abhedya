@echo off
REM ABHEDYA one-command check: installs everything, lints, runs every test and the demo pre-flight check.
REM If all of it passes, it starts the server. Use this on a fresh machine or before submitting.
cd /d "%~dp0"
setlocal

if not exist .venv\Scripts\python.exe (
  echo [1/5] Creating the Python environment ...
  py -3.11 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 ( echo Could not create .venv. Install Python 3.11+ from python.org. & goto :fail )
)
echo [2/5] Installing packages ...
.venv\Scripts\python.exe -m pip install -q --upgrade pip
.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt || goto :fail

echo [3/5] Lint (ruff) ...
.venv\Scripts\python.exe -m ruff check . || goto :fail

echo [4/5] Tests (about 4 minutes) ...
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider || goto :fail

echo [5/5] Demo pre-flight check ...
.venv\Scripts\python.exe scripts\preflight.py || goto :fail

echo.
echo  ALL CHECKS PASSED. Starting ABHEDYA ...
echo.
call run_windows.bat
exit /b 0

:fail
echo.
echo  STOPPED: the step above failed. Nothing was started. Send a screenshot of this window if you need help.
pause
exit /b 1
