"""Database 層 — upsert / 並發安全 / PRAGMA 設定。"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pandas as pd
import pytest

from app.data.db import Database


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    """每個 test 用獨立 SQLite 檔，避免互相污染。"""
    return Database(tmp_path / "test.db")


class TestUpsertDf:
    def test_basic_insert(self, tmp_db: Database):
        df = pd.DataFrame([
            {"stock_id": "2330", "date": "2025-01-01", "close": 500.0, "volume": 1000},
            {"stock_id": "2330", "date": "2025-01-02", "close": 510.0, "volume": 1200},
        ])
        # daily_price 表已由 SCHEMA 建立
        n = tmp_db.upsert_df(df, "daily_price")
        assert n == 2
        with tmp_db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
        assert count == 2

    def test_replace_overwrites_by_pk(self, tmp_db: Database):
        df1 = pd.DataFrame([{"stock_id": "2330", "date": "2025-01-01", "close": 500.0}])
        df2 = pd.DataFrame([{"stock_id": "2330", "date": "2025-01-01", "close": 600.0}])
        tmp_db.upsert_df(df1, "daily_price")
        tmp_db.upsert_df(df2, "daily_price")
        with tmp_db.connect() as conn:
            row = conn.execute(
                "SELECT close FROM daily_price WHERE stock_id='2330' AND date='2025-01-01'"
            ).fetchone()
        # PK 衝突 → 後寫蓋前寫
        assert row["close"] == 600.0

    def test_no_residual_tmp_table_on_success(self, tmp_db: Database):
        """成功路徑不該留下 _tmp_upsert_xxx 殘表。"""
        df = pd.DataFrame([{"stock_id": "2330", "date": "2025-01-01", "close": 500.0}])
        tmp_db.upsert_df(df, "daily_price")
        with tmp_db.connect() as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '_tmp_upsert%'"
                ).fetchall()
            ]
        assert tables == [], f"upsert 留下殘表：{tables}"

    def test_no_residual_tmp_table_on_failure(self, tmp_db: Database):
        """寫入失敗時也要把 tmp 表清掉，避免下一次呼叫看到殘表。"""
        # 故意給一個不存在的目標表
        df = pd.DataFrame([{"a": 1, "b": 2}])
        with pytest.raises(sqlite3.OperationalError):
            tmp_db.upsert_df(df, "this_table_does_not_exist")
        with tmp_db.connect() as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '_tmp_upsert%'"
                ).fetchall()
            ]
        assert tables == [], f"失敗路徑留下殘表：{tables}"

    def test_concurrent_upserts_use_distinct_tmp_tables(self, tmp_db: Database):
        """兩個 thread 同時 upsert 不該因為 tmp 表名衝突而互相破壞。"""
        # 用兩組互不重疊的資料模擬不同 writer
        df_a = pd.DataFrame([
            {"stock_id": "2330", "date": f"2025-01-{i:02d}", "close": 500.0 + i}
            for i in range(1, 11)
        ])
        df_b = pd.DataFrame([
            {"stock_id": "2317", "date": f"2025-01-{i:02d}", "close": 100.0 + i}
            for i in range(1, 11)
        ])
        results = {"a": None, "b": None, "errors": []}

        def writer(key: str, df: pd.DataFrame):
            try:
                results[key] = tmp_db.upsert_df(df, "daily_price")
            except Exception as e:  # noqa: BLE001
                results["errors"].append((key, repr(e)))

        ta = threading.Thread(target=writer, args=("a", df_a))
        tb = threading.Thread(target=writer, args=("b", df_b))
        ta.start(); tb.start()
        ta.join(); tb.join()

        # busy_timeout=5000 + uuid tmp 表 → 兩組都該成功
        assert results["errors"] == [], f"併發寫入失敗：{results['errors']}"
        assert results["a"] == 10
        assert results["b"] == 10
        with tmp_db.connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
        assert n == 20  # 兩組都進去


class TestPragmas:
    def test_busy_timeout_set(self, tmp_db: Database):
        with tmp_db.connect() as conn:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    def test_wal_mode(self, tmp_db: Database):
        with tmp_db.connect() as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
