@echo off
chcp 65001 >nul
REM ============================================================
REM Shared sub-script: launch FastAPI + Next.js dev servers.
REM Called by launch.bat / restart.bat.
REM Caller must cd to the project root before invoking.
REM ============================================================

if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=py
)

echo.
echo =========================================================
echo   FastAPI  : http://localhost:8000  ^(docs at /docs^)
echo   Next.js  : http://localhost:3000
echo =========================================================
echo.

if not exist "web\node_modules\" (
    echo [INFO] web\node_modules not found. Running npm install...
    pushd web
    call npm ci --no-audit --no-fund
    popd
    if errorlevel 1 (
        echo [ERROR] npm install failed.
        pause
        exit /b 1
    )
)

REM Start FastAPI in a new window (reload mode)
start "TW Stock API (FastAPI)" cmd /k "%PYTHON% -m uvicorn api.main:app --reload --port 8000"

REM Wait for API to come up
timeout /t 3 >nul

REM Start Next.js dev server in a new window
start "TW Stock Web (Next.js)" cmd /k "cd web && npm run dev"

REM Give the frontend a few seconds to compile, then open the browser
timeout /t 6 >nul
start "" "http://localhost:3000"

echo.
echo [DONE] Servers launched in separate windows.
echo Close this window when finished.
echo.
exit /b 0
