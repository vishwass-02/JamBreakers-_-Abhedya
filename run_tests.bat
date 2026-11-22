@echo off
REM Runs the ABHEDYA test suite with the project's .venv (create it first with run_windows.bat).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe ( echo Run run_windows.bat once first to create .venv. & pause & exit /b 1 )
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
echo.
pause
