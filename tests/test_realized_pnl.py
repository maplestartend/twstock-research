"""FIFO 已實現損益（realized_pnl）+ 標籤勝率（journal_stats）的 characterization 測試。

這兩個函式原本零測試（波次二 backlog）。本檔先用 characterization test 釘住現有
FIFO 配對行為（含 fee/tax 計入、多買對一賣、一買對多賣、跨股分組），再針對
「賣超 → 未配對殘量被靜默丟棄」這個既有缺陷補上「不再靜默」的護欄測試。

全程用 tmp_path 建臨時 DB + record_trade 灌資料，不需 prod DB（CI 可跑）。
"""
from __future__ import annotations

import logging

import pytest

from app.data.db import Database
from app.portfolio import journal_stats, realized_pnl, record_trade


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "pnl.db")


# ----------------------------------------------------------------------
# realized_pnl：基本 FIFO 配對
# ----------------------------------------------------------------------
class TestRealizedPnlBasic:
    def test_empty_when_no_trades(self, db):
        assert realized_pnl(db).empty

    def test_empty_when_only_buys(self, db):
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600, fee=0, tax=0)
        assert realized_pnl(db).empty

    def test_full_match_pnl_without_fees(self, db):
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 1000, 650, fee=0, tax=0)
        out = realized_pnl(db)
        assert len(out) == 1
        row = out.iloc[0]
        assert row["stock_id"] == "2330"
        assert row["buy_date"] == "2024-01-02"
        assert row["sell_date"] == "2024-02-15"
        assert row["shares"] == 1000
        assert row["cost"] == pytest.approx(600_000)
        assert row["proceed"] == pytest.approx(650_000)
        assert row["pnl"] == pytest.approx(50_000)
        assert row["pnl_pct"] == pytest.approx(50_000 / 600_000)

    def test_fees_and_tax_fold_into_cost_and_proceed(self, db):
        # 買進手續費攤進成本、賣出手續費+證交稅攤出 proceed
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600, fee=100, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 1000, 650, fee=200, tax=300)
        row = realized_pnl(db).iloc[0]
        # cost = (600 + 100/1000) * 1000 = 600_100
        assert row["cost"] == pytest.approx(600_100)
        # proceed = (650 - (200+300)/1000) * 1000 = 649_500
        assert row["proceed"] == pytest.approx(649_500)
        assert row["pnl"] == pytest.approx(49_400)
        assert row["pnl_pct"] == pytest.approx(49_400 / 600_100)


