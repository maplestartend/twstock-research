@echo off
chcp 65001 >nul
REM ============================================================
REM TW Stock Dashboard v2 - launch only (no data update)
REM Starts FastAPI (port 8000) + Next.js (port 3000), opens browser.
REM ============================================================
title TW Stock v2 UI (Next.js)

cd /d "%~dp0"

call "%~dp0_launch-servers.bat"
pause
