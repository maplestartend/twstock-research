"""月營收 publish date 推到「次月 10 號」+ 一次性 migration。

對應 Critical Fix #2（月營收 look-ahead bias）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.data.db import Database
from app.data.mops_fetcher import _publish_date


class TestPublishDate:
    def test_normal_month_stamps_next_month_10th(self):
        # 2026 年 3 月份營收 → 公告日 2026-04-10
        assert _publish_date(2026, 3) == "2026-04-10"

    def test_january_data(self):
        assert _publish_date(2026, 1) == "2026-02-10"

    def test_december_rolls_year(self):
        # 2025 年 12 月份營收 → 公告日 2026-01-10
        assert _publish_date(2025, 12) == "2026-01-10"


class TestMigrationLegacyPublishDate:
    """一次性 migration：把 DB 裡舊的 DAY=01 月營收 row 推 +9 天到 DAY=10。"""

    def _seed(self, db_path: Path, rows: list[tuple]) -> None:
        # 先建立 monthly_revenue 表（最小 schema 即可）
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_revenue (
                date TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                revenue REAL,
                revenue_month INTEGER,
                revenue_year INTEGER,
                mom_pct REAL,
                yoy_pct REAL,
                PRIMARY KEY (stock_id, date)
            )
        """)
        conn.executemany(
            "INSERT INTO monthly_revenue (date, stock_id, revenue, revenue_month, revenue_year) VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()

    def test_old_day01_rows_get_shifted(self, tmp_path):
        db_path = tmp_path / "x.db"
        # 種兩筆舊 DAY=01 row + 一筆新 DAY=10 row
        self._seed(db_path, [
            ("2026-04-01", "2330", 100.0, 3, 2026),
            ("2026-03-01", "2330", 90.0, 2, 2026),
            ("2026-04-10", "0050", 50.0, 3, 2026),
        ])
        Database(db_path)  # 觸發 _init_schema → _migrate_monthly_revenue_publish_date
        conn = sqlite3.connect(db_path)
        dates = sorted(r[0] for r in conn.execute(
            "SELECT date FROM monthly_revenue ORDER BY date"
        ))
        conn.close()
        # DAY=01 都被推到 DAY=10；DAY=10 維持不動
        assert dates == ["2026-03-10", "2026-04-10", "2026-04-10"]

    def test_migration_is_idempotent(self, tmp_path):
        db_path = tmp_path / "x.db"
        self._seed(db_path, [("2026-04-01", "2330", 100.0, 3, 2026)])
        Database(db_path)  # 第一次 → DAY 01 → 10
        Database(db_path)  # 第二次 → DAY 10 已不符 WHERE，不再推
        conn = sqlite3.connect(db_path)
        date = conn.execute("SELECT date FROM monthly_revenue").fetchone()[0]
        conn.close()
        assert date == "2026-04-10"  # 不會變成 19 號

    def test_no_table_no_error(self, tmp_path):
        # 全新空 DB，monthly_revenue 由 SCHEMA 自動建空表 → migration 跑零 row 不報錯
        db_path = tmp_path / "fresh.db"
        Database(db_path)  # 應該乾淨無例外
        # 重複 init 也安全
        Database(db_path)
