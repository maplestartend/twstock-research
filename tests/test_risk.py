"""app/risk.py 的單元測試。純函式，不碰 DB（concentration_warnings 除外）。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.risk import (
    atr_stop_loss,
    compute_atr,
    suggest_position_size,
    trailing_atr_stop,
    trailing_atr_take_profit,
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

    def test_future_entry_clamps_to_latest_data(self, synthetic_price_df):
        """資料源延遲時不該回 None（避免 UI 整個區塊消失）。"""
        latest = synthetic_price_df["date"].max()
        future = (latest + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        info = trailing_atr_stop(synthetic_price_df, future)
        assert info is not None
        assert info["latest_close"] == round(float(synthetic_price_df["close"].iloc[-1]), 2)


class TestTrailingATRTakeProfit:
    """Chandelier 動態停利 — 公式 peak_high - K×ATR + armed gate。"""

    def test_returns_dict_with_required_fields(self, synthetic_price_df):
        entry = synthetic_price_df["date"].iloc[5].strftime("%Y-%m-%d")
        info = trailing_atr_take_profit(
            synthetic_price_df, entry, entry_price=100.0,
        )
        assert info is not None
        for k in ("take_profit_price", "atr", "peak_since_entry", "latest_close",
                  "days_held", "unrealized_pnl_pct", "armed", "triggered",
                  "multiplier", "arm_pnl_threshold", "arm_days_threshold"):
            assert k in info

    def test_peak_uses_high_not_close(self, synthetic_price_df):
        """Chandelier 原始公式錨定 high；peak_since_entry 應 ≥ max(close)。"""
        entry = synthetic_price_df["date"].iloc[5].strftime("%Y-%m-%d")
        info = trailing_atr_take_profit(synthetic_price_df, entry, entry_price=100.0)
        max_close_since = synthetic_price_df.loc[5:, "close"].max()
        assert info["peak_since_entry"] >= max_close_since

    def test_armed_requires_pnl_threshold(self, synthetic_price_df):
        """進場價設超高 → 浮盈遠低於 8% → armed=False。"""
        entry = synthetic_price_df["date"].iloc[5].strftime("%Y-%m-%d")
        info = trailing_atr_take_profit(
            synthetic_price_df, entry, entry_price=10_000.0,  # 怎樣都虧
        )
        assert info["armed"] is False
        assert info["triggered"] is False

    def test_armed_requires_days_threshold(self, synthetic_price_df):
        """進場日設在最後一根 → days_held=1 < arm_days=5 → armed=False。"""
        last_idx = len(synthetic_price_df) - 1
        entry = synthetic_price_df["date"].iloc[last_idx].strftime("%Y-%m-%d")
        info = trailing_atr_take_profit(
            synthetic_price_df, entry, entry_price=1.0,  # 浮盈夠大
        )
        assert info is not None
        assert info["days_held"] == 1
        assert info["armed"] is False

    def test_armed_when_both_conditions_met(self, synthetic_price_df):
        """很早進場 + 進場價極低 → armed=True。"""
        entry = synthetic_price_df["date"].iloc[0].strftime("%Y-%m-%d")
        info = trailing_atr_take_profit(
            synthetic_price_df, entry, entry_price=1.0, arm_pnl=0.08, arm_days=5,
        )
        assert info["armed"] is True
        assert info["unrealized_pnl_pct"] > 0.08
        assert info["days_held"] >= 5

    def test_triggered_when_close_falls_to_tp_line(self):
        """構造：50 根遞增到 124，最後一根回落到 110（仍 armed，但跌穿 peak − K×ATR）。"""
        n = 50
        close = [100.0 + i * 0.5 for i in range(n - 1)] + [110.0]
        high = [c + 0.5 for c in close]
        low = [c - 0.5 for c in close]
        open_ = close.copy()
        df = pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=n, freq="B"),
            "open": open_, "high": high, "low": low, "close": close,
        })
        info = trailing_atr_take_profit(
            df, entry_date="2025-01-02", entry_price=100.0,
            multiplier=3.0, arm_pnl=0.08, arm_days=5,
        )
        # 浮盈 +10% > 8%、持有 50 日 > 5 → armed
        assert info["armed"] is True
        # peak ≈ 124.5（最後一根之前的 high），close=110 應低於 peak − 3×ATR
        assert info["latest_close"] < info["take_profit_price"]
        assert info["triggered"] is True

    def test_higher_multiplier_widens_tp_line(self, synthetic_price_df):
        """K 越大、停利線離 peak 越遠（觸發越保守）。"""
        entry = synthetic_price_df["date"].iloc[0].strftime("%Y-%m-%d")
        tight = trailing_atr_take_profit(synthetic_price_df, entry, entry_price=1.0, multiplier=2.0)
        wide = trailing_atr_take_profit(synthetic_price_df, entry, entry_price=1.0, multiplier=4.0)
        assert wide["take_profit_price"] < tight["take_profit_price"]

    def test_invalid_entry_price_returns_none(self, synthetic_price_df):
        entry = synthetic_price_df["date"].iloc[0].strftime("%Y-%m-%d")
        assert trailing_atr_take_profit(synthetic_price_df, entry, entry_price=0) is None
        assert trailing_atr_take_profit(synthetic_price_df, entry, entry_price=-5) is None

    def test_insufficient_data_returns_none(self):
        tiny = pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=5),
            "high": [10] * 5, "low": [9] * 5, "close": [9.5] * 5,
        })
        assert trailing_atr_take_profit(tiny, "2025-01-01", entry_price=9.0) is None

    def test_entry_date_one_day_after_data_clamps_to_latest(self, synthetic_price_df):
        """資料源延遲一兩天時（TPEX 常見）clamp 到最新日，仍能算 trailing TP。
        否則 UI 整個區塊會消失，體驗差。"""
        latest = synthetic_price_df["date"].max()
        future = (latest + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        info = trailing_atr_take_profit(synthetic_price_df, future, entry_price=1.0)
        assert info is not None
        assert info["days_held"] == 1   # 只有最新一根 K
        assert info["latest_close"] == round(float(synthetic_price_df["close"].iloc[-1]), 2)

    def test_entry_date_far_in_future_still_returns_via_clamp(self, synthetic_price_df):
        info = trailing_atr_take_profit(
            synthetic_price_df, "2099-01-01", entry_price=1.0,
        )
        # clamp 到最新日 → 仍可計算（不再回 None）
        assert info is not None
        assert info["days_held"] == 1


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
