@echo off
chcp 65001 >nul
REM ============================================================
REM TW Stock Dashboard - Status check
REM   1) port 8000 / 3000: running? PID?
REM   2) signal_history latest as_of vs daily_price latest date
REM      (if snapshot < daily_price -> radar/watchlist scores stale)
REM ============================================================
title TW Stock - status

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=py
)

set "PYTHONIOENCODING=utf-8"

echo.
echo =========================================================
echo   TW Stock Dashboard - status
echo =========================================================
echo.

echo [Server]
set "found8000=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo   FastAPI  http://localhost:8000   PID=%%P   RUNNING
    set "found8000=1"
)
if "%found8000%"=="0" echo   FastAPI  http://localhost:8000               STOPPED

set "found3000=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    echo   Next.js  http://localhost:3000   PID=%%P   RUNNING
    set "found3000=1"
)
if "%found3000%"=="0" echo   Next.js  http://localhost:3000               STOPPED

echo.
echo [Data]
%PYTHON% -c "import sqlite3; c=sqlite3.connect('data/stock.db'); c.row_factory=sqlite3.Row; cur=c.cursor(); p=cur.execute('SELECT MAX(date) AS m FROM daily_price').fetchone()['m']; s=cur.execute('SELECT MAX(as_of) AS m FROM signal_history').fetchone()['m']; n=cur.execute('SELECT COUNT(*) AS n FROM signal_history WHERE as_of=?', (s,)).fetchone()['n']; ok = (p and s and s >= p); print(f'  daily_price.MAX(date)     = {p}'); print(f'  signal_history.MAX(as_of) = {s}  ({n} rows)'); print('  -> snapshot OK (up-to-date with prices)' if ok else '  -> SNAPSHOT STALE (radar/watchlist may show old scores; run restart.bat)')"

echo.
echo =========================================================
echo   Commands:
echo     launch.bat     start dev servers
echo     stop.bat       one-click clean shutdown
echo     restart.bat    stop + rebuild snapshot + relaunch
echo                    (use after editing app/scoring/*)
echo =========================================================
echo.
pause
