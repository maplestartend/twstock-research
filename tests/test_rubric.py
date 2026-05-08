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


class TestRecurringEarningsWarning:
    """B 方案：當 recurring_earnings_warning=True（本業最新 + TTM 都虧），
    score_roe / score_eps_cagr_3y 切換到 OperatingIncome-based 指標，避免一次性業外膨脹。

    目標案例：3708 上緯投控 2025 Q4 處分子公司 → roe_ttm 24%、eps_cagr_3y 57%（看似健康），
    但本業 OP 大虧 → 必須拉低分數。
    """

    def test_score_roe_uses_core_when_warning(self):
        # 帳面 ROE 看起來高但本業在虧 → 必須用 core_roe_op
        fund = {
            "roe_ttm": 0.25,           # 看起來健康
            "core_roe_op": -0.04,      # 本業 ROE proxy 為負
            "recurring_earnings_warning": True,
        }
        score = rubric.score_roe(fund)
        # core 為負 → linear(-0.04, 0, 0.25) clip 0
        assert score == 0.0

    def test_score_roe_no_warning_uses_actual(self):
        # 沒警示 → 走原邏輯，core 即使存在也不該干擾
        fund = {
            "roe_ttm": 0.20,
            "core_roe_op": -0.04,      # 業外正貢獻是常態（投控股）
        }
        score = rubric.score_roe(fund)
        # linear(0.20, 0, 0.25) ≈ 80
        assert score is not None and 75 < score < 85

    def test_score_roe_warning_without_core_falls_back(self):
        # 警示觸發但無 core_roe_op → cap 在 min(roe_ttm, 0)
        fund = {"roe_ttm": 0.15, "recurring_earnings_warning": True}
        score = rubric.score_roe(fund)
        assert score == 0.0

    def test_score_eps_cagr_uses_core_when_warning(self):
        # 帳面 CAGR 衝高（一次性處分）但 core_op_cagr 為負
        fund = {
            "eps_cagr_3y": 0.60,
            "core_op_cagr_3y": -0.05,
            "recurring_earnings_warning": True,
        }
        score = rubric.score_eps_cagr_3y(fund)
        # _linear(-0.05, -0.10, 0.20) → 約 16~17 分
        assert score is not None and 10 < score < 25

    def test_score_eps_cagr_warning_without_core_caps_at_zero(self):
        # 警示觸發、無 core_op_cagr → eps_cagr clip 到 min(0, eps_cagr)
        fund = {"eps_cagr_3y": 0.60, "recurring_earnings_warning": True}
        score = rubric.score_eps_cagr_3y(fund)
        # min(0.60, 0) = 0 → _linear(0, -0.10, 0.20) 約 33
        assert score is not None and 30 < score < 36

    def test_score_eps_cagr_no_warning_unaffected(self):
        fund = {"eps_cagr_3y": 0.10, "core_op_cagr_3y": -0.05}
        score = rubric.score_eps_cagr_3y(fund)
        # 應走原邏輯：_linear(0.10, -0.10, 0.20) ≈ 66
        assert score is not None and 60 < score < 75


