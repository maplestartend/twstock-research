"""市場脈絡指標：相對強弱 RS vs 加權指數。"""
from __future__ import annotations

import pandas as pd

from app.data.db import Database

TAIEX_NAME = "發行量加權股價指數"


def load_taiex_series(db: Database) -> pd.DataFrame:
    """回傳 date, close 兩欄。"""
    with db.connect() as conn:
        df = pd.read_sql_query(
            "SELECT date, close FROM index_daily WHERE index_name = ? ORDER BY date",
            conn, params=[TAIEX_NAME],
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def taiex_return(series: pd.DataFrame, periods: int) -> float | None:
    """最近 N 個交易日的大盤報酬率。"""
    if series.empty or len(series) < periods + 1:
        return None
    last = float(series.iloc[-1]["close"])
    past = float(series.iloc[-periods - 1]["close"])
    if past == 0:
        return None
    return (last - past) / past


def compute_rs(price_df: pd.DataFrame, taiex_series: pd.DataFrame, periods: int) -> float | None:
    """個股相對強弱 = 個股 N 日報酬率 - 大盤 N 日報酬率。"""
    if price_df.empty or len(price_df) < periods + 1:
        return None
    s_last = float(price_df.iloc[-1]["close"])
    s_past = float(price_df.iloc[-periods - 1]["close"])
    if s_past == 0:
        return None
    stock_ret = (s_last - s_past) / s_past
    mkt_ret = taiex_return(taiex_series, periods)
    if mkt_ret is None:
        return None
    return stock_ret - mkt_ret
