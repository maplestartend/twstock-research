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

import numpy as np
import pandas as pd

from app.data.db import Database
from app.data.fetcher import FinMindError, FinMindFetcher

logger = logging.getLogger(__name__)


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


def update_stock_adjusted(db: Database, fetcher: FinMindFetcher, stock_id: str) -> int:
    """抓事件 → 計算 adj 序列 → 寫入 adj_event + daily_price_adj。回傳寫入筆數。"""
    events = fetch_events(fetcher, stock_id)
    if not events.empty:
        ev_out = events.copy()
        ev_out["date"] = pd.to_datetime(ev_out["date"]).dt.strftime("%Y-%m-%d")
        db.upsert_df(ev_out, "adj_event")

    price = db.load_daily_price(stock_id)
    if price.empty:
        return 0

    adj = compute_adj_series(price, events)
    if adj.empty:
        return 0
    adj["date"] = adj["date"].dt.strftime("%Y-%m-%d")
    return db.upsert_df(adj, "daily_price_adj")


def load_adjusted_price(db: Database, stock_id: str) -> pd.DataFrame:
    """回傳 daily_price + close_adj 等欄位合併後的 DataFrame。若沒還原資料，close_adj = close。"""
    with db.connect() as conn:
        df = pd.read_sql_query(
            """
            SELECT p.date, p.stock_id, p.open, p.high, p.low, p.close, p.volume,
                   p.amount, p.turnover, p.spread,
                   a.close_adj, a.open_adj, a.high_adj, a.low_adj
            FROM daily_price p
            LEFT JOIN daily_price_adj a
              ON a.stock_id = p.stock_id AND a.date = p.date
            WHERE p.stock_id = ?
            ORDER BY p.date
            """,
            conn, params=[stock_id],
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        # 沒還原資料就用原始值
        for col in ("close_adj", "open_adj", "high_adj", "low_adj"):
            raw = col.replace("_adj", "")
            df[col] = df[col].fillna(df[raw])
    return df
