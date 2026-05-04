"""market_update + snapshot_today 必須在預設模式下產出非 NULL 的 long 分數。

回歸測試起源：[2026-05-04] 使用者觀察到雷達掃描的長期分數整欄消失。
追查發現 daily-update.bat → market_update.py 的 `--snapshot-with-fundamentals` 旗標預設
False，傳給 `radar.score_all(include_fundamentals=False)` → fund DataFrames 全空 →
score_long_term 對所有股票回 None → signal_history.long 全 NULL。

修法是把預設改成 True，並把舊旗標改成 `--skip-snapshot-fundamentals` opt-out。本測試
直接 spy `snapshot_today`，確保 main() 在 default args 下會帶 include_fundamentals=True
進去（不是用 mock 跑全 snapshot — 那會載入 2300 檔資料，太慢）。
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_market_update_pipeline():
    """所有 market_update 內會打 DB / 網路的呼叫都 stub 掉，只留 snapshot_today 觀察。"""
    with patch("scripts.market_update.MarketUpdater") as mu, \
         patch("scripts.market_update.snapshot_today") as snap, \
         patch("scripts.market_update.generate_daily_report") as report, \
         patch("scripts.market_update.run_daily_backup") as backup, \
         patch("scripts.market_update.run_context"), \
         patch("scripts.market_update.Database") as db, \
         patch("scripts.market_update.Config") as cfg, \
         patch("scripts.market_update.notify"), \
         patch("scripts.market_update.wl_mod") as wl, \
         patch("scripts.market_update.FinMindFetcher"), \
         patch("scripts.market_update.update_stock_adjusted"), \
         patch("scripts.market_update.fetch_latest_financials_all") as mops, \
         patch("scripts.market_update.fetch_history_income_statement"):
        cfg.load.return_value.database.path = ":memory:"
        cfg.load.return_value.fetch.start_date = "2024-01-01"
        cfg.load.return_value.fetch.request_delay = 0
        cfg.load.return_value.finmind.token = ""
        cfg.load.return_value.logging.file = pytest.importorskip("pathlib").Path(__file__).parent / "_test.log"
        wl.load.return_value = {}
        snap.return_value = 0
        report.return_value = "/tmp/r.md"
        backup.return_value = None
        mops.return_value = {}
        yield {"snapshot": snap, "updater": mu}


def _run_main_with_args(argv: list[str]) -> int:
    from scripts.market_update import main as mu_main
    old = sys.argv
    try:
        sys.argv = ["market_update", *argv]
        return mu_main()
    finally:
        sys.argv = old


def test_default_invocation_passes_include_fundamentals_true(mock_market_update_pipeline):
    """daily-update.bat 用的是 `market_update --push --no-financials`。
    預設情況下 snapshot_today 必須收到 include_fundamentals=True，否則 long 全 NULL。"""
    snap = mock_market_update_pipeline["snapshot"]
    _run_main_with_args(["--push", "--no-financials"])
    snap.assert_called_once()
    _, kwargs = snap.call_args
    assert kwargs.get("include_fundamentals") is True, (
        "daily-update 預設下 include_fundamentals 必須為 True；False 會讓 signal_history.long "
        "全 NULL，雷達掃描的長期分數欄就會空掉（2026-05-04 的 regression）"
    )


def test_skip_snapshot_fundamentals_flag_opt_out(mock_market_update_pipeline):
    """顯式加 --skip-snapshot-fundamentals 才退回快速但無 long 的路徑（給 backfill 大量歷史日用）。"""
    snap = mock_market_update_pipeline["snapshot"]
    _run_main_with_args(["--skip-snapshot-fundamentals", "--no-financials"])
    snap.assert_called_once()
    _, kwargs = snap.call_args
    assert kwargs.get("include_fundamentals") is False


def test_legacy_snapshot_with_fundamentals_flag_is_noop(mock_market_update_pipeline):
    """舊旗標 --snapshot-with-fundamentals 現在是預設行為，留著只為 cron 向後相容。
    帶不帶它結果一樣（include_fundamentals=True）。"""
    snap = mock_market_update_pipeline["snapshot"]
    _run_main_with_args(["--snapshot-with-fundamentals", "--no-financials"])
    snap.assert_called_once()
    _, kwargs = snap.call_args
    assert kwargs.get("include_fundamentals") is True
