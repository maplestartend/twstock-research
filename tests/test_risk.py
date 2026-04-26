"""app/risk.py 的單元測試。純函式，不碰 DB（concentration_warnings 除外）。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.risk import (
    atr_stop_loss,
    compute_atr,
    suggest_position_size,
    trailing_atr_stop,
)


class TestComputeATR:
    def test_returns_series_with_same_length(self, synthetic_price_df):
        atr = compute_atr(synthetic_price_df, period=14)
        assert len(atr) == len(synthetic_price_df)

    def test_first_values_are_nan_until_period(self, synthetic_price_df):
        atr = compute_atr(synthetic_price_df, period=14)
        # Wilder smoothing 需要至少 period 期才有值
        assert atr.iloc[:13].isna().all()
        assert not pd.isna(atr.iloc[-1])

    def test_flat_price_gives_small_atr(self, flat_price_df):
        """完全平盤時 ATR 應該接近固定值（= high-low = 2）。"""
        atr = compute_atr(flat_price_df, period=5)
        assert atr.iloc[-1] == pytest.approx(2.0, abs=0.1)

    def test_empty_input(self):
        assert compute_atr(pd.DataFrame()).empty

    def test_too_short_input(self):
        tiny = pd.DataFrame({"high": [1], "low": [0.5], "close": [0.8]})
        # 只有 1 根，應該拿不到 ATR（NaN 或空）
        atr = compute_atr(tiny)
        assert atr.empty or pd.isna(atr.iloc[-1])


class TestATRStopLoss:
    def test_returns_stop_below_entry(self, synthetic_price_df):
        info = atr_stop_loss(synthetic_price_df, multiplier=2.0)
        assert info is not None
        assert info["stop_price"] < info["entry_ref"]
        assert info["distance_pct"] > 0

    def test_higher_multiplier_widens_stop(self, synthetic_price_df):
        tight = atr_stop_loss(synthetic_price_df, multiplier=1.0)
        wide = atr_stop_loss(synthetic_price_df, multiplier=3.0)
        assert wide["stop_price"] < tight["stop_price"]
        assert wide["distance_pct"] > tight["distance_pct"]

    def test_custom_entry_overrides_close(self, synthetic_price_df):
        info = atr_stop_loss(synthetic_price_df, entry_price=200.0, multiplier=2.0)
        assert info["entry_ref"] == 200.0
        assert info["stop_price"] < 200.0

    def test_insufficient_data_returns_none(self):
        tiny = pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=5),
            "high": [10] * 5, "low": [9] * 5, "close": [9.5] * 5,
        })
        assert atr_stop_loss(tiny) is None


class TestTrailingATRStop:
    def test_tracks_peak_since_entry(self, synthetic_price_df):
        entry = synthetic_price_df["date"].iloc[10].strftime("%Y-%m-%d")
        info = trailing_atr_stop(synthetic_price_df, entry)
        assert info is not None
        peak = synthetic_price_df.loc[10:, "close"].max()
        assert info["peak_since_entry"] == pytest.approx(peak, abs=0.1)

    def test_below_stop_flag(self, synthetic_price_df):
        # 第一個進場日 → peak 範圍涵蓋全資料 → latest 通常不會低於 peak - 2ATR，但確認 flag 是 bool
        entry = synthetic_price_df["date"].iloc[0].strftime("%Y-%m-%d")
        info = trailing_atr_stop(synthetic_price_df, entry)
        assert isinstance(info["below_stop"], bool)


class TestSuggestPositionSize:
    def test_basic_calculation(self):
        # 本金 100 萬、2% 風險 = 2 萬；進場 100 / 停損 95 → 每股虧 5 → 4000 股 = 4 張
        ps = suggest_position_size(capital=1_000_000, entry_price=100, stop_price=95, risk_per_trade=0.02)
        assert ps is not None
        assert ps.max_shares == 4000
        assert ps.max_position_value == 400_000
        assert ps.risk_amount == 20_000

    def test_rounds_down_to_full_lot(self):
        # 1.5 張的量應該被截到 1 張
        ps = suggest_position_size(capital=100_000, entry_price=100, stop_price=90, risk_per_trade=0.02)
        # 風險 2000 / 每股虧 10 = 200 股 = 0 張 (< 1 lot)
        assert ps.max_shares == 0

    def test_invalid_stop_returns_none(self):
        assert suggest_position_size(1_000_000, 100, 105, 0.02) is None  # stop > entry
        assert suggest_position_size(1_000_000, 100, 100, 0.02) is None  # stop = entry
        assert suggest_position_size(1_000_000, 0, 50, 0.02) is None     # zero entry

    def test_higher_risk_gives_more_shares(self):
        ps_low = suggest_position_size(1_000_000, 100, 95, 0.01)
        ps_high = suggest_position_size(1_000_000, 100, 95, 0.04)
        assert ps_high.max_shares > ps_low.max_shares
