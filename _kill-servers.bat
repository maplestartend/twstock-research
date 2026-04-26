@echo off
chcp 65001 >nul
REM ============================================================
REM Shared sub-script: kill uvicorn (port 8000) + next dev (port 3000)
REM and close TW Stock cmd windows. No pause / no verify - caller decides.
REM Used by stop.bat and restart.bat to avoid duplicating the kill block.
REM ============================================================

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo [kill] FastAPI uvicorn  PID=%%P  port 8000
    taskkill /PID %%P /T /F >nul 2>&1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    echo [kill] Next.js  next dev  PID=%%P  port 3000
    taskkill /PID %%P /T /F >nul 2>&1
)
taskkill /FI "WINDOWTITLE eq TW Stock API*"   /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq TW Stock Web*"   /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq TW Stock v2 UI*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq TW Stock Update*" /T /F >nul 2>&1

exit /b 0
