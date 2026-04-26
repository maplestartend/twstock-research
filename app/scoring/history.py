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


def snapshot_today(db: Database, include_fundamentals: bool = True, *, as_of=None) -> int:
    """計算指定日期全市場評分（預設今日），寫入 signal_history。回傳寫入筆數。

    分數缺失（資料不足）會寫入 NULL，而非 50 分中性假值，
    讓回看時能區分「當天中性」和「當天無法評估」。

    as_of: 評分基準日（YYYY-MM-DD or date）。歷史回放/回測時指定，所有 DB 查詢
    都會把 date <= as_of 設為上限，避免 look-ahead bias。
    """
    df = radar.score_all(db, include_fundamentals=include_fundamentals, as_of=as_of)
    if df.empty:
        return 0

    strategy_hits: dict[str, list[str]] = {sid: [] for sid in df["stock_id"]}
    for name, strat in radar.STRATEGIES.items():
        hit = strat.filter_fn(df)
        for sid in hit["stock_id"]:
            strategy_hits[sid].append(name)

    as_of = df["as_of"].iloc[0]
    rows = []
    for _, r in df.iterrows():
        sid = r["stock_id"]
        rows.append({
            "as_of": as_of,
            "stock_id": sid,
            "stock_name": r["stock_name"],
            "close": _nullable_float(r["close"]),
            "short": _nullable_float(r.get("short")),
            "mid": _nullable_float(r.get("mid")),
            "long": _nullable_float(r.get("long")),
            "composite": _nullable_float(r.get("composite")),
            "data_completeness": _nullable_float(r.get("data_completeness")),
            "is_stale": 1 if bool(r.get("is_stale")) else 0,
            "recommendation": r.get("recommendation") or "",
            "strategies": ",".join(strategy_hits[sid]),
        })
    out = pd.DataFrame(rows)
    n = db.upsert_df(out, "signal_history")
    logger.info("signal_history: 寫入 %d 筆（as_of=%s）", n, as_of)
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
