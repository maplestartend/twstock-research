"""產業輪動與市場寬度指標。

全部建在現有 daily_price + stock_info 表之上，不需要新資料來源。
"""
from __future__ import annotations

import pandas as pd

from app.data.db import Database


# ======================================================================
# 族群輪動：依 industry_category 聚合多天期報酬
# ======================================================================
def industry_rotation(
    db: Database,
    *,
    min_members: int = 3,
) -> dict:
    """快取入口：當日 daily_price.MAX(date) 已經有快取就讀盤，否則跑 `_industry_rotation_uncached`。

    呼叫方（api/routers/market.py）不需要關心快取邏輯。
    """
    # 延遲匯入，避免 import time 上 cache 模組 → market_scope 的循環依賴。
    from app.scoring.market_scope_cache import get_industry_rotation_cached
    return get_industry_rotation_cached(db, min_members=min_members)


def _industry_rotation_uncached(
    db: Database,
    *,
    min_members: int = 3,
) -> dict:
    """各產業多時間窗報酬 + 成交值 + 漲跌家數分布。

    回傳：{ "as_of": "YYYY-MM-DD" | None, "rows": pd.DataFrame }
    rows 欄位：industry, n, ret_1d, ret_1d_weighted, ret_5d, ret_20d, ret_60d,
              heat, total_amount, n_up, n_flat, n_down

    - ret_1d / 5d / 20d / 60d：等權算術平均（給排行表用）
    - ret_1d_weighted：成交值加權當日報酬 = Σ(ret_1d×amount) / Σ(amount)（給熱力圖著色用）
    - total_amount：最新交易日成交金額加總（TWD，給熱力圖磚塊面積用）
    - n_up / n_flat / n_down：當日該產業內 ret_1d > 0 / == 0 / < 0 的成員家數
    - as_of：daily_price 全表最新日期，給前端顯示「資料截至」
    """
    with db.connect() as conn:
        as_of_row = conn.execute("SELECT MAX(date) FROM daily_price").fetchone()
        as_of = as_of_row[0] if as_of_row and as_of_row[0] else None

        # 一次 SQL 把每檔個股的最新日 + 1/5/20/60 日前的收盤 + 最新日成交金額取出
        df = pd.read_sql_query(
            """
            WITH latest AS (
                SELECT stock_id, MAX(date) AS max_date
                FROM daily_price GROUP BY stock_id
            ),
            ranked AS (
                SELECT p.stock_id, p.date, p.close, p.amount,
                       ROW_NUMBER() OVER (PARTITION BY p.stock_id ORDER BY p.date DESC) AS rnk
                FROM daily_price p
                JOIN latest l ON l.stock_id = p.stock_id
            )
            SELECT r.stock_id, r.close, r.amount, r.rnk, i.industry_category
            FROM ranked r
            LEFT JOIN stock_info i ON i.stock_id = r.stock_id
            WHERE r.rnk IN (1, 2, 6, 21, 61)
              AND i.industry_category IS NOT NULL
              AND i.industry_category != ''
              AND LENGTH(r.stock_id) = 4
            """,
            conn,
        )

    if df.empty:
        return {"as_of": as_of, "rows": pd.DataFrame()}

    # pivot：每檔股票一列、四個 rnk 各一欄（值 = close）
    piv = df.pivot_table(index=["stock_id", "industry_category"], columns="rnk",
                          values="close", aggfunc="first").reset_index()
    piv.columns = [str(c) for c in piv.columns]

    for col, label in [("2", "ret_1d"), ("6", "ret_5d"),
                        ("21", "ret_20d"), ("61", "ret_60d")]:
        if col in piv.columns and "1" in piv.columns:
            piv[label] = (piv["1"] - piv[col]) / piv[col]
        else:
            piv[label] = None

    # 取每檔股票最新日（rnk=1）的成交金額，掛回 piv
    latest_amount = df.loc[df["rnk"] == 1, ["stock_id", "amount"]].drop_duplicates("stock_id")
    piv = piv.merge(latest_amount, on="stock_id", how="left")

    # 成交值加權報酬的分子：每檔 ret_1d × amount（任一為 NaN 就不貢獻）
    piv["_ret_amount"] = piv["ret_1d"] * piv["amount"]
    # 加權報酬「對齊的分母」：只計入有 ret_1d 的股票成交值，避免把缺資料的成交值算進去稀釋報酬
    piv["_w_denom"] = piv["amount"].where(piv["ret_1d"].notna(), other=0.0)

    # 依產業 groupby
    agg = piv.groupby("industry_category").agg(
        n=("stock_id", "count"),
        ret_1d=("ret_1d", "mean"),
        ret_5d=("ret_5d", "mean"),
        ret_20d=("ret_20d", "mean"),
        ret_60d=("ret_60d", "mean"),
        total_amount=("amount", "sum"),       # 真實總成交值（給熱力圖磚塊面積 / 大盤占比）
        _ret_amount_sum=("_ret_amount", "sum"),
        _w_denom_sum=("_w_denom", "sum"),     # 加權報酬的對齊分母
        n_up=("ret_1d", lambda s: int((s > 0).sum())),
        n_flat=("ret_1d", lambda s: int((s == 0).sum())),
        n_down=("ret_1d", lambda s: int((s < 0).sum())),
    ).reset_index()
    agg = agg[agg["n"] >= min_members].copy()
    agg = agg.rename(columns={"industry_category": "industry"})

    # 成交值加權當日報酬：用對齊分母，分母為 0 / NaN 時回 None
    def _weighted(row):
        denom = row["_w_denom_sum"]
        if denom is None or not (denom > 0):
            return None
        return float(row["_ret_amount_sum"] / denom)
    agg["ret_1d_weighted"] = agg.apply(_weighted, axis=1)
    agg = agg.drop(columns=["_ret_amount_sum", "_w_denom_sum"])

    # 依 1 日 + 5 日動能合成一個「今日熱度分」排序
    agg["heat"] = (
        agg["ret_1d"].fillna(0) * 0.5 + agg["ret_5d"].fillna(0) * 0.5
    )
    agg = agg.sort_values("heat", ascending=False).reset_index(drop=True)
    return {"as_of": as_of, "rows": agg}


