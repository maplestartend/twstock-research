"""/api/portfolio/* — 持股總覽 + 損益 + 風險。"""
from __future__ import annotations

import contextvars
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.common import get_stock_name as _stock_name, make_placeholders, safe_float as _safe
from api.deps import get_db
from api.schemas.portfolio import (
    HoldingRow,
    PortfolioSummary,
    RealizedPnlRow,
    RealizedPnlSummary,
    RiskAlert,
    TradeRow,
)
import pandas as pd

from app import portfolio as pf
from app import risk as risk_mod
from app import watchlist as wl_mod
from app.data.db import Database
from app.risk import (
    atr_stop_loss,
    concentration_warnings,
    enhanced_risk_signals,
    trailing_atr_stop,
)
from app.scoring.snapshot_freshness import ensure_fresh

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeCreateBody(BaseModel):
    """新增交易 request body。fee/tax 留空 → 後端依券商規則算
    （手續費 0.1425%×券商折扣；賣方證交稅依代號：一般股 0.3%、股票型 ETF 0.1%、債券 ETF 0%）。"""
    trade_date: str = Field(..., description="YYYY-MM-DD")
    stock_id: str
    action: Literal["BUY", "SELL"]
    shares: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fee: float | None = Field(default=None, ge=0)
    tax: float | None = Field(default=None, ge=0)
    note: str | None = None


class PositionSuggestBody(BaseModel):
    """部位試算：固定比例風險法。stop_price < entry_price，否則回 422。"""
    capital: float = Field(..., gt=0, description="帳戶本金")
    entry_price: float = Field(..., gt=0)
    stop_price: float = Field(..., gt=0)
    risk_per_trade: float = Field(default=0.02, gt=0, le=0.5, description="單筆最大虧損比例（預設 2%）")
    lot_size: int = Field(default=1000, ge=1)


class PositionSuggestResponse(BaseModel):
    max_shares: int
    max_lots: float
    max_position_value: float
    risk_amount: float
    risk_per_share: float


def _latest_scores_batch(conn, sids: list[str]) -> dict[str, dict]:
    """批次撈每檔最新一筆 signal_history（取每檔的 MAX(as_of)）。

    走 INNER JOIN + GROUP BY 子查詢比對「latest per stock_id」，比 N 次 ORDER BY ... LIMIT 1
    少 N-1 次 round-trip 與每次連線的 PRAGMA 開銷。
    """
    if not sids:
        return {}
    ph = make_placeholders(len(sids))
    rows = conn.execute(
        f"""SELECT s.stock_id, s.short, s.mid, s.long, s.composite
        FROM signal_history s
        INNER JOIN (
            SELECT stock_id, MAX(as_of) AS as_of
            FROM signal_history
            WHERE stock_id IN ({ph})
            GROUP BY stock_id
        ) latest ON s.stock_id = latest.stock_id AND s.as_of = latest.as_of""",
        sids,
    ).fetchall()
    return {r["stock_id"]: dict(r) for r in rows}


def _atr_for_holding(
    price_df: pd.DataFrame,
    entry_date: str | None,
    avg_cost: float,
    latest_close: float | None,
) -> tuple[float | None, float | None, str | None, bool]:
    """ATR-based 停損：有進場日用 trailing（high-watermark − N×ATR），否則 fixed（avg_cost − N×ATR）。

    回 (stop_price, distance_pct, kind, below_stop)。資料不足或 latest 未知 → 全 None / False。
    distance_pct = (latest_close - stop) / latest_close，正值代表距停損還有空間。
    """
    if price_df is None or price_df.empty or latest_close is None:
        return None, None, None, False
    if entry_date:
        info = trailing_atr_stop(price_df, entry_date, multiplier=2.0)
        if info is not None:
            stop = float(info["stop_price"])
            dist = (latest_close - stop) / latest_close if latest_close > 0 else None
            return stop, dist, "trailing", bool(info.get("below_stop", False))
    info = atr_stop_loss(price_df, entry_price=avg_cost, multiplier=2.0)
    if info is None:
        return None, None, None, False
    stop = float(info["stop_price"])
    dist = (latest_close - stop) / latest_close if latest_close > 0 else None
    return stop, dist, "fixed", latest_close < stop


