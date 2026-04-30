"""基本面指標：從 financials 表計算季度 EPS/營收成長、毛利率、ROE 等。

資料源優先序：
1. `financials`（FinMind 單季值）：最完整，可算 YoY / QoQ / TTM，僅限 watchlist
2. `financials_cumulative`（TWSE/TPEX OpenAPI 累計值）：全市場覆蓋，但只有最新一季
   - Q4 累計 ≈ 全年 TTM，可直接當 TTM 指標
   - Q1~Q3 累計只能算 margin（累計比累計）、無法算 YoY（無去年同期）

FinMind 的 TaiwanStockFinancialStatements 回傳的是單季標準化數字，
不需要做 YTD→單季差分。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def pivot_financials(financials: pd.DataFrame) -> pd.DataFrame:
    """long → wide。index=date, columns=type, values=value。"""
    if financials.empty:
        return pd.DataFrame()
    wide = financials.pivot_table(
        index="date", columns="type", values="value", aggfunc="first"
    ).sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide


def _to_quarterly(wide: pd.DataFrame) -> pd.DataFrame:
    """就是 pivot 結果。保留為函式名稱以保持下方呼叫一致。"""
    if wide.empty:
        return wide
    q = wide.copy()
    q["year"] = q.index.year
    q["quarter"] = q.index.quarter
    return q


def _yoy_from_cumulative(cum_df: pd.DataFrame, type_name: str) -> float | None:
    """從累計資料算 YoY：今年 Qn 累計 vs 去年 Qn 累計（同季比較）。

    要求 cum_df 至少有「最新一季 + 去年同季」這兩筆。
    EPS / Revenue 累計比較 = 同期累計成長，比單季差分更穩定（避開單季波動）。

    ⚠️ 與「單季 YoY」（_single_q_yoy_from_derived）語意不同：
        - 累計 YoY = YTD-Qn vs 去年 YTD-Qn（吃整個半年/三季的平均訊號）
        - 單季 YoY = Qn 單季 vs 去年 Qn 單季（單一時點訊號）
    score_eps_growth / score_revenue_growth 假設拿到單季 YoY（與主路徑一致）；
    derived path 已改走 _single_q_yoy_from_derived 以對齊。本函式只剩 _fill_from_cumulative
    (沒 derived 的最後 fallback) 在用 — 那條路徑沒單季資料，只能用累計 YoY。
    """
    sub = cum_df[cum_df["type"] == type_name]
    if sub.empty:
        return None
    sub = sub.sort_values("date")
    latest = sub.iloc[-1]
    try:
        latest_year = int(latest["year"])
        latest_q = int(latest["quarter"])
        latest_v = float(latest["value"])
    except (TypeError, ValueError):
        return None
    if pd.isna(latest_v):
        return None
    # 找去年同季
    prev = sub[(sub["year"] == latest_year - 1) & (sub["quarter"] == latest_q)]
    if prev.empty:
        return None
    try:
        prev_v = float(prev.iloc[0]["value"])
    except (TypeError, ValueError):
        return None
    if pd.isna(prev_v) or prev_v == 0:
        return None
    return (latest_v - prev_v) / abs(prev_v)


def _single_q_yoy_from_derived(
    derived_df: pd.DataFrame, type_name: str
) -> float | None:
    """從 derived 單季資料算「單季 YoY」：最新一季 vs 去年同季的單季比較。

    與主路徑（fundamental_snapshot 主體）的 YoY 語意對齊。為什麼不用 iloc[-5]：
    若該檔某季缺報（年/季 row 不連續），iloc 偏移會錯位 → 改用 (year-1, quarter)
    顯式比對更安全。
    """
    sub = derived_df[derived_df["type"] == type_name]
    if sub.empty:
        return None
    sub = sub.sort_values("date")
    latest = sub.iloc[-1]
    try:
        latest_year = int(latest["year"])
        latest_q = int(latest["quarter"])
        cur = float(latest["value"])
    except (TypeError, ValueError, KeyError):
        return None
    if pd.isna(cur):
        return None
    prev = sub[(sub["year"] == latest_year - 1) & (sub["quarter"] == latest_q)]
    if prev.empty:
        return None
    try:
        prev_v = float(prev.iloc[0]["value"])
    except (TypeError, ValueError):
        return None
    if pd.isna(prev_v) or prev_v == 0:
        return None
    return (cur - prev_v) / abs(prev_v)


def _eps_cagr_3y(eps_series: pd.Series) -> float | None:
    """3 年 EPS CAGR = (TTM_now / TTM_3y_ago) ** (1/3) - 1。

    需要 16 個有值的單季 EPS（最後 4 個當 now、再前 12 個的最初 4 個當 3 年前 = iloc[-16:-12]）。
    若 TTM_now 或 TTM_3y_ago 為非正數（EPS 衰退到 0/負）則 CAGR 沒幾何意義 → None。

    抽出共用 helper 避免 main path 與 derived path 兩處 16-季公式 drift。
    """
    s = eps_series.dropna() if hasattr(eps_series, "dropna") else eps_series
    if len(s) < 16:
        return None
    try:
        ttm_now = float(s.tail(4).sum())
        ttm_3y_ago = float(s.iloc[-16:-12].sum())
    except (TypeError, ValueError):
        return None
    if ttm_now <= 0 or ttm_3y_ago <= 0:
        return None
    return float((ttm_now / ttm_3y_ago) ** (1 / 3) - 1)


def _ttm_rolling_from_cumulative(
    cum_df: pd.DataFrame, type_name: str
) -> float | None:
    """從累計資料算 rolling TTM：(本期 YTD) + (去年全年) − (去年同期 YTD)。

    例：本期 = 2025 Q3 累計 = 9 個月 → TTM = 2025 Q3 YTD + 2024 Q4 YTD − 2024 Q3 YTD
    （2024 Q4 = 2024 全年；扣掉 2024 Q3 YTD = 加回 2024 Q4 單季）。

    若本期已是 Q4 → 直接 = 本期 YTD（即全年），不需要 rolling 公式。
    需要三筆資料：本期 / 去年同季 / 去年 Q4。任一缺則 None。
    """
    sub = cum_df[cum_df["type"] == type_name]
    if sub.empty:
        return None
    sub = sub.sort_values("date")
    latest = sub.iloc[-1]
    try:
        cur_year = int(latest["year"])
        cur_q = int(latest["quarter"])
        cur_ytd = float(latest["value"])
    except (TypeError, ValueError, KeyError):
        return None
    if pd.isna(cur_ytd):
        return None
    if cur_q == 4:
        return cur_ytd  # Q4 累計 = 全年 = TTM
    prev_year_q4 = sub[(sub["year"] == cur_year - 1) & (sub["quarter"] == 4)]
    prev_year_same = sub[(sub["year"] == cur_year - 1) & (sub["quarter"] == cur_q)]
    if prev_year_q4.empty or prev_year_same.empty:
        return None
    try:
        py_q4 = float(prev_year_q4.iloc[0]["value"])
        py_same = float(prev_year_same.iloc[0]["value"])
    except (TypeError, ValueError):
        return None
    if pd.isna(py_q4) or pd.isna(py_same):
        return None
    return cur_ytd + py_q4 - py_same


def _fill_from_quarterly_derived(
    snap: dict,
    derived_df: pd.DataFrame,
    cum_df: pd.DataFrame,
) -> None:
    """從 derived（差分後單季值）+ cumulative（equity 用）填齊：TTM、ROE、YoY、CAGR、PEG、margin。

    優先於 _fill_from_cumulative；只要 derived 有 ≥4 季就走這條。
    - TTM = 最近 4 個單季的加總（Revenue / IncomeAfterTaxes / EPS）
    - ROE = TTM net income / 最新累計 equity（cum_df）
    - YoY = 單季 Qn vs 去年同季 Qn（與主路徑語意對齊；用 derived 直接比，不走累計）
    - CAGR_3y = (TTM_now / TTM_3y_ago) ^ (1/3) − 1，需 ≥16 季
    - PEG = PER / (CAGR×100)，需 PER（per_pbr stanza 已先設）+ 正 CAGR
    - margin 從最新單季算（避免累計平均稀釋）
    """
    if derived_df.empty:
        return

    derived_df = derived_df.sort_values(["type", "date"])

    def _get_latest(t: str) -> float | None:
        sub = derived_df[derived_df["type"] == t]
        if sub.empty:
            return None
        try:
            v = float(sub.iloc[-1]["value"])
            return None if pd.isna(v) else v
        except (TypeError, ValueError):
            return None

    def _ttm(t: str) -> float | None:
        sub = derived_df[derived_df["type"] == t]
        if len(sub) < 4:
            return None
        try:
            return float(sub.tail(4)["value"].sum())
        except (TypeError, ValueError):
            return None

    snap["data_source"] = "derived"

    # 最新一季時間
    if "date" in derived_df.columns:
        latest_dt = derived_df["date"].max()
        snap.setdefault("latest_report_date", str(latest_dt)[:10])

    # 單季 margin（最新一季）
    revenue_q = _get_latest("Revenue")
    if revenue_q and revenue_q > 0:
        gross_q = _get_latest("GrossProfit")
        op_q = _get_latest("OperatingIncome")
        ni_q = _get_latest("IncomeAfterTaxes")
        if gross_q is not None:
            snap["gross_margin"] = gross_q / revenue_q
        if op_q is not None:
            snap["operating_margin"] = op_q / revenue_q
        if ni_q is not None:
            snap["net_margin"] = ni_q / revenue_q

    # 單季 EPS
    eps_q = _get_latest("EPS")
    if eps_q is not None:
        snap["eps_q"] = eps_q

    # TTM
    ttm_ni = _ttm("IncomeAfterTaxes")
    ttm_eps = _ttm("EPS")
    if ttm_eps is not None:
        snap["eps_ttm"] = ttm_eps

    # 3 年 EPS CAGR — 共用 helper 與主路徑一致
    eps_all = derived_df[derived_df["type"] == "EPS"].dropna(subset=["value"])
    cagr = _eps_cagr_3y(eps_all["value"]) if not eps_all.empty else None
    if cagr is not None:
        snap["eps_cagr_3y"] = cagr

    # PEG：PER 來自 per_pbr stanza（已在 fundamental_snapshot 開頭執行），CAGR 來自上一段
    per_val = snap.get("per")
    cagr_val = snap.get("eps_cagr_3y")
    if per_val is not None and per_val > 0 and cagr_val is not None and cagr_val > 0:
        snap["peg"] = round(per_val / (cagr_val * 100), 3)

    # ROE = TTM 淨利 / 當前 equity（從 cumulative 取最新一季的 EquityAttributableToOwnersOfParent）
    if ttm_ni is not None and not cum_df.empty:
        eq_sub = cum_df[cum_df["type"] == "EquityAttributableToOwnersOfParent"]
        if not eq_sub.empty:
            try:
                eq = float(eq_sub.sort_values("date").iloc[-1]["value"])
                if eq > 0:
                    roe = ttm_ni / eq
                    if 0 < roe < 0.6:
                        snap["roe_ttm"] = roe
            except (TypeError, ValueError):
                pass

    # YoY：用 derived 單季資料做 Qn vs 去年同季 Qn（與主路徑語意一致）。
    # 之前用 _yoy_from_cumulative 是「YTD-Qn vs 去年 YTD-Qn」 — 兩條路徑同名指標
    # 但語意不同 → 同個 score 拿到不同口徑數字會拖累 IC，改用單季比對對齊。
    eps_yoy = _single_q_yoy_from_derived(derived_df, "EPS")
    rev_yoy = _single_q_yoy_from_derived(derived_df, "Revenue")
    if eps_yoy is not None:
        snap["eps_yoy"] = eps_yoy
    if rev_yoy is not None:
        snap["revenue_yoy"] = rev_yoy

    # QoQ：用最近 2 個單季 EPS / Revenue 比較
    eps_sub = derived_df[derived_df["type"] == "EPS"]
    if len(eps_sub) >= 2:
        try:
            cur = float(eps_sub.iloc[-1]["value"])
            prev = float(eps_sub.iloc[-2]["value"])
            if prev != 0 and not pd.isna(prev):
                snap["eps_qoq"] = (cur - prev) / abs(prev)
        except (TypeError, ValueError):
            pass
    rev_sub = derived_df[derived_df["type"] == "Revenue"]
    if len(rev_sub) >= 2:
        try:
            cur = float(rev_sub.iloc[-1]["value"])
            prev = float(rev_sub.iloc[-2]["value"])
            if prev > 0:
                snap["revenue_qoq"] = (cur - prev) / prev
        except (TypeError, ValueError):
            pass


def _fill_from_cumulative(snap: dict, cum_df: pd.DataFrame) -> None:
    """財報 fallback：從 TWSE/TPEX 累計值填基本指標（最後 fallback，沒 derived 才用）。

    - 比率（gross/operating/net margin）：累計比累計仍有意義，全季都填
    - TTM（eps_ttm / roe_ttm）：用 rolling 公式 (本期 YTD + 去年 Q4 − 去年同期 YTD)
      讓 Q1~Q3 也能算出 TTM，不再只 Q4 才填
    - YoY（eps_yoy / revenue_yoy）：用 _yoy_from_cumulative 算 YTD-Qn vs 去年 YTD-Qn
      （這條路徑沒有單季資料 → 只能用累計 YoY，不像 derived 路徑可以對齊主路徑語意）
    - 標記 `data_source = 'cumulative'` + `cumulative_quarter`，讓下游可辨識
    """
    if cum_df.empty:
        return
    # 最新一季
    latest_date = cum_df["date"].max()
    latest = cum_df[cum_df["date"] == latest_date]
    if latest.empty:
        return
    wide = latest.set_index("type")["value"].to_dict()

    def _get(k: str) -> float | None:
        v = wide.get(k)
        try:
            return float(v) if v is not None and not pd.isna(v) else None
        except (TypeError, ValueError):
            return None

    q_row = latest.iloc[0]
    try:
        quarter = int(q_row.get("quarter"))
    except (TypeError, ValueError):
        quarter = None
    snap.setdefault("latest_report_date", str(latest_date)[:10])
    snap["data_source"] = "cumulative"
    snap["cumulative_quarter"] = quarter

    revenue = _get("Revenue")
    gross = _get("GrossProfit")
    op_income = _get("OperatingIncome")
    net_income = _get("IncomeAfterTaxes")
    eps = _get("EPS")
    equity = _get("EquityAttributableToOwnersOfParent")

    # 比率：累計比累計仍有意義（即便不是 Q4）
    if revenue and revenue > 0:
        if gross is not None:
            snap["gross_margin"] = gross / revenue
        if op_income is not None:
            snap["operating_margin"] = op_income / revenue
        if net_income is not None:
            snap["net_margin"] = net_income / revenue

    if eps is not None:
        snap["eps_q"] = eps

    # TTM EPS：rolling 公式（Q4 退化成 = 本期 YTD = 全年）
    ttm_eps = _ttm_rolling_from_cumulative(cum_df, "EPS")
    if ttm_eps is not None:
        snap["eps_ttm"] = ttm_eps

    # ROE：用 rolling TTM 淨利 / 最新 equity；Q4 即等於原本「全年淨利 / equity」
    ttm_ni = _ttm_rolling_from_cumulative(cum_df, "IncomeAfterTaxes")
    if ttm_ni is not None and equity and equity > 0:
        roe = ttm_ni / equity
        if 0 < roe < 0.6:
            snap["roe_ttm"] = roe

    # YoY：YTD-Qn vs 去年 YTD-Qn（這條路徑無單季資料，只能用累計 YoY；
    # 與 derived 路徑改用單季 YoY 不同 — 但這條 fallback 覆蓋率小，影響有限）
    eps_yoy = _yoy_from_cumulative(cum_df, "EPS")
    rev_yoy = _yoy_from_cumulative(cum_df, "Revenue")
    if eps_yoy is not None:
        snap["eps_yoy"] = eps_yoy
    if rev_yoy is not None:
        snap["revenue_yoy"] = rev_yoy


def fundamental_snapshot(
    financials: pd.DataFrame,
    per_pbr: pd.DataFrame,
    financials_cumulative: pd.DataFrame | None = None,
    financials_derived: pd.DataFrame | None = None,
) -> dict:
    """回傳基本面關鍵指標快照。

    優先序：
    1. `financials`（FinMind 單季）：完整 → 走原邏輯
    2. `financials_derived`（MOPS 累計差分後單季） + `financials_cumulative`：可算 TTM、ROE、YoY
    3. `financials_cumulative` 單獨：只能算 margin + Q4 TTM
    """
    snap: dict = {}

    # 每日型：PER/PBR/殖利率
    if not per_pbr.empty:
        last = per_pbr.sort_values("date").iloc[-1]
        snap["per"] = float(last.get("per") or 0) if pd.notna(last.get("per")) else None
        snap["pbr"] = float(last.get("pbr") or 0) if pd.notna(last.get("pbr")) else None
        snap["dividend_yield"] = float(last.get("dividend_yield") or 0) if pd.notna(last.get("dividend_yield")) else None
        # PER 歷史分位數（越低越便宜）
        per_series = per_pbr["per"].dropna()
        if not per_series.empty and snap.get("per"):
            snap["per_percentile"] = float((per_series < snap["per"]).mean())

    # 季度型
    wide = pivot_financials(financials)
    if wide.empty:
        # 沒 FinMind → 嘗試 derived，再 fallback 到 cumulative
        if financials_derived is not None and not financials_derived.empty and financials_cumulative is not None:
            _fill_from_quarterly_derived(snap, financials_derived, financials_cumulative)
            return snap
        if financials_cumulative is not None:
            _fill_from_cumulative(snap, financials_cumulative)
        return snap

    quarterly = _to_quarterly(wide)
    if quarterly.empty or len(quarterly) < 2:
        if financials_derived is not None and not financials_derived.empty and financials_cumulative is not None:
            _fill_from_quarterly_derived(snap, financials_derived, financials_cumulative)
            return snap
        if financials_cumulative is not None:
            _fill_from_cumulative(snap, financials_cumulative)
        return snap

    latest = quarterly.iloc[-1]
    snap["latest_report_date"] = str(quarterly.index[-1].date())

    # 單季數字
    revenue = latest.get("Revenue")
    gross = latest.get("GrossProfit")
    op_income = latest.get("OperatingIncome")
    eps = latest.get("EPS")
    net_income = latest.get("IncomeAfterTaxes")

    if revenue and revenue > 0:
        if gross is not None:
            snap["gross_margin"] = float(gross / revenue)
        if op_income is not None:
            snap["operating_margin"] = float(op_income / revenue)
        if net_income is not None:
            snap["net_margin"] = float(net_income / revenue)

    if eps is not None:
        snap["eps_q"] = float(eps)

    # YoY 成長：對比去年同一季（shift 4 季）
    if len(quarterly) >= 5:
        prev_year = quarterly.iloc[-5]
        if "EPS" in quarterly.columns and prev_year.get("EPS"):
            snap["eps_yoy"] = float((eps - prev_year["EPS"]) / abs(prev_year["EPS"])) if prev_year["EPS"] else None
        if "Revenue" in quarterly.columns and prev_year.get("Revenue"):
            snap["revenue_yoy"] = float((revenue - prev_year["Revenue"]) / abs(prev_year["Revenue"])) if prev_year["Revenue"] else None

    # QoQ 成長：對比上一季
    if len(quarterly) >= 2:
        prev_q = quarterly.iloc[-2]
        if eps is not None and prev_q.get("EPS"):
            snap["eps_qoq"] = float((eps - prev_q["EPS"]) / abs(prev_q["EPS"])) if prev_q["EPS"] else None
        if revenue is not None and prev_q.get("Revenue") and prev_q["Revenue"] > 0:
            snap["revenue_qoq"] = float((revenue - prev_q["Revenue"]) / prev_q["Revenue"])

    # 近 4 季 EPS 合計（TTM EPS）
    if "EPS" in quarterly.columns and len(quarterly) >= 4:
        ttm_eps = quarterly["EPS"].tail(4).sum()
        snap["eps_ttm"] = float(ttm_eps)

    # 3 年 EPS CAGR — 共用 helper 與 derived path 一致
    if "EPS" in quarterly.columns:
        cagr = _eps_cagr_3y(quarterly["EPS"])
        if cagr is not None:
            snap["eps_cagr_3y"] = cagr

    # PEG = PER / (EPS 成長率%)：< 1 成長合算、> 2 過貴
    # 只在有 PER 且 CAGR > 0 時計算（負成長股算 PEG 沒意義）
    per_val = snap.get("per")
    cagr_val = snap.get("eps_cagr_3y")
    if per_val is not None and per_val > 0 and cagr_val is not None and cagr_val > 0:
        snap["peg"] = round(per_val / (cagr_val * 100), 3)

    # ROE 粗估：用最近 4 季稅後淨利 / 最近股東權益
    # 優先序：(1) financials_cumulative 的 OpenAPI 期末權益（最準確，TWSE 官方資料）
    #         (2) FinMind 的 EquityAttributableToOwnersOfParent（語意可能不準，作 fallback）
    # 0~60% sanity check：超過 60% 視為資料異常，跳過
    if "IncomeAfterTaxes" in quarterly.columns and len(quarterly) >= 4:
        ttm_ni = float(quarterly["IncomeAfterTaxes"].tail(4).sum())
        latest_equity: float | None = None
        # 來源 1: financials_cumulative
        if financials_cumulative is not None and not financials_cumulative.empty:
            eq_sub = financials_cumulative[
                financials_cumulative["type"] == "EquityAttributableToOwnersOfParent"
            ]
            if not eq_sub.empty:
                try:
                    v = float(eq_sub.sort_values("date").iloc[-1]["value"])
                    if v > 0:
                        latest_equity = v
                except (TypeError, ValueError):
                    pass
        # 來源 2: FinMind wide
        if latest_equity is None and "EquityAttributableToOwnersOfParent" in wide.columns:
            equity_series = wide["EquityAttributableToOwnersOfParent"].dropna()
            try:
                v = float(equity_series.iloc[-1]) if not equity_series.empty else None
                if v and v > 0:
                    latest_equity = v
            except (TypeError, ValueError):
                pass
        if latest_equity:
            roe = ttm_ni / latest_equity
            if 0 < roe < 0.6:
                snap["roe_ttm"] = roe

    return snap
