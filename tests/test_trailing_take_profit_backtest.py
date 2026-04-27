"""ATR 動態停利在回測引擎的整合測試。

涵蓋：
- mode="off"（預設）→ 不影響原有四種 exit_reason
- mode="both" → 動態與固定並存，誰先觸發先出
- mode="only" → 固定 take_profit_pct 被忽略
- exit 優先序：stop_loss > trailing_take_profit > take_profit > score_exit > max_hold
"""
from __future__ import annotations

import pandas as pd

from app.backtest.engine import StrategyConfig, _run_strategy_loop


def _series(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _zero_friction(**overrides) -> StrategyConfig:
    base = dict(
        entry_threshold=65.0,
        exit_threshold=40.0,
        stop_loss_pct=0.30,         # 寬鬆，避免測試誤觸
        take_profit_pct=0.50,       # 寬鬆，動態先觸發
        max_hold_days=200,
        fee_rate=0.0,
        tax_rate=0.0,
        slippage_bps=0.0,
    )
    base.update(overrides)
    return StrategyConfig(**base)


def _trend_then_drop_series(n_up: int = 30, drop_pct: float = 0.15) -> pd.DataFrame:
    """構造一段先穩定上漲、最後重摔的價格序列，讓 trailing TP armed 後觸發。

    score 全程 70（避免 score_exit 干擾），最後再下調以驗證優先序。
    """
    rows = []
    price = 100.0
    for i in range(n_up):
        rows.append({
            "date": f"2025-{1 + i // 22:02d}-{1 + i % 22:02d}",
            "open": price, "high": price + 1.0, "low": price - 0.5, "close": price,
            "short_score": 70.0,
        })
        price += 0.5  # 穩定漲
    # 接著 5 根崩跌
    for i in range(5):
        price *= (1 - drop_pct / 5)
        rows.append({
            "date": f"2025-03-{1 + i:02d}",
            "open": price, "high": price + 0.2, "low": price - 0.5, "close": price,
            "short_score": 70.0,
        })
    return _series(rows)


class TestModeOffPreservesOldBehavior:
    def test_default_off_means_no_trailing_tp_exit(self):
        """mode='off'（預設）下不應出現 trailing_take_profit。"""
        # 漲 30 根 → 觸發固定 take_profit_pct=0.10（10%）
        rows = []
        price = 100.0
        rows.append({"date": "2025-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "short_score": 30})
        rows.append({"date": "2025-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "short_score": 70})
        for i in range(20):
            price += 1.0
            rows.append({
                "date": f"2025-02-{1 + i:02d}",
                "open": price, "high": price + 1, "low": price - 1, "close": price,
                "short_score": 70.0,
            })
        rows.append({"date": "2025-02-22", "open": price, "high": price, "low": price, "close": price, "short_score": 70})
        series = _series(rows)
        cfg = _zero_friction(stop_loss_pct=0.30, take_profit_pct=0.10)
        trades = _run_strategy_loop(series, cfg)
        assert len(trades) == 1
        assert trades[0].exit_reason == "take_profit"


class TestTrailingTPMode:
    def test_both_mode_fires_trailing_before_fixed(self):
        """mode='both'：穩定漲 30 根後崩跌 → trailing 先觸發（peak 回落 K×ATR），
        固定停利 50% 還沒到。"""
        # 進場訊號在 i=1（score=70），i=2 開盤 100 進場
        series = _trend_then_drop_series()
        # i=1 改成 entry trigger
        series.loc[0, "short_score"] = 30
        series.loc[1, "short_score"] = 70
        cfg = _zero_friction(
            trailing_tp_mode="both",
            trailing_tp_atr_multiplier=3.0,
            trailing_tp_arm_pnl=0.05,
            trailing_tp_arm_days=5,
            stop_loss_pct=0.30,
            take_profit_pct=0.50,    # 寬到不會被觸發
        )
        trades = _run_strategy_loop(series, cfg)
        assert len(trades) >= 1
        assert trades[0].exit_reason == "trailing_take_profit"

    def test_only_mode_ignores_fixed_take_profit(self):
        """mode='only'：固定停利門檻設極低（5%）也應被忽略，等動態觸發。"""
        # 設一段穩定漲（不崩跌），固定 5% 容易達；動態因為沒回撤不會 armed-and-trigger
        rows = [{"date": "2025-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "short_score": 30}]
        rows.append({"date": "2025-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "short_score": 70})
        price = 100.0
        for i in range(40):
            price += 0.8
            rows.append({
                "date": f"2025-02-{1 + (i % 28):02d}",
                "open": price, "high": price + 1, "low": price - 1, "close": price,
                "short_score": 70.0,
            })
        rows.append({"date": "2025-04-01", "open": price, "high": price, "low": price, "close": price, "short_score": 70})
        series = pd.DataFrame(rows)
        series["date"] = pd.to_datetime(series["date"], errors="coerce")
        # 重排 date 確保連續（測試環境可忽略 calendar 真實性，只看 score 邏輯）
        series["date"] = pd.date_range("2025-01-01", periods=len(series), freq="B")

        cfg = _zero_friction(
            trailing_tp_mode="only",
            trailing_tp_atr_multiplier=3.0,
            trailing_tp_arm_pnl=0.08,
            trailing_tp_arm_days=5,
            stop_loss_pct=0.30,
            take_profit_pct=0.05,    # 極低門檻，若沒被忽略會 trade exit_reason="take_profit"
            max_hold_days=200,
        )
        trades = _run_strategy_loop(series, cfg)
        # 不應出現 take_profit reason（被 only mode 忽略）
        assert all(t.exit_reason != "take_profit" for t in trades)

    def test_priority_stop_loss_beats_trailing_tp(self):
        """同一根 K 同時觸發 stop_loss 與 trailing_tp → stop_loss 優先（保命第一）。

        構造：先漲讓 trailing 進入 armed，再一根超大跌穿停損線。
        """
        rows = [{"date": "2025-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "short_score": 30}]
        rows.append({"date": "2025-01-02", "open": 100, "high": 101, "low": 99, "close": 100, "short_score": 70})
        price = 100.0
        for i in range(20):
            price += 1.0  # 穩定漲到 ~120
            rows.append({
                "date": "x", "open": price, "high": price + 1, "low": price - 1, "close": price,
                "short_score": 70.0,
            })
        # 一根崩跌至 60（停損 50%、stop_loss_pct=0.10 → 觸發；同時 trailing 也會觸發）
        rows.append({"date": "x", "open": price, "high": price, "low": 60, "close": 60, "short_score": 70})
        rows.append({"date": "x", "open": 60, "high": 60, "low": 60, "close": 60, "short_score": 70})
        series = pd.DataFrame(rows)
        series["date"] = pd.date_range("2025-01-01", periods=len(series), freq="B")

        cfg = _zero_friction(
            trailing_tp_mode="both",
            trailing_tp_atr_multiplier=3.0,
            trailing_tp_arm_pnl=0.05,
            trailing_tp_arm_days=5,
            stop_loss_pct=0.10,
            take_profit_pct=0.80,
            max_hold_days=200,
        )
        trades = _run_strategy_loop(series, cfg)
        assert len(trades) >= 1
        # 停損優先，不該是 trailing_take_profit
        assert trades[0].exit_reason == "stop_loss"


class TestArmGate:
    def test_unarmed_does_not_trigger(self):
        """進場後馬上小跌（未達 arm_pnl）→ trailing 不應觸發。"""
        rows = [{"date": "x", "open": 100, "high": 101, "low": 99, "close": 100, "short_score": 30}]
        rows.append({"date": "x", "open": 100, "high": 101, "low": 99, "close": 100, "short_score": 70})
        # 進場後微跌但不到停損
        for c in [99, 98, 97, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96]:
            rows.append({"date": "x", "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "short_score": 70})
        rows.append({"date": "x", "open": 96, "high": 96, "low": 96, "close": 96, "short_score": 70})
        series = pd.DataFrame(rows)
        series["date"] = pd.date_range("2025-01-01", periods=len(series), freq="B")

        cfg = _zero_friction(
            trailing_tp_mode="both",
            trailing_tp_atr_multiplier=3.0,
            trailing_tp_arm_pnl=0.08,
            trailing_tp_arm_days=5,
            stop_loss_pct=0.10,
            take_profit_pct=0.50,
            max_hold_days=10,
        )
        trades = _run_strategy_loop(series, cfg)
        # 進場後沒漲過 8%、最後 max_hold 出場；不該是 trailing_take_profit
        for t in trades:
            assert t.exit_reason != "trailing_take_profit"