def _compute_holdings(db: Database) -> list[HoldingRow]:
    """共用主體：依 trade_log 重建 holdings、撈 price/score、組成 HoldingRow 列表。

    `holdings()`、`summary()`、`risk_alerts()` 三個 endpoint 都需要同一份結果，
    透過 request-scoped cache（`db._req_holdings_cache`）避免單一請求內被重算多次。
    上層每個 router function 進入時呼叫 `_holdings_cached(db)` 並在出去前清掉。

    效能：N 檔持股原本需要 ~3N 次 db.connect()（price + score + name 各一）。改為單一
    連線內批次抓 score/name，price 仍逐檔讀但共用 connection 省 PRAGMA 開銷。
    """
    ensure_fresh(db)
    # 一次載 watchlist set（檔案 IO，避免每檔重讀 yaml）
    watchlist_ids = set(wl_mod.load().keys())
    holdings_list = list(pf.list_holdings(db))
    if not holdings_list:
        return []

    sids = [h.stock_id for h in holdings_list]

    # 一個連線跑完所有 DB 讀取（score 批次、name 批次、price 逐檔但共用 conn）
    with db.connect() as conn:
        scores_by_sid = _latest_scores_batch(conn, sids)

        ph = make_placeholders(len(sids))
        name_rows = conn.execute(
            f"SELECT stock_id, stock_name FROM stock_info WHERE stock_id IN ({ph})",
            sids,
        ).fetchall()
        names_by_sid = {r["stock_id"]: (r["stock_name"] or r["stock_id"]) for r in name_rows}

        price_dfs: dict[str, pd.DataFrame] = {}
        for sid in sids:
            df = pd.read_sql_query(
                "SELECT * FROM daily_price WHERE stock_id = ? ORDER BY date",
                conn, params=[sid],
            )
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
            price_dfs[sid] = df

    out: list[HoldingRow] = []
    for h in holdings_list:
        price_df = price_dfs.get(h.stock_id, pd.DataFrame())
        close: float | None = float(price_df["close"].iloc[-1]) if not price_df.empty else None
        prev: float | None = float(price_df["close"].iloc[-2]) if len(price_df) >= 2 else None
        today_pct = None
        if close is not None and prev is not None and prev != 0:
            today_pct = (close - prev) / prev
        mv = h.market_value(close) if close is not None else None
        pnl = h.unrealized_pnl(close) if close is not None else None
        pnl_pct = h.unrealized_pnl_pct(close) if close is not None else None
        net_pnl = h.net_unrealized_pnl(close) if close is not None else None
        net_pnl_pct = h.net_unrealized_pnl_pct(close) if close is not None else None
        sell_costs = h.estimated_sell_costs(close) if close is not None else None
        scores = scores_by_sid.get(h.stock_id, {})
        short = scores.get("short")
        warnings: list[str] = []
        if close is not None:
            try:
                warnings = enhanced_risk_signals(
                    db, h.stock_id, h.avg_cost, h.entry_date,
                    close, float(short) if short is not None else 0.0,
                    price_df=price_df,
                )
            except Exception:
                warnings = []
        atr_stop, atr_dist, atr_kind, atr_below = _atr_for_holding(
            price_df, h.entry_date, h.avg_cost, close,
        )
        out.append(HoldingRow(
            stock_id=h.stock_id,
            stock_name=names_by_sid.get(h.stock_id, h.stock_id),
            shares=h.shares,
            avg_cost=h.avg_cost,
            entry_date=h.entry_date,
            price=close,
            prev_close=prev,
            today_pct=today_pct,
            market_value=mv,
            unrealized_pnl=pnl,
            unrealized_pnl_pct=pnl_pct,
            net_unrealized_pnl=net_pnl,
            net_unrealized_pnl_pct=net_pnl_pct,
            estimated_sell_costs=sell_costs,
            short_score=_safe(scores.get("short")),
            mid_score=_safe(scores.get("mid")),
            long_score=_safe(scores.get("long")),
            composite_score=_safe(scores.get("composite")),
            warnings=warnings,
            atr_stop=atr_stop,
            atr_distance_pct=atr_dist,
            atr_kind=atr_kind,
            atr_below_stop=atr_below,
            in_watchlist=h.stock_id in watchlist_ids,
        ))
    return out


# Request-scoped cache via ContextVar：原本掛在 Database singleton 屬性上的版本是
# process-global 的，FastAPI 多 thread 並發時會交叉污染（Stage 1 之後首頁 Suspense 會
# 並行打 holdings/summary/risk-alerts，剛好踩到這個 race）。ContextVar 對每條 request
# 都是獨立 token，跨 thread 不會互相看到。每個 endpoint handler 進入時用 `_holdings_cached`
# 拿（命中或現算），handler 結束 ContextVar 自動失效（不需手動 clear）。
_holdings_ctx: contextvars.ContextVar[list[HoldingRow] | None] = contextvars.ContextVar(
    "_holdings_ctx", default=None,
)


