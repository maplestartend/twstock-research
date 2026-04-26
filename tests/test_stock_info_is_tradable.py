"""stock_info.is_tradable migration + radar.list_candidate_stocks 過濾。

對應 P1 DB audit Fix #5：用 schema 欄位取代每次掃描跑 regex，並把篩選邏輯下推到 SQL。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.data.db import Database
from app.scoring.radar import list_candidate_stocks


def _seed_legacy_stock_info(db_path: Path, rows: list[tuple]) -> None:
    """模擬「migration 之前就存在」的 stock_info 表（沒 is_tradable 欄位）。"""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE stock_info (
            stock_id TEXT PRIMARY KEY,
            stock_name TEXT,
            industry_category TEXT,
            type TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.executemany(
        "INSERT INTO stock_info (stock_id, stock_name, type) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


class TestMigrationAddIsTradable:
    def test_column_added_on_existing_db(self, tmp_path):
        db_path = tmp_path / "x.db"
        _seed_legacy_stock_info(
            db_path,
            [("2330", "TSMC", "twse"), ("030001", "權證A", "warrant")],
        )
        Database(db_path)  # 觸發 migration
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_info)").fetchall()}
        conn.close()
        assert "is_tradable" in cols

    def test_backfill_marks_warrants_zero(self, tmp_path):
        db_path = tmp_path / "x.db"
        _seed_legacy_stock_info(
            db_path,
            [
                ("2330", "TSMC", "twse"),       # 4 碼 → 1
                ("0050", "ETF50", "twse"),      # 4 碼 ETF → 1
                ("2881A", "富邦特", "twse"),    # 4 碼+字母 → 1
                ("030001", "權證A", "warrant"), # 6 碼 → 0
                ("070001", "牛證B", "warrant"), # 6 碼 → 0
                ("00878", "ETF878", "twse"),    # 5 碼 ETF (00xxx) → 1
            ],
        )
        Database(db_path)  # backfill
        conn = sqlite3.connect(db_path)
        rows = dict(conn.execute(
            "SELECT stock_id, is_tradable FROM stock_info"
        ).fetchall())
        conn.close()
        assert rows["2330"] == 1
        assert rows["0050"] == 1
        assert rows["2881A"] == 1
        assert rows["00878"] == 1
        assert rows["030001"] == 0
        assert rows["070001"] == 0

    def test_migration_is_idempotent(self, tmp_path):
        db_path = tmp_path / "x.db"
        _seed_legacy_stock_info(db_path, [("2330", "TSMC", "twse")])
        Database(db_path)
        Database(db_path)  # 第二次：is_tradable 已是 1，不該變
        conn = sqlite3.connect(db_path)
        v = conn.execute("SELECT is_tradable FROM stock_info WHERE stock_id='2330'").fetchone()[0]
        conn.close()
        assert v == 1

    def test_no_table_no_error(self, tmp_path):
        # 全新空 DB → SCHEMA 自動建空表 → backfill 跑零 row 不報錯
        Database(tmp_path / "fresh.db")
        Database(tmp_path / "fresh.db")  # 重複 init 也安全

    def test_fresh_schema_has_column(self, tmp_path):
        # 新 DB 直接由 SCHEMA 建出來時也要有 is_tradable
        Database(tmp_path / "fresh.db")
        conn = sqlite3.connect(tmp_path / "fresh.db")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_info)").fetchall()}
        conn.close()
        assert "is_tradable" in cols


class TestListCandidateStocksFilter:
    def _seed_full(self, db_path: Path) -> Database:
        # 用 legacy schema 起 → 觸發 migration → 補上日線
        _seed_legacy_stock_info(
            db_path,
            [
                ("2330", "TSMC", "twse"),
                ("030001", "權證A", "warrant"),
                ("0050", "ETF50", "twse"),
            ],
        )
        db = Database(db_path)
        # 種足夠多日線（min_days 預設 60）
        from datetime import date, timedelta
        d0 = date(2025, 1, 1)
        with db.connect() as conn:
            for sid in ("2330", "030001", "0050"):
                for i in range(70):
                    d = (d0 + timedelta(days=i)).isoformat()
                    conn.execute(
                        "INSERT OR REPLACE INTO daily_price (date, stock_id, close) VALUES (?,?,?)",
                        (d, sid, 100.0 + i),
                    )
            conn.commit()
        return db

    def test_warrant_excluded(self, tmp_path):
        db = self._seed_full(tmp_path / "x.db")
        out = list_candidate_stocks(db, min_days=60)
        sids = {sid for sid, _ in out}
        assert "2330" in sids
        assert "0050" in sids
        assert "030001" not in sids  # is_tradable=0

    def test_warrant_with_default_is_tradable_still_excluded(self, tmp_path):
        """模擬 market_updater.py 舊版 UPSERT 沒帶 is_tradable 時的情境：
        權證 row 的 is_tradable 被 schema DEFAULT 1 填上 → list_candidate_stocks
        必須仍靠 regex 兜底擋掉。對應今天的 'home 跑出一堆權證' bug 防退步。
        """
        db_path = tmp_path / "x.db"
        db = Database(db_path)  # 用新 schema 建表
        from datetime import date, timedelta
        d0 = date(2025, 1, 1)
        with db.connect() as conn:
            # 模擬 market_updater 沒帶 is_tradable → 全 default 1
            conn.executemany(
                "INSERT INTO stock_info (stock_id, stock_name, type) VALUES (?,?,?)",
                [
                    ("2330", "TSMC", "twse"),
                    ("701697", "宜鼎統一5C購04", "tpex"),  # 權證
                    ("731730", "順達統一59購03", "tpex"),  # 權證
                    ("00878", "ETF878", "twse"),
                ],
            )
            for sid in ("2330", "701697", "731730", "00878"):
                for i in range(70):
                    d = (d0 + timedelta(days=i)).isoformat()
                    conn.execute(
                        "INSERT OR REPLACE INTO daily_price (date, stock_id, close) VALUES (?,?,?)",
                        (d, sid, 100.0 + i),
                    )
            conn.commit()
        # 確認上面確實踩到「DEFAULT 1 但其實是權證」的雷
        with db.connect() as conn:
            v = conn.execute("SELECT is_tradable FROM stock_info WHERE stock_id='701697'").fetchone()[0]
            assert v == 1, "前置條件：應該踩到 DEFAULT 1 沒被 backfill"
        out = list_candidate_stocks(db, min_days=60)
        sids = {sid for sid, _ in out}
        assert "2330" in sids
        assert "00878" in sids
        assert "701697" not in sids
        assert "731730" not in sids

    def test_migration_rebackfills_when_default_value_wrong(self, tmp_path):
        """模擬：column 已存在、有舊權證 row 的 is_tradable=1（被 ADD COLUMN DEFAULT 1
        或 UPSERT 時填的）。重新 init Database 必須能糾正回 0。
        """
        db_path = tmp_path / "x.db"
        Database(db_path)  # 建表
        with sqlite3.connect(db_path) as conn:
            # 直接寫一個 is_tradable=1 但其實是權證的 row（模擬資料污染）
            conn.execute(
                "INSERT INTO stock_info (stock_id, stock_name, is_tradable) VALUES (?,?,?)",
                ("701697", "宜鼎統一5C購04", 1),
            )
            conn.commit()
        # 重新 init → migration 應該偵測到不一致並糾正
        Database(db_path)
        with sqlite3.connect(db_path) as conn:
            v = conn.execute(
                "SELECT is_tradable FROM stock_info WHERE stock_id='701697'"
            ).fetchone()[0]
        assert v == 0, "migration 必須能糾正 column 值與 regex 不一致的 row"
