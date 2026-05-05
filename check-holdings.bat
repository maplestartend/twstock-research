@echo off
chcp 65001 >nul
REM ============================================================
REM Holdings EOD Check — ATR stops + structural warning + chandelier TP
REM Run after market close (13:30+) to get next-day action list.
REM
REM Usage:
REM   check-holdings.bat              ← uses default capital 760000
REM   check-holdings.bat 800000       ← override capital for position-pct calc
REM ============================================================
setlocal
title TW Stock — Holdings EOD Check

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=py
)

set CAPITAL=%1
if "%CAPITAL%"=="" set CAPITAL=760000

%PYTHON% -m scripts.holdings_eod_check --capital %CAPITAL%
echo.
echo Press any key to close...
pause >nul
