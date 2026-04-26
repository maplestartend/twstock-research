"""還原價計算 (compute_adj_series) 的單元測試。

重點：因子鏈邏輯、事件日邊界、無事件、多事件疊加。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.data.adjuster import compute_adj_series


def _make_prices(dates: list[str], closes: list[float]) -> pd.DataFrame:
    """快速建構 daily_price DataFrame。"""
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "stock_id": "T",
        "open": closes, "high": closes, "low": closes, "close": closes,
    })


def _make_events(events: list[tuple[str, float]]) -> pd.DataFrame:
    """[(date, factor), ...] → DataFrame"""
    return pd.DataFrame({
        "date": pd.to_datetime([e[0] for e in events]),
        "stock_id": "T",
        "event_type": ["dividend"] * len(events),
        "factor": [e[1] for e in events],
    })


class TestComputeAdjSeries:
    def test_no_events_returns_original_prices(self):
        prices = _make_prices(["2025-01-01", "2025-01-02", "2025-01-03"], [100, 101, 102])
        result = compute_adj_series(prices, pd.DataFrame())
        assert list(result["close_adj"]) == [100, 101, 102]

    def test_single_event_applied_before_date(self):
        """2025-01-03 除息 factor=0.9：之前的日子 × 0.9，當天與之後不變。"""
        prices = _make_prices(
            ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
            [100, 100, 100, 100],
        )
        events = _make_events([("2025-01-03", 0.9)])
        result = compute_adj_series(prices, events)
        assert result["close_adj"].iloc[0] == pytest.approx(90)
        assert result["close_adj"].iloc[1] == pytest.approx(90)
        assert result["close_adj"].iloc[2] == pytest.approx(100)  # 當天不乘
        assert result["close_adj"].iloc[3] == pytest.approx(100)

    def test_multiple_events_compound(self):
        """兩個事件：2025-01-02 factor=0.9、2025-01-04 factor=0.8。
        2025-01-01 的因子 = 0.9 × 0.8 = 0.72；
        2025-01-02 當天 × 0.8（僅 01-04 事件作用）；
        2025-01-03 × 0.8；
        2025-01-04 當天及以後不乘。
        """
        prices = _make_prices(
            ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
            [100, 100, 100, 100],
        )
        events = _make_events([("2025-01-02", 0.9), ("2025-01-04", 0.8)])
        result = compute_adj_series(prices, events)
        assert result["close_adj"].iloc[0] == pytest.approx(72)
        assert result["close_adj"].iloc[1] == pytest.approx(80)
        assert result["close_adj"].iloc[2] == pytest.approx(80)
        assert result["close_adj"].iloc[3] == pytest.approx(100)

    def test_empty_prices_returns_empty(self):
        result = compute_adj_series(pd.DataFrame(), pd.DataFrame())
        assert result.empty
        assert set(result.columns) >= {"date", "stock_id", "close_adj"}

    def test_ohlc_all_adjusted_consistently(self):
        """OHLC 四個欄位應該都被同一個因子調整，比例關係保持。"""
        prices = pd.DataFrame({
            "date": pd.to_datetime(["2025-01-01", "2025-01-02"]),
            "stock_id": "T",
            "open": [95, 100], "high": [105, 110], "low": [93, 98], "close": [100, 105],
        })
        events = _make_events([("2025-01-02", 0.5)])
        result = compute_adj_series(prices, events)
        # 第 0 列被 × 0.5
        assert result["open_adj"].iloc[0] == pytest.approx(47.5)
        assert result["high_adj"].iloc[0] == pytest.approx(52.5)
        assert result["low_adj"].iloc[0] == pytest.approx(46.5)
        assert result["close_adj"].iloc[0] == pytest.approx(50)
        # 第 1 列（事件當天）不乘
        assert result["close_adj"].iloc[1] == pytest.approx(105)

    def test_event_before_all_prices_has_no_effect(self):
        """事件日早於所有 price，任何 price 都 >= event.date，因子全為 1。"""
        prices = _make_prices(["2025-06-01", "2025-06-02"], [100, 101])
        events = _make_events([("2025-01-01", 0.5)])
        result = compute_adj_series(prices, events)
        assert result["close_adj"].iloc[0] == pytest.approx(100)
        assert result["close_adj"].iloc[1] == pytest.approx(101)