def industry_members(db: Database, industry: str, top_n: int = 30) -> pd.DataFrame:
    """指定產業內的成員股 + 近期報酬。"""
    with db.connect() as conn:
        df = pd.read_sql_query(
            """
            WITH latest AS (
                SELECT stock_id, MAX(date) AS max_date
                FROM daily_price GROUP BY stock_id
            ),
            ranked AS (
                SELECT p.stock_id, p.date, p.close,
                       ROW_NUMBER() OVER (PARTITION BY p.stock_id ORDER BY p.date DESC) AS rnk
                FROM daily_price p
                JOIN latest l ON l.stock_id = p.stock_id
            )
            SELECT r.stock_id, i.stock_name, r.close, r.rnk
            FROM ranked r
            JOIN stock_info i ON i.stock_id = r.stock_id
            WHERE r.rnk IN (1, 2, 6, 21)
              AND i.industry_category = ?
              AND LENGTH(r.stock_id) = 4
            """,
            conn, params=[industry],
        )

    if df.empty:
        return pd.DataFrame()
    piv = df.pivot_table(index=["stock_id", "stock_name"], columns="rnk",
                          values="close", aggfunc="first").reset_index()
    piv.columns = [str(c) for c in piv.columns]
    if "1" not in piv.columns:
        return pd.DataFrame()
    piv = piv.rename(columns={"1": "close"})
    for col, label in [("2", "ret_1d"), ("6", "ret_5d"), ("21", "ret_20d")]:
        if col in piv.columns:
            piv[label] = (piv["close"] - piv[col]) / piv[col]
        else:
            piv[label] = None
    return piv[["stock_id", "stock_name", "close", "ret_1d", "ret_5d", "ret_20d"]] \
        .sort_values("ret_5d", ascending=False).head(top_n).reset_index(drop=True)


# ======================================================================
# 市場寬度（Market Breadth）
# ======================================================================
def market_breadth(db: Database) -> dict:
    """快取入口：daily_price.MAX(date) 沒換就讀盤，否則跑 `_market_breadth_uncached`。"""
    from app.scoring.market_scope_cache import get_market_breadth_cached
    return get_market_breadth_cached(db)


