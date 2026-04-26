"""/api/stocks/* — 個股詳情。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from api.common import safe_float as _safe_float
from api.deps import get_db
from api.schemas.common import CamelModel
from api.schemas.stock import (
    IndicatorPoint,
    OHLCV,
    ScoreHistoryPoint,
    ScoreParts,
    StockMeta,
    StockPriceBundle,
    StockScoreView,
)
from app import risk as risk_mod
from app.data.db import Database
from app.indicators import technical as tech
from app.scoring.engine import score_stock

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/{stock_id}/meta", response_model=StockMeta)
def meta(stock_id: str, db: Database = Depends(get_db)) -> StockMeta:
    """股票 meta。完全找不到（既無 stock_info、也無 daily_price 紀錄）→ 404。
    若 stock_info 缺名稱但有價格紀錄，仍回 200 並以代號當名稱（避免漏抓資料時整頁不可用）。"""
    with db.connect() as conn:
        r = conn.execute(
            "SELECT stock_id, stock_name, industry_category, type FROM stock_info WHERE stock_id=?",
            (stock_id,),
        ).fetchone()
        if not r:
            has_price = conn.execute(
                "SELECT 1 FROM daily_price WHERE stock_id=? LIMIT 1", (stock_id,)
            ).fetchone()
            if not has_price:
                raise HTTPException(status_code=404, detail="stock not found")
            return StockMeta(stock_id=stock_id, stock_name=stock_id)
    return StockMeta(
        stock_id=r["stock_id"],
        stock_name=r["stock_name"] or r["stock_id"],
        industry=r["industry_category"],
        market_type=r["type"],
    )


@router.get("/{stock_id}/price", response_model=StockPriceBundle)
def price(stock_id: str, days: int = 180, db: Database = Depends(get_db)) -> StockPriceBundle:
    df = db.load_daily_price(stock_id)
    if df.empty:
        raise HTTPException(status_code=404, detail="no data")
    df = tech.enrich(df)
    df = df.tail(days).reset_index(drop=True)

    ohlcv: list[OHLCV] = []
    indicators: list[IndicatorPoint] = []
    for _, row in df.iterrows():
        d = pd.to_datetime(row["date"]).strftime("%Y-%m-%d")
        ohlcv.append(OHLCV(
            date=d,
            open=_safe_float(row.get("open")) or 0.0,
            high=_safe_float(row.get("high")) or 0.0,
            low=_safe_float(row.get("low")) or 0.0,
            close=_safe_float(row.get("close")) or 0.0,
            volume=_safe_float(row.get("volume")),
        ))
        indicators.append(IndicatorPoint(
            date=d,
            ma5=_safe_float(row.get("ma5")),
            ma20=_safe_float(row.get("ma20")),
            ma60=_safe_float(row.get("ma60")),
            k9=_safe_float(row.get("k9")),
            d9=_safe_float(row.get("d9")),
            rsi14=_safe_float(row.get("rsi14")),
            bb_upper=_safe_float(row.get("bb_upper")),
            bb_lower=_safe_float(row.get("bb_lower")),
        ))
    return StockPriceBundle(stock_id=stock_id, ohlcv=ohlcv, indicators=indicators)


@router.get("/{stock_id}/score", response_model=StockScoreView)
def score(stock_id: str, db: Database = Depends(get_db)) -> StockScoreView:
    with db.connect() as conn:
        r = conn.execute("SELECT stock_name FROM stock_info WHERE stock_id=?", (stock_id,)).fetchone()
    name = r["stock_name"] if r and r["stock_name"] else stock_id

    s = score_stock(db, stock_id, name)
    if s is None:
        # score_stock 返回 None 的情況：無 daily_price 或不滿 60 日；
        # 用 404 而非 422 比較貼合語意（resource not available）
        with db.connect() as conn:
            has_price = conn.execute(
                "SELECT 1 FROM daily_price WHERE stock_id=? LIMIT 1", (stock_id,)
            ).fetchone()
        if not has_price:
            raise HTTPException(status_code=404, detail="stock not found")
        raise HTTPException(status_code=422, detail="insufficient data: 至少需 60 個交易日的資料才能評分")

    return StockScoreView(
        stock_id=s.stock_id,
        stock_name=s.stock_name,
        as_of=s.as_of,
        close=s.close,
        short=ScoreParts(
            total=_safe_float(s.short.total),
            completeness=s.short.completeness,
            parts={k: _safe_float(v) for k, v in s.short.parts.items()},
        ),
        mid=ScoreParts(
            total=_safe_float(s.mid.total),
            completeness=s.mid.completeness,
            parts={k: _safe_float(v) for k, v in s.mid.parts.items()},
        ),
        long=ScoreParts(
            total=_safe_float(s.long.total),
            completeness=s.long.completeness,
            parts={k: _safe_float(v) for k, v in s.long.parts.items()},
        ),
        composite_score=_safe_float(s.signals.get("composite_score")),
        data_completeness=float(s.signals.get("data_completeness", 1.0)),
        is_stale=bool(s.is_stale),
        stale_days=int(s.signals.get("stale_days", 0) or 0),
        is_pending=bool(getattr(s, "is_pending", False)),
        recommendation=str(s.signals.get("recommendation", "")),
        entry=list(s.signals.get("entry") or []),
        stop_loss=list(s.signals.get("stop_loss") or []),
        take_profit=list(s.signals.get("take_profit") or []),
        warnings=list(s.signals.get("warnings") or []),
    )


@router.get("/{stock_id}/score-history", response_model=list[ScoreHistoryPoint])
def score_history(stock_id: str, days: int = 90, db: Database = Depends(get_db)) -> list[ScoreHistoryPoint]:
    from app.data.clock import taipei_today
    start = (taipei_today() - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT as_of, short, mid, long, composite FROM signal_history "
            "WHERE stock_id=? AND as_of >= ? ORDER BY as_of",
            (stock_id, start),
        ).fetchall()
    return [
        ScoreHistoryPoint(
            date=r["as_of"],
            short=_safe_float(r["short"]),
            mid=_safe_float(r["mid"]),
            long=_safe_float(r["long"]),
            composite=_safe_float(r["composite"]),
        )
        for r in rows
    ]


class AtrFixed(CamelModel):
    stop_price: float
    atr: float
    distance_pct: float | None = None
    entry_ref: float


class AtrTrailing(CamelModel):
    stop_price: float
    atr: float
    peak_since_entry: float
    latest_close: float
    below_stop: bool


class AtrStopView(CamelModel):
    """ATR 停損建議。trailing 段在無 entry_date 時為 None。"""
    stock_id: str
    multiplier: float
    period: int
    fixed: AtrFixed | None = None
    trailing: AtrTrailing | None = None


@router.get("/{stock_id}/atr-stop", response_model=AtrStopView)
def atr_stop(
    stock_id: str,
    entry_price: float | None = None,
    entry_date: str | None = None,
    multiplier: float = 2.0,
    period: int = 14,
    db: Database = Depends(get_db),
) -> AtrStopView:
    """ATR-based 停損建議。
    - `entry_price` / `entry_date` 都不給：fixed 用最後收盤算、trailing=None
    - 給 `entry_date`：trailing 段算「進場以來最高 − N×ATR」
    - 資料不足（< period+1 日）→ 422
    """
    df = db.load_daily_price(stock_id)
    if df.empty:
        raise HTTPException(status_code=404, detail="stock not found")
    fixed = risk_mod.atr_stop_loss(df, entry_price=entry_price, multiplier=multiplier, period=period)
    trailing = risk_mod.trailing_atr_stop(df, entry_date or "", multiplier=multiplier, period=period) if entry_date else None
    if fixed is None and trailing is None:
        raise HTTPException(status_code=422, detail=f"資料不足計算 ATR（至少需 {period + 1} 日）")
    return AtrStopView(
        stock_id=stock_id,
        multiplier=multiplier,
        period=period,
        fixed=AtrFixed(**fixed) if fixed else None,
        trailing=AtrTrailing(**trailing) if trailing else None,
    )