class TestRoeAssetStockFloor:
    """C 方案（修正版）：score_asset_value 子因子已撤回，只保留 ROE floor 40 in score_roe。

    動機：原計畫新增 score_asset_value 0.10 權重 + 縮 dividend/margin 權重，但 cohort audit 顯示
    1922 檔非資產股有 62.7% 下跌（中位 -0.79、110 檔「高股利+高毛利」cohort 跌 -1~-7 分），
    副作用 19× 治療效果。改為純 ROE floor 40：對非資產股 0 影響、surgical。
    """

    def test_score_roe_floor_for_asset_stock(self):
        # 厚生 ROE 3.77% 原本 score 15，資產股 floor 拉到 40
        fund = {
            "roe_ttm": 0.0377,
            "pbr": 0.60, "dividend_yield": 5.28, "debt_ratio": 0.125,
            "operating_margin": 0.142, "asset_turnover": 0.094,
        }
        score = rubric.score_roe(fund)
        assert score == 40.0

    def test_score_roe_no_floor_for_normal_stock(self):
        # 非資產股 ROE 3.77% 應該保持原分（~15）
        fund = {
            "roe_ttm": 0.0377,
            "pbr": 1.5, "dividend_yield": 3.0, "debt_ratio": 0.4,
            "operating_margin": 0.1, "asset_turnover": 1.0,
        }
        score = rubric.score_roe(fund)
        # _linear(0.0377, 0, 0.25) ≈ 15
        assert score is not None and 10 < score < 20

    def test_score_roe_high_roe_unaffected_by_floor(self):
        # 高 ROE (10%) 資產股：floor 40 不會拉低，應保持 base 40
        fund = {
            "roe_ttm": 0.10,
            "pbr": 0.60, "dividend_yield": 5.28, "debt_ratio": 0.125,
            "operating_margin": 0.142, "asset_turnover": 0.094,
        }
        score = rubric.score_roe(fund)
        # _linear(0.10, 0, 0.25) = 40, max(40, 40) = 40
        assert score == 40.0

    def test_score_roe_warning_takes_precedence_over_floor(self):
        # B 警示優先於 C floor：本業在虧的股不該被資產股 floor 救
        fund = {
            "roe_ttm": 0.05, "core_roe_op": -0.03,
            "recurring_earnings_warning": True,
            "pbr": 0.60, "dividend_yield": 5.28, "debt_ratio": 0.125,
            "operating_margin": 0.142, "asset_turnover": 0.094,
        }
        score = rubric.score_roe(fund)
        # core_roe_op 為負 → _linear clip 0，floor 不應介入
        assert score == 0.0


class TestFinancialStockGate:
    """D 修：金融業 _is_financial_stock 識別。"""

    def test_financial_industries_recognized(self):
        # 實際 DB 字串：金融保險（70 檔上市）+ 金融業（15 檔上櫃）
        for ind in ("金融保險", "金融業"):
            assert rubric._is_financial_stock({"industry_category": ind}), f"{ind} 應被識別"

    def test_non_financial_not_recognized(self):
        for ind in ("半導體業", "橡膠工業", "化學生技醫療", "建材營造"):
            assert not rubric._is_financial_stock({"industry_category": ind})

    def test_missing_industry_returns_false(self):
        assert not rubric._is_financial_stock({})


class TestEpsGrowthSafeguards:
    """M1 + M2：score_eps_growth 一次性業外保護 + 負基期 + 低基期保護。"""

    def test_recurring_warning_uses_core_op_yoy(self):
        # 警示時：用 OP yoy（不是 EPS yoy）
        fund = {
            "recurring_earnings_warning": True,
            "core_op_q": 100.0, "core_op_q_yoy_base": 200.0,
            "eps_yoy": 5.0,  # 帳面看起來爆衝
        }
        score = rubric.score_eps_growth(fund)
        # OP yoy = (100-200)/200 = -0.5 → _linear(-0.5, -0.3, 0.5) clip 0
        assert score == 0.0

    def test_recurring_warning_without_core_caps_at_zero(self):
        # 警示但缺 core series → 退回 cap min(yoy, 0)
        fund = {"recurring_earnings_warning": True, "eps_yoy": 5.0}
        score = rubric.score_eps_growth(fund)
        # min(5.0, 0) = 0 → _linear(0, -0.3, 0.5) ≈ 37.5
        assert score is not None and 35 < score < 40

    def test_negative_base_caps_yoy(self):
        # 去年同季 EPS 為負時，今年回正 → cap yoy 在 0.5
        fund = {"eps_yoy": 10.0, "eps_q_yoy_base": -1.0, "eps_q": 5.0}
        score = rubric.score_eps_growth(fund)
        # cap min(10, 0.5) = 0.5 → _linear(0.5, -0.3, 0.5) = 100
        # 但 eps_q=5.0 ≥ 0.5 不觸發低基期
        assert score == 100.0

    def test_low_base_caps_score_at_75(self):
        # 微利公司 EPS 0.10 → 0.15，yoy +50% 但絕對量不該滿分
        fund = {"eps_yoy": 0.5, "eps_q": 0.15, "eps_q_yoy_base": 0.10}
        score = rubric.score_eps_growth(fund)
        # _linear(0.5, -0.3, 0.5) = 100，但 eps_q < 0.5 cap 75
        assert score == 75.0

    def test_normal_growth_unaffected(self):
        # 正常成長股：無警示、基期正、絕對量大
        fund = {"eps_yoy": 0.20, "eps_q_yoy_base": 5.0, "eps_q": 6.0}
        score = rubric.score_eps_growth(fund)
        # _linear(0.20, -0.3, 0.5) = 62.5
        assert score is not None and 60 < score < 65