def _market_breadth_uncached(db: Database) -> dict:
    """一組市場寬度指標，全部算自 daily_price（只看 4 碼一般股票）。

    回傳：
      n_total, n_up, n_down, n_unchanged, advance_decline_ratio,
      pct_above_ma20, pct_above_ma60,
      n_new_high_50d, n_new_low_50d, new_high_low_ratio
    """
    with db.connect() as conn:
        df = pd.read_sql_query(
            """
            WITH recent AS (
                SELECT p.stock_id, p.date, p.close,
                       ROW_NUMBER() OVER (PARTITION BY p.stock_id ORDER BY p.date DESC) AS rnk
                FROM daily_price p
                WHERE LENGTH(p.stock_id) = 4
            )
            SELECT stock_id, date, close, rnk FROM recent WHERE rnk <= 65
            """,
            conn,
        )
    if df.empty:
        return {}

    latest_rows = df[df["rnk"] == 1]
    prev_rows = df[df["rnk"] == 2][["stock_id", "close"]].rename(columns={"close": "prev_close"})
    merged = latest_rows.merge(prev_rows, on="stock_id", how="left")
    merged["chg_pct"] = (merged["close"] - merged["prev_close"]) / merged["prev_close"]

    n_total = len(merged)
    n_up = int((merged["chg_pct"] > 0).sum())
    n_down = int((merged["chg_pct"] < 0).sum())
    n_unchanged = int((merged["chg_pct"] == 0).sum())

    # 每檔的 20/60 日均線（從 df 算）
    df_sorted = df.sort_values(["stock_id", "rnk"])
    ma = df_sorted.groupby("stock_id").agg(
        ma20=("close", lambda s: s.head(20).mean()),
        ma60=("close", lambda s: s.head(60).mean()),
        high50=("close", lambda s: s.head(50).max()),
        low50=("close", lambda s: s.head(50).min()),
    ).reset_index()
    merged = merged.merge(ma, on="stock_id", how="left")

    above_ma20 = merged[merged["ma20"].notna() & (merged["close"] > merged["ma20"])]
    above_ma60 = merged[merged["ma60"].notna() & (merged["close"] > merged["ma60"])]
    n_new_high = int((merged["close"] >= merged["high50"]).sum())
    n_new_low = int((merged["close"] <= merged["low50"]).sum())

    ad_ratio = n_up / n_down if n_down > 0 else float("inf")
    high_low_ratio = n_new_high / n_new_low if n_new_low > 0 else float("inf")

    return {
        "as_of": str(latest_rows["date"].iloc[0]),
        "n_total": n_total,
        "n_up": n_up,
        "n_down": n_down,
        "n_unchanged": n_unchanged,
        "advance_decline_ratio": round(ad_ratio, 2) if ad_ratio != float("inf") else None,
        "pct_above_ma20": round(len(above_ma20) / n_total, 3) if n_total else 0,
        "pct_above_ma60": round(len(above_ma60) / n_total, 3) if n_total else 0,
        "n_new_high_50d": n_new_high,
        "n_new_low_50d": n_new_low,
        "new_high_low_ratio": round(high_low_ratio, 2) if high_low_ratio != float("inf") else None,
    }


def breadth_health_label(b: dict) -> tuple[str, str]:
    """把一組 breadth 指標濃縮成「燈號 + 標語」。回傳 (color_name, label_zh)。"""
    if not b:
        return ("gray", "資料不足")
    p20 = b.get("pct_above_ma20", 0)
    ad = b.get("advance_decline_ratio") or 1
    nh = b.get("n_new_high_50d", 0)
    nl = b.get("n_new_low_50d", 0)

    score = 0
    if p20 >= 0.6: score += 2
    elif p20 >= 0.45: score += 1
    elif p20 <= 0.25: score -= 2
    elif p20 <= 0.4: score -= 1

    if ad >= 1.5: score += 1
    elif ad <= 0.67: score -= 1

    if nh >= nl * 2: score += 1
    elif nl >= nh * 2: score -= 1

    if score >= 3: return ("green", "強勢多頭（寬度極好）")
    if score >= 1: return ("lightgreen", "偏多")
    if score <= -3: return ("red", "弱勢空頭（寬度極差）")
    if score <= -1: return ("orange", "偏空")
    return ("gray", "中性")
