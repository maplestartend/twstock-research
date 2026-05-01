"""鎖定 derived path（_fill_from_quarterly_derived）的指標完整性。

對應 2026-04-30 P0 fix：之前 derived 路徑漏算 eps_cagr_3y、peg、yoy 用錯語意。
這支測試確保未來再改 fundamentals.py 不會把這些 regression 放回去。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.indicators.fundamentals import (
    _fill_balance_sheet_ratios,
    _fill_from_cumulative,
    _fill_from_quarterly_derived,
    _single_q_yoy_from_derived,
    _ttm_rolling_from_cumulative,
    fundamental_snapshot,
)


def _mk_derived(rows: list[dict]) -> pd.DataFrame:
    """rows = [{date, year, quarter, type, value}, ...]"""
    return pd.DataFrame(rows)


def _mk_cum(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _mk_per_pbr(per: float | None = None) -> pd.DataFrame:
    if per is None:
        return pd.DataFrame()
    return pd.DataFrame([{"date": "2026-03-31", "per": per, "pbr": 2.0, "dividend_yield": 3.0}])


class TestSingleQYoY:
    def test_basic_yoy(self):
        # Q1 2026 EPS = 5.0；Q1 2025 EPS = 4.0 → YoY = +25%
        df = _mk_derived([
            {"date": "2025-03-31", "year": 2025, "quarter": 1, "type": "EPS", "value": 4.0},
            {"date": "2026-03-31", "year": 2026, "quarter": 1, "type": "EPS", "value": 5.0},
        ])
        assert _single_q_yoy_from_derived(df, "EPS") == 0.25

    def test_returns_none_when_prior_year_missing(self):
        df = _mk_derived([
            {"date": "2026-03-31", "year": 2026, "quarter": 1, "type": "EPS", "value": 5.0},
        ])
        assert _single_q_yoy_from_derived(df, "EPS") is None

    def test_uses_year_quarter_lookup_not_iloc(self):
        """若中間缺一季，iloc[-5] 會拿錯資料；以 (year-1, quarter) 顯式查找才對。"""
        df = _mk_derived([
            {"date": "2024-12-31", "year": 2024, "quarter": 4, "type": "EPS", "value": 99.0},  # 干擾項
            {"date": "2025-03-31", "year": 2025, "quarter": 1, "type": "EPS", "value": 4.0},
            # 故意跳過 Q2/Q3/Q4 2025
            {"date": "2026-03-31", "year": 2026, "quarter": 1, "type": "EPS", "value": 5.0},
        ])
        # 應該找到 2025-Q1=4.0，YoY = (5-4)/4 = +25%；不該誤拿 2024-Q4=99 算
        assert _single_q_yoy_from_derived(df, "EPS") == 0.25

    def test_negative_base_uses_abs(self):
        df = _mk_derived([
            {"date": "2025-03-31", "year": 2025, "quarter": 1, "type": "EPS", "value": -2.0},
            {"date": "2026-03-31", "year": 2026, "quarter": 1, "type": "EPS", "value": 1.0},
        ])
        # (1 - (-2)) / abs(-2) = 1.5
        assert _single_q_yoy_from_derived(df, "EPS") == 1.5


class TestRollingTTMFromCumulative:
    def test_q4_just_returns_ytd(self):
        df = _mk_cum([
            {"date": "2025-12-31", "year": 2025, "quarter": 4, "type": "EPS", "value": 20.0},
        ])
        # Q4 累計 = 全年 = TTM
        assert _ttm_rolling_from_cumulative(df, "EPS") == 20.0

    def test_q3_uses_rolling_formula(self):
        # 2025 Q3 YTD = 12, 2024 Q4 = 18, 2024 Q3 YTD = 13
        # TTM = 12 + 18 - 13 = 17
        df = _mk_cum([
            {"date": "2024-09-30", "year": 2024, "quarter": 3, "type": "EPS", "value": 13.0},
            {"date": "2024-12-31", "year": 2024, "quarter": 4, "type": "EPS", "value": 18.0},
            {"date": "2025-09-30", "year": 2025, "quarter": 3, "type": "EPS", "value": 12.0},
        ])
        assert _ttm_rolling_from_cumulative(df, "EPS") == 17.0

    def test_q1_returns_none_without_prior_year_data(self):
        # 只有最新一筆 → 缺去年 Q4 + 去年同期
        df = _mk_cum([
            {"date": "2026-03-31", "year": 2026, "quarter": 1, "type": "EPS", "value": 5.0},
        ])
        assert _ttm_rolling_from_cumulative(df, "EPS") is None


class TestDerivedPathCagrAndPeg:
    """P0 #1 + #2 regression guard：derived path 必須能算 eps_cagr_3y + peg。"""

    def _build_16q_eps(self, base: float = 4.0, growth_per_q: float = 0.1) -> list[dict]:
        rows = []
        year, q = 2022, 1
        v = base
        for _ in range(16):
            md = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
            rows.append({
                "date": f"{year}-{md}", "year": year, "quarter": q,
                "type": "EPS", "value": round(v, 4),
            })
            v += growth_per_q
            q += 1
            if q > 4:
                q = 1
                year += 1
        return rows

    def test_eps_cagr_3y_computed(self):
        rows = self._build_16q_eps(base=4.0, growth_per_q=0.5)
        # tail(4)（2025）總和，iloc[-16:-12]（2022）總和；穩定成長 → CAGR > 0
        snap = {}
        _fill_from_quarterly_derived(snap, _mk_derived(rows), pd.DataFrame())
        assert snap.get("eps_cagr_3y") is not None
        assert snap["eps_cagr_3y"] > 0

    def test_peg_computed_when_per_and_cagr_present(self):
        rows = self._build_16q_eps(base=4.0, growth_per_q=0.5)
        snap = {"per": 20.0}  # 模擬 per_pbr stanza 已先設過
        _fill_from_quarterly_derived(snap, _mk_derived(rows), pd.DataFrame())
        assert snap.get("peg") is not None
        # PEG = per / (cagr * 100)；cagr > 0 → peg 應為正
        assert snap["peg"] > 0

    def test_peg_none_when_no_per(self):
        rows = self._build_16q_eps(base=4.0, growth_per_q=0.5)
        snap = {}  # 無 per
        _fill_from_quarterly_derived(snap, _mk_derived(rows), pd.DataFrame())
        assert snap.get("peg") is None
        # 但 cagr 仍應算出
        assert snap.get("eps_cagr_3y") is not None

    def test_peg_none_when_negative_cagr(self):
        # 衰退序列 → CAGR < 0 → 不算 PEG（負成長股 PEG 沒意義）
        rows = self._build_16q_eps(base=10.0, growth_per_q=-0.3)
        snap = {"per": 20.0}
        _fill_from_quarterly_derived(snap, _mk_derived(rows), pd.DataFrame())
        # CAGR < 0 → 不算 peg
        assert snap.get("peg") is None

    def test_yoy_uses_single_q_semantic(self):
        """P0 #3：derived path YoY 必須是 Qn vs Qn-4 單季比較，不是 YTD-Qn。"""
        rows = self._build_16q_eps(base=4.0, growth_per_q=0.5)
        snap = {}
        _fill_from_quarterly_derived(snap, _mk_derived(rows), pd.DataFrame())
        eps_yoy = snap.get("eps_yoy")
        assert eps_yoy is not None
        # 16 季從 2022Q1 開始：最新 = 2025Q4 = 4.0 + 0.5*15 = 11.5
        # 去年同季 = 2024Q4 = 4.0 + 0.5*11 = 9.5
        # 單季 YoY = (11.5 - 9.5) / 9.5 ≈ 0.2105
        expected = (11.5 - 9.5) / 9.5
        assert abs(eps_yoy - expected) < 1e-6


