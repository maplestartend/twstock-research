"""訊號歷史化：每日把 radar 產出的分數 + 命中策略存檔，方便回顧「一週前選的股票現在怎樣」。"""
from __future__ import annotations

import logging

import pandas as pd

from app.data.db import Database
from app.scoring import radar

logger = logging.getLogger(__name__)


def _nullable_float(v) -> float | None:
    """radar 輸出的分數可能為 None（資料不足）或 NaN，統一轉成 None（SQL NULL）。"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)


def snapshot_today(
    db: Database,
    include_fundamentals: bool = True,
    *,
    as_of=None,
    candidate_stocks: list[tuple[str, str]] | None = None,
) -> int:
    """計算指定日期全市場評分（預設今日），寫入 signal_history。回傳寫入筆數。

    分數缺失（資料不足）會寫入 NULL，而非 50 分中性假值，
    讓回看時能區分「當天中性」和「當天無法評估」。

    as_of: 評分基準日（YYYY-MM-DD or date）。歷史回放/回測時指定，所有 DB 查詢
    都會把 date <= as_of 設為上限，避免 look-ahead bias。
    """
    df = radar.score_all(
        db,
        include_fundamentals=include_fundamentals,
        as_of=as_of,
        candidate_stocks=candidate_stocks,
    )
    if df.empty:
        return 0

    # 流動性硬過濾：策略命中的候選池剔除「實際上買不到 / 賣不掉」的股票（成交量過低、
    # 當日漲跌停鎖死）。為什麼只 gating 策略標籤而不影響分數本身：score_all 的分數仍要
    # 寫進 signal_history（讓個股歷史分數曲線不出現空洞），只有 radar hits（讀 signal_history
    # 的 strategies 欄位）需要被「可交易性」過濾。
    #
    # 安全閥：若流動性欄位有效資料 < 50%（例：舊 DB 還沒 backfill amount），就跳過該條件，
    # 避免整個雷達清空。
    tradable = df
    amt = df.get("amount_20d")
    if amt is not None and amt.notna().mean() > 0.5:
        # 20 日平均成交額 < 100 萬元：太薄，掛單即移動報價、無法以接近收盤價成交
        tradable = tradable[amt.fillna(0) >= 1_000_000]
    chg = df.get("pct_change_today")
    if chg is not None and chg.notna().mean() > 0.5:
        # 當日漲跌幅 ≥ 9.5%：接近 ±10% 漲跌停，多半已鎖死無交易（流動性蒸發）
        tradable = tradable[chg.reindex(tradable.index).fillna(0).abs() < 0.095]

    # 策略命中（每檔可能命中多個 strategy）：用 dict 收，最後 vector 化 ',' join
    strategy_hits: dict[str, list[str]] = {sid: [] for sid in df["stock_id"]}
    for name, strat in radar.STRATEGIES.items():
        hit = strat.filter_fn(tradable)
        for sid in hit["stock_id"]:
            strategy_hits[sid].append(name)

    as_of = df["as_of"].iloc[0]
    # 向量化建構 — 改寫自過去 iterrows + dict append 版本（~2300 row × Python loop ≈ 1-2s）。
    # numeric 欄位用 .where(notna, None) 把 NaN/None 統一成 SQL NULL（DB 端能區分「無資料」vs 50 中性分）。
    out = pd.DataFrame({
        "as_of": as_of,
        "stock_id": df["stock_id"].values,
        "stock_name": df["stock_name"].values,
        "close": df["close"].astype(object).where(df["close"].notna(), None),
        "short": df.get("short", pd.Series(dtype=object)).astype(object).where(
            df.get("short", pd.Series(dtype=object)).notna(), None,
        ) if "short" in df.columns else None,
        "mid": df.get("mid", pd.Series(dtype=object)).astype(object).where(
            df.get("mid", pd.Series(dtype=object)).notna(), None,
        ) if "mid" in df.columns else None,
        "long": df.get("long", pd.Series(dtype=object)).astype(object).where(
            df.get("long", pd.Series(dtype=object)).notna(), None,
        ) if "long" in df.columns else None,
        "composite": df.get("composite", pd.Series(dtype=object)).astype(object).where(
            df.get("composite", pd.Series(dtype=object)).notna(), None,
        ) if "composite" in df.columns else None,
        "vr_macd": df.get("vr_macd", pd.Series(dtype=object)).astype(object).where(
            df.get("vr_macd", pd.Series(dtype=object)).notna(), None,
        ) if "vr_macd" in df.columns else None,
        "data_completeness": df.get("data_completeness", pd.Series(dtype=object)).astype(object).where(
            df.get("data_completeness", pd.Series(dtype=object)).notna(), None,
        ) if "data_completeness" in df.columns else None,
        "is_stale": df.get("is_stale", pd.Series([False] * len(df))).fillna(False).astype(int).values,
        "recommendation": df.get("recommendation", pd.Series([""] * len(df))).fillna("").values,
        "strategies": df["stock_id"].map(lambda sid: ",".join(strategy_hits[sid])).values,
    })
    n = db.upsert_df(out, "signal_history")
    logger.info("signal_history: 寫入 %d 筆（as_of=%s）", n, as_of)

    # 子因子分數（給 /diagnostics sub-factor IC 用）。score_all 透過 .attrs 帶出長格式 DataFrame。
    # 缺值（資料不足造成的 None）也寫，下游 IC 算法跑 dropna 排除。
    parts_df = df.attrs.get("parts") if hasattr(df, "attrs") else None
    if parts_df is not None and not parts_df.empty:
        # 與 signal_history 同 transaction 邏輯：upsert by (stock_id, as_of, horizon, factor)
        # NaN/None → SQL NULL；upsert_df 會處理。
        m = db.upsert_df(parts_df, "signal_history_factor_parts")
        logger.info("signal_history_factor_parts: 寫入 %d 筆（as_of=%s）", m, as_of)

    return n


def load_snapshot(db: Database, as_of: str) -> pd.DataFrame:
    with db.connect() as conn:
        df = pd.read_sql_query(
            "SELECT * FROM signal_history WHERE as_of = ? ORDER BY composite DESC",
            conn, params=[as_of],
        )
    return df


def available_dates(db: Database) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT as_of FROM signal_history ORDER BY as_of DESC"
        ).fetchall()
    return [r["as_of"] for r in rows]


def track_performance(db: Database, as_of: str, strategy: str | None = None) -> pd.DataFrame:
    """回看 as_of 那天各策略命中的股票，算到最新一天的漲跌幅。"""
    snap = load_snapshot(db, as_of)
    if snap.empty:
        return pd.DataFrame()

    if strategy:
        snap = snap[snap["strategies"].fillna("").str.contains(strategy, regex=False)]
    if snap.empty:
        return pd.DataFrame()

    with db.connect() as conn:
        # 最新收盤與當時收盤比較。
        # 舊版 subquery `SELECT stock_id, MAX(date) FROM daily_price GROUP BY stock_id`
        # 會掃整個 2.3M-row PK；改成「先撈全市場最新交易日」再 `WHERE date = ?`
        # 走 idx_price_date 索引，~800× 快。
        max_row = conn.execute("SELECT MAX(date) AS mx FROM daily_price").fetchone()
        max_date = max_row["mx"] if max_row else None
        if max_date is None:
            latest = pd.DataFrame(columns=["stock_id", "latest_close", "latest_date"])
        else:
            latest = pd.read_sql_query(
                """
                SELECT stock_id, close AS latest_close, date AS latest_date
                FROM daily_price
                WHERE date = ?
                """, conn, params=[max_date],
            )
    merged = snap.merge(latest, on="stock_id", how="left")
    merged["change_pct"] = (merged["latest_close"] - merged["close"]) / merged["close"]
    return merged[[
        "stock_id", "stock_name", "close", "latest_close", "change_pct",
        "short", "mid", "long", "composite", "recommendation", "strategies",
        "latest_date",
    ]].sort_values("composite", ascending=False, na_position="last").reset_index(drop=True)
