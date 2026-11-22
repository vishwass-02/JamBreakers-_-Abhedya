@echo off
REM ABHEDYA on port 8001 (use if another copy already holds 8000)
cd /d "%~dp0"
echo  ABHEDYA starting on http://localhost:8001
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8001
pause
