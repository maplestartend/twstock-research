@echo off
chcp 65001 >nul
REM ============================================================
REM TW Stock Dashboard - One-click clean shutdown
REM   Kills uvicorn (port 8000) + next dev (port 3000) + cmd windows,
REM   then verifies both ports are released.
REM ============================================================
title TW Stock - stop

cd /d "%~dp0"

echo.
echo =========================================================
echo   Stopping TW Stock servers
echo =========================================================
echo.

call "%~dp0_kill-servers.bat"

REM Verify ports are released
timeout /t 1 >nul
echo.
echo === Verify ===
set "still=0"
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul && (
    echo [WARN] port 8000 still listening
    set "still=1"
)
netstat -ano | findstr ":3000 " | findstr "LISTENING" >nul && (
    echo [WARN] port 3000 still listening
    set "still=1"
)
if "%still%"=="0" (
    echo [OK] ports 8000 / 3000 released. Safe to relaunch.
) else (
    echo.
    echo Some processes still alive. Manual:
    echo   netstat -ano ^| findstr ":8000 :3000 "
    echo   taskkill /PID ^<pid^> /F
)

echo.
pause