def _holdings_cached(db: Database) -> list[HoldingRow]:
    cached = _holdings_ctx.get()
    if cached is not None:
        return cached
    rows = _compute_holdings(db)
    _holdings_ctx.set(rows)
    return rows


@router.get("/holdings", response_model=list[HoldingRow])
def holdings(db: Database = Depends(get_db)) -> list[HoldingRow]:
    return _holdings_cached(db)


@router.get("/summary", response_model=PortfolioSummary)
def summary(db: Database = Depends(get_db)) -> PortfolioSummary:
    rows = _holdings_cached(db)
    total_mv = sum((r.market_value or 0.0) for r in rows)
    total_cost = sum(r.shares * r.avg_cost for r in rows)
    unrealized = total_mv - total_cost
    unrealized_pct = (unrealized / total_cost) if total_cost > 0 else None
    sell_costs = sum((r.estimated_sell_costs or 0.0) for r in rows)
    net_unrealized = unrealized - sell_costs
    net_unrealized_pct = (net_unrealized / total_cost) if total_cost > 0 else None
    today_pnl = sum(
        r.shares * ((r.price or 0.0) - (r.prev_close or 0.0))
        for r in rows if r.price is not None and r.prev_close is not None
    )
    today_pct = (today_pnl / total_mv) if total_mv > 0 else None
    return PortfolioSummary(
        total_market_value=total_mv,
        total_cost=total_cost,
        unrealized_pnl=unrealized,
        unrealized_pnl_pct=unrealized_pct,
        net_unrealized_pnl=net_unrealized,
        net_unrealized_pnl_pct=net_unrealized_pct,
        estimated_sell_costs=sell_costs,
        today_pnl=today_pnl,
        today_pnl_pct=today_pct,
        holding_count=len(rows),
    )


@router.get("/risk-alerts", response_model=list[RiskAlert])
def risk_alerts(db: Database = Depends(get_db)) -> list[RiskAlert]:
    alerts: list[RiskAlert] = []
    rows = _holdings_cached(db)
    for r in rows:
        for msg in r.warnings:
            alerts.append(RiskAlert(
                severity="warning",
                title=f"{r.stock_id} {r.stock_name}",
                description=msg,
                stock_id=r.stock_id,
            ))
    try:
        holdings_mv = {r.stock_id: (r.market_value or 0.0) for r in rows if r.market_value}
        concent = concentration_warnings(db, holdings_mv)
    except Exception:
        concent = []
    for msg in concent or []:
        alerts.append(RiskAlert(severity="info", title="集中度提醒", description=msg))
    return alerts


@router.get("/trades", response_model=list[TradeRow])
def trades(
    limit: int = Query(100, ge=1, le=500),
    stock_id: str | None = None,
    db: Database = Depends(get_db),
) -> list[TradeRow]:
    """交易紀錄（trade_log）。最新優先，預設取 100 筆，最多 500（防止前端誤帶大數字）。
    可帶 `stock_id` 只看單檔。"""
    df = pf.load_trades(db, stock_id=stock_id)
    if df.empty:
        return []
    df = df.head(limit)
    # 撈名稱
    names: dict[str, str] = {}
    if not df.empty:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT stock_id, stock_name FROM stock_info "
                f"WHERE stock_id IN ({','.join('?' * df['stock_id'].nunique())})",
                list(df["stock_id"].unique()),
            ).fetchall()
        names = {r["stock_id"]: r["stock_name"] or r["stock_id"] for r in rows}

    out: list[TradeRow] = []
    for _, t in df.iterrows():
        out.append(TradeRow(
            id=int(t["id"]),
            trade_date=str(t["trade_date"]),
            stock_id=str(t["stock_id"]),
            stock_name=names.get(str(t["stock_id"])),
            action=str(t["action"]),
            shares=float(t["shares"]),
            price=float(t["price"]),
            fee=float(t["fee"]) if t["fee"] is not None else None,
            tax=float(t["tax"]) if t["tax"] is not None else None,
            note=str(t["note"]) if t["note"] else None,
        ))
    return out


