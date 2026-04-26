"""max_drawdown 改用 daily mark-to-market 的 equity 曲線。

對應 Critical Fix #5：舊版只記 trade exit 報酬，「持有期內 -30% 但停損出在 -8%」
的 trade 會被低估成 -8% MDD。
"""
from __future__ import annotations

import pandas as pd

from app.backtest.engine import BacktestResult, StrategyConfig, Trade


def _series(closes: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    """簡單的 daily_series fixture：每日 open=close。"""
    dates = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "close": closes,
        "short_score": [50.0] * len(closes),
    }).reset_index(drop=True)


class TestDailyMaxDrawdown:
    def test_no_trades_returns_zero(self):
        r = BacktestResult(stock_id="X", config=StrategyConfig(), daily_series=_series([100, 100, 100]))
        assert r.max_drawdown == 0.0

    def test_intra_trade_dip_is_captured(self):
        """持有 5 天，中間最低點 -25%、出場時 -8%。MDD 應接近 -25% 而非 -8%。"""
        # daily close: 100 → 100 → 90 → 75（intra-trade 最低）→ 88 → 92（出場）
        ds = _series([100, 100, 90, 75, 88, 92])
        # entry 在 day 0 next_open（day 1, close=100），exit 在 day 4 next_open（day 5, close=92）
        cfg = StrategyConfig(fee_rate=0.001425, tax_rate=0.003, slippage_bps=5.0)
        # 給定 entry=100, exit=92 → gross = -8%, net 約 -8.585%
        net_return = (92 - 100) / 100 - (cfg.fee_rate * 2 + cfg.tax_rate)
        trades = [Trade(
            entry_date=ds.iloc[1]["date"], exit_date=ds.iloc[5]["date"],
            entry_price=100.0, exit_price=92.0, hold_days=4,
            gross_return=-0.08, net_return=round(net_return, 4),
            exit_reason="stop_loss",
        )]
        r = BacktestResult(stock_id="X", config=cfg, trades=trades, daily_series=ds)
        mdd = r.max_drawdown
        # 預期：在 day 3（close=75）時 mtm equity = 1.0 * 0.75 = 0.75 → MDD ≈ -25%
        # 容忍：≥ -28%（持有期內最低 -25%）；不該是淺淺的 -8.6%
        assert mdd <= -0.20, f"daily MDD should capture -25% intra-trade dip, got {mdd}"

    def test_no_dip_matches_trade_return(self):
        """持有期內無 dip：MDD 應等於最終 net_return（負時）或 0（正時）。"""
        ds = _series([100, 100, 102, 104, 105, 106])
        cfg = StrategyConfig(fee_rate=0.001425, tax_rate=0.003, slippage_bps=5.0)
        # entry=100, exit=106 → gross=+6%, net 正
        trades = [Trade(
            entry_date=ds.iloc[1]["date"], exit_date=ds.iloc[5]["date"],
            entry_price=100.0, exit_price=106.0, hold_days=4,
            gross_return=0.06, net_return=0.054,
            exit_reason="take_profit",
        )]
        r = BacktestResult(stock_id="X", config=cfg, trades=trades, daily_series=ds)
        # 一路向上 → 沒有 drawdown
        assert r.max_drawdown >= -0.005

    def test_falls_back_when_daily_series_empty(self):
        """daily_series 空（極簡 fixture） → 用舊版 trade-by-trade 演算法不爆。"""
        cfg = StrategyConfig()
        trades = [
            Trade(entry_date=pd.Timestamp("2024-01-02"), exit_date=pd.Timestamp("2024-01-05"),
                  entry_price=100, exit_price=90, hold_days=3, gross_return=-0.10, net_return=-0.105,
                  exit_reason="stop_loss"),
            Trade(entry_date=pd.Timestamp("2024-01-08"), exit_date=pd.Timestamp("2024-01-12"),
                  entry_price=90, exit_price=99, hold_days=4, gross_return=0.10, net_return=0.094,
                  exit_reason="take_profit"),
        ]
        r = BacktestResult(stock_id="X", config=cfg, trades=trades)
        mdd = r.max_drawdown
        # trade-by-trade equity: 1 → 0.895 → 0.979 → peak=1.0, MDD=-0.105
        assert abs(mdd - (-0.105)) < 0.01
