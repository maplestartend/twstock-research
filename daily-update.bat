@echo off
chcp 65001 >nul
REM ============================================================
REM TW Stock Dashboard - Daily Data Update
REM Run after market close (16:30+)
REM Pushes to Discord on success/failure (requires config.yaml notify)
REM ============================================================
title TW Stock Daily Update

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=py
)

echo.
echo =========================================================
echo   Daily Data Update ^(1-2 min^)
echo =========================================================
echo.

%PYTHON% -m scripts.market_update --push
set RC=%errorlevel%

REM 公告期內自動回補新季財報（Q1→5/15, Q2→8/14, Q3→11/14, Q4→次年3/31）
REM idempotent：MOPS 沒新資料就什麼都不寫。
%PYTHON% -m scripts.refresh_recent_financials --quiet

echo.
if %RC% neq 0 (
    echo [ERROR] Update failed with code %RC%. Check logs/app.log or Discord.
) else (
    echo [DONE] Update successful.
)
echo.
echo Window closes in 10 seconds, or press any key to close now.
timeout /t 10 >nul
