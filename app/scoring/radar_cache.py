"""雷達掃描結果的持久化磁碟快取。

用途：避免每次重啟服務都要重新跑 ~20-30 秒的全市場掃描。
只要 DB 裡 daily_price 的最新日期沒變，就直接讀上次存的 parquet。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from app.data.db import Database

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"

# Bump 此值以讓舊的快取 parquet 失效（例：radar 新增欄位、權重重算邏輯改動）。
# v2: 加入 data_completeness / is_stale、composite 改為 None-aware
# v3: 基本面新增 financials_cumulative fallback（非 watchlist 也能有長期分數）
# v4: 加入 financials_quarterly_derived，可算 TTM/YoY/ROE（Q1~Q3 也能算）
CACHE_SCHEMA_VERSION = "v4"


def _latest_price_date(db: Database) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(date) AS mx FROM daily_price").fetchone()
    return row["mx"] if row and row["mx"] else None


def _latest_revenue_date(db: Database) -> str:
    """月營收最新日（若尚無資料回空字串，不影響 key 成立）。"""
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(date) AS mx FROM monthly_revenue").fetchone()
    return (row["mx"] or "") if row else ""


def _cache_key(db: Database, include_fundamentals: bool) -> str | None:
    pd_date = _latest_price_date(db)
    if not pd_date:
        return None
    rev_date = _latest_revenue_date(db).replace("-", "") or "norev"
    return f"{pd_date}_{rev_date}"


def _cache_path(key: str, include_fundamentals: bool) -> Path:
    fund_tag = "fund" if include_fundamentals else "nofund"
    return CACHE_DIR / f"radar_{CACHE_SCHEMA_VERSION}_{key}_{fund_tag}.parquet"


def load(db: Database, include_fundamentals: bool) -> pd.DataFrame | None:
    """若當日 DB 資料已有快取就回傳 DataFrame，否則回 None。"""
    key = _cache_key(db, include_fundamentals)
    if not key:
        return None
    path = _cache_path(key, include_fundamentals)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        logger.info("radar cache hit: %s (%d rows)", path.name, len(df))
        return df
    except Exception as e:
        logger.warning("radar cache 讀取失敗 %s: %s", path, e)
        return None


def save(df: pd.DataFrame, db: Database, include_fundamentals: bool) -> Path | None:
    """把 score_all 結果寫成 parquet。同時清掉舊 key 的檔案。"""
    if df is None or df.empty:
        return None
    key = _cache_key(db, include_fundamentals)
    if not key:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(key, include_fundamentals)
    try:
        df.to_parquet(path, index=False)
        logger.info("radar cache 寫入: %s (%d rows)", path.name, len(df))
    except Exception as e:
        logger.warning("radar cache 寫入失敗 %s: %s", path, e)
        return None
    _cleanup_stale(keep_name=path.name)
    return path


def _cleanup_stale(keep_name: str) -> None:
    """保留目前的 key，刪掉其他所有 radar_*.parquet（含不同 schema 版本的舊 cache）。"""
    if not CACHE_DIR.exists():
        return
    for p in CACHE_DIR.glob("radar_*.parquet"):
        if p.name != keep_name:
            try:
                p.unlink()
            except OSError:
                pass
