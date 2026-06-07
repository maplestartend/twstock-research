@echo off
chcp 65001 >nul
REM ============================================================
REM sync-from-cloud.bat — 副機從 OneDrive 拉最新 stock.db
REM
REM 用途：副機（不跑 daily-update 排程的那台）開始用之前雙擊這個。
REM 流程：1) 確保 server 沒在跑 → 2) 找雲端最新 stock_YYYYMMDD.db
REM        → 3) 覆蓋本機 data\stock.db → 4) 提示可以 launch.bat
REM
REM 主機（跑排程的那台）不需要這支腳本，直接 launch.bat 即可。
REM ============================================================
setlocal enableextensions enabledelayedexpansion

REM 1. 雲端備份資料夾。OneDrive personal 預設會在 %USERPROFILE%\OneDrive\
REM    若副機用的是 OneDrive Business，請改成 %USERPROFILE%\OneDrive - 公司名\台股備份
set "CLOUD_DIR=%USERPROFILE%\OneDrive\台股備份"

if not exist "%CLOUD_DIR%" (
    echo [ERROR] 找不到雲端備份資料夾：
    echo         %CLOUD_DIR%
    echo.
    echo 可能原因：
    echo   - OneDrive 還沒同步好（檢查工具列 OneDrive icon 是不是綠勾）
    echo   - 路徑不對（OneDrive Business 路徑不一樣，編輯本檔頂端 CLOUD_DIR）
    echo   - 主機還沒跑過第一次 market_update（雲端還沒任何備份）
    pause
    exit /b 1
)

REM 2. 確保 server 沒在跑（避免覆蓋到 lock 中的 DB）
echo [1/4] 停掉本機 server（如果有在跑）...
call "%~dp0_kill-servers.bat" >nul 2>&1

REM 3. 找雲端最新一份 stock_YYYYMMDD.db
echo [2/4] 在雲端找最新備份...
set "LATEST="
for /f "delims=" %%f in ('dir /b /o-n "%CLOUD_DIR%\stock_*.db" 2^>nul') do (
    if not defined LATEST set "LATEST=%%f"
)

if not defined LATEST (
    echo [ERROR] %CLOUD_DIR% 裡沒有任何 stock_*.db 檔案
    echo 請主機跑一次 python -m scripts.market_update 觸發第一次備份
    pause
    exit /b 1
)

echo         找到：%LATEST%

REM 4. 覆蓋本機 data\stock.db（先備份本機原檔以防萬一）
echo [3/4] 覆蓋本機 data\stock.db...
set "LOCAL_DB=%~dp0data\stock.db"
if exist "%LOCAL_DB%" (
    copy /y "%LOCAL_DB%" "%LOCAL_DB%.before-sync" >nul
    echo         本機原檔已備份成 stock.db.before-sync
)
copy /y "%CLOUD_DIR%\%LATEST%" "%LOCAL_DB%" >nul
if errorlevel 1 (
    echo [ERROR] 複製失敗。檢查雲端檔案權限或本機是否被防毒鎖住。
    pause
    exit /b 1
)

REM 5. 完成
echo [4/4] 完成。
echo.
echo 雲端來源：%CLOUD_DIR%\%LATEST%
echo 本機目標：%LOCAL_DB%
echo.
echo 接下來雙擊 launch.bat 開儀表板。
echo （首次使用本機請先跑一次 python -m pytest tests/ -q 驗證 DB 沒問題）
echo.
pause
endlocal
