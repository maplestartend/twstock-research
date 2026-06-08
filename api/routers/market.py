"""/api/market/* — 大盤 snapshot + 廣度。"""
from __future__ import annotations

import logging
import math

from fastapi import APIRouter, Depends, HTTPException, Response

from api.deps import get_db
from api.schemas.market import (
    IndustryMemberRow,
    IndustryRotationResponse,
    IndustryRotationRow,
    MarketBreadth,
    MarketIntradayQuote,
    MarketSnapshot,
)
from app.data import intraday as intraday_mod
from app.data.db import Database
from app.indicators.market_scope import (
    breadth_health_label,
    industry_members,
    industry_rotation,
    market_breadth,
)

router = APIRouter(prefix="/api/market", tags=["market"])

logger = logging.getLogger(__name__)


def _sanitize(value):
    """Pydantic v2 會拒絕 NaN/inf — 把它們轉成 None。"""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


@router.get("/snapshot", response_model=MarketSnapshot)
def snapshot(db: Database = Depends(get_db)) -> MarketSnapshot:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT date, close, change_pct FROM index_daily "
            "WHERE index_name = '發行量加權股價指數' "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return MarketSnapshot()
    return MarketSnapshot(
        date=row["date"],
        close=_sanitize(row["close"]),
        change_pct=_sanitize(row["change_pct"]),
    )


@router.get("/intraday", response_model=MarketIntradayQuote)
def intraday(response: Response) -> MarketIntradayQuote:
    """大盤加權指數盤中即時值（TWSE mis）。

    - 30 秒 in-memory cache（避免 hammer 上游）；前端輪詢頻率 30s
    - mis 失敗 / 休市 → 422，前端 fallback 到 /api/market/snapshot 的昨日收盤
    - 強制 no-store：避免 Next.js Data Cache / 反向代理把這支當成 60s 可快取的端點，
      讓 Topbar 看到的指數值跟個股的「即時」標籤對得上時間
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    q = intraday_mod.fetch_index_quote()
    if q is None:
        raise HTTPException(status_code=422, detail="大盤即時報價無法取得（mis 異常或休市）")
    chg_pct: float | None = None
    if q.prev_close and q.prev_close > 0 and q.value is not None:
        chg_pct = (q.value - q.prev_close) / q.prev_close
    return MarketIntradayQuote(
        index_id=q.index_id,
        name=q.name or None,
        value=q.value,
        prev_close=q.prev_close,
        open=q.open,
        high=q.high,
        low=q.low,
        change_pct=chg_pct,
        quote_time=q.quote_time,
        is_live=q.is_live,
        quote_source=q.quote_source,
    )


_HEALTH_TONE = {
    "green": "up",
    "lightgreen": "up",
    "orange": "down",
    "red": "down",
    "gray": "neutral",
}


@router.get("/breadth", response_model=MarketBreadth)
def breadth(db: Database = Depends(get_db)) -> MarketBreadth:
    data = market_breadth(db) or {}
    if not data:
        return MarketBreadth()
    try:
        color, label = breadth_health_label(data)
    except Exception as e:
        logger.debug("breadth_health_label 失敗，回退中性: %s", e)
        color, label = "gray", "中性"
    return MarketBreadth(
        n_total=int(data.get("n_total", 0) or 0),
        n_up=int(data.get("n_up", 0) or 0),
        n_down=int(data.get("n_down", 0) or 0),
        n_unchanged=int(data.get("n_unchanged", 0) or 0),
        advance_decline_ratio=_sanitize(data.get("advance_decline_ratio")),
        pct_above_ma20=_sanitize(data.get("pct_above_ma20")),
        pct_above_ma60=_sanitize(data.get("pct_above_ma60")),
        n_new_high_50d=int(data.get("n_new_high_50d", 0) or 0),
        n_new_low_50d=int(data.get("n_new_low_50d", 0) or 0),
        new_high_low_ratio=_sanitize(data.get("new_high_low_ratio")),
        health_label=label,
        health_tone=_HEALTH_TONE.get(color, "neutral"),
    )


@router.get("/industry-rotation", response_model=IndustryRotationResponse)
def rotation(min_members: int = 3, db: Database = Depends(get_db)) -> IndustryRotationResponse:
    result = industry_rotation(db, min_members=min_members)
    as_of = result.get("as_of")
    df = result.get("rows")
    if df is None or df.empty:
        return IndustryRotationResponse(as_of=as_of, rows=[])
    rows: list[IndustryRotationRow] = []
    for _, r in df.iterrows():
        rows.append(IndustryRotationRow(
            industry=str(r["industry"]),
            n_members=int(r["n"]),
            ret_1d=_sanitize(r.get("ret_1d")),
            ret_1d_weighted=_sanitize(r.get("ret_1d_weighted")),
            ret_5d=_sanitize(r.get("ret_5d")),
            ret_20d=_sanitize(r.get("ret_20d")),
            ret_60d=_sanitize(r.get("ret_60d")),
            heat=_sanitize(r.get("heat")),
            total_amount=_sanitize(r.get("total_amount")),
            n_up=int(r.get("n_up") or 0),
            n_flat=int(r.get("n_flat") or 0),
            n_down=int(r.get("n_down") or 0),
        ))
    return IndustryRotationResponse(as_of=as_of, rows=rows)


@router.get("/industry-members", response_model=list[IndustryMemberRow])
def members(industry: str, top: int = 30, db: Database = Depends(get_db)) -> list[IndustryMemberRow]:
    if not industry:
        raise HTTPException(status_code=400, detail="industry required")
    df = industry_members(db, industry, top_n=top)
    if df.empty:
        return []
    rows: list[IndustryMemberRow] = []
    for _, r in df.iterrows():
        rows.append(IndustryMemberRow(
            stock_id=str(r["stock_id"]),
            stock_name=str(r["stock_name"]) if r["stock_name"] else str(r["stock_id"]),
            close=_sanitize(r.get("close")),
            ret_1d=_sanitize(r.get("ret_1d")),
            ret_5d=_sanitize(r.get("ret_5d")),
            ret_20d=_sanitize(r.get("ret_20d")),
        ))
    return rows
