@echo off
chcp 65001 >nul
REM ============================================================
REM pull-latest-db.bat — alias for sync-from-cloud.bat
REM
REM 名稱比較精確（這支是「單向 pull」，不是雙向 sync）。
REM 既有 sync-from-cloud.bat 仍保留，避免破壞使用者雙擊習慣。
REM ============================================================
call "%~dp0sync-from-cloud.bat" %*
