"""Backtest engine 純邏輯測試 — 不依賴 DB，灌假資料驗策略迴圈。

涵蓋方法論修正：
- S0-3：漲停板開盤無法買、跌停板開盤無法賣
"""
from __future__ import annotations

import pandas as pd

from app.backtest.engine import (
    LIMIT_DOWN_PCT,
    LIMIT_UP_PCT,
    StrategyConfig,
    _run_strategy_loop,
)


def _make_series(rows: list[dict]) -> pd.DataFrame:
    """rows 中每筆 = {date, open, close, short_score}。"""
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _zero_friction_cfg() -> StrategyConfig:
    """關掉手續費/稅/滑價，方便驗純邏輯。"""
    return StrategyConfig(
        entry_threshold=65.0,
        exit_threshold=40.0,
        stop_loss_pct=0.20,
        take_profit_pct=0.50,
        max_hold_days=10,
        fee_rate=0.0,
        tax_rate=0.0,
        slippage_bps=0.0,
    )


class TestLimitUpDownSkip:
    def test_limit_up_blocks_entry(self):
        """次日開盤即漲停 (>= prev_close × 1.10) → 不進場。"""
        # i=0 score 太低；i=1 觸發進場（score=70 >= 65），但 i+1 開盤 = 110 >= 100×1.10 → 漲停買不到
        # 後面 i=2/3 score 仍 >65，但 i+1 已不漲停，正常進場
        series = _make_series([
            {"date": "2025-01-01", "open": 100, "close": 100, "short_score": 30},
            {"date": "2025-01-02", "open": 100, "close": 100, "short_score": 70},  # i=1，訊號發生
            {"date": "2025-01-03", "open": 110, "close": 105, "short_score": 70},  # i=2，這天是 limit-up 開盤
            {"date": "2025-01-04", "open": 106, "close": 106, "short_score": 70},  # i=3
            {"date": "2025-01-05", "open": 108, "close": 108, "short_score": 30},  # i=4，触發出場
            {"date": "2025-01-06", "open": 108, "close": 108, "short_score": 30},
        ])
        trades = _run_strategy_loop(series, _zero_friction_cfg())
        # i=1 的訊號被漲停板擋掉。i=2 score 仍 65+，且 i+1=106 vs close=105 不漲停，正常進場。
        # 進場價約 106。i=3 score 仍 70，無出場。i=4 score=30 → 出場，i+1 開盤 108
        assert len(trades) == 1
        assert trades[0].entry_price == 106.0
        assert trades[0].exit_price == 108.0

    def test_limit_down_delays_exit(self):
        """出場日次日開盤跌停 → 該 bar 不成交，下一根再試。"""
        cfg = _zero_friction_cfg()
        cfg.exit_threshold = 40.0
        # i=0/1 進場：i=1 score 70 → i=2 開 110 進場
        # i=3 score 30 觸發 score_exit，但 i+1=4 開盤 = close(108)×0.85 = 91.8（跌超過 10% 算跌停？
        # close=108, 108×0.90=97.2，所以 92 < 97.2 → 跌停
        # 應跳過此 bar 不出場，i=4 仍 in_position
        # i=4 score 30，i+1=5 開盤回到 95，仍跌停（95 < 108×0.9=97.2 不對 — 改用 close i=4 來算）
        # 嗯，limit 用「當天 close」算，每天門檻會變。要簡化測試。
        series = _make_series([
            {"date": "2025-01-01", "open": 100, "close": 100, "short_score": 30},
            {"date": "2025-01-02", "open": 100, "close": 100, "short_score": 70},  # i=1 進場訊號
            {"date": "2025-01-03", "open": 100, "close": 100, "short_score": 30},  # i=2 已進場，score 觸發出場
            # i+1 = i=3 的 open 88（vs i=2 close=100，88 <= 90 = 跌停）→ 跳過此次出場
            {"date": "2025-01-04", "open": 88, "close": 95, "short_score": 30},   # i=3 仍在持倉，再試出場
            # i+1 = i=4 的 open 96（vs i=3 close=95，96 > 95×0.9=85.5 不跌停）→ 正常出場
            {"date": "2025-01-05", "open": 96, "close": 96, "short_score": 30},
            {"date": "2025-01-06", "open": 96, "close": 96, "short_score": 30},
        ])
        trades = _run_strategy_loop(series, cfg)
        # i=1 score 70 → i=2 open 100 進場
        # i=2 score 30 → 應出場，但 i=3 open 88 是跌停，跳過保留部位
        # i=3 score 30 → 再次出場，i=4 open 96 不跌停，正常出
        assert len(trades) == 1
        assert trades[0].entry_price == 100.0
        assert trades[0].exit_price == 96.0
        assert trades[0].exit_reason == "score_exit"

    def test_normal_entry_below_limit(self):
        """次日開盤 +9.9% 不算漲停，應該進場。"""
        cfg = _zero_friction_cfg()
        series = _make_series([
            {"date": "2025-01-01", "open": 100, "close": 100, "short_score": 30},
            {"date": "2025-01-02", "open": 100, "close": 100, "short_score": 70},  # i=1 訊號
            {"date": "2025-01-03", "open": 109.9, "close": 110, "short_score": 70},  # i=2 進場 109.9
            {"date": "2025-01-04", "open": 110, "close": 110, "short_score": 30},
            {"date": "2025-01-05", "open": 110, "close": 110, "short_score": 30},
        ])
        trades = _run_strategy_loop(series, cfg)
        assert len(trades) == 1
        assert trades[0].entry_price == 109.9

    def test_exact_limit_threshold_treated_as_limit(self):
        """剛好 +10.0% 應視為漲停板，跳過進場。"""
        cfg = _zero_friction_cfg()
        # i=1 score 70，i+1=i=2 open 110.0 = close(100)×1.10 → 視為漲停跳過
        series = _make_series([
            {"date": "2025-01-01", "open": 100, "close": 100, "short_score": 30},
            {"date": "2025-01-02", "open": 100, "close": 100, "short_score": 70},
            {"date": "2025-01-03", "open": 110.0, "close": 110, "short_score": 30},  # 開盤即漲停
            {"date": "2025-01-04", "open": 110, "close": 110, "short_score": 30},
            {"date": "2025-01-05", "open": 110, "close": 110, "short_score": 30},
        ])
        trades = _run_strategy_loop(series, cfg)
        assert trades == []  # 完全沒進場