# ----------------------------------------------------------------------
# realized_pnl：FIFO 配對順序（多買對一賣 / 一買對多賣）
# ----------------------------------------------------------------------
class TestRealizedPnlFIFO:
    def test_two_buys_one_sell_consumes_oldest_first(self, db):
        record_trade(db, "2024-01-02", "2330", "BUY", 500, 100, fee=0, tax=0)
        record_trade(db, "2024-01-10", "2330", "BUY", 500, 200, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 800, 300, fee=0, tax=0)
        out = realized_pnl(db).reset_index(drop=True)
        assert len(out) == 2
        # 第一筆吃光最早的 500@100
        assert out.iloc[0]["shares"] == 500
        assert out.iloc[0]["buy_price"] == 100
        assert out.iloc[0]["pnl"] == pytest.approx((300 - 100) * 500)
        # 第二筆吃 300 股 200@... 的 lot
        assert out.iloc[1]["shares"] == 300
        assert out.iloc[1]["buy_price"] == 200
        assert out.iloc[1]["pnl"] == pytest.approx((300 - 200) * 300)

    def test_one_buy_two_sells(self, db):
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 100, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 400, 200, fee=0, tax=0)
        record_trade(db, "2024-03-15", "2330", "SELL", 300, 250, fee=0, tax=0)
        out = realized_pnl(db).reset_index(drop=True)
        assert len(out) == 2
        assert out.iloc[0]["shares"] == 400
        assert out.iloc[0]["pnl"] == pytest.approx((200 - 100) * 400)
        assert out.iloc[1]["shares"] == 300
        assert out.iloc[1]["pnl"] == pytest.approx((250 - 100) * 300)

    def test_grouped_by_stock(self, db):
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 1000, 650, fee=0, tax=0)
        record_trade(db, "2024-01-02", "2317", "BUY", 1000, 100, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2317", "SELL", 1000, 90, fee=0, tax=0)
        out = realized_pnl(db)
        assert set(out["stock_id"]) == {"2330", "2317"}
        assert out[out["stock_id"] == "2330"].iloc[0]["pnl"] == pytest.approx(50_000)
        assert out[out["stock_id"] == "2317"].iloc[0]["pnl"] == pytest.approx(-10_000)

    def test_stock_id_filter(self, db):
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 1000, 650, fee=0, tax=0)
        record_trade(db, "2024-01-02", "2317", "BUY", 1000, 100, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2317", "SELL", 1000, 90, fee=0, tax=0)
        out = realized_pnl(db, stock_id="2330")
        assert set(out["stock_id"]) == {"2330"}


# ----------------------------------------------------------------------
# realized_pnl：賣超（over-sell）— 既有缺陷的護欄
# ----------------------------------------------------------------------
class TestRealizedPnlOversell:
    def test_oversell_only_matches_available_shares(self, db):
        """賣超：買 1000、賣 1500 → 只配對到 1000，多賣的 500 不產生配對列。

        這是既有行為（FIFO 沒有空頭/做空概念），保留不變。
        """
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 100, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 1500, 200, fee=0, tax=0)
        out = realized_pnl(db)
        assert len(out) == 1
        assert out.iloc[0]["shares"] == 1000
        assert out.iloc[0]["pnl"] == pytest.approx((200 - 100) * 1000)

    def test_oversell_emits_warning_not_silent(self, db, caplog):
        """賣超的未配對殘量不再被『靜默』丟棄 — 至少要記一筆 WARNING。"""
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 100, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 1500, 200, fee=0, tax=0)
        with caplog.at_level(logging.WARNING, logger="app.portfolio"):
            realized_pnl(db)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "賣超應記 WARNING，不該靜默丟棄"
        msg = warnings[0].getMessage()
        assert "2330" in msg

    def test_sell_with_no_buy_emits_warning(self, db, caplog):
        """完全沒有對應 BUY 的 SELL → 0 配對列 + WARNING。"""
        record_trade(db, "2024-02-15", "2330", "SELL", 500, 200, fee=0, tax=0)
        with caplog.at_level(logging.WARNING, logger="app.portfolio"):
            out = realized_pnl(db)
        assert out.empty
        assert any(r.levelno == logging.WARNING for r in caplog.records)


# ----------------------------------------------------------------------
# journal_stats：標籤勝率 / 平均報酬
# ----------------------------------------------------------------------
class TestJournalStats:
    def test_empty_when_no_realized(self, db):
        out = journal_stats(db)
        assert out.empty
        assert list(out.columns) == ["tag", "count", "win_rate", "avg_pnl_pct", "total_pnl"]

    def test_empty_when_realized_but_no_tags(self, db):
        record_trade(db, "2024-01-02", "2330", "BUY", 1000, 600, fee=0, tax=0)
        record_trade(db, "2024-02-15", "2330", "SELL", 1000, 650, fee=0, tax=0)
        assert journal_stats(db).empty

    def test_tag_win_rate_and_sorting(self, db):
        # winner: +100%；loser: -20%
        record_trade(db, "2024-01-02", "1111", "BUY", 1000, 100, fee=0, tax=0, tags="winner")
        record_trade(db, "2024-02-15", "1111", "SELL", 1000, 200, fee=0, tax=0)
        record_trade(db, "2024-01-02", "2222", "BUY", 1000, 500, fee=0, tax=0, tags="loser")
        record_trade(db, "2024-02-15", "2222", "SELL", 1000, 400, fee=0, tax=0)
        out = journal_stats(db)
        assert list(out["tag"]) == ["winner", "loser"]  # 依 total_pnl 由高到低
        winner = out[out["tag"] == "winner"].iloc[0]
        loser = out[out["tag"] == "loser"].iloc[0]
        assert winner["count"] == 1
        assert winner["win_rate"] == pytest.approx(1.0)
        assert winner["total_pnl"] == pytest.approx(100_000)
        assert loser["win_rate"] == pytest.approx(0.0)
        assert loser["total_pnl"] == pytest.approx(-100_000)

    def test_multi_tag_explodes(self, db):
        # 一筆 BUY 帶兩個逗號分隔 tag → 兩個 tag 各算一次
        record_trade(db, "2024-01-02", "1111", "BUY", 1000, 100, fee=0, tax=0, tags="強勢,法人連買")
        record_trade(db, "2024-02-15", "1111", "SELL", 1000, 200, fee=0, tax=0)
        out = journal_stats(db)
        assert set(out["tag"]) == {"強勢", "法人連買"}
        for _, r in out.iterrows():
            assert r["count"] == 1
            assert r["win_rate"] == pytest.approx(1.0)