class TestRevenueGrowthLumpyIndustries:
    """M3：建設股 score_revenue_growth 切到 TTM Revenue YoY。"""

    def test_construction_uses_ttm(self):
        # 興富發案例：Q1 yoy −78%、TTM yoy −15%
        fund = {
            "industry_category": "建材營造",
            "revenue_yoy": -0.78,    # 單季崩跌
            "revenue_ttm_yoy": -0.15,  # TTM 較合理
        }
        score = rubric.score_revenue_growth(fund)
        # _linear(-0.15, -0.2, 0.4) ≈ 8.3 (-15% 接近 -20% 下限)
        assert score is not None and 5 < score < 15

    def test_non_construction_uses_quarterly(self):
        # 一般製造業仍用單季 yoy
        fund = {
            "industry_category": "半導體業",
            "revenue_yoy": -0.78,
            "revenue_ttm_yoy": -0.15,  # 不該被用
        }
        score = rubric.score_revenue_growth(fund)
        # _linear(-0.78, -0.2, 0.4) clip 0
        assert score == 0.0

    def test_construction_falls_back_when_ttm_missing(self):
        fund = {
            "industry_category": "營造工程",
            "revenue_yoy": 0.10,
            # 無 revenue_ttm_yoy
        }
        score = rubric.score_revenue_growth(fund)
        # 退回單季：_linear(0.10, -0.2, 0.4) = 50
        assert score == 50.0


class TestAdvLowLiquidityFloor:
    """M4：_scale_by_adv 對 ADV<1M 視為中性。"""

    def test_low_adv_returns_zero(self):
        # 厚生 ADV ≈ 36 萬股
        ratio = rubric._scale_by_adv(cum20=500_000, avg_vol_20=360_000)
        assert ratio == 0.0  # 中性

    def test_normal_adv_returns_ratio(self):
        # 大型股 ADV 5M 股、外資累計 10M → ratio = 10M/(5M*20) = 0.10
        ratio = rubric._scale_by_adv(cum20=10_000_000, avg_vol_20=5_000_000)
        assert ratio is not None and abs(ratio - 0.10) < 0.001

    def test_missing_adv_returns_none(self):
        assert rubric._scale_by_adv(cum20=1_000_000, avg_vol_20=None) is None


class TestVolumeRatio20:
    """vol_ratio5 → vol_ratio20 切換。"""

    def test_uses_vol_ratio20_when_present(self):
        row = pd.Series({"vol_ratio20": 1.5, "vol_ratio5": 0.3})
        score = rubric.score_volume(row)
        # 用 vr20=1.5 → 70（不是 vr5=0.3 → 35）
        assert score == 70.0

    def test_falls_back_to_vol_ratio5(self):
        row = pd.Series({"vol_ratio20": np.nan, "vol_ratio5": 1.5})
        score = rubric.score_volume(row)
        assert score == 70.0

    def test_both_missing_returns_none(self):
        row = pd.Series({"vol_ratio20": np.nan, "vol_ratio5": np.nan})
        assert rubric.score_volume(row) is None