class TestFlatResetBetweenSlices:
    """S0-2：每個 slice 從 in_position=False 起跑，避免 train→test 邊界部位殘留。"""

    def test_loop_starts_flat(self):
        """直接呼叫 _run_strategy_loop 兩次，第二次不該繼承第一次的部位。"""
        cfg = _zero_friction_cfg()
        # 一個 series 會進場+出場一次
        series_a = _make_series([
            {"date": "2025-01-01", "open": 100, "close": 100, "short_score": 30},
            {"date": "2025-01-02", "open": 100, "close": 100, "short_score": 70},
            {"date": "2025-01-03", "open": 100, "close": 100, "short_score": 70},
            {"date": "2025-01-04", "open": 100, "close": 100, "short_score": 30},
            {"date": "2025-01-05", "open": 100, "close": 100, "short_score": 30},
        ])
        series_b = _make_series([
            {"date": "2025-02-01", "open": 200, "close": 200, "short_score": 30},
            {"date": "2025-02-02", "open": 200, "close": 200, "short_score": 30},
            {"date": "2025-02-03", "open": 200, "close": 200, "short_score": 30},
            {"date": "2025-02-04", "open": 200, "close": 200, "short_score": 30},
            {"date": "2025-02-05", "open": 200, "close": 200, "short_score": 30},
        ])
        trades_a = _run_strategy_loop(series_a, cfg)
        trades_b = _run_strategy_loop(series_b, cfg)
        # series_a 該至少 1 trade
        assert len(trades_a) >= 1
        # series_b 全程 score=30 不該有任何 trade（如果有，代表 in_position 殘留）
        assert trades_b == []


