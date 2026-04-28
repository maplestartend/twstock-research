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
    IntradayQuoteView,
    NarrativeView,
    OHLCV,
    ScoreHistoryPoint,
    ScoreParts,
    StockMeta,
    StockPriceBundle,
    StockScoreView,
)
from app import risk as risk_mod
from app.data import intraday as intraday_mod
from app.data.db import Database
from app.indicators import technical as tech
from app.narrative import (
    NarrativeNotAvailable,
    generate_stock_narrative,
    is_available as narrative_is_available,
)
from app.narrative.stock_narrative import KIND_STOCK_OVERVIEW
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
    # 多載 90 天讓 ma60/bb 等指標的 lookback 不受裁切影響，最終只回傳 days 筆。
    from app.data.clock import taipei_today
    start = (taipei_today() - timedelta(days=days + 90)).isoformat()
    df = db.load_daily_price(stock_id, start=start)
    if df.empty:
        raise HTTPException(status_code=404, detail="no data")
    df = tech.enrich(df)
    df = df.tail(days).reset_index(drop=True)

    # 向量化序列化：避免 N 次 row-by-row 建立 dict / Pydantic instance
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    ohlcv = [
        OHLCV(
            date=row["date"],
            open=_safe_float(row.get("open")) or 0.0,
            high=_safe_float(row.get("high")) or 0.0,
            low=_safe_float(row.get("low")) or 0.0,
            close=_safe_float(row.get("close")) or 0.0,
            volume=_safe_float(row.get("volume")),
        )
        for row in df.to_dict("records")
    ]
    indicators = [
        IndicatorPoint(
            date=row["date"],
            ma5=_safe_float(row.get("ma5")),
            ma20=_safe_float(row.get("ma20")),
            ma60=_safe_float(row.get("ma60")),
            k9=_safe_float(row.get("k9")),
            d9=_safe_float(row.get("d9")),
            rsi14=_safe_float(row.get("rsi14")),
            bb_upper=_safe_float(row.get("bb_upper")),
            bb_lower=_safe_float(row.get("bb_lower")),
        )
        for row in df.to_dict("records")
    ]
    return StockPriceBundle(stock_id=stock_id, ohlcv=ohlcv, indicators=indicators)


@router.get("/{stock_id}/score", response_model=StockScoreView)
def score(
    stock_id: str,
    live: int = 0,
    override_price: float | None = None,
    db: Database = Depends(get_db),
) -> StockScoreView:
    """個股短/中/長期評分。

    - `live=1`：抓 mis 即時報價當作最新一筆 close，重算技術面 → 短/中分數反映盤中實況。
      盤後 / 興櫃 / mis 失敗 → fallback 走收盤分數（不報錯，前端透過 `liveUsed=false` 得知）。
    - `override_price=X`：what-if 模式，把最新 close 換成 X 重算。X 必須 > 0；同時帶 live=1 時
      以 override_price 為準（手動輸入優先於即時報價）。
    """
    with db.connect() as conn:
        r = conn.execute(
            "SELECT stock_name, type FROM stock_info WHERE stock_id=?",
            (stock_id,),
        ).fetchone()
    name = r["stock_name"] if r and r["stock_name"] else stock_id
    market_type = r["type"] if r else None

    live_price: float | None = None
    if override_price is not None and override_price > 0:
        live_price = float(override_price)
    elif live:
        q = intraday_mod.fetch_quote(stock_id, market_type)
        if q is not None and q.is_live and q.price > 0:
            live_price = q.price

    s = score_stock(db, stock_id, name, live_price=live_price)
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
        live_price_used=bool(s.signals.get("live_price_used", False)),
        live_price=_safe_float(s.signals.get("live_price")),
        recommendation=str(s.signals.get("recommendation", "")),
        entry=list(s.signals.get("entry") or []),
        stop_loss=list(s.signals.get("stop_loss") or []),
        take_profit=list(s.signals.get("take_profit") or []),
        warnings=list(s.signals.get("warnings") or []),
    )


