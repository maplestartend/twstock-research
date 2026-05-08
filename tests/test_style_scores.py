"""Style Score（v5c Wave 2）測試。"""
from __future__ import annotations

from app.scoring.style import compute_style_scores, style_label


class TestStyleScores:
    """4 個風格分數的計算邏輯。"""

    def _full_parts(self):
        """齊全的 sub-factor scores 給測試用（每個 horizon 都齊）。"""
        return {
            "short": {
                "ma_alignment": 80, "kd": 50, "macd": 50, "rsi": 50,
                "bollinger": 50, "volume": 70, "vr_macd": 60,
                "foreign": 65, "trust": 50, "margin_change": 55,
            },
            "mid": {
                "trend": 80, "foreign_cum": 70, "trust_cum": 65,
                "eps_growth": 75, "vr_macd": 50,
            },
            "long": {
                "roe": 70, "margin_quality": 60, "eps_cagr_3y": 80,
                "dividend": 55, "valuation": 65,
            },
        }

    def test_returns_four_styles(self):
        p = self._full_parts()
        result = compute_style_scores(p["short"], p["mid"], p["long"])
        assert set(result.keys()) == {"value", "growth", "momentum", "income"}

    def test_all_scores_in_range(self):
        p = self._full_parts()
        result = compute_style_scores(p["short"], p["mid"], p["long"])
        for style, score in result.items():
            assert 0 <= score <= 100, f"{style} = {score} 超出 [0, 100]"

    def test_growth_trend_gate_caps_at_70(self):
        """Growth bug 修補：trend < 60 時 cap 在 70（解 6165 浪凡型「帳面成長但股價不漲」）"""
        p = self._full_parts()
        # eps_growth/cagr/roe 全部高 → 原本 Growth 會 80+
        p["mid"]["trend"] = 50  # < 60 觸發 gate
        p["mid"]["eps_growth"] = 100
        p["long"]["eps_cagr_3y"] = 100
        p["long"]["roe"] = 100
        result = compute_style_scores(p["short"], p["mid"], p["long"])
        assert result["growth"] <= 70.0, f"trend gate 應 cap Growth 在 70，實際 {result['growth']}"

    def test_growth_no_cap_when_trend_strong(self):
        """trend ≥ 60 時 Growth 不該被 cap"""
        p = self._full_parts()
        p["mid"]["trend"] = 80
        p["mid"]["eps_growth"] = 100
        p["long"]["eps_cagr_3y"] = 100
        p["long"]["roe"] = 100
        result = compute_style_scores(p["short"], p["mid"], p["long"])
        assert result["growth"] > 80, f"trend 強時 Growth 不該 cap，實際 {result['growth']}"

    def test_value_emphasizes_valuation_and_dividend(self):
        """Value 對 valuation + dividend 高敏感。"""
        p = self._full_parts()
        p["long"]["valuation"] = 100
        p["long"]["dividend"] = 100
        result_high = compute_style_scores(p["short"], p["mid"], p["long"])
        p["long"]["valuation"] = 0
        p["long"]["dividend"] = 0
        result_low = compute_style_scores(p["short"], p["mid"], p["long"])
        assert result_high["value"] - result_low["value"] >= 50

    def test_momentum_emphasizes_trend_and_ma(self):
        """Momentum 對 mid.trend + short.ma_alignment 高敏感。"""
        p = self._full_parts()
        p["mid"]["trend"] = 100
        p["short"]["ma_alignment"] = 100
        result_high = compute_style_scores(p["short"], p["mid"], p["long"])
        p["mid"]["trend"] = 0
        p["short"]["ma_alignment"] = 0
        result_low = compute_style_scores(p["short"], p["mid"], p["long"])
        assert result_high["momentum"] - result_low["momentum"] >= 50

    def test_income_emphasizes_dividend_50_percent(self):
        """Income 對 dividend 0.50 weighting。"""
        p = self._full_parts()
        p["long"]["dividend"] = 100
        result_high = compute_style_scores(p["short"], p["mid"], p["long"])
        p["long"]["dividend"] = 0
        result_low = compute_style_scores(p["short"], p["mid"], p["long"])
        # 相差至少 35（0.50 weight 的影響）
        assert result_high["income"] - result_low["income"] >= 35

    def test_none_aware_when_subfactor_missing(self):
        """sub-factor None 時不參與權重歸一化、其他子因子重新分布。"""
        p = self._full_parts()
        p["long"]["valuation"] = None  # 缺值
        result = compute_style_scores(p["short"], p["mid"], p["long"])
        assert result["value"] is not None  # 不是全空就該有分數

    def test_returns_none_when_all_subfactors_missing(self):
        """全 None 時該風格回 None（避免假分數）。"""
        empty_long = {"roe": None, "margin_quality": None, "eps_cagr_3y": None,
                      "dividend": None, "valuation": None}
        empty_mid = {"trend": None, "foreign_cum": None, "trust_cum": None,
                     "eps_growth": None, "vr_macd": None}
        empty_short = {"ma_alignment": None, "kd": None, "macd": None, "rsi": None,
                       "bollinger": None, "volume": None, "vr_macd": None,
                       "foreign": None, "trust": None, "margin_change": None}
        result = compute_style_scores(empty_short, empty_mid, empty_long)
        assert result["value"] is None
        assert result["growth"] is None
        assert result["momentum"] is None
        assert result["income"] is None

    def test_etf_long_none_still_get_momentum(self):
        """ETF long 全 None（仿 score_long_term 對 ETF 處理），但 Momentum 仍可算（用 mid+short）。"""
        p = self._full_parts()
        p["long"] = {"roe": None, "margin_quality": None, "eps_cagr_3y": None,
                     "dividend": None, "valuation": None}
        result = compute_style_scores(p["short"], p["mid"], p["long"])
        # Value / Income 全靠 long → None
        assert result["value"] is None
        assert result["income"] is None
        # Momentum 用 mid + short → 仍可算
        assert result["momentum"] is not None


class TestStyleLabel:
    def test_strong_match(self):
        assert "強符合" in style_label(75)
        assert "強符合" in style_label(70)

    def test_neutral(self):
        assert "中性" in style_label(60)
        assert "中性" in style_label(50)

    def test_weak(self):
        assert "不符" in style_label(40)
        assert "不符" in style_label(0)

    def test_none(self):
        assert style_label(None) == "—"