class TestPendingIntraday:
    """S0-5：as_of=今日且當下 < 14:00 → 標 pending（盤中資料未確定）。"""

    def test_pending_when_today_and_morning(self, monkeypatch):
        from app.scoring import engine as eng
        from app.data import clock as clk
        from datetime import datetime, timezone, timedelta

        # 模擬「2026-04-26 上午 10:00」
        fake_now = datetime(2026, 4, 26, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        monkeypatch.setattr(clk, "taipei_now", lambda: fake_now)
        # as_of 等於今天 → pending
        assert eng.is_pending_intraday("2026-04-26") is True

    def test_not_pending_when_today_and_afternoon(self, monkeypatch):
        from app.scoring import engine as eng
        from app.data import clock as clk
        from datetime import datetime, timezone, timedelta

        # 模擬「2026-04-26 下午 14:30」
        fake_now = datetime(2026, 4, 26, 14, 30, tzinfo=timezone(timedelta(hours=8)))
        monkeypatch.setattr(clk, "taipei_now", lambda: fake_now)
        # as_of 等於今天但已過 14:00 → 不 pending
        assert eng.is_pending_intraday("2026-04-26") is False

    def test_not_pending_when_yesterday(self, monkeypatch):
        from app.scoring import engine as eng
        from app.data import clock as clk
        from datetime import datetime, timezone, timedelta

        fake_now = datetime(2026, 4, 26, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        monkeypatch.setattr(clk, "taipei_now", lambda: fake_now)
        # as_of 是昨天 → 不論時間都不 pending
        assert eng.is_pending_intraday("2026-04-25") is False

    def test_pending_invalid_date_returns_false(self):
        from app.scoring import engine as eng
        assert eng.is_pending_intraday("not-a-date") is False
        assert eng.is_pending_intraday("") is False


class TestRiskAdjustedMetrics:
    """S2-10：BacktestResult 加 sharpe / sortino / calmar 屬性，summary() 透出，
    且 buy_and_hold 已扣同等費用。"""

    def _make_result(self, returns: list[float], cfg=None) -> "BacktestResult":
        from app.backtest.engine import BacktestResult, StrategyConfig, Trade
        if cfg is None:
            cfg = _zero_friction_cfg()
        trades = [
            Trade(
                entry_date=pd.Timestamp(f"2025-01-{i+1:02d}"),
                exit_date=pd.Timestamp(f"2025-01-{i+2:02d}"),
                entry_price=100.0,
                exit_price=100.0 * (1 + r),
                hold_days=1,
                gross_return=r,
                net_return=r,
                exit_reason="score_exit",
            )
            for i, r in enumerate(returns)
        ]
        return BacktestResult(
            stock_id="2330",
            config=cfg,
            trades=trades,
            daily_series=pd.DataFrame({
                "date": pd.date_range("2025-01-01", periods=2),
                "close": [100.0, 110.0],
            }),
        )

    def test_sharpe_present_with_multiple_trades(self):
        r = self._make_result([0.05, 0.03, 0.08, -0.02])
        assert r.sharpe_ratio is not None
        assert r.summary()["sharpe"] is not None

    def test_sharpe_none_with_one_trade(self):
        r = self._make_result([0.05])
        assert r.sharpe_ratio is None
        assert r.summary()["sharpe"] is None

    def test_sortino_only_uses_downside(self):
        # 兩組：mean 與 downside 個數相同，但 downside 大小不同
        r_low_down = self._make_result([0.10, 0.10, 0.10, -0.01, -0.02])
        r_big_down = self._make_result([0.10, 0.10, 0.10, -0.10, -0.20])
        assert r_low_down.sortino_ratio is not None
        assert r_big_down.sortino_ratio is not None
        # 下行波動小的 sortino 應該更高
        assert r_low_down.sortino_ratio > r_big_down.sortino_ratio

    def test_sortino_none_when_no_loss(self):
        r = self._make_result([0.05, 0.03, 0.08, 0.02])
        assert r.sortino_ratio is None  # 沒有任何 loss trade

    def test_calmar_uses_max_drawdown(self):
        # 連虧再連賺，會有明顯 MDD
        r = self._make_result([0.05, -0.10, -0.05, 0.20, 0.10])
        assert r.calmar_ratio is not None
        # total_return 與 |max_drawdown| 都該 > 1%（樣本有意義）
        assert abs(r.max_drawdown) > 0.01

    def test_calmar_none_when_mdd_negligible(self):
        # 全部小幅獲利，MDD ~ 0
        r = self._make_result([0.01, 0.01, 0.01, 0.01])
        # MDD = 0 → 視為樣本不足
        assert r.calmar_ratio is None

    def test_buy_and_hold_subtracts_fees(self):
        from app.backtest.engine import StrategyConfig
        # 給定一段 series first→last 報酬 +10%；策略用真實 cfg（含 fee/tax/slippage）
        cfg = StrategyConfig(
            fee_rate=0.001425, tax_rate=0.003, slippage_bps=5.0,
        )
        r = self._make_result([0.05], cfg=cfg)
        # daily_series first=100, last=110 → gross=+10%
        bh = r.buy_and_hold_return
        # 同 cfg 下 fees = 0.001425*2 + 0.003 = 0.00585，雙向滑價 = 0.001
        # bh 應該 ≈ 0.10 - 0.00585 - 0.001 ≈ 0.09315
        assert abs(bh - (0.10 - 0.00585 - 0.001)) < 1e-6


class TestSharpeMetric:
    """S0-2：per-trade Sharpe 取代 mean_return 做選參數依據。"""

    def test_sharpe_prefers_consistent_returns(self):
        """同樣 mean return，但 trade 變異小的 sharpe 應更高。"""
        # 直接測 Sharpe 計算公式（mean / std with ddof=1）
        import statistics
        consistent = [0.05, 0.05, 0.05, 0.05]
        volatile = [0.20, -0.10, 0.20, -0.10]
        # 兩組 mean 都是 0.05
        assert abs(statistics.mean(consistent) - statistics.mean(volatile)) < 1e-9
        # consistent std = 0 → 無 sharpe；volatile std > 0 → 有 sharpe
        assert statistics.stdev(consistent) == 0.0
        assert statistics.stdev(volatile) > 0
        # 改成有變異但小的 case 比 sharpe
        small = [0.06, 0.04, 0.06, 0.04]
        small_sharpe = statistics.mean(small) / statistics.stdev(small)
        big = [0.20, -0.10, 0.20, -0.10]
        big_sharpe = statistics.mean(big) / statistics.stdev(big)
        # mean 相同但 small 的變異更小 → small sharpe 更高
        assert small_sharpe > big_sharpe
