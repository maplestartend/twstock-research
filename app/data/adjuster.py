"""歷史股價的除權息/分割還原。

資料來源：
- FinMind TaiwanStockDividendResult（每次除權息的前後參考價）
- FinMind TaiwanStockSplitPrice（分割事件）

演算法：把事件視為「當日收盤後乘以 factor = after / before」。
往前（歷史方向）累乘即可得到「還原到最新基準」的歷史價格。
也就是：close_adj[d] = close[d] × (cumulative_factor of events after d)
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from app.data.clock import taipei_today
from app.data.db import Database
from app.data.fetcher import FinMindError, FinMindFetcher

logger = logging.getLogger(__name__)

ADJ_FETCH_LOG_DATASET = "adj_event"
# 1 天 TTL：除權息事件多在每年 6~9 月密集發生，7 天 TTL 會讓「6/15 跑過、6/22 該股除息」
# 的情境在 6/22~6/22+7 天內漏掉新 factor → 還原價斷層、技術指標誤觸停損。
# 縮短為 1 天，最差情況：每天 daily-update 多打 N 檔 FinMind dividend/split endpoint，
# 但 FinMind 對這兩個免費 endpoint 沒嚴格 quota，且大多數股票當日無事件 → 回 0 row 很快。
ADJ_FETCH_TTL_DAYS = 1


def fetch_events(fetcher: FinMindFetcher, stock_id: str, start: str = "2015-01-01") -> pd.DataFrame:
    """抓取單一股票的所有除權息 + 分割事件，合併為 (date, event_type, before, after, factor)。"""
    frames: list[pd.DataFrame] = []

    try:
        div = fetcher.dividend_result(stock_id, start)
        if not div.empty:
            div = div[["date", "before_price", "after_price"]].copy()
            div["event_type"] = "dividend"
            frames.append(div)
    except FinMindError as e:
        logger.warning("%s dividend 失敗: %s", stock_id, e)

    try:
        split = fetcher.split_events(stock_id, start)
        if not split.empty:
            split = split[["date", "before_price", "after_price"]].copy()
            split["event_type"] = "split"
            frames.append(split)
    except FinMindError as e:
        logger.warning("%s split 失敗: %s", stock_id, e)

    if not frames:
        return pd.DataFrame(columns=["date", "stock_id", "event_type", "before_price", "after_price", "factor"])

    out = pd.concat(frames, ignore_index=True)
    out["stock_id"] = stock_id
    # 過濾 before/after 合理性
    out = out.dropna(subset=["before_price", "after_price"])
    out = out[(out["before_price"] > 0) & (out["after_price"] > 0)]
    out["factor"] = out["after_price"] / out["before_price"]
    out = out.sort_values("date").reset_index(drop=True)
    return out[["date", "stock_id", "event_type", "before_price", "after_price", "factor"]]


def compute_adj_series(daily_price: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """根據 events 把 daily_price 還原。

    回傳：date, stock_id, close_adj, open_adj, high_adj, low_adj
    """
    if daily_price.empty:
        return pd.DataFrame(columns=["date", "stock_id", "close_adj", "open_adj", "high_adj", "low_adj"])

    dp = daily_price.sort_values("date").reset_index(drop=True).copy()
    dp["date"] = pd.to_datetime(dp["date"])

    # 累積還原因子：從最新到最舊，遇到 event 就把「之前(不含當天)」的因子乘以 event.factor
    factor = np.ones(len(dp), dtype=float)

    if not events.empty:
        ev = events.copy()
        ev["date"] = pd.to_datetime(ev["date"])
        dates_arr = dp["date"].to_numpy()
        for _, e in ev.iterrows():
            ed, f = e["date"], float(e["factor"])
            mask = dates_arr < ed.to_numpy() if hasattr(ed, "to_numpy") else dates_arr < ed
            factor[mask] = factor[mask] * f

    dp["adj_factor"] = factor
    dp["close_adj"] = dp["close"] * dp["adj_factor"]
    dp["open_adj"] = dp["open"] * dp["adj_factor"]
    dp["high_adj"] = dp["high"] * dp["adj_factor"]
    dp["low_adj"] = dp["low"] * dp["adj_factor"]

    out = dp[["date", "stock_id", "close_adj", "open_adj", "high_adj", "low_adj"]].copy()
    return out


def _load_cached_events(db: Database, stock_id: str) -> pd.DataFrame:
    with db.connect() as conn:
        return pd.read_sql_query(
            """
            SELECT date, stock_id, event_type, before_price, after_price, factor
            FROM adj_event WHERE stock_id = ? ORDER BY date
            """,
            conn, params=[stock_id],
        )


def update_stock_adjusted(
    db: Database,
    fetcher: FinMindFetcher,
    stock_id: str,
    *,
    force_refetch: bool = False,
) -> int:
    """抓事件 → 計算 adj 序列 → 寫入 adj_event + daily_price_adj。回傳寫入筆數。

    為避免每日重抓 FinMind dividend/split (除權息一年才幾次)，使用 fetch_log 做 TTL：
    若 7 天內已 refresh 過、改用 DB 快取重算 daily_price_adj。force_refetch=True 強制重抓。
    """
    today_dt = taipei_today()
    today_iso = today_dt.isoformat()
    last_run = db.get_last_fetch_date(stock_id, ADJ_FETCH_LOG_DATASET)
    fresh = (
        not force_refetch
        and last_run is not None
        and (today_dt - date.fromisoformat(last_run)).days < ADJ_FETCH_TTL_DAYS
    )

    if fresh:
        events = _load_cached_events(db, stock_id)
        logger.debug("%s adj 用 %d 天內快取 (last=%s)，跳過 FinMind",
                     stock_id, ADJ_FETCH_TTL_DAYS, last_run)
    else:
        events = fetch_events(fetcher, stock_id)
        if not events.empty:
            ev_out = events.copy()
            ev_out["date"] = pd.to_datetime(ev_out["date"]).dt.strftime("%Y-%m-%d")
            db.upsert_df(ev_out, "adj_event")
        # 即使 events 為空也記 last_run, 避免空檔每天重打 FinMind
        db.set_last_fetch_date(stock_id, ADJ_FETCH_LOG_DATASET, today_iso)

    price = db.load_daily_price(stock_id)
    if price.empty:
        return 0

    adj = compute_adj_series(price, events)
    if adj.empty:
        return 0
    adj["date"] = adj["date"].dt.strftime("%Y-%m-%d")
    return db.upsert_df(adj, "daily_price_adj")


def read_ohlc_with_adj(
    conn,
    *,
    stock_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    extra_cols: bool = True,
) -> pd.DataFrame:
    """讀 daily_price + daily_price_adj 的 LEFT JOIN，回傳 raw OHLC + adj OHLC 各自一欄。

    用 `_swap_to_adjusted` 後處理（engine.py）就能讓技術指標走還原價但 raw 保留給流動性 /
    漲跌停偵測。caller 自選要不要呼叫 swap。

    參數：
    - stock_id: 提供 → 過濾單檔；None → 全市場
    - since / until: SQL `BETWEEN since AND until`（含端點），任一可空
    - extra_cols=True → 多帶 turnover, spread；False → 8 欄精簡（給 radar bulk 省 I/O）

    這個 helper 取代 4 處重複的 `LEFT JOIN daily_price_adj a ON ...` 拼字串。
    """
    cols = "p.date, p.stock_id, p.open, p.high, p.low, p.close, p.volume, p.amount"
    if extra_cols:
        cols += ", p.turnover, p.spread"
    cols += ", a.close_adj, a.open_adj, a.high_adj, a.low_adj"
    query = (
        f"SELECT {cols} "
        "FROM daily_price p "
        "LEFT JOIN daily_price_adj a ON a.stock_id=p.stock_id AND a.date=p.date "
        "WHERE 1=1"
    )
    params: list = []
    if stock_id is not None:
        query += " AND p.stock_id = ?"
        params.append(stock_id)
    if since is not None:
        query += " AND p.date >= ?"
        params.append(since)
    if until is not None:
        query += " AND p.date <= ?"
        params.append(until)
    query += " ORDER BY p.date"
    return pd.read_sql_query(query, conn, params=params)


def read_close_with_adj_coalesced(
    conn,
    *,
    stock_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> pd.DataFrame:
    """讀 (date, stock_id, close) — close = COALESCE(adj, raw)，SQL 端直接合併成單一欄。

    用於 IC / forward return / backtest 大盤比較等只需要單一價格序列的場景。比
    `read_ohlc_with_adj` 輕量（3 欄 vs 14 欄）。
    """
    query = (
        "SELECT p.date, p.stock_id, COALESCE(a.close_adj, p.close) AS close "
        "FROM daily_price p "
        "LEFT JOIN daily_price_adj a ON a.stock_id=p.stock_id AND a.date=p.date "
        "WHERE 1=1"
    )
    params: list = []
    if stock_id is not None:
        query += " AND p.stock_id = ?"
        params.append(stock_id)
    if since is not None:
        query += " AND p.date >= ?"
        params.append(since)
    if until is not None:
        query += " AND p.date <= ?"
        params.append(until)
    query += " ORDER BY p.date"
    return pd.read_sql_query(query, conn, params=params)


def load_adjusted_price(db: Database, stock_id: str, *, as_of: str | None = None) -> pd.DataFrame:
    """回傳 daily_price + close_adj 合併後的 DataFrame（單檔，含 adj 欄、NaN-fillna 後處理）。

    as_of: 若提供，僅回傳 `date <= as_of` 的資料，供歷史重播避免 look-ahead。

    與 `read_ohlc_with_adj` 的差別：這個會把 NaN adj 用 raw 補滿（因為呼叫端 score_stock
    `_swap_to_adjusted` 假設 adj cols 沒有 NaN）。`read_ohlc_with_adj` 不做後處理，留給
    caller 決定。
    """
    with db.connect() as conn:
        df = read_ohlc_with_adj(conn, stock_id=stock_id, until=as_of, extra_cols=True)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        # 沒還原資料就用原始值
        for col in ("close_adj", "open_adj", "high_adj", "low_adj"):
            raw = col.replace("_adj", "")
            df[col] = df[col].fillna(df[raw])
    return df
