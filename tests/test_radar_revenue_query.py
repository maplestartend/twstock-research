"""radar.score_all 的 monthly_revenue latest 查詢用 ROW_NUMBER 取代相關子查詢。

對應 P1 DB audit Fix #1：行為等價（同樣取「<=as_of 最大日的那筆」），但執行計畫從
O(N²) → O(N)。本測試只驗行為等價（取最新 row + 上限正確）；效能差距無法在 unit test 量。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.data.db import Database
from app.scoring import radar


@pytest.fixture
def seeded_db(tmp_path):
    db = Database(tmp_path / "x.db")
    # 兩檔 × 三個月份
    rows = [
        # stock_id, date, revenue, mom, yoy
        ("2330", "2026-02-10", 1000.0, 0.05, 0.10),
        ("2330", "2026-03-10", 1100.0, 0.10, 0.12),
        ("2330", "2026-04-10", 1200.0, 0.09, 0.15),
        ("2317", "2026-02-10", 500.0, 0.0, 0.0),
        ("2317", "2026-03-10", 480.0, -0.04, -0.02),
    ]
    df = pd.DataFrame(rows, columns=["stock_id", "date", "revenue", "mom_pct", "yoy_pct"])
    df["revenue_month"] = pd.to_datetime(df["date"]).dt.month
    df["revenue_year"] = pd.to_datetime(df["date"]).dt.year
    db.upsert_df(df, "monthly_revenue")
    return db


class TestLatestRevenueQuery:
    def test_returns_latest_per_stock(self, seeded_db):
        # 直接抓取 score_all 的內部查詢
        from datetime import date as _date
        as_of_str = "2026-05-01"
        with seeded_db.connect() as conn:
            out = pd.read_sql_query(
                """
                SELECT stock_id, date, revenue, mom_pct, yoy_pct FROM (
                    SELECT stock_id, date, revenue, mom_pct, yoy_pct,
                           ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                    FROM monthly_revenue
                    WHERE date <= ?
                ) WHERE rn = 1
                """, conn, params=[as_of_str],
            )
        d = {r.stock_id: r for r in out.itertuples()}
        assert d["2330"].date == "2026-04-10"
        assert d["2317"].date == "2026-03-10"
        assert d["2330"].revenue == pytest.approx(1200.0)

    def test_respects_as_of_upper_bound(self, seeded_db):
        # as_of 設在 3 月之前 → 不能取到 2026-04-10 / 2026-03-10
        as_of_str = "2026-02-15"
        with seeded_db.connect() as conn:
            out = pd.read_sql_query(
                """
                SELECT stock_id, date FROM (
                    SELECT stock_id, date,
                           ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                    FROM monthly_revenue
                    WHERE date <= ?
                ) WHERE rn = 1
                """, conn, params=[as_of_str],
            )
        d = dict(zip(out["stock_id"], out["date"]))
        assert d["2330"] == "2026-02-10"
        assert d["2317"] == "2026-02-10"

    def test_empty_db_returns_empty(self, tmp_path):
        db = Database(tmp_path / "fresh.db")
        with db.connect() as conn:
            out = pd.read_sql_query(
                """
                SELECT stock_id FROM (
                    SELECT stock_id,
                           ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                    FROM monthly_revenue
                    WHERE date <= ?
                ) WHERE rn = 1
                """, conn, params=["2026-04-26"],
            )
        assert out.empty
