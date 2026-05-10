@echo off
chcp 65001 >nul
REM ============================================================
REM TW Stock Dashboard - Daily Data Update
REM Run after market close (16:30+, OpenAPI final data)
REM Pushes to Discord on success/failure (requires config.yaml notify)
REM ============================================================
setlocal enabledelayedexpansion
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

REM 跳過 market_update 內建的 MOPS bulk（每天打一次太貴且多餘），
REM 改由下面的 refresh_recent_financials 在公告期內精準觸發。
%PYTHON% -m scripts.market_update --push --no-financials
set RC=%errorlevel%

REM 公告期內自動回補新季財報 (Q1=5/15, Q2=8/14, Q3=11/14, Q4=次年3/31)
REM idempotent: MOPS 沒新資料就什麼都不寫。
REM market_update 失敗時跳過, MOPS 通常會跟著掛, 徒增雜訊。
if %RC% equ 0 (
    %PYTHON% -m scripts.refresh_recent_financials --quiet
    set RC2=!errorlevel!
    if !RC2! neq 0 (
        set RC=!RC2!
        echo [WARN] refresh_recent_financials failed with code !RC2!.
    )
) else (
    echo [SKIP] refresh_recent_financials skipped because market_update failed.
)

REM 全市場最新月營收 (TWSE/TPEX OpenAPI, ~1 秒)
REM idempotent: 月初 1~10 號 OpenAPI 仍是上月資料 (re-upsert 無副作用),
REM 月中 10 號 deadline 後才會換到當月。每天順手跑就不會漏抓。
if %RC% equ 0 (
    %PYTHON% -m scripts.update_monthly_revenue --mops
    set RC3=!errorlevel!
    if !RC3! neq 0 (
        set RC=!RC3!
        echo [WARN] update_monthly_revenue --mops failed with code !RC3!.
    )
) else (
    echo [SKIP] update_monthly_revenue skipped because market_update failed.
)

REM ETF 還原價 backfill (yfinance auto_adjust)
REM 0050 / 00631L / 00692 / 00878 的 daily_price_adj 跟著 daily_price 一起更新,
REM 否則最新交易日的 close_adj 會是 NULL → 回測 / scoring 對 ETF 斷層。
REM idempotent: INSERT OR REPLACE，每天跑沒副作用。
if %RC% equ 0 (
    %PYTHON% -m scripts.backfill_etf_adj_yfinance --quiet
    set RC4=!errorlevel!
    if !RC4! neq 0 (
        echo [WARN] backfill_etf_adj_yfinance failed with code !RC4!.
    )
)

echo.
if %RC% neq 0 (
    echo [ERROR] Update failed with code %RC%. Check logs/app.log or Discord.
    echo.
    pause
) else (
    echo [DONE] Update successful.
    echo.
    echo Window closes in 10 seconds, or press any key to close now.
    timeout /t 10 >nul
)
