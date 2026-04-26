"""跌停連板：第一天觸發 stop_loss 但 next_open 跌停 → 隔天恢復後仍以原 reason 出場。

對應 Critical Fix #7（金融分析師審查）：原版 continue 後重新算 change/score，
連續跌停會在第三天才出，價格更糟，且 exit_reason 可能變成 score_exit。
"""
from __future__ import annotations

import pandas as pd

from app.backtest.engine import StrategyConfig, _run_strategy_loop


def _series(rows: list[tuple[float, float, float]], start: str = "2024-01-02") -> pd.DataFrame:
    """rows: list of (open, close, short_score)。"""
    dates = pd.bdate_range(start=start, periods=len(rows))
    return pd.DataFrame({
        "date": dates,
        "open": [r[0] for r in rows],
        "close": [r[1] for r in rows],
        "short_score": [r[2] for r in rows],
    }).reset_index(drop=True)


def _cfg() -> StrategyConfig:
    return StrategyConfig(
        entry_threshold=65, exit_threshold=40, stop_loss_pct=0.05, take_profit_pct=0.20,
        max_hold_days=60, fee_rate=0.0, tax_rate=0.0, slippage_bps=0,
    )


class TestLimitDownPendingExit:
    def test_stop_loss_held_through_one_limit_down_bar(self):
        """模擬：進場後一天觸發 stop_loss，但 next_open 跌停 → 隔天才出場。
        exit_reason 應仍是 'stop_loss'（不該被 score_exit 蓋掉）。

        loop 從 i=1 起跑，i=L-1 結束（不含），所以最少要有 6 根 bar 才能跑出
        進場 → 觸發 → pending → 出場的流程。
        """
        # i=0: padding（loop 不看）
        # i=1: score=70 → 觸發進場，entry_idx=2, entry_price=series[2]["open"]=100
        # i=2: 在倉、close=92、change=-8% → stop_loss；next_open(day3)=80，80≤92*0.9=82.8 → limit_down → pending
        # i=3: 在倉、pending=stop_loss；close=80；next_open(day4)=78，78>80*0.9=72 → 不跌停 → 出場
        # i=4: 已出場，no-op
        rows = [
            (100, 100, 0),   # day 0: padding
            (100, 100, 70),  # day 1: 訊號（i=1）
            (100, 92, 60),   # day 2: stop_loss 觸發；day3 limit_down
            (80, 80, 50),    # day 3: pending；day4 不跌停
            (78, 79, 50),    # day 4: 出場時的 next_open 來源
            (79, 79, 50),    # day 5: padding
        ]
        ds = _series(rows)
        trades = _run_strategy_loop(ds, _cfg())
        assert len(trades) == 1, f"expected 1 trade, got {len(trades)}"
        t = trades[0]
        assert t.exit_reason == "stop_loss", f"expected stop_loss, got {t.exit_reason}"

    def test_two_consecutive_limit_down_then_exit(self):
        """連續 2 根跌停板都不能賣，第 3 根才出。reason 一路保留 stop_loss。"""
        # i=1 進場 entry=100; i=2 stop_loss → day3 limit_down (≤90%); i=3 pending → day4 limit_down; i=4 pending → day5 不跌停 → 出
        rows = [
            (100, 100, 0),
            (100, 100, 70),       # i=1 訊號
            (100, 92, 60),        # i=2 stop_loss; next_open day3=82.8 → 92*0.9=82.8 → limit_down
            (82.8, 82.8, 50),     # i=3 pending; next_open day4=74.5 → 82.8*0.9=74.52 → limit_down
            (74.5, 74.5, 50),     # i=4 pending; next_open day5=72 → 74.5*0.9=67.05 → 72>67.05 不跌停 → 出
            (72, 73, 50),         # day 5: next_open 來源
            (73, 73, 50),         # padding
        ]
        ds = _series(rows)
        trades = _run_strategy_loop(ds, _cfg())
        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"

    def test_normal_exit_unaffected(self):
        """沒撞跌停 → 觸發當下就出場，行為不變。"""
        rows = [
            (100, 100, 0),
            (100, 100, 70),  # i=1 訊號
            (100, 92, 60),   # i=2 stop_loss; next_open day3=96 → 不跌停 → 立即出
            (96, 96, 50),    # day 3
            (96, 96, 50),    # padding
        ]
        ds = _series(rows)
        trades = _run_strategy_loop(ds, _cfg())
        assert len(trades) == 1
        assert trades[0].exit_reason == "stop_loss"
