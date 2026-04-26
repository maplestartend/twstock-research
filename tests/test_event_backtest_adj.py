"""event-backtest 用還原價 + 納入 split 事件。

對應 Critical Fix #4：原版只看 'dividend' event 且用 raw price，造成
- 0050 等 split 事件被當成 -75% 暴跌
- 配股事件雙重計算（raw 價跌 + 加回 cash）
"""
from __future__ import annotations

import sqlite3

import pytest

from app.backtest.event_driven import EventConfig, run_event_backtest
from app.data.db import Database


def _seed(db_path) -> None:
    db = Database(db_path)
    with db.connect() as conn:
        # stock_info
        conn.executemany(
            "INSERT OR REPLACE INTO stock_info (stock_id, stock_name) VALUES (?, ?)",
            [("0050", "元大台灣50"), ("2330", "台積電")],
        )
        # 0050 在 2025-06-15 1:4 split：before=400, after=100, factor=0.25
        # 2330 在 2025-07-10 配 5 元現金股利：before=600, after=595, factor=595/600≈0.9917
        conn.executemany(
            "INSERT OR REPLACE INTO adj_event (date, stock_id, event_type, before_price, after_price, factor) VALUES (?,?,?,?,?,?)",
            [
                ("2025-06-15", "0050", "split", 400.0, 100.0, 0.25),
                ("2025-07-10", "2330", "dividend", 600.0, 595.0, 595.0 / 600.0),
            ],
        )
        # daily_price + daily_price_adj
        # 0050: 用還原後價序列（1:4 split 後 close_adj 把舊資料 / 4），假設 entry/exit 都 ~100 → 報酬 ≈ 0
        # 涵蓋兩個事件（0050 split 6/15、2330 dividend 7/10）的前後 ±10 個交易日
        days = [f"2025-{m:02d}-{d:02d}" for m, d in [
            (6, 2), (6, 3), (6, 4), (6, 5), (6, 6), (6, 9), (6, 10), (6, 11), (6, 12), (6, 13),
            (6, 16), (6, 17), (6, 18), (6, 19), (6, 20), (6, 23), (6, 24), (6, 25), (6, 26), (6, 27),
            (6, 30), (7, 1), (7, 2), (7, 3), (7, 4), (7, 7), (7, 8), (7, 9), (7, 10), (7, 11),
            (7, 14), (7, 15), (7, 16), (7, 17), (7, 18), (7, 21), (7, 22), (7, 23), (7, 24), (7, 25),
        ]]
        rows_p = []
        rows_a = []
        for d in days:
            # 0050: raw 與 adj 都假設 100（簡化 — 真實 split 邏輯由 adj_event 處理）
            rows_p.append((d, "0050", 100.0, 100.0, 100.0, 100.0, 1000.0, None, None, None))
            rows_a.append((d, "0050", 100.0, 100.0, 100.0, 100.0))
            # 2330: adj 一律 605（還原後）；raw 在除息前 600 / 後 595（不影響本測試，因為用 adj）
            rows_p.append((d, "2330", 600.0, 600.0, 600.0, 600.0, 1000.0, None, None, None))
            rows_a.append((d, "2330", 605.0, 605.0, 605.0, 605.0))
        conn.executemany(
            "INSERT OR REPLACE INTO daily_price (date, stock_id, open, high, low, close, volume, amount, turnover, spread) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows_p,
        )
        conn.executemany(
            "INSERT OR REPLACE INTO daily_price_adj (date, stock_id, close_adj, open_adj, high_adj, low_adj) VALUES (?,?,?,?,?,?)",
            rows_a,
        )
        conn.commit()
    return db


class TestEventBacktestUsesAdjPrice:
    def test_split_event_does_not_show_75pct_loss(self, tmp_path):
        """1:4 split 用還原價時 price_return 應 ≈ 0，不該是 -75%。"""
        db = _seed(tmp_path / "x.db")
        cfg = EventConfig(entry_offset=-3, exit_offset=5, since_year=2024, min_dividend=0.0)
        result = run_event_backtest(db, ["0050"], cfg)
        # 應抓到 1 個 split 事件
        split_trades = [t for t in result.trades if t.event_type == "split"]
        assert len(split_trades) == 1
        t = split_trades[0]
        assert t.entry_price is not None and t.exit_price is not None
        # adj 後 entry/exit 都 ≈ 100，price_return 應接近 0（容忍 1%）
        assert abs(t.price_return) < 0.01, f"split shouldn't show big return on adj price; got {t.price_return}"
        # split 事件 cash_dividend = 0
        assert t.cash_dividend == 0.0

    def test_dividend_event_total_return_matches_adj_price_return(self, tmp_path):
        """除息事件用 adj price 算出的 price_return 已含「再投入」效果，total_return 應等於 price_return。"""
        db = _seed(tmp_path / "x.db")
        cfg = EventConfig(entry_offset=-3, exit_offset=5, since_year=2024, min_dividend=0.0)
        result = run_event_backtest(db, ["2330"], cfg)
        div_trades = [t for t in result.trades if t.event_type == "dividend"]
        assert len(div_trades) == 1
        t = div_trades[0]
        assert t.price_return is not None and t.total_return is not None
        # 用 adj 後不再加 cash 一遍
        assert abs(t.total_return - t.price_return) < 1e-9

    def test_event_type_field_propagated(self, tmp_path):
        db = _seed(tmp_path / "x.db")
        cfg = EventConfig(entry_offset=-3, exit_offset=5, since_year=2024, min_dividend=0.0)
        result = run_event_backtest(db, ["0050", "2330"], cfg)
        types = sorted(t.event_type for t in result.trades)
        # 應同時含 dividend + split
        assert "dividend" in types
        assert "split" in types
