@echo off
chcp 65001 >nul
REM ============================================================
REM TW Stock — 主選單 (single entry point)
REM   把散落的 launch / stop / restart / status / daily-update /
REM   sync-from-cloud / check-holdings / install-schedule 等 .bat
REM   收進一個選單。雙擊這支就好，其餘 .bat 仍可單獨雙擊 (向後相容)。
REM ============================================================
setlocal
title TW Stock — 主選單

cd /d "%~dp0"

:menu
cls
echo.
echo  =====================================================
echo    台股研究系統 — 主選單
echo  =====================================================
echo.
echo    [ 啟動 / 關閉 ]
echo      1^) 開啟儀表板            ^(launch^)
echo      2^) 關閉儀表板            ^(stop^)
echo      3^) 重啟 + 重算分數        ^(restart，改完 scoring 用^)
echo      4^) 查看狀態              ^(status^)
echo.
echo    [ 資料更新 ]
echo      5^) 立即更新今日資料        ^(daily-update^)
echo      6^) 從雲端拉最新 DB ^(副機^)  ^(sync-from-cloud^)
echo.
echo    [ 持股 ]
echo      7^) 盤後持股檢查           ^(check-holdings^)
echo.
echo    [ 排程 ]
echo      8^) 安裝每日自動更新排程     ^(install-schedule^)
echo      9^) 移除排程              ^(uninstall-schedule^)
echo.
echo      0^) 離開
echo.
echo  =====================================================
set "choice="
set /p "choice=  請輸入選項代號後按 Enter: "

if "%choice%"=="1" goto do_launch
if "%choice%"=="2" goto do_stop
if "%choice%"=="3" goto do_restart
if "%choice%"=="4" goto do_status
if "%choice%"=="5" goto do_update
if "%choice%"=="6" goto do_sync
if "%choice%"=="7" goto do_holdings
if "%choice%"=="8" goto do_install
if "%choice%"=="9" goto do_uninstall
if "%choice%"=="0" goto end
if /i "%choice%"=="q" goto end

echo.
echo   [X] 無效的選項: "%choice%"
timeout /t 2 >nul
goto menu

:do_launch
call "%~dp0launch.bat"
goto menu

:do_stop
call "%~dp0stop.bat"
goto menu

:do_restart
call "%~dp0restart.bat"
goto menu

:do_status
call "%~dp0status.bat"
goto menu

:do_update
call "%~dp0daily-update.bat"
goto menu

:do_sync
call "%~dp0sync-from-cloud.bat"
goto menu

:do_install
call "%~dp0install-schedule.bat"
goto menu

:do_uninstall
call "%~dp0uninstall-schedule.bat"
goto menu

:do_holdings
echo.
set "cap="
set /p "cap=  本金 (直接 Enter 用預設 760000): "
if "%cap%"=="" (
    call "%~dp0check-holdings.bat"
) else (
    call "%~dp0check-holdings.bat" %cap%
)
goto menu

:end
endlocal
exit /b 0