class TestCumulativeFallbackEnhanced:
    """P1 #4 + #5：_fill_from_cumulative 應提供 rolling TTM + yoy（不再只 Q4 才填）。"""

    def test_q3_eps_ttm_filled_with_rolling_formula(self):
        # 2025 Q3 YTD=12, 2024 Q4=18, 2024 Q3 YTD=13 → TTM = 17
        cum = _mk_cum([
            {"date": "2024-09-30", "year": 2024, "quarter": 3, "type": "EPS", "value": 13.0},
            {"date": "2024-12-31", "year": 2024, "quarter": 4, "type": "EPS", "value": 18.0},
            {"date": "2025-09-30", "year": 2025, "quarter": 3, "type": "EPS", "value": 12.0},
        ])
        snap = {}
        _fill_from_cumulative(snap, cum)
        assert snap.get("eps_ttm") == 17.0  # 不再是 None

    def test_q3_yoy_filled(self):
        # 2025 Q3 YTD=12, 2024 Q3 YTD=10 → YoY = 0.2
        cum = _mk_cum([
            {"date": "2024-09-30", "year": 2024, "quarter": 3, "type": "EPS", "value": 10.0},
            {"date": "2025-09-30", "year": 2025, "quarter": 3, "type": "EPS", "value": 12.0},
        ])
        snap = {}
        _fill_from_cumulative(snap, cum)
        assert snap.get("eps_yoy") == 0.2


