@echo off
REM Remove the scheduled task
setlocal
set TASK_NAME=TWStockDailyUpdate

echo Removing scheduled task: %TASK_NAME%
schtasks /delete /tn "%TASK_NAME%" /f
if errorlevel 1 (
    echo.
    echo [WARNING] Task may not exist or cannot be removed.
    pause
    exit /b 1
)
echo [DONE] Task removed.
pause
