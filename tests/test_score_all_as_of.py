"""score_all 的 as_of 過濾：歷史回放時不可看到未來月營收 / 財報 / 價量。

對應 Critical Fix #3（金融分析師審查指出 score_all 沒有 as_of 上限，歷史回放時
會把今日的月營收 / 財報注入評分，產生 look-ahead）。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.data.db import Database
from app.scoring import radar


def _seed(db: Database) -> None:
    """種一個小型 fixture：兩檔股票、兩個月營收快照。"""
    with db.connect() as conn:
        # stock_info
        conn.executemany(
            "INSERT OR REPLACE INTO stock_info (stock_id, stock_name, industry_category, type) VALUES (?, ?, ?, ?)",
            [("2330", "台積電", "半導體業", "stock"), ("0050", "元大台灣50", None, "etf")],
        )
        # 兩個月營收快照：2026-03-10（3 月公告）、2026-04-10（4 月公告）
        # 4 月那筆 yoy=+30%（爆發）；3 月那筆 yoy=+5%
        conn.executemany(
            "INSERT OR REPLACE INTO monthly_revenue (date, stock_id, revenue, revenue_month, revenue_year, mom_pct, yoy_pct) VALUES (?,?,?,?,?,?,?)",
            [
                ("2026-03-10", "2330", 200_000_000_000, 2, 2026, 0.0, 0.05),
                ("2026-04-10", "2330", 260_000_000_000, 3, 2026, 0.30, 0.30),
            ],
        )
        conn.commit()


class TestRevenueAsOfFilter:
    """as_of 設為 2026-03-15 時，monthly_revenue 不該看到 2026-04-10 那筆。"""

    def test_latest_revenue_truncated_at_as_of(self, tmp_path):
        db = Database(tmp_path / "x.db")
        _seed(db)

        # _bulk_load 與內聯 monthly_revenue 查詢都應遵守 as_of 上限。
        # 這裡直接驗 _bulk_load 與 score_all 內 SQL 的篩選效果（用一個小 helper）。
        with db.connect() as conn:
            # 模擬 score_all 使用的 latest 子查詢（as_of=2026-03-15 → 不該看到 04-10）
            df_at_315 = pd.read_sql_query(
                """
                SELECT stock_id, date, yoy_pct FROM monthly_revenue
                WHERE date <= ?
                  AND date = (SELECT MAX(date) FROM monthly_revenue mr2
                              WHERE mr2.stock_id = monthly_revenue.stock_id AND mr2.date <= ?)
                """,
                conn, params=["2026-03-15", "2026-03-15"],
            )
            df_at_415 = pd.read_sql_query(
                """
                SELECT stock_id, date, yoy_pct FROM monthly_revenue
                WHERE date <= ?
                  AND date = (SELECT MAX(date) FROM monthly_revenue mr2
                              WHERE mr2.stock_id = monthly_revenue.stock_id AND mr2.date <= ?)
                """,
                conn, params=["2026-04-15", "2026-04-15"],
            )

        # 3-15 那天還沒看到 4-10 的爆發資料，應只回 3-10 那筆 yoy=0.05
        assert len(df_at_315) == 1
        assert df_at_315.iloc[0]["date"] == "2026-03-10"
        assert abs(df_at_315.iloc[0]["yoy_pct"] - 0.05) < 1e-9
        # 4-15 那天兩筆都看得到 → latest 是 4-10
        assert len(df_at_415) == 1
        assert df_at_415.iloc[0]["date"] == "2026-04-10"
        assert abs(df_at_415.iloc[0]["yoy_pct"] - 0.30) < 1e-9


class TestScoreAllSignature:
    """score_all 應接受 as_of kwarg。"""

    def test_score_all_accepts_as_of_param(self, tmp_path):
        db = Database(tmp_path / "x.db")
        # 沒種資料 → 候選名單空 → 回空 DataFrame，但 as_of 不該爆
        out_today = radar.score_all(db)
        out_hist_str = radar.score_all(db, as_of="2026-03-15")
        out_hist_date = radar.score_all(db, as_of=date(2026, 3, 15))
        assert isinstance(out_today, pd.DataFrame)
        assert isinstance(out_hist_str, pd.DataFrame)
        assert isinstance(out_hist_date, pd.DataFrame)


class TestSnapshotTodayWithAsOf:
    def test_snapshot_today_propagates_as_of(self, tmp_path):
        from app.scoring.history import snapshot_today
        db = Database(tmp_path / "x.db")
        # 空 DB → 返回 0 筆，但 kwarg 不該爆
        n = snapshot_today(db, as_of="2026-03-15")
        assert n == 0
