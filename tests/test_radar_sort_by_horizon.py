"""短/中/長期策略各以自身維度排序，其他策略維持 composite。

策略：query_radar_hits 改採 STRATEGY_SORT_COLUMN 對映，避免使用者選「短線強勢」卻看到
按綜合分排序的命中清單（短期分數第一名可能因為長期偏低而被擠到後面）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.data.db import Database
from app.scoring.radar_queries import query_radar_hits


@pytest.fixture
def seeded_db(tmp_path):
    """三檔股票故意讓 short / mid / long / composite 各自有不同的「第一名」，
    這樣排序欄位生效時的順序差異一眼就能看出來。"""
    db = Database(tmp_path / "x.db")
    rows = [
        # stock_id, name, short, mid, long, composite
        ("A001", "TopShort", 90.0, 50.0, 50.0, 60.0),
        ("A002", "TopMid",   50.0, 90.0, 50.0, 65.0),
        ("A003", "TopLong",  50.0, 50.0, 90.0, 55.0),
        ("A004", "TopComp",  60.0, 60.0, 60.0, 80.0),
    ]
    sh = pd.DataFrame(rows, columns=["stock_id", "stock_name", "short", "mid", "long", "composite"])
    sh["as_of"] = "2026-04-27"
    sh["close"] = 100.0
    sh["recommendation"] = "🟢 偏多"
    # 每檔都掛上四個策略，讓不同策略選同一組標的、只差排序
    sh["strategies"] = "短線強勢,中期波段,長期價值,回檔布局"
    db.upsert_df(sh, "signal_history")

    info = pd.DataFrame(
        [(r[0], r[1], "twse") for r in rows],
        columns=["stock_id", "stock_name", "type"],
    )
    db.upsert_df(info, "stock_info")
    return db


def _ids(rows: list[dict]) -> list[str]:
    return [r["stock_id"] for r in rows]


class TestStrategySort:
    def test_short_strategy_sorts_by_short_score(self, seeded_db):
        rows = query_radar_hits(seeded_db, strategy="短線強勢", markets={"上市", "上櫃"})
        # short=90 的 A001 必須排第一；後三檔都 short=50 → 由 composite 決勝負（A004 80 > A002 65 > A003 55）
        assert _ids(rows) == ["A001", "A004", "A002", "A003"]

    def test_mid_strategy_sorts_by_mid_score(self, seeded_db):
        rows = query_radar_hits(seeded_db, strategy="中期波段", markets={"上市", "上櫃"})
        assert _ids(rows)[0] == "A002"  # mid=90 第一

    def test_long_strategy_sorts_by_long_score(self, seeded_db):
        rows = query_radar_hits(seeded_db, strategy="長期價值", markets={"上市", "上櫃"})
        assert _ids(rows)[0] == "A003"  # long=90 第一

    def test_other_strategy_keeps_composite_sort(self, seeded_db):
        """回檔布局 / 三榜俱佳等策略不在 mapping 內 → 依舊 composite 降序。"""
        rows = query_radar_hits(seeded_db, strategy="回檔布局", markets={"上市", "上櫃"})
        assert _ids(rows)[0] == "A004"  # composite=80 第一

    def test_no_strategy_keeps_composite_sort(self, seeded_db):
        """dashboard 戰情室不指定 strategy → 維持 composite 排序，dashboard 行為不變。"""
        rows = query_radar_hits(seeded_db, strategy=None, markets={"上市", "上櫃"})
        assert _ids(rows)[0] == "A004"

    def test_null_score_pushed_to_end(self, tmp_path):
        """主排序欄位 NULL 不能擠到前面（'NULLS LAST' 行為）。"""
        db = Database(tmp_path / "y.db")
        rows = [
            ("B001", "HasShort", 80.0, 60.0, 60.0, 65.0),
            ("B002", "NullShort", None, 90.0, 90.0, 90.0),  # composite 較高但 short 是 None
        ]
        sh = pd.DataFrame(rows, columns=["stock_id", "stock_name", "short", "mid", "long", "composite"])
        sh["as_of"] = "2026-04-27"
        sh["close"] = 100.0
        sh["recommendation"] = "🟢 偏多"
        sh["strategies"] = "短線強勢"
        db.upsert_df(sh, "signal_history")
        db.upsert_df(
            pd.DataFrame([(r[0], r[1], "twse") for r in rows], columns=["stock_id", "stock_name", "type"]),
            "stock_info",
        )

        out = query_radar_hits(db, strategy="短線強勢", markets={"上市", "上櫃"})
        # B001 有 short 必須排前面；B002 short=None 即使 composite 較高也排後面
        assert _ids(out) == ["B001", "B002"]
