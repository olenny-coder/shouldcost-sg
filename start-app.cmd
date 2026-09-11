@echo off
REM ---------------------------------------------------------------------------
REM  shouldcost - launcher.  Double-click, or run from cmd:  start-app.cmd
REM  Starts the backend and frontend, seeds the database, opens the browser.
REM  If the app is already serving, it just opens the browser.
REM  Requires: Python 3.11 on PATH, and Node.js (for npm).
REM ---------------------------------------------------------------------------
setlocal
title shouldcost launcher
cd /d "%~dp0"

echo Checking whether shouldcost is already running ...
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://localhost:4173/' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 goto startit

echo The app is already running.
goto openit

:startit
echo Starting the backend on http://localhost:8000 ...
start "shouldcost backend" /d "%~dp0backend" cmd /k python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo Starting the frontend on http://localhost:4173 ...
start "shouldcost frontend" /d "%~dp0frontend" cmd /k npm run preview

echo Seeding the database (idempotent - safe to run every time) ...
pushd backend
python -m app.etl
popd

echo Waiting for the servers to come up ...
REM ping rather than timeout: timeout fails when stdin is redirected.
ping -n 11 127.0.0.1 >nul

:openit
start http://localhost:4173
echo.
echo   App        http://localhost:4173
echo   API docs   http://localhost:8000/docs
echo   Health     http://localhost:8000/api/healthz
echo.
echo Close the two server windows to stop the app.
endlocal
