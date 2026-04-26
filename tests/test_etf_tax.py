"""ETF 證交稅率分流：00xxx 0.1%、00xxxB 0%、其他 0.3%。

對應 Critical Fix #1（金融分析師審查指出 backtest engine 與 portfolio 寫死 0.3% 會
讓所有 ETF 回測 alpha 與持股淨損益估算系統性偏低 ~0.2%/round trip）。
"""
from __future__ import annotations

import pandas as pd

from app.backtest.engine import BacktestResult, StrategyConfig, backtest_stock
from app.portfolio import (
    BOND_ETF_TAX_RATE,
    DEFAULT_TAX_RATE,
    ETF_TAX_RATE,
    Holding,
    tax_rate_for,
)


class TestTaxRateFor:
    def test_general_stock_keeps_03pct(self):
        assert tax_rate_for("2330") == DEFAULT_TAX_RATE
        assert tax_rate_for("1101") == DEFAULT_TAX_RATE
        assert tax_rate_for("9999") == DEFAULT_TAX_RATE

    def test_stock_etf_uses_01pct(self):
        assert tax_rate_for("0050") == ETF_TAX_RATE
        assert tax_rate_for("0056") == ETF_TAX_RATE
        assert tax_rate_for("00878") == ETF_TAX_RATE
        assert tax_rate_for("00919") == ETF_TAX_RATE

    def test_bond_etf_is_tax_free(self):
        assert tax_rate_for("00679B") == BOND_ETF_TAX_RATE
        assert tax_rate_for("00687B") == BOND_ETF_TAX_RATE
        assert tax_rate_for("00772B") == BOND_ETF_TAX_RATE

    def test_blank_falls_back_to_default(self):
        assert tax_rate_for(None) == DEFAULT_TAX_RATE
        assert tax_rate_for("") == DEFAULT_TAX_RATE

    def test_lowercase_b_also_treated_as_bond(self):
        assert tax_rate_for("00679b") == BOND_ETF_TAX_RATE


class TestHoldingEstimatedSellCosts:
    """Holding.estimated_sell_costs 應依 stock_id 套對應稅率。"""

    def _holding(self, sid: str) -> Holding:
        return Holding(stock_id=sid, shares=1000, avg_cost=100, entry_date="2024-01-01", note=None)

    def test_general_stock_uses_03pct_tax(self):
        h = self._holding("2330")
        # tax = 1000 * 100 * 0.003 = 300（手續費另計，這裡只看稅率影響）
        cost = h.estimated_sell_costs(100)
        assert cost > 100 * 1000 * 0.003 * 0.99  # 含手續費所以略高

    def test_stock_etf_uses_01pct_tax(self):
        h_etf = self._holding("0050")
        h_general = self._holding("2330")
        # ETF 的 sell cost 應比一般股少約 1000*100*(0.003-0.001) = 200
        diff = h_general.estimated_sell_costs(100) - h_etf.estimated_sell_costs(100)
        assert abs(diff - 200) < 1.0  # 容忍手續費 rounding

    def test_bond_etf_only_charges_fee(self):
        h_bond = self._holding("00679B")
        # 債券 ETF 稅率 = 0，sell_cost 只剩手續費
        cost = h_bond.estimated_sell_costs(100)
        # 賣方手續費 < 賣金 * 0.001425 + 一些誤差
        assert cost < 1000 * 100 * 0.0015


class TestBacktestStockAppliesEtfTax:
    """backtest_stock 進入時應 dataclasses.replace(cfg, tax_rate=tax_rate_for(stock_id))，
    使 BacktestResult.config.tax_rate 反映該股實際稅率。"""

    def test_general_stock_keeps_default_tax(self, tmp_path):
        # 不需要真資料：backtest_stock 在 price 為空時直接回 BacktestResult，已套了正確稅率
        from app.data.db import Database
        db = Database(tmp_path / "x.db")
        cfg = StrategyConfig()
        r = backtest_stock(db, "2330", cfg, lookback_days=10)
        assert r.config.tax_rate == DEFAULT_TAX_RATE

    def test_stock_etf_tax_overridden(self, tmp_path):
        from app.data.db import Database
        db = Database(tmp_path / "x.db")
        cfg = StrategyConfig()
        r = backtest_stock(db, "0050", cfg, lookback_days=10)
        assert r.config.tax_rate == ETF_TAX_RATE

    def test_bond_etf_tax_overridden(self, tmp_path):
        from app.data.db import Database
        db = Database(tmp_path / "x.db")
        cfg = StrategyConfig()
        r = backtest_stock(db, "00679B", cfg, lookback_days=10)
        assert r.config.tax_rate == BOND_ETF_TAX_RATE

    def test_original_cfg_not_mutated(self, tmp_path):
        from app.data.db import Database
        db = Database(tmp_path / "x.db")
        cfg = StrategyConfig(tax_rate=0.003)
        backtest_stock(db, "0050", cfg, lookback_days=10)
        # 原本 cfg 不該被改 — 用 dataclasses.replace 做 copy
        assert cfg.tax_rate == 0.003

    def test_buy_and_hold_uses_etf_tax(self):
        """B&H 報酬內扣稅率應跟著 cfg.tax_rate 走（cfg 由 backtest_stock 設定）。"""
        cfg_etf = StrategyConfig(fee_rate=0.001425, tax_rate=ETF_TAX_RATE, slippage_bps=5.0)
        cfg_gen = StrategyConfig(fee_rate=0.001425, tax_rate=DEFAULT_TAX_RATE, slippage_bps=5.0)
        # 同樣 +10% gross：ETF 的 B&H 應比一般股高 ~0.2pp（稅率差 0.002）
        series = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=2),
            "open": [100, 110], "close": [100, 110], "short_score": [50, 50],
        })
        r_etf = BacktestResult(stock_id="0050", config=cfg_etf, daily_series=series)
        r_gen = BacktestResult(stock_id="2330", config=cfg_gen, daily_series=series)
        diff = r_etf.buy_and_hold_return - r_gen.buy_and_hold_return
        assert abs(diff - 0.002) < 1e-6
