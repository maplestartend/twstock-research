"""Regime detection (v5e #1) 測試。"""
from __future__ import annotations

from app.scoring.regime import composite_weights_for_regime, COMPOSITE_WEIGHTS_BY_REGIME


class TestComposeWeightsByRegime:
    def test_three_regimes_present(self):
        assert set(COMPOSITE_WEIGHTS_BY_REGIME.keys()) == {"bull", "bear", "neutral"}

    def test_each_regime_sums_to_one(self):
        for regime, w in COMPOSITE_WEIGHTS_BY_REGIME.items():
            total = sum(w.values())
            assert abs(total - 1.0) < 0.001, f"{regime} sum={total}"

    def test_bear_emphasizes_long(self):
        """空頭時 long 權重應高於多頭。"""
        bull_long = COMPOSITE_WEIGHTS_BY_REGIME["bull"]["long"]
        bear_long = COMPOSITE_WEIGHTS_BY_REGIME["bear"]["long"]
        assert bear_long > bull_long, f"bear long {bear_long} should > bull long {bull_long}"

    def test_bull_emphasizes_mid(self):
        """多頭時 mid 權重應高於空頭。"""
        bull_mid = COMPOSITE_WEIGHTS_BY_REGIME["bull"]["mid"]
        bear_mid = COMPOSITE_WEIGHTS_BY_REGIME["bear"]["mid"]
        assert bull_mid > bear_mid

    def test_neutral_in_between(self):
        """neutral long 權重應介於 bull / bear。"""
        bull_long = COMPOSITE_WEIGHTS_BY_REGIME["bull"]["long"]
        neutral_long = COMPOSITE_WEIGHTS_BY_REGIME["neutral"]["long"]
        bear_long = COMPOSITE_WEIGHTS_BY_REGIME["bear"]["long"]
        assert bull_long <= neutral_long <= bear_long

    def test_helper_returns_correct_weights(self):
        assert composite_weights_for_regime("bull") == COMPOSITE_WEIGHTS_BY_REGIME["bull"]
        assert composite_weights_for_regime("bear") == COMPOSITE_WEIGHTS_BY_REGIME["bear"]
        assert composite_weights_for_regime("neutral") == COMPOSITE_WEIGHTS_BY_REGIME["neutral"]

    def test_helper_unknown_regime_falls_back_to_bull(self):
        """未知 regime（避免 KeyError、保守 fallback 到 bull = v5c 默認）。"""
        result = composite_weights_for_regime("unknown")  # type: ignore
        assert result == COMPOSITE_WEIGHTS_BY_REGIME["bull"]


class TestRegimeAwareCompositeScore:
    """composite_score 接 regime 參數動態調權。"""

    def test_bull_regime_uses_bull_weights(self):
        from app.scoring.engine import composite_score
        # 三個都齊：short=80 mid=60 long=40
        c, _ = composite_score(80, 60, 40, regime="bull")
        # bull weights 0.20/0.60/0.20 → 80*0.2 + 60*0.6 + 40*0.2 = 16+36+8 = 60
        assert abs(c - 60) < 0.5

    def test_bear_regime_emphasizes_long(self):
        from app.scoring.engine import composite_score
        # 同 short/mid/long，bear 應比 bull 給 long 更多權重
        c_bull, _ = composite_score(80, 60, 40, regime="bull")
        c_bear, _ = composite_score(80, 60, 40, regime="bear")
        # short=80 / mid=60 / long=40: bull > bear（因為 long 是低分、bear 升 long 權重）
        assert c_bear < c_bull

    def test_no_regime_falls_back_to_default(self):
        """未傳 regime 時用預設 R.COMPOSITE_WEIGHTS（向後相容）。"""
        from app.scoring.engine import composite_score
        c1, _ = composite_score(80, 60, 40)
        c2, _ = composite_score(80, 60, 40, regime=None)
        assert c1 == c2
