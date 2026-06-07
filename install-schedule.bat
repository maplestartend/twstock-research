@echo off
chcp 65001 >nul
REM ============================================================
REM TW Stock - Install Windows Task Scheduler entry
REM One-click setup: registers a daily task that runs market_update at 16:45
REM   — TWSE/TPEx OpenAPI final data ready ~16:30, wait 15 min for safety.
REM Features: wake computer, run even if missed, retry on failure
REM
REM Re-run to update; to remove run: uninstall-schedule.bat
REM ============================================================
setlocal

cd /d "%~dp0"
set TASK_NAME=TWStockDailyUpdate
set TASK_SCRIPT=%~dp0daily-update.bat
set TASK_TIME=16:45

echo.
echo =========================================================
echo   Installing scheduled task:
echo     Name : %TASK_NAME%
echo     Time : %TASK_TIME% daily
echo     Runs : %TASK_SCRIPT%
echo =========================================================
echo.

REM Remove any existing task with the same name (ignore errors)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Create the task.
REM   /sc DAILY        - run every day
REM   /st %TASK_TIME%  - start time (16:45 = TWSE final 16:30 + 15 min buffer)
REM   /rl LIMITED      - use the current logon user's rights (no admin)
REM   /f               - force create
REM XML-based creation would allow "wake to run" + "run if missed", but
REM that requires building an XML file. The basic create works for most cases;
REM after it's created, users can right-click the task in Task Scheduler ->
REM Properties -> Settings -> tick "Run task as soon as possible after a
REM scheduled start is missed" and "Wake the computer to run this task".
schtasks /create /tn "%TASK_NAME%" /tr "\"%TASK_SCRIPT%\"" /sc DAILY /st %TASK_TIME% /rl LIMITED /f

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install scheduled task.
    echo Tips:
    echo   - Run this .bat as Administrator if permission denied
    echo   - Or open Task Scheduler manually and import/create the task
    echo.
    pause
    exit /b 1
)

echo.
echo [DONE] Scheduled task installed.
echo.
echo NEXT STEPS (recommended, one-time):
echo   1. Open Task Scheduler ^(Win+R -^> taskschd.msc^)
echo   2. Find "%TASK_NAME%" in Task Scheduler Library
echo   3. Right-click -^> Properties -^> Settings tab
echo   4. Tick: "Run task as soon as possible after a scheduled start is missed"
echo   5. Tick: "If the task fails, restart every: 30 minutes, up to 3 times"
echo   6. Properties -^> Conditions tab -^> tick "Wake the computer to run this task"
echo.
echo To run now to test:
echo   schtasks /run /tn %TASK_NAME%
echo.
echo To remove:
echo   uninstall-schedule.bat
echo.
pause
