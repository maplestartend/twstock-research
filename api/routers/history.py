"""/api/history/* — 歷史追蹤。回看某個快照日的雷達命中，比到現在的表現。"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from api.common import make_placeholders, safe_float as _sf
from api.deps import get_db
from api.schemas.stock import HistoryPerfRow, HistoryPerfSummary, RadarStrategy
from app.data.db import Database
from app.data.market_type import classify_market
from app.scoring import history as sh
from app.scoring.radar import STRATEGIES

router = APIRouter(prefix="/api/history", tags=["history"])

logger = logging.getLogger(__name__)


@router.get("/dates", response_model=list[str])
def dates(db: Database = Depends(get_db)) -> list[str]:
    return sh.available_dates(db)


@router.get("/strategies", response_model=list[RadarStrategy])
def strategies_for_date(
    as_of: str,
    market: list[str] = Query(default=["上市", "上櫃", "ETF"]),
    db: Database = Depends(get_db),
) -> list[RadarStrategy]:
    """指定 as_of 各策略命中數。可指定 market filter（個股 tab 排除 ETF）。
    當日命中數=0 的策略由前端決定要不要過濾。"""
    market_set = set(market)
    out: list[RadarStrategy] = []
    with db.connect() as conn:
        # 先抓當日所有命中行 + type，本端依 market 過濾再算每策略 count
        rows = conn.execute(
            "SELECT s.stock_id, s.strategies, i.type "
            "FROM signal_history s "
            "LEFT JOIN stock_info i ON i.stock_id = s.stock_id "
            "WHERE s.as_of=? AND s.strategies IS NOT NULL AND s.strategies != '' ",
            (as_of,),
        ).fetchall()
        # 過濾 market
        rows = [r for r in rows if classify_market(r["stock_id"], r["type"]) in market_set]
        for name, strat in STRATEGIES.items():
            count = sum(1 for r in rows if name in (r["strategies"] or ""))
            out.append(RadarStrategy(
                name=name, description=strat.description, hit_count=count,
                stocks_only=strat.stocks_only,
            ))
    return out


_HISTORY_PERF_MAX_ROWS = 500


@router.get("/performance", response_model=HistoryPerfSummary)
def performance(
    as_of: str,
    strategy: str | None = None,
    top: int = Query(0, ge=0, le=_HISTORY_PERF_MAX_ROWS),  # 0 = 自動套用 hard cap (500)
    market: list[str] = Query(default=["上市", "上櫃", "ETF"]),
    db: Database = Depends(get_db),
) -> HistoryPerfSummary:
    """歷史追蹤 performance。
    - top=0 預設套用 hard cap (500 列)，避免一次回傳上千列拖慢前端
    - 排序：composite 降序（None 墊底）
    - 統計（勝率 / 平均漲幅）由完整集合計算，不受截斷影響
    - `summary.truncated`：被 hard cap 截掉時為 True，UI 應提示使用者用 strategy filter 縮小範圍
    - market filter：個股 (上市/上櫃) / ETF 分流
    """
    perf = sh.track_performance(db, as_of, strategy)

    # 若沒指定策略，排除「當日沒命中任一策略」的股票
    if not strategy:
        snap = sh.load_snapshot(db, as_of)
        if not snap.empty:
            hit_ids = set(snap[snap["strategies"].fillna("") != ""]["stock_id"])
            if not perf.empty:
                perf = perf[perf["stock_id"].isin(hit_ids)].reset_index(drop=True)

    # market filter（用 stock_info.type）
    if not perf.empty:
        sids = perf["stock_id"].tolist()
        with db.connect() as conn:
            type_rows = conn.execute(
                f"SELECT stock_id, type FROM stock_info WHERE stock_id IN ({make_placeholders(len(sids))})",
                sids,
            ).fetchall()
        type_map = {r["stock_id"]: r["type"] for r in type_rows}
        market_set = set(market)
        perf = perf[perf["stock_id"].apply(
            lambda s: classify_market(s, type_map.get(s)) in market_set
        )].reset_index(drop=True)

    if perf.empty:
        raise HTTPException(status_code=404, detail="當日無命中資料")

    latest_date = perf["latest_date"].max() if "latest_date" in perf.columns else None
    days = 0
    if latest_date:
        try:
            days = (datetime.fromisoformat(str(latest_date)[:10]).date()
                    - datetime.fromisoformat(as_of).date()).days
        except Exception as e:
            logger.debug("history days 計算失敗（latest_date=%s as_of=%s）: %s", latest_date, as_of, e)
            days = 0

    # 統計（勝率 / 平均漲幅）一律由完整集合計算，不受截斷影響
    all_changes = [_sf(v) for v in perf["change_pct"].tolist()]
    all_changes = [c for c in all_changes if c is not None]
    wins = sum(1 for c in all_changes if c > 0)
    losses = sum(1 for c in all_changes if c < 0)
    avg = sum(all_changes) / len(all_changes) if all_changes else None
    total = len(perf)

    # 截斷：top=0 套 hard cap，否則用 caller 指定的 top
    effective_top = top if top > 0 else _HISTORY_PERF_MAX_ROWS
    truncated = total > effective_top
    perf = perf.head(effective_top).reset_index(drop=True)

    rows: list[HistoryPerfRow] = []
    for _, r in perf.iterrows():
        rows.append(HistoryPerfRow(
            stock_id=str(r["stock_id"]),
            stock_name=str(r["stock_name"]) if r.get("stock_name") else str(r["stock_id"]),
            snapshot_close=_sf(r.get("close")),
            latest_close=_sf(r.get("latest_close")),
            change_pct=_sf(r.get("change_pct")),
            short=_sf(r.get("short")),
            mid=_sf(r.get("mid")),
            long=_sf(r.get("long")),
            composite=_sf(r.get("composite")),
            recommendation=r.get("recommendation"),
            strategies=r.get("strategies"),
            latest_date=str(r["latest_date"])[:10] if r.get("latest_date") else None,
        ))
    return HistoryPerfSummary(
        as_of=as_of,
        latest_date=str(latest_date)[:10] if latest_date else None,
        days_elapsed=days,
        hit_count=total,
        win_count=wins,
        loss_count=losses,
        win_rate=(wins / total) if total else None,
        avg_change_pct=avg,
        rows=rows,
        truncated=truncated,
    )
