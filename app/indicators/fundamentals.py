"""基本面指標：從 financials 表計算季度 EPS/營收成長、毛利率、ROE 等。

資料源優先序：
1. `financials`（FinMind 單季值）：最完整，可算 YoY / QoQ / TTM，僅限 watchlist
2. `financials_cumulative`（TWSE/TPEX OpenAPI 累計值）：全市場覆蓋，但只有最新一季
   - Q4 累計 ≈ 全年 TTM，可直接當 TTM 指標
   - Q1~Q3 累計只能算 margin（累計比累計）+ rolling-TTM 公式

FinMind 的 TaiwanStockFinancialStatements 回傳的是單季標準化數字，
不需要做 YTD→單季差分。

長表 → 寬表 helper layer (`_wide_by_quarter` / `_value_at` / `_latest_value` / ...) 把
原本散落在多個函式裡的 "df[df['type']==X].sort_values('date').iloc[-1]['value']" pattern
集中起來，避免「忘記 sort_values」這種隱性 bug 風險。
"""
from __future__ import annotations

import pandas as pd


# ======================================================================
# 寬表 / 取值 helpers — 共用給 derived + cumulative 兩條路徑
# ======================================================================

def _wide_by_quarter(long_df: pd.DataFrame) -> pd.DataFrame:
    """把 (date, year, quarter, type, value) 長表 pivot 成 (year, quarter) MultiIndex × type 寬表。

    NaN value 在 pivot 前先 drop。輸入空 / 缺欄位 → 回空 DF。Index 已 sort 升冪。
    用 wide 表後 "拿最新一筆 X" / "拿 (year-1, quarter) 的 X" 都變成 column ops、不需要再
    sort + iloc 查找，邏輯穩定。
    """
    if long_df.empty:
        return pd.DataFrame()
    needed = {"year", "quarter", "type", "value"}
    if not needed.issubset(long_df.columns):
        return pd.DataFrame()
    df = long_df.dropna(subset=["value"])
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(
        index=["year", "quarter"], columns="type", values="value", aggfunc="first"
    )
    return wide.sort_index()


def _value_at(wide: pd.DataFrame, year: int, quarter: int, col: str) -> float | None:
    """取 wide 表 (year, quarter) row × col 的值。NaN / 缺 row / 缺 col → None。"""
    if wide.empty or col not in wide.columns:
        return None
    try:
        v = wide.at[(year, quarter), col]
    except (KeyError, TypeError):
        return None
    if pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest_yq_with(wide: pd.DataFrame, col: str) -> tuple[int, int] | None:
    """col 最新「有非 NaN 值」的 (year, quarter)。空 → None。"""
    if wide.empty or col not in wide.columns:
        return None
    s = wide[col].dropna()
    if s.empty:
        return None
    return s.index[-1]


def _latest_value(wide: pd.DataFrame, col: str) -> float | None:
    """col 最新非 NaN 值。內部包 _latest_yq_with + _value_at。"""
    yq = _latest_yq_with(wide, col)
    if yq is None:
        return None
    return _value_at(wide, *yq, col)


def _ttm_sum(wide: pd.DataFrame, col: str) -> float | None:
    """col 最近 4 季加總（單季資料的 TTM）。樣本不足 → None。"""
    if wide.empty or col not in wide.columns:
        return None
    s = wide[col].dropna()
    if len(s) < 4:
        return None
    try:
        return float(s.tail(4).sum())
    except (TypeError, ValueError):
        return None


def _yoy_same_quarter(wide: pd.DataFrame, col: str) -> float | None:
    """最新一季 vs 去年同季的百分比變化。

    語意取決於 wide 來源：
    - cumulative pivot → 累計 YoY（YTD-Qn vs 去年 YTD-Qn）
    - derived pivot    → 單季 YoY（Qn 單季 vs 去年 Qn 單季）
    公式對稱；caller 用哪份 wide 決定要哪種訊號。
    """
    yq = _latest_yq_with(wide, col)
    if yq is None:
        return None
    cur = _value_at(wide, *yq, col)
    prev = _value_at(wide, yq[0] - 1, yq[1], col)
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _qoq_change(
    wide: pd.DataFrame, col: str, *, positive_prev_only: bool = False
) -> float | None:
    """最新一季 vs 上一季（最近兩個非 NaN 值）。

    positive_prev_only=True：要求 prev > 0 才算（用於 Revenue，避免基期 0/負時公式失真）。
    其他情境（EPS）只要 prev != 0 即可（負基期用 abs 規一化）。
    """
    if wide.empty or col not in wide.columns:
        return None
    s = wide[col].dropna()
    if len(s) < 2:
        return None
    try:
        cur = float(s.iloc[-1])
        prev = float(s.iloc[-2])
    except (TypeError, ValueError):
        return None
    if pd.isna(prev) or prev == 0:
        return None
    if positive_prev_only and prev <= 0:
        return None
    return (cur - prev) / abs(prev)


