"""track_performance 的「最新收盤」查詢用 MAX(date) 常數 + WHERE date=? 取代 GROUP BY 子查詢。

對應 P1 DB audit Fix #2：行為等價（每檔取最新收盤），但走 idx_price_date 索引而非全表掃。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.data.db import Database
from app.scoring.history import track_performance, snapshot_today


@pytest.fixture
def seeded_db(tmp_path):
    db = Database(tmp_path / "x.db")
    # 多日多檔的日線
    rows = []
    for sid, base in [("2330", 500.0), ("2317", 100.0)]:
        for i in range(1, 8):
            rows.append({
                "stock_id": sid,
                "date": f"2026-04-{i:02d}",
                "close": base + i,
            })
    df = pd.DataFrame(rows)
    db.upsert_df(df, "daily_price")
    return db


class TestLatestClosePin:
    def test_returns_only_latest_date(self, seeded_db):
        """track_performance 內部會用 MAX(date) → WHERE date=?，
        應該只回每檔最新一筆，且 latest_date 一致。"""
        # 種一筆 signal_history
        snap = pd.DataFrame([
            {
                "as_of": "2026-04-01", "stock_id": "2330", "stock_name": "TSMC",
                "close": 501.0, "short": 60, "mid": 60, "long": 60,
                "composite": 60, "recommendation": "BUY", "strategies": "",
                "data_completeness": 1.0, "is_stale": 0,
            },
            {
                "as_of": "2026-04-01", "stock_id": "2317", "stock_name": "Hon Hai",
                "close": 101.0, "short": 50, "mid": 50, "long": 50,
                "composite": 50, "recommendation": "HOLD", "strategies": "",
                "data_completeness": 1.0, "is_stale": 0,
            },
        ])
        seeded_db.upsert_df(snap, "signal_history")

        out = track_performance(seeded_db, "2026-04-01")
        assert not out.empty
        # 兩檔 latest_date 都應該是 2026-04-07（最大日期）
        assert set(out["latest_date"]) == {"2026-04-07"}
        # 漲跌幅 = (507 - 501) / 501 vs (107 - 101) / 101
        d = {r["stock_id"]: r for _, r in out.iterrows()}
        assert d["2330"]["latest_close"] == pytest.approx(507.0)
        assert d["2317"]["latest_close"] == pytest.approx(107.0)

    def test_empty_daily_price_no_crash(self, tmp_path):
        db = Database(tmp_path / "fresh.db")
        snap = pd.DataFrame([{
            "as_of": "2026-04-01", "stock_id": "2330", "stock_name": "TSMC",
            "close": 500.0, "short": 60, "mid": 60, "long": 60,
            "composite": 60, "recommendation": "BUY", "strategies": "",
            "data_completeness": 1.0, "is_stale": 0,
        }])
        db.upsert_df(snap, "signal_history")
        # 沒日線資料 → latest_close 全 NaN，但函式不該爆
        out = track_performance(db, "2026-04-01")
        assert not out.empty
        assert pd.isna(out.iloc[0]["latest_close"])
