"""VR (Volume Ratio 26) 指標 + score_vr_macd（純 VR 版本）測試。

VR = (UV + 0.5*FV) / (DV + 0.5*FV) * 100，台股 26 日慣用版。
score_vr_macd 已改為純 VR 因子（保留舊函式名以維持相容）。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.indicators import technical as tech
from app.scoring import rubric


def _row(**kwargs) -> pd.Series:
    return pd.Series(kwargs)


# ======================================================================
# vr() 指標函式
# ======================================================================
class TestVrIndicator:
    def test_formula_correctness_small_series(self):
        """構造一個 8 天的小資料、period=4，手算 VR：
            day:    0  1  2  3  4  5  6  7
            close: 10 11 12 11 12 13 12 13
            volume:100 200 300 100 200 300 100 200
          delta:    -  +1 +1 -1 +1 +1 -1 +1
        Period=4 從 index=4 開始有值（rolling 需要前 4 筆）：
        idx=3 起算 rolling window 4 (含自身 + 前 3 筆)：
          idx=3 window=[0,1,2,3] (delta: NaN, +1, +1, -1)
            UV = vol(1)+vol(2) = 200+300 = 500
            DV = vol(3) = 100
            FV = 0
            VR = (500 + 0) / (100 + 0) * 100 = 500.0
          idx=4 window=[1,2,3,4] (delta: +1, +1, -1, +1)
            UV = 200+300+200 = 700
            DV = 100
            FV = 0
            VR = 700 / 100 * 100 = 700.0
        """
        close = pd.Series([10, 11, 12, 11, 12, 13, 12, 13], dtype=float)
        volume = pd.Series([100, 200, 300, 100, 200, 300, 100, 200], dtype=float)
        result = tech.vr(close, volume, period=4)
        # 前 4 筆（rolling 暖機）為 NaN
        assert all(pd.isna(result.iloc[:3]))
        # idx=3: VR = 500.0
        assert math.isclose(result.iloc[3], 500.0, abs_tol=0.01)
        # idx=4: VR = 700.0
        assert math.isclose(result.iloc[4], 700.0, abs_tol=0.01)

    def test_warmup_period_is_nan(self):
        """前 `period` rows 應該是 NaN（rolling 暖機）。"""
        close = pd.Series([10.0] * 30)
        volume = pd.Series([100.0] * 30)
        result = tech.vr(close, volume, period=26)
        # 前 25 筆一定 NaN（rolling min_periods=26）
        assert all(pd.isna(result.iloc[:25]))

    def test_all_up_days_caps_at_1000(self):
        """連續 26 個上漲日（DV+0.5*FV == 0）→ cap 1000.0。"""
        # 嚴格遞增 close，ensure 全部 delta>0 → DV=0, FV=0, UV>0 → cap 1000
        close = pd.Series(np.arange(1, 30, dtype=float))  # 1..29 嚴格遞增
        volume = pd.Series([100.0] * 29)
        result = tech.vr(close, volume, period=26)
        # 第 26 筆（index 25）開始有值 — 注意 delta 第 0 筆是 NaN
        # rolling window of 26 從 idx=25 開始 cover [0..25]
        last_valid = result.iloc[-1]
        assert math.isclose(last_valid, 1000.0, abs_tol=0.01), f"expected cap 1000, got {last_valid}"

    def test_entire_window_flat_returns_nan(self):
        """整窗都是平盤（UV == 0 AND DV == 0）→ NaN。"""
        close = pd.Series([100.0] * 30)  # 完全沒變
        volume = pd.Series([1000.0] * 30)
        result = tech.vr(close, volume, period=26)
        # 所有非暖機期的值都該是 NaN
        assert all(pd.isna(result.iloc[26:])), "完全平盤窗應回 NaN"


class TestEnrichAddsVr26:
    def test_vr26_column_present(self):
        """enrich(df) 應該產生 vr26 欄位。"""
        n = 80
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "open": rng.uniform(95, 105, n),
            "high": rng.uniform(100, 110, n),
            "low": rng.uniform(90, 100, n),
            "close": rng.uniform(95, 105, n),
            "volume": rng.uniform(1000, 5000, n),
        })
        out = tech.enrich(df)
        assert "vr26" in out.columns
        # 至少有些非 NaN 值（暖機期之後）
        assert out["vr26"].notna().sum() > 0


# ======================================================================
# score_vr_macd（純 VR）
# ======================================================================
class TestScoreVrMacd:
    def test_returns_zone_score_when_prev_row_is_none(self):
        """prev 缺失時，回傳純 VR 分區 baseline。"""
        assert rubric.score_vr_macd(_row(vr26=60), prev_row=None) == 80.0

    def test_returns_none_when_vr_missing(self):
        """vr26 NaN → None。"""
        last = _row(vr26=np.nan)
        prev = _row(vr26=50)
        assert rubric.score_vr_macd(last, prev) is None

    def test_returns_zone_score_when_prev_vr_missing(self):
        """prev 有但 vr26 缺值時，退回 baseline。"""
        last = _row(vr26=120)
        prev = _row(vr26=np.nan)
        assert rubric.score_vr_macd(last, prev) == 60.0

    def test_low_vr_rising_gets_bonus(self):
        """低量區回升：zone 80 + 8 = 88。"""
        last = _row(vr26=60)
        prev = _row(vr26=50)
        assert rubric.score_vr_macd(last, prev) == 88.0

    def test_low_vr_falling_gets_penalty(self):
        """低量區續降：zone 80 - 10 = 70。"""
        last = _row(vr26=60)
        prev = _row(vr26=75)
        assert rubric.score_vr_macd(last, prev) == 70.0

    def test_normal_zone_rising_small_bonus(self):
        """常態區回升：zone 70 + 5。"""
        last = _row(vr26=200)
        prev = _row(vr26=180)
        assert rubric.score_vr_macd(last, prev) == 75.0

    def test_normal_zone_falling_small_penalty(self):
        """常態區走弱：zone 70 - 5。"""
        last = _row(vr26=200)
        prev = _row(vr26=220)
        assert rubric.score_vr_macd(last, prev) == 65.0

    def test_overheated_rising_gets_penalty(self):
        """過熱區續升：zone 35 - 5 = 30。"""
        last = _row(vr26=300)
        prev = _row(vr26=260)
        assert rubric.score_vr_macd(last, prev) == 30.0

    def test_overheated_falling_gets_relief_bonus(self):
        """過熱區降溫：zone 35 + 8 = 43。"""
        last = _row(vr26=300)
        prev = _row(vr26=340)
        assert rubric.score_vr_macd(last, prev) == 43.0


# ======================================================================
# 權重總和不變式
# ======================================================================
class TestWeightSums:
    def test_short_weights_sum_to_one(self):
        s = sum(rubric.SHORT_TERM_WEIGHTS.values())
        assert math.isclose(s, 1.0, abs_tol=1e-9), f"SHORT_TERM_WEIGHTS sum = {s}"

    def test_mid_weights_sum_to_one(self):
        s = sum(rubric.MID_TERM_WEIGHTS.values())
        assert math.isclose(s, 1.0, abs_tol=1e-9), f"MID_TERM_WEIGHTS sum = {s}"

    def test_vr_macd_in_both_short_and_mid(self):
        """vr_macd 必須出現在短期和中期權重 dict（engine.py 的 parts 有對應 key）。"""
        assert "vr_macd" in rubric.SHORT_TERM_WEIGHTS
        assert "vr_macd" in rubric.MID_TERM_WEIGHTS
