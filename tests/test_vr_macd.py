"""VR (Volume Ratio 26) 指標 + score_vr_macd 複合評分測試。

VR = (UV + 0.5*FV) / (DV + 0.5*FV) * 100，台股 26 日慣用版。
score_vr_macd 用 VR 分區 ✕ MACD 柱方向組成 11 條 decision rule。
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
# score_vr_macd
# ======================================================================
class TestScoreVrMacd:
    def test_rule_a_low_vr_rising_turning_up(self):
        """A: vr < 80, vr_rising, hist_turning_up → 88."""
        last = _row(vr26=60, macd_hist=0.5)
        prev = _row(vr26=50, macd_hist=-0.2)
        assert rubric.score_vr_macd(last, prev) == 88.0

    def test_rule_g_high_vr_turning_down(self):
        """G: vr >= 450, hist_turning_down → 10."""
        last = _row(vr26=500, macd_hist=-0.3)
        prev = _row(vr26=480, macd_hist=0.1)
        assert rubric.score_vr_macd(last, prev) == 10.0

    def test_returns_none_when_vr_missing(self):
        """vr26 NaN → None。"""
        last = _row(vr26=np.nan, macd_hist=0.5)
        prev = _row(vr26=50, macd_hist=-0.2)
        assert rubric.score_vr_macd(last, prev) is None

    def test_returns_none_when_macd_hist_missing(self):
        """macd_hist NaN → None（two inputs both required）。"""
        last = _row(vr26=60, macd_hist=np.nan)
        prev = _row(vr26=50, macd_hist=0.1)
        assert rubric.score_vr_macd(last, prev) is None

    def test_prev_row_none_falls_back_to_zone_with_hist_pos_bonus(self):
        """prev_row=None：用 zone-only +5（hist_pos）或 -5（hist <= 0）。"""
        # vr=60 → zone=80, hist_pos=True → 80+5 = 85
        last = _row(vr26=60, macd_hist=0.5)
        assert rubric.score_vr_macd(last, prev_row=None) == 85.0
        # vr=60, hist <= 0 → 80-5 = 75
        last2 = _row(vr26=60, macd_hist=-0.5)
        assert rubric.score_vr_macd(last2, prev_row=None) == 75.0

    def test_rule_i_low_vr_turning_up_no_rising(self):
        """I: vr < 40, hist_turning_up（A 不適用因 vr_rising=False）→ 75."""
        last = _row(vr26=30, macd_hist=0.2)
        prev = _row(vr26=35, macd_hist=-0.1)  # vr 下降 → 不滿足 A 的 vr_rising
        assert rubric.score_vr_macd(last, prev) == 75.0

    def test_rule_b_low_vr_rising_hist_pos(self):
        """B: vr < 80, vr_rising, hist_pos（沒到 turning_up）→ 78."""
        last = _row(vr26=60, macd_hist=0.5)
        prev = _row(vr26=50, macd_hist=0.3)  # hist 已正 + 持續正 → 不是 turning_up
        # A: vr_rising=True, hist_turning_up=False (prev_hist > 0)
        # B: vr<80, vr_rising, hist_pos → 78
        assert rubric.score_vr_macd(last, prev) == 78.0

    def test_rule_c_mid_vr_hist_pos_growing(self):
        """C: 80 <= vr < 150, hist_pos, hist_growing → 72."""
        last = _row(vr26=120, macd_hist=0.5)
        prev = _row(vr26=110, macd_hist=0.3)
        assert rubric.score_vr_macd(last, prev) == 72.0

    def test_rule_h_high_vr_hist_pos(self):
        """H: vr >= 450, hist_pos（不是 turning_down）→ 25."""
        last = _row(vr26=500, macd_hist=0.5)
        prev = _row(vr26=480, macd_hist=0.3)  # hist 正且增加 → 不 turning_down
        assert rubric.score_vr_macd(last, prev) == 25.0

    def test_rule_k_fallback_default(self):
        """落到 K：vr=300 (zone=35), hist_pos=False, prev_hist 也不滿足 turning_down → fallback 35-5=30。
        實際上 vr=300 落在 250-450，hist=-0.5, prev_hist=-0.6 → hist_growing=True (rule E)
        改成讓沒人 match：vr=200, hist=-0.5, prev=-0.6 → 150-250 zone, hist_pos=False（D 失敗），
          其他 rule 都不對應 vr=200 → 落 K：zone=70, hist_pos=False → 70-5=65"""
        last = _row(vr26=200, macd_hist=-0.5)
        prev = _row(vr26=210, macd_hist=-0.6)
        assert rubric.score_vr_macd(last, prev) == 65.0

    def test_rule_j_low_vr_not_hist_pos(self):
        """J: vr < 40, hist <= 0 → 35."""
        last = _row(vr26=30, macd_hist=-0.2)
        prev = _row(vr26=35, macd_hist=-0.1)
        # A: hist_turning_up = -0.2 > -0.1? No → A no
        # I: hist_turning_up = No → I no
        # B: vr_rising = 30>35? No → B no
        # J: vr<40 AND not hist_pos → 35
        assert rubric.score_vr_macd(last, prev) == 35.0


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
