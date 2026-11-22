@echo off
REM ABHEDYA - Windows run script. Creates .venv on first run, stops any older ABHEDYA server, then starts.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment...
  py -3.11 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 ( echo Could not create venv. Install Python 3.11+ from python.org & pause & exit /b 1 )
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
  if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )
)

REM Free ports 8000 and 8001 if an older copy of the server is still running.
for %%P in (8000 8001) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P  *[0-9.:]*  *LISTENING"') do (
    echo Stopping the old server on port %%P ^(PID %%I^)...
    taskkill /PID %%I /F >nul 2>&1
  )
)

echo.
echo  ABHEDYA starting on http://localhost:8000
echo    Mission deck ... http://localhost:8000/intro/   ^(start here^)
echo    3D view ........ http://localhost:8000/viewer/
echo    Judges' screen . http://localhost:8000/demo
echo    Phone app ...... scan the QR code (Phone button), or see http://localhost:8000/api/lan
echo  If phones cannot connect: allow Python through Windows Firewall on PRIVATE networks.
echo  Phones sign in with a user ID and password (accounts: python -m backend.users list).
echo  Before presenting, run check_demo.bat once.
echo.
REM Optional HTTPS: put a certificate in data\cert.pem and data\key.pem (for example made with mkcert) and the
REM server uses it, so passwords never cross the Wi-Fi in clear text.
if exist data\cert.pem if exist data\key.pem (
  echo  HTTPS on: open https://localhost:8000
  .venv\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --ssl-certfile data\cert.pem --ssl-keyfile data\key.pem
  goto :end
)
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
:end
pause
