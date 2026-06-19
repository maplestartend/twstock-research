"""scripts/backfill_etf_adj_yfinance.py 純邏輯測試（效能優化波次一 #2）。

不打網路、不吃 prod DB：用記憶體 sqlite + 合成 DataFrame 驗增量起點決策、overlap 還原
基準變動偵測、big-jump 截斷。核心不變量是「增量模式不會在配息/分割日於新舊還原基準間
留下斷層」——靠 _basis_changed 偵測到變動就升級 full 重抓（避免 CLAUDE.md 地雷 #7 的失真）。
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

pytest.importorskip("yfinance")  # 模組 import 需要 yfinance（CI 由 requirements.lock 安裝）

from scripts.backfill_etf_adj_yfinance import (  # noqa: E402
    FULL_START,
    _basis_changed,
    _resolve_start,
    _truncate_bad_jumps,
)


def _df(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000] * len(closes),
    })


# ---- _truncate_bad_jumps ----

def test_truncate_no_jump_keeps_all():
    df = _df(["2025-01-02", "2025-01-03", "2025-01-06"], [100.0, 101.0, 102.0])
    assert len(_truncate_bad_jumps(df, "0050")) == 3


def test_truncate_drops_before_big_jump():
    # 22× gap（模擬 00631L 2014→2015 拼接 bug）：截掉 jump 之前
    df = _df(["2014-12-30", "2014-12-31", "2015-01-05", "2015-01-06"],
             [5.0, 5.1, 112.0, 113.0])
    out = _truncate_bad_jumps(df, "00631L")
    assert list(out["date"]) == ["2015-01-05", "2015-01-06"]


def test_truncate_empty_df():
    assert _truncate_bad_jumps(pd.DataFrame(columns=["date", "close"]), "X").empty


# ---- _basis_changed ----

def test_basis_unchanged_when_overlap_matches():
    df = _df(["2026-06-10", "2026-06-11"], [50.0, 50.5])
    stored = {"2026-06-10": 50.0, "2026-06-11": 50.5}
    assert _basis_changed(df, stored) is False


def test_basis_changed_when_dividend_rescales_history():
    # 配息後 yfinance 把舊值往下調 ~2% → 應偵測為基準變動 → 觸發 full 重抓
    df = _df(["2026-06-10", "2026-06-11"], [49.0, 49.5])
    stored = {"2026-06-10": 50.0, "2026-06-11": 50.5}
    assert _basis_changed(df, stored) is True


def test_basis_tiny_noise_not_flagged():
    # 0.1% 取數浮動（< 0.5% 容差）不該誤判
    df = _df(["2026-06-10"], [50.05])
    assert _basis_changed(df, {"2026-06-10": 50.0}) is False


def test_basis_no_overlap_returns_false():
    df = _df(["2026-06-12"], [50.0])
    assert _basis_changed(df, {"2026-06-10": 50.0}) is False


def test_basis_empty_stored_false():
    assert _basis_changed(_df(["2026-06-10"], [50.0]), {}) is False


# ---- _resolve_start ----

def _cur_with(rows: list[tuple]) -> sqlite3.Cursor:
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE daily_price_adj (date TEXT, stock_id TEXT, close_adj REAL, "
        "open_adj REAL, high_adj REAL, low_adj REAL, PRIMARY KEY(stock_id,date))"
    )
    con.executemany(
        "INSERT INTO daily_price_adj(stock_id,date,close_adj) VALUES (?,?,?)", rows
    )
    return con.cursor()


def test_resolve_full_flag_overrides():
    cur = _cur_with([("0050", "2026-06-18", 50.0)])
    start, mode, prior = _resolve_start(cur, "0050", full=True, explicit_start=None)
    assert (start, mode, prior) == (FULL_START, "full", "2026-06-18")


def test_resolve_explicit_start():
    cur = _cur_with([("0050", "2026-06-18", 50.0)])
    start, mode, _ = _resolve_start(cur, "0050", full=False, explicit_start="2020-01-01")
    assert (start, mode) == ("2020-01-01", "explicit")


def test_resolve_incremental_from_prior_max():
    cur = _cur_with([("0050", "2026-06-18", 50.0)])
    start, mode, prior = _resolve_start(cur, "0050", full=False, explicit_start=None)
    # 2026-06-18 − OVERLAP_DAYS(10) = 2026-06-08
    assert (start, mode, prior) == ("2026-06-08", "incremental", "2026-06-18")


def test_resolve_full_when_no_prior_data():
    cur = _cur_with([])  # 空表 → 首次 backfill 一律 full
    assert _resolve_start(cur, "0050", full=False, explicit_start=None) == (FULL_START, "full", None)
