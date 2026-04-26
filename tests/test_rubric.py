"""app/scoring/rubric.py 的單元測試。

重點：所有 score_* 函式輸出必須在 [0, 100]；
**新設計（2026-04-25）**：缺資料回傳 None（不是 50），由上層 None-aware 加權跳過該維度。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.scoring import rubric


def _row(**kwargs) -> pd.Series:
    """快速建一個 pd.Series 當 last_row。"""
    return pd.Series(kwargs)


class TestScoreBounds:
    """任何 score_* 在資料齊全時輸出必須在 [0, 100]。"""

    @pytest.mark.parametrize("fn,arg", [
        (rubric.score_ma_alignment_short, _row(close=100, ma5=99, ma10=98, ma20=97, ma60=96)),
        (rubric.score_rsi, _row(rsi14=75)),
        (rubric.score_bollinger, _row(bb_pos=0.5)),
        # vol_ratio5 是新欄位名（technical.enrich 寫入的）
        (rubric.score_volume, _row(vol_ratio5=1.5)),
    ])
    def test_within_bounds(self, fn, arg):
        val = fn(arg)
        assert val is not None, "資料齊全時不應回 None"
        assert 0 <= val <= 100

    def test_missing_data_returns_none(self):
        """重構後：缺資料回傳 None 而非中性 50，由上層 _weighted 跳過該項並 re-normalize。"""
        # MA 全部 NaN → None
        row = _row(close=np.nan, ma5=np.nan, ma10=np.nan, ma20=np.nan, ma60=np.nan)
        assert rubric.score_ma_alignment_short(row) is None
        # RSI NaN → None
        assert rubric.score_rsi(_row(rsi14=np.nan)) is None
        # KD 缺值 → None
        assert rubric.score_kd(_row(k9=np.nan, d9=np.nan), prev_row=None) is None
        # MACD 缺值 → None
        assert rubric.score_macd(_row(macd_hist=np.nan), prev_row=None) is None
        # Bollinger 缺值 → None
        assert rubric.score_bollinger(_row(bb_pos=np.nan)) is None
        # Volume 缺值 → None
        assert rubric.score_volume(_row(vol_ratio5=np.nan)) is None


class TestMAAlignment:
    def test_full_bullish_alignment_high_score(self):
        # 完美多頭排列
        row = _row(close=110, ma5=108, ma10=105, ma20=102, ma60=100)
        score = rubric.score_ma_alignment_short(row)
        assert score >= 80, f"多頭排列應給高分，實際 {score}"

    def test_full_bearish_alignment_low_score(self):
        # 完美空頭：收盤跌破所有均線
        row = _row(close=90, ma5=100, ma10=105, ma20=110, ma60=115)
        score = rubric.score_ma_alignment_short(row)
        assert score <= 30, f"空頭排列應給低分，實際 {score}"


class TestRSI:
    def test_neutral_rsi_gives_mid_score(self):
        # 50 落在 50-60 band：回傳 55
        score = rubric.score_rsi(_row(rsi14=50))
        assert 40 <= score <= 60

    def test_overbought_severely_penalized(self):
        # RSI 85 ≥ 80：rubric 回 15，屬強烈警示區
        score = rubric.score_rsi(_row(rsi14=85))
        assert score <= 20, f"極端超買應得低分，實際 {score}"

    def test_mildly_overbought_penalized(self):
        # RSI 75：落在 70-80 band，回 35（比中性 50 低）
        score = rubric.score_rsi(_row(rsi14=75))
        assert score < 50

    def test_oversold_rebound_zone_rewarded(self):
        # RSI 25 < 30：rubric 回 75，反彈區
        score = rubric.score_rsi(_row(rsi14=25))
        assert score >= 70


class TestRSIStrongStockRelief:
    """強勢股 (ma5>ma10>ma20>ma60 且 close>ma5) 的高 RSI 不該被當超買重罰。"""

    def _strong_row(self, rsi: float) -> pd.Series:
        # close > ma5 > ma10 > ma20 > ma60 → 強勢股
        return _row(rsi14=rsi, close=110, ma5=108, ma10=105, ma20=100, ma60=95)

    def _weak_row(self, rsi: float) -> pd.Series:
        # 沒有 ma 欄位 → 走原 logic
        return _row(rsi14=rsi)

    def test_70_to_80_strong_stays_neutral(self):
        # 強勢股 RSI 75 → 50（中性）；弱勢股 → 35（小罰）
        assert rubric.score_rsi(self._strong_row(75)) == 50.0
        assert rubric.score_rsi(self._weak_row(75)) == 35.0

    def test_above_80_strong_eased(self):
        # 強勢股 RSI 85 → 35；弱勢股 → 15
        assert rubric.score_rsi(self._strong_row(85)) == 35.0
        assert rubric.score_rsi(self._weak_row(85)) == 15.0

    def test_low_rsi_unchanged_regardless(self):
        # 低 RSI 不受 strong-stock relief 影響
        assert rubric.score_rsi(self._strong_row(25)) == rubric.score_rsi(self._weak_row(25))

    def test_partial_ma_alignment_not_strong(self):
        # ma5>ma10>ma20 但 ma20<ma60（破線）→ 不算強勢，仍走原本扣分
        row = _row(rsi14=85, close=110, ma5=108, ma10=105, ma20=100, ma60=120)
        assert rubric.score_rsi(row) == 15.0


class TestDividendYieldZ:
    """殖利率 z-score 優先於絕對閾值。"""

    def test_z_above_one_high_score(self):
        # z >= +1.0 → 75
        assert rubric.score_dividend({"dividend_yield_z": 1.2}) == 75.0

    def test_z_above_1_5_top_score(self):
        # z >= +1.5 → 85（相當於同產業前段班）
        assert rubric.score_dividend({"dividend_yield_z": 2.0}) == 85.0

    def test_z_zero_neutral(self):
        # z = 0（產業平均）→ 55
        assert rubric.score_dividend({"dividend_yield_z": 0.0}) == 55.0

    def test_z_below_minus_one_low(self):
        # z < -1.0 → 30
        assert rubric.score_dividend({"dividend_yield_z": -2.0}) == 30.0

    def test_z_takes_precedence_over_absolute(self):
        # 同時有 z 和 yield，應走 z
        # 殖利率 5%（絕對 75）但 z=-1（同產業墊底）→ 走 z 路徑得 35
        assert rubric.score_dividend({"dividend_yield_z": -1.0, "dividend_yield": 5.0}) == 35.0

    def test_falls_back_to_absolute_when_no_z(self):
        # 沒有 z → 用絕對閾值
        assert rubric.score_dividend({"dividend_yield": 5.0}) == 75.0


class TestValuationDimensions:
    """score_valuation 是 PER / PER 分位 / PBR / PEG 的複合平均，每維度 None-safe。"""

    def test_per_only(self):
        # 只有 PER（沒分位、PBR、CAGR）→ 走第一條規則
        # PER 12 → 75
        assert rubric.score_valuation({"per": 12.0}) == 75.0

    def test_per_with_percentile(self):
        # PER 12 (75) + 分位 0.2 (低分位 = 便宜 → 80) → 平均 77.5
        s = rubric.score_valuation({"per": 12.0, "per_percentile": 0.2})
        assert abs(s - 77.5) < 0.01

    def test_pbr_low_increases_score(self):
        # 加 PBR 0.8（便宜，80）會把整體拉高
        # PER 30（< 50 → 30）+ PBR 0.8（80）→ 平均 55
        no_pbr = rubric.score_valuation({"per": 30.0})
        with_pbr = rubric.score_valuation({"per": 30.0, "pbr": 0.8})
        assert with_pbr > no_pbr
        assert abs(with_pbr - 55.0) < 0.01

    def test_pbr_high_decreases_score(self):
        # PBR 8（很貴，20）拉低整體
        s = rubric.score_valuation({"per": 12.0, "pbr": 8.0})
        # PER 12 (75) + PBR 8 (20) → 47.5
        assert abs(s - 47.5) < 0.01

    def test_peg_under_one_great(self):
        # PER 20、CAGR 30% → PEG = 20 / 30 = 0.67 (< 1) → 75
        # 子分數: PER 20 (45 ← per<30 bracket), PEG 0.67 (75) → 平均 60
        s = rubric.score_valuation({"per": 20.0, "eps_cagr_3y": 0.30})
        assert abs(s - 60.0) < 0.01

    def test_peg_above_two_penalized(self):
        # PER 30、CAGR 10% → PEG = 30 / 10 = 3 (> 2) → 20
        # 子分數: PER 30 (30 ← per<50 bracket), PEG 3 (20) → 平均 25
        s = rubric.score_valuation({"per": 30.0, "eps_cagr_3y": 0.10})
        assert abs(s - 25.0) < 0.01

    def test_peg_skipped_when_negative_growth(self):
        # CAGR < 0 → 不算 PEG（用負數算 PEG 沒意義）
        # 只剩 PER 30 → 30
        s = rubric.score_valuation({"per": 30.0, "eps_cagr_3y": -0.05})
        assert s == 30.0

    def test_returns_none_when_no_data(self):
        assert rubric.score_valuation({}) is None
        assert rubric.score_valuation({"per": None, "pbr": None}) is None

    def test_pbr_only_works(self):
        # 沒 PER 也能用 PBR 算
        s = rubric.score_valuation({"pbr": 1.5})  # 1-2 → 70
        assert s == 70.0


class TestLinearHelper:
    """_linear 是 internal helper：呼叫前需確保 x 不是缺失值（由各 score_* 函式自己負責 None 檢查）。"""

    def test_maps_to_zero_at_lo(self):
        assert rubric._linear(0, 0, 100) == 0.0

    def test_maps_to_hundred_at_hi(self):
        assert rubric._linear(100, 0, 100) == 100.0

    def test_reverse_flips_direction(self):
        assert rubric._linear(0, 0, 100, reverse=True) == 100.0
        assert rubric._linear(100, 0, 100, reverse=True) == 0.0

    def test_out_of_range_clipped(self):
        assert rubric._linear(-50, 0, 100) == 0.0
        assert rubric._linear(200, 0, 100) == 100.0

    def test_zero_range_returns_neutral(self):
        """lo == hi 時無法映射，應給中性。"""
        assert rubric._linear(50, 100, 100) == 50.0


class TestIsMissing:
    """_is_missing 統一判 None / NaN / Inf / pd.NA。"""

    def test_none(self):
        assert rubric._is_missing(None) is True

    def test_nan(self):
        assert rubric._is_missing(float("nan")) is True

    def test_inf(self):
        assert rubric._is_missing(float("inf")) is True

    def test_pd_na(self):
        assert rubric._is_missing(pd.NA) is True

    def test_valid_number(self):
        assert rubric._is_missing(0) is False
        assert rubric._is_missing(42.5) is False
        assert rubric._is_missing(-1) is False


class TestEpsCagr3y:
    """S2-9：long-term EPS 用 3 年 CAGR，不能跟 mid 的 yoy 同來源 (avoid double counting)。"""

    def test_cutoff_endpoints(self):
        # 線性 [-10%, +20%] → [0, 100]。中點 = +5% → 50 分。
        assert rubric.score_eps_cagr_3y({"eps_cagr_3y": -0.10}) == 0.0
        assert rubric.score_eps_cagr_3y({"eps_cagr_3y": 0.20}) == 100.0
        # 中點 +5% → 50 ± 1
        assert abs(rubric.score_eps_cagr_3y({"eps_cagr_3y": 0.05}) - 50.0) < 1.0

    def test_clipped_outside_range(self):
        # 30% → clip 到 100
        assert rubric.score_eps_cagr_3y({"eps_cagr_3y": 0.30}) == 100.0
        # -20% → clip 到 0
        assert rubric.score_eps_cagr_3y({"eps_cagr_3y": -0.20}) == 0.0

    def test_zero_cagr_below_neutral(self):
        # CAGR = 0% (停滯) 在 [-10%, +20%] 線性映射下約 33 分（不是中性 50）。
        # 這是刻意的：3 年 EPS 沒成長對「長期價值」維度本來就是負分項
        score = rubric.score_eps_cagr_3y({"eps_cagr_3y": 0.0})
        assert 30 < score < 40

    def test_missing_returns_none(self):
        # 沒這 key（< 16 季資料、新上市股）→ None，由 _weighted 跳過
        assert rubric.score_eps_cagr_3y({}) is None
        # 有 yoy 但沒 cagr 也算 None（不會自動 fallback 到 yoy）
        assert rubric.score_eps_cagr_3y({"eps_yoy": 0.30}) is None

    def test_long_does_not_double_count_yoy(self):
        """關鍵不變式：long 的 EPS 維度不該因 yoy 變動而連動，必須只看 cagr。"""
        # 兩筆 fund：CAGR 相同、yoy 不同 → score_eps_cagr_3y 一樣
        f1 = {"eps_cagr_3y": 0.10, "eps_yoy": 0.50}    # 短期爆衝
        f2 = {"eps_cagr_3y": 0.10, "eps_yoy": -0.20}   # 短期回檔
        assert rubric.score_eps_cagr_3y(f1) == rubric.score_eps_cagr_3y(f2)
        # 但 mid 的 score_eps_growth (吃 yoy) 必然不一樣
        assert rubric.score_eps_growth(f1) != rubric.score_eps_growth(f2)
