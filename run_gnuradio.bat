@echo off
REM ABHEDYA with the GNU Radio signal chain. Needs radioconda (https://github.com/ryanvolz/radioconda).
cd /d "%~dp0"
set RC=
for %%D in ("%USERPROFILE%\radioconda" "%LOCALAPPDATA%\radioconda" "C:\radioconda" "C:\ProgramData\radioconda" "D:\radioconda") do (
  if not defined RC if exist "%%~D\python.exe" set "RC=%%~D"
)
if not defined RC (
  echo radioconda not found. Install it, or edit this file and set RC to its folder.
  echo The normal run_windows.bat works without GNU Radio, using the NumPy chain.
  pause & exit /b 1
)
echo Using radioconda at %RC%
"%RC%\python.exe" -m pip install -q -r requirements.txt
for %%P in (8000 8001) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P  *[0-9.:]*  *LISTENING"') do taskkill /PID %%I /F >nul 2>&1
)
set SIGNAL_BACKEND=gnuradio
echo ABHEDYA (GNU Radio chain) on http://localhost:8000/demo
"%RC%\python.exe" -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
pause