@router.post("/{stock_id}/narrative", response_model=NarrativeView)
def narrative(
    stock_id: str,
    refresh: int = 0,
    db: Database = Depends(get_db),
) -> NarrativeView:
    """LLM 解讀個股分數（中文 3 段，散戶導向）。

    - 永久快取：同 stock_id + as_of (signal 快照日) 第二次呼叫直接讀 DB，不打 LLM。
    - 缺 ANTHROPIC_API_KEY → 503，前端應先打 /api/system/narrative-status 灰掉按鈕。
    - refresh=1：跳過快取強制重打 LLM（debug 用，會花錢）。
    - 用 POST 而非 GET：行為偏「執行動作 + 可能扣費」，用 POST 更貼語意，也避免被各種
      預先 fetch / link prefetch 意外觸發。
    """
    if not narrative_is_available():
        raise HTTPException(
            status_code=503,
            detail="LLM 敘事功能未啟用：請設定 ANTHROPIC_API_KEY 環境變數並安裝 anthropic 套件",
        )

    # 先跑 score_stock，拿到當下的分數結構（與 /score endpoint 同一份邏輯）
    with db.connect() as conn:
        r = conn.execute(
            "SELECT stock_name FROM stock_info WHERE stock_id=?", (stock_id,)
        ).fetchone()
    name = r["stock_name"] if r and r["stock_name"] else stock_id

    s = score_stock(db, stock_id, name)
    if s is None:
        raise HTTPException(status_code=422, detail="資料不足無法評分（至少需 60 個交易日）")

    # 把 StockScore dataclass 攤成 dict 餵給 prompts.build_user_prompt
    score_view = {
        "stock_id": s.stock_id,
        "stock_name": s.stock_name,
        "as_of": s.as_of,
        "close": s.close,
        "short": {
            "total": _safe_float(s.short.total),
            "completeness": s.short.completeness,
            "parts": {k: _safe_float(v) for k, v in s.short.parts.items()},
        },
        "mid": {
            "total": _safe_float(s.mid.total),
            "completeness": s.mid.completeness,
            "parts": {k: _safe_float(v) for k, v in s.mid.parts.items()},
        },
        "long": {
            "total": _safe_float(s.long.total),
            "completeness": s.long.completeness,
            "parts": {k: _safe_float(v) for k, v in s.long.parts.items()},
        },
        "composite_score": _safe_float(s.signals.get("composite_score")),
        "is_stale": bool(s.is_stale),
        "is_pending": bool(s.is_pending),
        "recommendation": str(s.signals.get("recommendation", "")),
        "entry": list(s.signals.get("entry") or []),
        "warnings": list(s.signals.get("warnings") or []),
    }
    chip_snap = s.signals.get("chip_snapshot") or {}
    fund_snap = s.signals.get("fundamental_snapshot") or {}

    try:
        result = generate_stock_narrative(
            db,
            score_view,
            chip_snap,
            fund_snap,
            force_refresh=bool(refresh),
        )
    except NarrativeNotAvailable as e:
        # 理論上 narrative_is_available() 已過濾；保險起見再補一層
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        # anthropic.RateLimitError / APIConnectionError / APIStatusError 等
        # 不暴露底層 stack，但給足夠訊息讓前端顯示
        raise HTTPException(status_code=502, detail=f"LLM 敘事生成失敗：{type(e).__name__}: {e}")

    return NarrativeView(
        stock_id=s.stock_id,
        as_of=s.as_of,
        kind=KIND_STOCK_OVERVIEW,
        narrative=result.narrative,
        model=result.model,
        cached=result.cached,
    )


