"""賣光 → 刪 holdings row（不留 0-share 殭屍）。

對應 Critical Fix #8（金融分析師審查）：原版 SELL 全部時走 UPDATE shares=0，
holdings 表會逐筆累積殭屍紀錄。
"""
from __future__ import annotations

import pytest

from app.data.db import Database
from app.portfolio import record_trade, rebuild_holding


def _holdings_count(db: Database, stock_id: str) -> int:
    """直接查 holdings 表（含 shares=0）— 不走 list_holdings 的 WHERE shares > 0。"""
    with db.connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM holdings WHERE stock_id=?",
            (stock_id,),
        ).fetchone()[0]


class TestRecordTradeSellAllRemovesRow:
    def test_buy_then_sell_all_removes_row(self, tmp_path):
        db = Database(tmp_path / "x.db")
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600)
        assert _holdings_count(db, "2330") == 1
        record_trade(db, "2024-02-15", "2330", "SELL", 1000, 650)
        # 全部賣光 → 不該留 row
        assert _holdings_count(db, "2330") == 0

    def test_partial_sell_keeps_row(self, tmp_path):
        db = Database(tmp_path / "x.db")
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600)
        record_trade(db, "2024-02-15", "2330", "SELL", 400, 650)
        # 只賣 400/1000 → 還有 600 股 → row 應留著
        assert _holdings_count(db, "2330") == 1


class TestRebuildHoldingDropsZero:
    def test_rebuild_after_sell_all_drops_row(self, tmp_path):
        db = Database(tmp_path / "x.db")
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600)
        record_trade(db, "2024-02-15", "2330", "SELL", 1000, 650)
        # 即使 record_trade 已經刪了，rebuild 重來一次也應保持空
        rebuild_holding(db, "2330")
        assert _holdings_count(db, "2330") == 0

    def test_rebuild_with_no_trades_drops_row(self, tmp_path):
        """holdings 有 row 但 trade_log 已被 delete_trade 清光 → rebuild 應刪 row。"""
        db = Database(tmp_path / "x.db")
        # 直接塞一筆 holdings（模擬殭屍）
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO holdings (stock_id, shares, avg_cost) VALUES (?, ?, ?)",
                ("9999", 0, 0),
            )
            conn.commit()
        rebuild_holding(db, "9999")
        assert _holdings_count(db, "9999") == 0

    def test_rebuild_with_remaining_shares_keeps_row(self, tmp_path):
        db = Database(tmp_path / "x.db")
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600)
        record_trade(db, "2024-02-15", "2330", "SELL", 400, 650)
        rebuild_holding(db, "2330")
        assert _holdings_count(db, "2330") == 1
