"""鎖定 financials.publish_date 法定下限 stamp + look-ahead 守則。

對應 P3 #7（2026-04-30）：FinMind 單季 financials 表本來只有 quarter-end date，
backtest 用 date 過濾會在公告前 6 週就「看到」當季財報 → look-ahead bias。
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from app.data.db import Database
from app.data.updater import _finmind_quarter_publish_date


class TestFinmindPublishDateHelper:
    def test_q1_stamps_may_15(self):
        assert _finmind_quarter_publish_date("2026-03-31") == "2026-05-15"

    def test_q2_stamps_aug_14(self):
        assert _finmind_quarter_publish_date("2026-06-30") == "2026-08-14"

    def test_q3_stamps_nov_14(self):
        assert _finmind_quarter_publish_date("2026-09-30") == "2026-11-14"

    def test_q4_rolls_to_next_year_mar_31(self):
        # 2025 Q4 報表（年報）法定下限 = 2026-03-31
        assert _finmind_quarter_publish_date("2025-12-31") == "2026-03-31"

    def test_handles_timestamp(self):
        ts = pd.Timestamp("2025-12-31")
        assert _finmind_quarter_publish_date(ts) == "2026-03-31"

    def test_non_quarter_end_returns_none(self):
        # 中間月份 / 非標準季末 → 不知道是哪季 → None
        assert _finmind_quarter_publish_date("2026-04-15") is None
        assert _finmind_quarter_publish_date(None) is None
        assert _finmind_quarter_publish_date("") is None


class TestMigrationBackfillsPublishDate:
    """新建 DB 時 financials.publish_date 必須加上欄位且依 quarter 回填。"""

    def _seed_legacy_financials(self, db_path) -> None:
        # 模擬「舊版 financials 表沒有 publish_date 欄位」的場景
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE financials (
                date TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                type TEXT NOT NULL,
                value REAL,
                origin_name TEXT,
                PRIMARY KEY (stock_id, date, type)
            )
        """)
        conn.executemany(
            "INSERT INTO financials (date, stock_id, type, value) VALUES (?,?,?,?)",
            [
                ("2025-03-31", "2330", "EPS", 7.0),  # Q1 → 2025-05-15
                ("2025-06-30", "2330", "EPS", 8.0),  # Q2 → 2025-08-14
                ("2025-09-30", "2330", "EPS", 9.0),  # Q3 → 2025-11-14
                ("2025-12-31", "2330", "EPS", 10.0), # Q4 → 2026-03-31
            ],
        )
        conn.commit()
        conn.close()

    def test_migration_adds_column_and_backfills(self, tmp_path):
        db_path = tmp_path / "x.db"
        self._seed_legacy_financials(db_path)
        Database(db_path)  # 觸發 _init_schema → migration
        conn = sqlite3.connect(db_path)
        rows = sorted(conn.execute(
            "SELECT date, publish_date FROM financials ORDER BY date"
        ).fetchall())
        conn.close()
        assert rows == [
            ("2025-03-31", "2025-05-15"),
            ("2025-06-30", "2025-08-14"),
            ("2025-09-30", "2025-11-14"),
            ("2025-12-31", "2026-03-31"),
        ]

    def test_migration_idempotent(self, tmp_path):
        db_path = tmp_path / "x.db"
        self._seed_legacy_financials(db_path)
        Database(db_path)  # 第一次
        Database(db_path)  # 第二次：publish_date 已填，WHERE publish_date IS NULL 比不到任何 row
        conn = sqlite3.connect(db_path)
        # 數值不變
        row = conn.execute(
            "SELECT publish_date FROM financials WHERE date='2025-12-31'"
        ).fetchone()
        conn.close()
        assert row[0] == "2026-03-31"

    def test_no_table_no_error(self, tmp_path):
        # 全新空 DB，financials 由 SCHEMA 自動建表 → migration 跑零 row 不報錯
        db_path = tmp_path / "fresh.db"
        Database(db_path)
        Database(db_path)  # 重複 init 也安全


class TestLookAheadFiltering:
    """score_stock 的 _load_stock_bundle 用 publish_date 過濾，
    backtest 在 publish 前不該看到未公告的當季財報。"""

    def test_bundle_excludes_unreleased_q4(self, tmp_path):
        from app.scoring.engine import _load_stock_bundle

        db_path = tmp_path / "x.db"
        Database(db_path)  # 建表

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Q4 2025 報表：date=2025-12-31, publish_date=2026-03-31
        conn.execute(
            "INSERT INTO financials (date, stock_id, type, value, publish_date) "
            "VALUES (?,?,?,?,?)",
            ("2025-12-31", "2330", "EPS", 10.0, "2026-03-31"),
        )
        # Q3 2025 報表：date=2025-09-30, publish_date=2025-11-14
        conn.execute(
            "INSERT INTO financials (date, stock_id, type, value, publish_date) "
            "VALUES (?,?,?,?,?)",
            ("2025-09-30", "2330", "EPS", 9.0, "2025-11-14"),
        )
        conn.commit()

        # 在 2026-01-15 重播：Q4 還沒公告（publish_date=2026-03-31），不該看到
        bundle = _load_stock_bundle(conn, "2330", as_of="2026-01-15")
        fin = bundle["fin"]
        assert len(fin) == 1  # 只看到 Q3
        assert fin.iloc[0]["date"] == "2025-09-30"

        # 在 2026-04-01 重播：Q4 已公告（publish_date=2026-03-31 < 2026-04-01）
        bundle2 = _load_stock_bundle(conn, "2330", as_of="2026-04-01")
        assert len(bundle2["fin"]) == 2

        conn.close()