# ======================================================================
# Pivot 給主路徑（FinMind financials 表）— 索引是 date，不是 (year, quarter)
# ======================================================================

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


# ======================================================================
# 公式 helpers — CAGR / rolling TTM / YoY thin wrappers
# ======================================================================

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


def compute_peg(per: float | None, eps_cagr_3y: float | None) -> float | None:
    """PEG 單一來源公式：PER / (EPS CAGR * 100)，僅在兩者皆為正值時計算。"""
    if per is None or eps_cagr_3y is None:
        return None
    try:
        per_v = float(per)
        cagr_v = float(eps_cagr_3y)
    except (TypeError, ValueError):
        return None
    if per_v <= 0 or cagr_v <= 0:
        return None
    return round(per_v / (cagr_v * 100), 3)


def _yoy_from_cumulative(cum_df: pd.DataFrame, type_name: str) -> float | None:
    """累計 YoY：本期 YTD-Qn vs 去年 YTD-Qn。本檔只剩 _fill_from_cumulative 這條 fallback 在用
    （沒 derived 時才走）。
    """
    return _yoy_same_quarter(_wide_by_quarter(cum_df), type_name)


def _single_q_yoy_from_derived(
    derived_df: pd.DataFrame, type_name: str
) -> float | None:
    """單季 YoY：最新 Qn 單季 vs 去年同季 Qn 單季（與主路徑語意對齊）。"""
    return _yoy_same_quarter(_wide_by_quarter(derived_df), type_name)


def _fill_balance_sheet_ratios(snap: dict, cum_df: pd.DataFrame | None) -> None:
    """從 financials_cumulative 取最新一季的資產負債表科目，算出兩個槓桿/流動性比率。

    - `debt_ratio`     = TotalLiabilities / TotalAssets        （越低越穩；金融業天生偏高）
    - `current_ratio`  = CurrentAssets   / CurrentLiabilities  （>1 才有短期償債空間）

    為什麼放在這裡而不是評分引擎：這兩個比率是「體質檢查」訊號，不直接進短/中/長分數
    （已有 ROE / margin 涵蓋獲利能力面），但需要在個股詳情頁與同業比較區塊曝光，作為
    避雷的第二道防線。

    自由現金流（FCF = OperatingCashFlow − CapEx）暫不納入：MOPS OpenAPI 的
    t164sb04（現金流量表）目前沒被 fetcher 拉進來，加之需處理累計差分，工程量單獨成
    一個 PR；待用戶反饋確認需要再補。
    """
    if cum_df is None or cum_df.empty:
        return
    wide = _wide_by_quarter(cum_df)
    if wide.empty:
        return

    total_liab = _latest_value(wide, "TotalLiabilities")
    total_assets = _latest_value(wide, "TotalAssets")
    if total_liab is not None and total_assets and total_assets > 0:
        # 0~1 sanity 區間（>100% 表負債超過總資產，理論上不可能 — 通常是抓資料錯置）
        ratio = total_liab / total_assets
        if 0 < ratio < 1.5:
            snap["debt_ratio"] = ratio

    cur_assets = _latest_value(wide, "CurrentAssets")
    cur_liab = _latest_value(wide, "CurrentLiabilities")
    if cur_assets is not None and cur_liab and cur_liab > 0:
        # 上限給 100x（防止 cur_liab 接近 0 時噴極端值）；下限 0.05 以下視為異常
        ratio = cur_assets / cur_liab
        if 0.05 < ratio < 100:
            snap["current_ratio"] = ratio


