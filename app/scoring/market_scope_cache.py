"""市場輪動 / 廣度指標的磁碟快取。

跟 radar_cache.py 走同一個套路：
- cache key = `MAX(daily_price.date)`，日期沒換就讀舊檔。
- cache 落在 data/cache/market_scope_*.parquet（rows）+ JSON sidecar（純 dict）。

industry_rotation 回傳 `{"as_of": ..., "rows": DataFrame}`，所以 rows 寫 parquet、
as_of 編進檔名即可。market_breadth 回傳純 dict，直接 JSON 寫盤。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from app.data.db import Database
from app.indicators import market_scope as ms

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"

# Bump 這個讓舊 cache 失效（例：rotation 加新欄位、breadth 改公式）。
CACHE_SCHEMA_VERSION = "v1"


def _latest_price_date(db: Database) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(date) AS mx FROM daily_price").fetchone()
    return row["mx"] if row and row["mx"] else None


def _rotation_paths(key: str, min_members: int) -> tuple[Path, Path]:
    base = CACHE_DIR / f"market_scope_rotation_{CACHE_SCHEMA_VERSION}_{key}_m{min_members}"
    return base.with_suffix(".parquet"), base.with_suffix(".json")


def _breadth_path(key: str) -> Path:
    return CACHE_DIR / f"market_scope_breadth_{CACHE_SCHEMA_VERSION}_{key}.json"


def get_industry_rotation_cached(db: Database, *, min_members: int = 3) -> dict:
    """讀盤 → 沒有就算 → 寫盤。回傳 shape 與 ms.industry_rotation 完全一致。"""
    key = _latest_price_date(db)
    if not key:
        return ms._industry_rotation_uncached(db, min_members=min_members)
    parquet_path, meta_path = _rotation_paths(key, min_members)
    if parquet_path.exists() and meta_path.exists():
        try:
            df = pd.read_parquet(parquet_path)
            with meta_path.open("r", encoding="utf-8") as fp:
                meta = json.load(fp)
            logger.info("market_scope rotation cache hit: %s (%d rows)", parquet_path.name, len(df))
            return {"as_of": meta.get("as_of"), "rows": df}
        except Exception as e:
            logger.warning("market_scope rotation cache 讀取失敗 %s: %s", parquet_path, e)

    result = ms._industry_rotation_uncached(db, min_members=min_members)
    df = result.get("rows")
    as_of = result.get("as_of")
    if df is None or df.empty:
        return result
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(parquet_path, index=False)
        with meta_path.open("w", encoding="utf-8") as fp:
            json.dump({"as_of": as_of}, fp)
        logger.info("market_scope rotation cache 寫入: %s (%d rows)", parquet_path.name, len(df))
    except Exception as e:
        logger.warning("market_scope rotation cache 寫入失敗 %s: %s", parquet_path, e)
    _cleanup_stale(parquet_path.name, meta_path.name)
    return result


def get_market_breadth_cached(db: Database) -> dict:
    """讀盤 → 沒有就算 → 寫盤。回傳 shape 與 ms.market_breadth 完全一致（純 dict）。"""
    key = _latest_price_date(db)
    if not key:
        return ms._market_breadth_uncached(db)
    path = _breadth_path(key)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            logger.info("market_scope breadth cache hit: %s", path.name)
            return data
        except Exception as e:
            logger.warning("market_scope breadth cache 讀取失敗 %s: %s", path, e)

    data = ms._market_breadth_uncached(db)
    if not data:
        return data
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as fp:
            json.dump(data, fp)
        logger.info("market_scope breadth cache 寫入: %s", path.name)
    except Exception as e:
        logger.warning("market_scope breadth cache 寫入失敗 %s: %s", path, e)
    _cleanup_stale_breadth(path.name)
    return data


def _cleanup_stale(keep_parquet: str, keep_meta: str) -> None:
    if not CACHE_DIR.exists():
        return
    for p in CACHE_DIR.glob("market_scope_rotation_*"):
        if p.name not in (keep_parquet, keep_meta):
            try:
                p.unlink()
            except OSError:
                pass


def _cleanup_stale_breadth(keep_name: str) -> None:
    if not CACHE_DIR.exists():
        return
    for p in CACHE_DIR.glob("market_scope_breadth_*.json"):
        if p.name != keep_name:
            try:
                p.unlink()
            except OSError:
                pass
