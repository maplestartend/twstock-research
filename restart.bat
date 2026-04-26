@echo off
chcp 65001 >nul
REM ============================================================
REM TW Stock Dashboard - One-click restart
REM   stop -> regenerate signal_history snapshot -> launch
REM Use after editing app/scoring/* when radar/watchlist scores no longer
REM match the stock-detail page (live engine differs from snapshot written
REM by an older engine version).
REM ============================================================
title TW Stock - restart

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=py
)

REM --- Step 1/3: stop running servers ---
echo.
echo =========================================================
echo   Step 1/3: Stopping current servers
echo =========================================================
echo.

call "%~dp0_kill-servers.bat"
timeout /t 2 >nul

REM --- Step 2/3: rebuild signal_history with the current engine ---
REM     ensure_fresh() only triggers when snapshot.as_of < daily_price.MAX(date).
REM     If the engine code changed but date is unchanged, snapshot stays stale -
REM     radar/watchlist scores will not match the stock-detail page.
REM     Forcing snapshot_today() here guarantees the table reflects current code.
echo.
echo =========================================================
echo   Step 2/3: Rebuild snapshot (signal_history) with current engine
echo =========================================================
echo.

set "PYTHONIOENCODING=utf-8"
%PYTHON% -c "from pathlib import Path; from app.data.db import Database; from app.scoring.history import snapshot_today; n = snapshot_today(Database(Path('data/stock.db'))); print(f'[OK] signal_history rewritten: {n} rows')"
if errorlevel 1 (
    echo [WARN] snapshot rebuild failed; launching anyway (radar/watchlist may still be stale).
    timeout /t 2 >nul
)

REM --- Step 3/3: launch servers ---
echo.
echo =========================================================
echo   Step 3/3: Launch FastAPI + Next.js
echo =========================================================

call "%~dp0_launch-servers.bat"
pause
