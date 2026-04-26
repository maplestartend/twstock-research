"""radar._industry_yield_z_map：同產業殖利率 z-score 預計算。"""
from __future__ import annotations

import pandas as pd

from app.scoring.radar import _industry_yield_z_map


def _per_pbr(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestIndustryYieldZMap:
    def test_empty_returns_empty(self):
        assert _industry_yield_z_map(pd.DataFrame(), {}) == {}

    def test_no_dividend_column_returns_empty(self):
        df = pd.DataFrame([{"stock_id": "1101", "date": "2026-04-25", "per": 12.0}])
        assert _industry_yield_z_map(df, {"1101": "水泥"}) == {}

    def test_industry_with_too_few_peers_skipped(self):
        # 同產業只有 3 檔（< 4）→ 跳過該產業，z 不算
        df = _per_pbr([
            {"stock_id": "A", "date": "2026-04-25", "dividend_yield": 5.0},
            {"stock_id": "B", "date": "2026-04-25", "dividend_yield": 4.0},
            {"stock_id": "C", "date": "2026-04-25", "dividend_yield": 6.0},
        ])
        ind = {"A": "x", "B": "x", "C": "x"}
        assert _industry_yield_z_map(df, ind) == {}

    def test_z_score_computed_for_qualified_industry(self):
        # 4 檔同產業，殖利率 [2, 3, 4, 5]，mean=3.5、std (sample)=1.290994
        # z(2)=-1.16, z(5)=+1.16
        df = _per_pbr([
            {"stock_id": s, "date": "2026-04-25", "dividend_yield": y}
            for s, y in [("A", 2.0), ("B", 3.0), ("C", 4.0), ("D", 5.0)]
        ])
        ind = {"A": "金融", "B": "金融", "C": "金融", "D": "金融"}
        z = _industry_yield_z_map(df, ind)
        assert set(z.keys()) == {"A", "B", "C", "D"}
        assert z["A"] < -1.0
        assert z["D"] > 1.0
        assert abs(z["B"] + 0.387) < 0.01

    def test_zero_std_industry_skipped(self):
        # 同產業 4 檔殖利率全相同 → std=0 → 跳過避免除以 0
        df = _per_pbr([
            {"stock_id": s, "date": "2026-04-25", "dividend_yield": 3.0}
            for s in ["A", "B", "C", "D"]
        ])
        ind = {"A": "x", "B": "x", "C": "x", "D": "x"}
        assert _industry_yield_z_map(df, ind) == {}

    def test_uses_latest_date_per_stock(self):
        # 同股票兩筆，z 該用最新那筆
        df = _per_pbr([
            {"stock_id": "A", "date": "2026-04-20", "dividend_yield": 1.0},
            {"stock_id": "A", "date": "2026-04-25", "dividend_yield": 5.0},
            {"stock_id": "B", "date": "2026-04-25", "dividend_yield": 2.0},
            {"stock_id": "C", "date": "2026-04-25", "dividend_yield": 3.0},
            {"stock_id": "D", "date": "2026-04-25", "dividend_yield": 4.0},
        ])
        ind = {s: "x" for s in ["A", "B", "C", "D"]}
        z = _industry_yield_z_map(df, ind)
        # A 用 5.0（最新），mean=3.5，z(A) > 0
        assert z["A"] > 0

    def test_stocks_without_industry_skipped(self):
        # E 沒有 industry → 不進入任何分組（即便夠 4 檔）
        df = _per_pbr([
            {"stock_id": s, "date": "2026-04-25", "dividend_yield": float(i)}
            for i, s in enumerate(["A", "B", "C", "D", "E"], start=1)
        ])
        ind = {"A": "x", "B": "x", "C": "x", "D": "x"}  # E 缺
        z = _industry_yield_z_map(df, ind)
        assert "E" not in z
        assert set(z.keys()) == {"A", "B", "C", "D"}