@router.post("/trades", response_model=TradeRow, status_code=status.HTTP_201_CREATED)
def create_trade(body: TradeCreateBody, db: Database = Depends(get_db)) -> TradeRow:
    """新增一筆買/賣交易。fee/tax 為 None 時後端用 0.1425% 手續費自動算；
    證交稅依代號（一般股 0.3% / 股票型 ETF 0.1% / 債券 ETF 0%）。"""
    try:
        trade_id = pf.record_trade(
            db,
            trade_date=body.trade_date,
            stock_id=body.stock_id,
            action=body.action,
            shares=body.shares,
            price=body.price,
            fee=body.fee,
            tax=body.tax,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    with db.connect() as conn:
        r = conn.execute(
            "SELECT id, trade_date, stock_id, action, shares, price, fee, tax, note "
            "FROM trade_log WHERE id=?",
            (trade_id,),
        ).fetchone()
    name = _stock_name(db, body.stock_id)
    return TradeRow(
        id=int(r["id"]),
        trade_date=str(r["trade_date"]),
        stock_id=str(r["stock_id"]),
        stock_name=name,
        action=str(r["action"]),
        shares=float(r["shares"]),
        price=float(r["price"]),
        fee=float(r["fee"]) if r["fee"] is not None else None,
        tax=float(r["tax"]) if r["tax"] is not None else None,
        note=str(r["note"]) if r["note"] else None,
    )


@router.delete("/trades/{trade_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_trade(trade_id: int, db: Database = Depends(get_db)) -> None:
    """刪除指定交易並從 trade_log 重建該股 holdings。對不存在的 id 也回 204（idempotent）。"""
    pf.delete_trade(db, trade_id)


@router.post("/position-suggest", response_model=PositionSuggestResponse)
def position_suggest(body: PositionSuggestBody) -> PositionSuggestResponse:
    """買股前的張數試算。固定比例風險法：單筆最多虧 capital × risk_per_trade。

    - 失敗條件：entry/stop 非正數或 stop ≥ entry → 422
    - 回傳的 `max_lots` 是「張」（會自動向下取整對齊整張）
    """
    suggestion = risk_mod.suggest_position_size(
        capital=body.capital,
        entry_price=body.entry_price,
        stop_price=body.stop_price,
        risk_per_trade=body.risk_per_trade,
        lot_size=body.lot_size,
    )
    if suggestion is None:
        raise HTTPException(status_code=422, detail="stop_price 必須 < entry_price，且兩者皆 > 0")
    return PositionSuggestResponse(
        max_shares=suggestion.max_shares,
        max_lots=suggestion.max_shares / max(1, body.lot_size),
        max_position_value=suggestion.max_position_value,
        risk_amount=suggestion.risk_amount,
        risk_per_share=suggestion.risk_per_share,
    )


@router.get("/realized-pnl", response_model=RealizedPnlSummary)
def realized_pnl(
    stock_id: str | None = None,
    db: Database = Depends(get_db),
) -> RealizedPnlSummary:
    """FIFO 配對已實現損益。可帶 `stock_id` 只看單檔。"""
    df = pf.realized_pnl(db, stock_id=stock_id)
    if df.empty:
        return RealizedPnlSummary()

    names: dict[str, str] = {}
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT stock_id, stock_name FROM stock_info "
            f"WHERE stock_id IN ({','.join('?' * df['stock_id'].nunique())})",
            list(df["stock_id"].unique()),
        ).fetchall()
    names = {r["stock_id"]: r["stock_name"] or r["stock_id"] for r in rows}

    out_rows: list[RealizedPnlRow] = []
    for _, r in df.iterrows():
        out_rows.append(RealizedPnlRow(
            stock_id=str(r["stock_id"]),
            stock_name=names.get(str(r["stock_id"])),
            buy_date=str(r["buy_date"]),
            sell_date=str(r["sell_date"]),
            shares=float(r["shares"]),
            buy_price=float(r["buy_price"]),
            sell_price=float(r["sell_price"]),
            cost=float(r["cost"]),
            proceed=float(r["proceed"]),
            pnl=float(r["pnl"]),
            pnl_pct=float(r["pnl_pct"]) if r["pnl_pct"] is not None else None,
        ))

    total_pnl = sum(r.pnl for r in out_rows)
    wins = sum(1 for r in out_rows if r.pnl > 0)
    count = len(out_rows)
    return RealizedPnlSummary(
        total_pnl=total_pnl,
        pair_count=count,
        win_count=wins,
        win_rate=(wins / count) if count else None,
        rows=out_rows,
    )
