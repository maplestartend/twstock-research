"""/api/calendar/* — 除權息行事曆（現場從 TWSE TWT49U 抓）。"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends

from api.deps import get_db
from api.schemas.stock import ExDividendCalendarEvent
from app import watchlist as wl_mod
from app.data.db import Database
from app.data.twse_fetcher import TwseFetcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# 簡單 in-memory 快取。key = days_ahead → (expire_ts, raw_rows_dict_list)
# 只快取不含 watchlist/holdings flag 的純 TWSE 資料，flag 每次重算
_CACHE: dict[int, tuple[float, list[dict]]] = {}
_CACHE_TTL = 30 * 60  # 30 分鐘
_CACHE_LOCK = threading.Lock()


def _nz(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _fetch_raw(days_ahead: int) -> list[dict]:
    """從 TWSE 抓，轉成純 dict list（方便快取）。"""
    from app.data.clock import taipei_today
    today = taipei_today()
    start = today.strftime("%Y%m%d")
    end = (today + timedelta(days=days_ahead)).strftime("%Y%m%d")
    try:
        df = TwseFetcher(request_delay=0.3).upcoming_dividends(start, end)
    except Exception as e:
        logger.warning("TWSE upcoming_dividends 失敗: %s", e)
        return []

    if df is None or df.empty:
        return []

    rows: list[dict] = []
    for _, r in df.iterrows():
        try:
            d_raw = r.get("date")
            d_str = str(d_raw)[:10] if d_raw is not None else ""
            rows.append({
                "ex_date": d_str,
                "stock_id": str(r["stock_id"]),
                "stock_name": str(r["stock_name"]) if r.get("stock_name") else str(r["stock_id"]),
                "cum_price": _nz(r.get("cum_price")),
                "ex_price": _nz(r.get("ex_price")),
                "dividend_value": _nz(r.get("dividend_value")),
                "event_type": r.get("type") if r.get("type") else None,
            })
        except Exception as e:
            logger.warning("row 轉換失敗: %s", e)
            continue
    return rows


@router.get("/ex-dividend", response_model=list[ExDividendCalendarEvent])
def ex_dividend(
    days_ahead: int = 60,
    db: Database = Depends(get_db),
) -> list[ExDividendCalendarEvent]:
    days_ahead = max(1, min(days_ahead, 180))

    now_ts = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(days_ahead)
    if cached and cached[0] > now_ts:
        raw = cached[1]
    else:
        raw = _fetch_raw(days_ahead)
        with _CACHE_LOCK:
            _CACHE[days_ahead] = (now_ts + _CACHE_TTL, raw)

    # 每次重新計算持股/自選 flag（可變）
    watch_ids = set(wl_mod.load().keys())
    with db.connect() as conn:
        hold_rows = conn.execute("SELECT stock_id FROM holdings WHERE shares > 0").fetchall()
    hold_ids = {r["stock_id"] for r in hold_rows}

    out: list[ExDividendCalendarEvent] = []
    for r in raw:
        cum = r.get("cum_price")
        val = r.get("dividend_value")
        yield_pct = (val / cum) if (cum and val is not None and cum != 0) else None
        out.append(ExDividendCalendarEvent(
            ex_date=r["ex_date"],
            stock_id=r["stock_id"],
            stock_name=r["stock_name"],
            cum_price=cum,
            ex_price=r.get("ex_price"),
            dividend_value=val,
            event_type=r.get("event_type"),
            yield_pct=yield_pct,
            in_holdings=r["stock_id"] in hold_ids,
            in_watchlist=r["stock_id"] in watch_ids,
        ))
    return out