def _ttm_rolling_from_cumulative(
    cum_df: pd.DataFrame, type_name: str
) -> float | None:
    """從累計資料算 rolling TTM：(本期 YTD) + (去年全年) − (去年同期 YTD)。

    例：本期 = 2025 Q3 累計 = 9 個月 → TTM = 2025 Q3 YTD + 2024 Q4 YTD − 2024 Q3 YTD
    （2024 Q4 = 2024 全年；扣掉 2024 Q3 YTD = 加回 2024 Q4 單季）。

    若本期已是 Q4 → 直接 = 本期 YTD（即全年），不需要 rolling 公式。
    需要三筆資料：本期 / 去年同季 / 去年 Q4。任一缺則 None。
    """
    wide = _wide_by_quarter(cum_df)
    yq = _latest_yq_with(wide, type_name)
    if yq is None:
        return None
    cur_year, cur_q = yq
    cur_ytd = _value_at(wide, cur_year, cur_q, type_name)
    if cur_ytd is None:
        return None
    if cur_q == 4:
        return cur_ytd  # Q4 累計 = 全年 = TTM
    py_q4 = _value_at(wide, cur_year - 1, 4, type_name)
    py_same = _value_at(wide, cur_year - 1, cur_q, type_name)
    if py_q4 is None or py_same is None:
        return None
    return cur_ytd + py_q4 - py_same