class TestBalanceSheetRatios:
    """_fill_balance_sheet_ratios 從 financials_cumulative 算 debt_ratio / current_ratio。"""

    def _mk_bs(self, rows: list[tuple[str, float]]) -> pd.DataFrame:
        """rows = [(type, value), ...]，全部歸到同一季 2025 Q4 簡化 fixture。"""
        return pd.DataFrame([
            {"date": "2025-12-31", "year": 2025, "quarter": 4, "type": t, "value": v}
            for t, v in rows
        ])

    def test_basic_debt_and_current_ratio(self):
        snap: dict = {}
        bs = self._mk_bs([
            ("TotalLiabilities", 300.0),
            ("TotalAssets", 1000.0),
            ("CurrentAssets", 500.0),
            ("CurrentLiabilities", 200.0),
        ])
        _fill_balance_sheet_ratios(snap, bs)
        assert snap["debt_ratio"] == pytest.approx(0.30)
        assert snap["current_ratio"] == pytest.approx(2.5)

    def test_skips_when_assets_zero(self):
        """TotalAssets = 0 → 不該 ZeroDivisionError，靜默 skip。"""
        snap: dict = {}
        bs = self._mk_bs([("TotalLiabilities", 100.0), ("TotalAssets", 0.0)])
        _fill_balance_sheet_ratios(snap, bs)
        assert "debt_ratio" not in snap

    def test_partial_data_only_one_ratio(self):
        """只有 BS 的一部分時，能算的指標仍寫進 snap，不能算的省略。"""
        snap: dict = {}
        bs = self._mk_bs([("TotalLiabilities", 50.0), ("TotalAssets", 200.0)])
        _fill_balance_sheet_ratios(snap, bs)
        assert snap.get("debt_ratio") == pytest.approx(0.25)
        assert "current_ratio" not in snap

    def test_sanity_clip_extreme_debt_ratio(self):
        """debt_ratio > 1.5 視為資料錯亂，不寫入（避免拖偏 peer median）。"""
        snap: dict = {}
        bs = self._mk_bs([("TotalLiabilities", 5000.0), ("TotalAssets", 100.0)])
        _fill_balance_sheet_ratios(snap, bs)
        assert "debt_ratio" not in snap

    def test_sanity_clip_extreme_current_ratio(self):
        """current_ratio 超過 100x（流動負債接近 0）視為異常。"""
        snap: dict = {}
        bs = self._mk_bs([("CurrentAssets", 1000.0), ("CurrentLiabilities", 0.5)])
        _fill_balance_sheet_ratios(snap, bs)
        assert "current_ratio" not in snap

    def test_empty_cum_df_noop(self):
        snap: dict = {"existing": "untouched"}
        _fill_balance_sheet_ratios(snap, pd.DataFrame())
        assert snap == {"existing": "untouched"}

    def test_none_cum_df_noop(self):
        snap: dict = {}
        _fill_balance_sheet_ratios(snap, None)
        assert snap == {}


class TestEndToEndDerivedSnapshot:
    """整支 fundamental_snapshot 走 derived path 的 e2e。"""

    def test_no_finmind_full_snap(self):
        # 16 季 EPS、Revenue、IncomeAfterTaxes，全部 derived，外加 cum_df 提供 equity
        derived_rows = []
        year, q = 2022, 1
        eps = 4.0
        rev = 1000.0
        ni = 100.0
        for _ in range(16):
            md = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
            for t, v in [("EPS", eps), ("Revenue", rev), ("IncomeAfterTaxes", ni),
                         ("GrossProfit", rev * 0.4), ("OperatingIncome", rev * 0.2)]:
                derived_rows.append({
                    "date": f"{year}-{md}", "year": year, "quarter": q,
                    "type": t, "value": v,
                })
            eps += 0.3
            rev *= 1.02
            ni *= 1.03
            q += 1
            if q > 4:
                q = 1
                year += 1
        cum_rows = [
            {"date": "2025-12-31", "year": 2025, "quarter": 4,
             "type": "EquityAttributableToOwnersOfParent", "value": 5000.0},
        ]

        snap = fundamental_snapshot(
            financials=pd.DataFrame(),
            per_pbr=_mk_per_pbr(per=15.0),
            financials_cumulative=_mk_cum(cum_rows),
            financials_derived=_mk_derived(derived_rows),
        )
        assert snap.get("data_source") == "derived"
        assert snap.get("eps_cagr_3y") is not None
        assert snap.get("peg") is not None  # per=15 + cagr > 0
        assert snap.get("eps_yoy") is not None
        assert snap.get("eps_ttm") is not None
        assert snap.get("roe_ttm") is not None
        assert snap.get("gross_margin") == pytest.approx(0.4)
        assert snap.get("operating_margin") == pytest.approx(0.2)
