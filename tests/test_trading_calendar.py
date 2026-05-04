"""trading_calendar — 國定假日感知的交易日 / 休市日判斷。

測試重點：
- 過去日期：DB 觀察（daily_price 缺的工作日 = 休市）
- 今天 / 未來日期：INLINE_TWSE_HOLIDAYS 列表 + weekday fallback
- expected_latest_close_date 在 5/1 勞動節這類連休後仍能正確回到上一個交易日
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.data import trading_calendar as tc
from app.data.db import Database


@pytest.fixture
def seeded_db(tmp_path: Path) -> Database:
    """灌入一段 daily_price 樣本：2025-04-25(Fri) ~ 2025-05-05(Mon)。
    其中 2025-05-01(Thu) 是勞動節（NO data） + 5/3-5/4 週末，所以缺三個 calendar 天。
    模擬「今天 5/5 開盤前看 5/4 dashboard」的真實狀況。
    """
    db = Database(tmp_path / "test.db")
    rows = [
        ("2330", "2025-04-25"),  # Fri
        ("2330", "2025-04-28"),  # Mon
        ("2330", "2025-04-29"),  # Tue
        ("2330", "2025-04-30"),  # Wed
        # 2025-05-01 (Thu) 缺資料 = 勞動節
        ("2330", "2025-05-02"),  # Fri  ← 真實 2025/5/2 是 Fri，台股有開
        ("2330", "2025-05-05"),  # Mon
    ]
    df = pd.DataFrame(rows, columns=["stock_id", "date"])
    df["open"] = 100.0
    df["high"] = 100.0
    df["low"] = 100.0
    df["close"] = 100.0
    df["volume"] = 1000.0
    db.upsert_df(df, "daily_price")
    tc.clear_cache()
    return db


def test_observed_holidays_picks_up_missing_weekday(seeded_db: Database):
    """工作日缺資料 → 視為休市。"""
    obs = tc._observed_holidays(seeded_db)
    assert date(2025, 5, 1) in obs, "5/1 應被識別為休市（勞動節）"
    assert date(2025, 4, 30) not in obs, "4/30 有資料，不該是休市"
    assert date(2025, 4, 28) not in obs, "4/28 有資料，不該是休市"


def test_observed_holidays_skips_weekends(seeded_db: Database):
    """週末本來就不算休市（已被 weekday filter 篩掉）。"""
    obs = tc._observed_holidays(seeded_db)
    assert date(2025, 5, 3) not in obs  # Sat
    assert date(2025, 5, 4) not in obs  # Sun


def test_is_trading_day_past_uses_db(seeded_db: Database):
    assert tc.is_trading_day(date(2025, 4, 30), seeded_db) is True   # Wed, has data
    assert tc.is_trading_day(date(2025, 5, 1), seeded_db) is False   # 勞動節
    assert tc.is_trading_day(date(2025, 5, 3), seeded_db) is False   # Sat
    assert tc.is_trading_day(date(2025, 5, 5), seeded_db) is True    # Mon, has data


def test_is_trading_day_future_uses_inline(seeded_db: Database, monkeypatch):
    """未來日期（date > today）靠 INLINE_TWSE_HOLIDAYS。"""
    # 把 trading_calendar 內部的 today 鎖在 2025-04-30，讓 2026 全部都是「未來」
    monkeypatch.setattr("app.data.trading_calendar.date", _fix_today(date(2025, 4, 30)))
    assert tc.is_trading_day(date(2026, 5, 1), seeded_db) is False, "2026-05-01 勞動節"
    assert tc.is_trading_day(date(2026, 6, 19), seeded_db) is False, "2026-06-19 端午節"
    assert tc.is_trading_day(date(2026, 9, 25), seeded_db) is False, "2026-09-25 中秋"
    # 2026-05-04 是 Mon 工作日且不在 inline → 預期是交易日
    assert tc.is_trading_day(date(2026, 5, 4), seeded_db) is True


def test_previous_trading_day_skips_holiday(seeded_db: Database):
    """5/2 之前最近交易日 = 4/30（5/1 勞動節跳過）。"""
    prev = tc.previous_trading_day(date(2025, 5, 2), seeded_db)
    assert prev == date(2025, 4, 30)


def test_previous_trading_day_skips_holiday_and_weekend(seeded_db: Database):
    """5/5 (Mon) 之前最近交易日 = 5/2（5/3-5/4 週末，5/1 勞動節在更早）。"""
    prev = tc.previous_trading_day(date(2025, 5, 5), seeded_db)
    assert prev == date(2025, 5, 2)


def test_expected_latest_close_today_holiday(seeded_db: Database, monkeypatch):
    """今天是勞動節 5/1 → 預期最新交易日 = 4/30。"""
    monkeypatch.setattr("app.data.trading_calendar.date", _fix_today(date(2025, 5, 1)))
    assert tc.expected_latest_close_date(date(2025, 5, 1), seeded_db) == date(2025, 4, 30)


def test_expected_latest_close_today_trading_day(seeded_db: Database, monkeypatch):
    """今天是交易日 → 預期就是今天。"""
    monkeypatch.setattr("app.data.trading_calendar.date", _fix_today(date(2025, 4, 30)))
    assert tc.expected_latest_close_date(date(2025, 4, 30), seeded_db) == date(2025, 4, 30)


def test_calendar_lag_after_labor_day_is_correct(seeded_db: Database, monkeypatch):
    """5/2(Fri) 看 4/30(Wed) 資料：5/1 勞動節 → expected=5/2、lag=2 calendar 天的 (expected − latest)。

    這對應 dashboard `_expected_lag` 用的 ok 門檻計算：lag <= expected_cal_lag 就 fresh。
    """
    monkeypatch.setattr("app.data.trading_calendar.date", _fix_today(date(2025, 5, 2)))
    # latest_date = 4/30 (Wed), expected_latest = 5/2 (Fri, today is itself trading)
    lag = tc.calendar_lag_for_expected(date(2025, 4, 30), date(2025, 5, 2), seeded_db)
    assert lag == 2  # 5/2 - 4/30 = 2 calendar days, but only 5/1 was missed (= holiday)


def test_calendar_lag_zero_when_caught_up(seeded_db: Database, monkeypatch):
    """resync 完之後：latest_date 跟 expected_latest 同一天 → lag=0。"""
    monkeypatch.setattr("app.data.trading_calendar.date", _fix_today(date(2025, 4, 30)))
    lag = tc.calendar_lag_for_expected(date(2025, 4, 30), date(2025, 4, 30), seeded_db)
    assert lag == 0


def _fix_today(d: date):
    """Helper：產生一個 fake date class，把 .today() 鎖在 d。給 trading_calendar.date 替換用。"""
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return d

        @classmethod
        def fromisoformat(cls, s: str) -> date:  # type: ignore[override]
            return date.fromisoformat(s)
    return _FixedDate