@router.get("/{stock_id}/intraday", response_model=IntradayQuoteView)
def intraday(stock_id: str, db: Database = Depends(get_db)) -> IntradayQuoteView:
    """盤中即時報價（TWSE mis）。

    - 30 秒記憶體快取避免 hammer 外部 API
    - 興櫃 / mis 失敗 / 抓不到該股 → 422，前端可隱藏「即時」按鈕並 fallback 收盤分數
    - 盤後或休市時 mis 仍會回前一日收盤（`isLive=false`），UI 應該標示「非盤中」
    """
    with db.connect() as conn:
        r = conn.execute(
            "SELECT type FROM stock_info WHERE stock_id=?", (stock_id,)
        ).fetchone()
    market_type = r["type"] if r else None
    q = intraday_mod.fetch_quote(stock_id, market_type)
    if q is None:
        raise HTTPException(status_code=422, detail="即時報價無法取得（興櫃 / 休市 / 上游異常）")
    chg_pct: float | None = None
    if q.prev_close and q.prev_close > 0 and q.price is not None:
        chg_pct = (q.price - q.prev_close) / q.prev_close
    return IntradayQuoteView(
        stock_id=stock_id,
        price=q.price,
        prev_close=q.prev_close,
        open=q.open,
        high=q.high,
        low=q.low,
        bid1=q.bid1,
        ask1=q.ask1,
        volume_lots=q.volume_lots,
        quote_time=q.quote_time,
        is_live=q.is_live,
        quote_source=q.quote_source,
        change_pct=chg_pct,
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


class AtrTakeProfit(CamelModel):
    """Chandelier-style 動態停利。需 entry_date + entry_price 才有值。"""
    take_profit_price: float
    atr: float
    peak_since_entry: float
    latest_close: float
    days_held: int
    unrealized_pnl_pct: float
    armed: bool                # 浮盈 ≥ arm_pnl AND 持有 ≥ arm_days
    triggered: bool            # armed AND latest_close ≤ take_profit_price
    multiplier: float
    arm_pnl_threshold: float
    arm_days_threshold: int


class AtrStopView(CamelModel):
    """ATR 進出場建議。

    - fixed 段：永遠回傳（用 entry_price 或最後收盤當參考）
    - trailing 段：需 entry_date
    - take_profit 段：需 entry_date + entry_price（armed 條件需算浮盈）
    """
    stock_id: str
    multiplier: float
    period: int
    fixed: AtrFixed | None = None
    trailing: AtrTrailing | None = None
    take_profit: AtrTakeProfit | None = None


@router.get("/{stock_id}/atr-stop", response_model=AtrStopView)
def atr_stop(
    stock_id: str,
    entry_price: float | None = None,
    entry_date: str | None = None,
    multiplier: float = 2.0,
    period: int = 14,
    tp_multiplier: float = 3.0,
    tp_arm_pnl: float = 0.08,
    tp_arm_days: int = 5,
    db: Database = Depends(get_db),
) -> AtrStopView:
    """ATR-based 進出場建議（停損 + 動態停利）。

    - `entry_price` / `entry_date` 都不給：fixed 用最後收盤算、trailing/take_profit=None
    - 給 `entry_date`：trailing 段算「進場以來最高 − multiplier×ATR」
    - 給 `entry_date` + `entry_price`：take_profit 段算 Chandelier「進場以來最高 high − tp_multiplier×ATR」
        * armed 條件：浮盈 ≥ tp_arm_pnl AND 持有 ≥ tp_arm_days
    - 資料不足（< period+1 日）→ 422
    """
    df = db.load_daily_price(stock_id)
    if df.empty:
        raise HTTPException(status_code=404, detail="stock not found")
    fixed = risk_mod.atr_stop_loss(df, entry_price=entry_price, multiplier=multiplier, period=period)
    trailing = risk_mod.trailing_atr_stop(df, entry_date or "", multiplier=multiplier, period=period) if entry_date else None
    take_profit = (
        risk_mod.trailing_atr_take_profit(
            df, entry_date, entry_price=entry_price,
            multiplier=tp_multiplier, period=period,
            arm_pnl=tp_arm_pnl, arm_days=tp_arm_days,
        )
        if entry_date and entry_price
        else None
    )
    if fixed is None and trailing is None and take_profit is None:
        raise HTTPException(status_code=422, detail=f"資料不足計算 ATR（至少需 {period + 1} 日）")
    return AtrStopView(
        stock_id=stock_id,
        multiplier=multiplier,
        period=period,
        fixed=AtrFixed(**fixed) if fixed else None,
        trailing=AtrTrailing(**trailing) if trailing else None,
        take_profit=AtrTakeProfit(**take_profit) if take_profit else None,
    )