# ======================================================================
# Snap fillers — derived path / cumulative fallback
# ======================================================================

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
    - margin 從最新單季算（與 Revenue 對齊同一 (year, quarter)，避免抓到不同期）
    """
    if derived_df.empty:
        return

    wide = _wide_by_quarter(derived_df)
    if wide.empty:
        return

    snap["data_source"] = "derived"

    # 最新一季時間（保留 long-format date 給 latest_report_date 顯示）
    if "date" in derived_df.columns:
        latest_dt = derived_df["date"].max()
        snap.setdefault("latest_report_date", str(latest_dt)[:10])

    # 單季 margin：用 Revenue 所在的 (year, quarter) 對齊取 GP / OP / NI，避免不同期錯置
    rev_yq = _latest_yq_with(wide, "Revenue")
    if rev_yq is not None:
        revenue_q = _value_at(wide, *rev_yq, "Revenue")
        if revenue_q and revenue_q > 0:
            for col, key in (
                ("GrossProfit", "gross_margin"),
                ("OperatingIncome", "operating_margin"),
                ("IncomeAfterTaxes", "net_margin"),
            ):
                v = _value_at(wide, *rev_yq, col)
                if v is not None:
                    snap[key] = v / revenue_q

    # 單季 EPS
    eps_q = _latest_value(wide, "EPS")
    if eps_q is not None:
        snap["eps_q"] = eps_q

    # TTM
    ttm_eps = _ttm_sum(wide, "EPS")
    if ttm_eps is not None:
        snap["eps_ttm"] = ttm_eps
    ttm_ni = _ttm_sum(wide, "IncomeAfterTaxes")

    # 3 年 EPS CAGR
    eps_series = wide["EPS"].dropna() if "EPS" in wide.columns else pd.Series(dtype=float)
    cagr = _eps_cagr_3y(eps_series)
    if cagr is not None:
        snap["eps_cagr_3y"] = cagr

    # PEG：PER 來自 per_pbr stanza（已在 fundamental_snapshot 開頭執行），CAGR 來自上一段
    peg = compute_peg(snap.get("per"), snap.get("eps_cagr_3y"))
    if peg is not None:
        snap["peg"] = peg

    # ROE = TTM 淨利 / 最新 equity（從 cumulative 取）
    if ttm_ni is not None and not cum_df.empty:
        eq_latest = _latest_value(_wide_by_quarter(cum_df), "EquityAttributableToOwnersOfParent")
        if eq_latest and eq_latest > 0:
            roe = ttm_ni / eq_latest
            if 0 < roe < 0.6:
                snap["roe_ttm"] = roe

    # 槓桿 / 流動性比率
    _fill_balance_sheet_ratios(snap, cum_df)

    # YoY：單季比對（與主路徑同口徑；之前用累計 YoY 是 bug）
    eps_yoy = _yoy_same_quarter(wide, "EPS")
    rev_yoy = _yoy_same_quarter(wide, "Revenue")
    if eps_yoy is not None:
        snap["eps_yoy"] = eps_yoy
    if rev_yoy is not None:
        snap["revenue_yoy"] = rev_yoy

    # QoQ
    eps_qoq = _qoq_change(wide, "EPS")
    rev_qoq = _qoq_change(wide, "Revenue", positive_prev_only=True)
    if eps_qoq is not None:
        snap["eps_qoq"] = eps_qoq
    if rev_qoq is not None:
        snap["revenue_qoq"] = rev_qoq


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

    wide = _wide_by_quarter(cum_df)
    if wide.empty:
        return

    # 找「最新一季有任何資料」的 (year, quarter) — 用 Revenue 為錨（最常存在的欄）
    # 若 Revenue 缺，退一階用整個 wide 的最後 row
    anchor_yq = _latest_yq_with(wide, "Revenue") or wide.index[-1]
    cur_year, quarter = anchor_yq
    snap.setdefault("latest_report_date", f"{cur_year}-{['03-31','06-30','09-30','12-31'][quarter-1]}")
    snap["data_source"] = "cumulative"
    snap["cumulative_quarter"] = quarter

    revenue = _value_at(wide, cur_year, quarter, "Revenue")
    eps = _value_at(wide, cur_year, quarter, "EPS")

    # 比率：累計比累計仍有意義（即便不是 Q4）
    if revenue and revenue > 0:
        for col, key in (
            ("GrossProfit", "gross_margin"),
            ("OperatingIncome", "operating_margin"),
            ("IncomeAfterTaxes", "net_margin"),
        ):
            v = _value_at(wide, cur_year, quarter, col)
            if v is not None:
                snap[key] = v / revenue

    if eps is not None:
        snap["eps_q"] = eps

    # TTM EPS：rolling 公式（Q4 退化成 = 本期 YTD = 全年）
    ttm_eps = _ttm_rolling_from_cumulative(cum_df, "EPS")
    if ttm_eps is not None:
        snap["eps_ttm"] = ttm_eps

    # ROE：用 rolling TTM 淨利 / 最新 equity（同期 anchor）
    ttm_ni = _ttm_rolling_from_cumulative(cum_df, "IncomeAfterTaxes")
    equity = _value_at(wide, cur_year, quarter, "EquityAttributableToOwnersOfParent")
    if ttm_ni is not None and equity and equity > 0:
        roe = ttm_ni / equity
        if 0 < roe < 0.6:
            snap["roe_ttm"] = roe

    # YoY：YTD-Qn vs 去年 YTD-Qn（這條路徑無單季資料，只能用累計 YoY；
    # 與 derived 路徑改用單季 YoY 不同 — 但這條 fallback 覆蓋率小，影響有限）
    eps_yoy = _yoy_same_quarter(wide, "EPS")
    rev_yoy = _yoy_same_quarter(wide, "Revenue")
    if eps_yoy is not None:
        snap["eps_yoy"] = eps_yoy
    if rev_yoy is not None:
        snap["revenue_yoy"] = rev_yoy

    # 槓桿 / 流動性比率（這條 fallback 路徑可能完全沒 BS 資料，helper 會自動 noop）
    _fill_balance_sheet_ratios(snap, cum_df)


# ======================================================================
# 對外入口
# ======================================================================

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
    peg = compute_peg(snap.get("per"), snap.get("eps_cagr_3y"))
    if peg is not None:
        snap["peg"] = peg

    # ROE 粗估：用最近 4 季稅後淨利 / 最近股東權益
    # 優先序：(1) financials_cumulative 的 OpenAPI 期末權益（最準確，TWSE 官方資料）
    #         (2) FinMind 的 EquityAttributableToOwnersOfParent（語意可能不準，作 fallback）
    # 0~60% sanity check：超過 60% 視為資料異常，跳過
    if "IncomeAfterTaxes" in quarterly.columns and len(quarterly) >= 4:
        ttm_ni = float(quarterly["IncomeAfterTaxes"].tail(4).sum())
        latest_equity: float | None = None
        # 來源 1: financials_cumulative
        if financials_cumulative is not None and not financials_cumulative.empty:
            latest_equity = _latest_value(
                _wide_by_quarter(financials_cumulative),
                "EquityAttributableToOwnersOfParent",
            )
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

    # 槓桿 / 流動性比率（與 ROE 同來源 financials_cumulative）
    _fill_balance_sheet_ratios(snap, financials_cumulative)

    return snap
