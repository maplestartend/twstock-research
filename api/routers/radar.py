"""/api/radar/* — 雷達掃描。讀 signal_history 當天快照；snapshot 比 daily_price 舊時自動補跑。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from api.schemas.stock import RadarHit, RadarStrategy
from app.data.db import Database
from app.scoring.radar import STRATEGIES
from app.scoring.radar_queries import latest_as_of, query_radar_hits
from app.scoring.snapshot_freshness import ensure_fresh

router = APIRouter(prefix="/api/radar", tags=["radar"])


@router.get("/strategies", response_model=list[RadarStrategy])
def strategies(db: Database = Depends(get_db)) -> list[RadarStrategy]:
    """列出所有策略 + 當日命中數。

    舊版對每個策略各跑一次 `LIKE '%name%'` 全表掃描（N 次 query），改成單一 query 撈當日
    所有非空 strategies 字串後在 Python 端 split + count。STRATEGIES 約 7~10 條時整體
    從 N×掃表降到 1 次。
    """
    ensure_fresh(db)
    as_of = latest_as_of(db)
    counts: dict[str, int] = {}
    if as_of:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT strategies FROM signal_history WHERE as_of=? AND strategies != ''",
                (as_of,),
            ).fetchall()
        for r in rows:
            for token in (r["strategies"] or "").split(","):
                token = token.strip()
                if token:
                    counts[token] = counts.get(token, 0) + 1
    return [
        RadarStrategy(
            name=name,
            description=strat.description,
            hit_count=counts.get(name, 0),
            stocks_only=strat.stocks_only,
        )
        for name, strat in STRATEGIES.items()
    ]


@router.get("/hits", response_model=list[RadarHit])
def hits(
    strategy: str | None = None,
    market: list[str] = Query(default=["上市", "上櫃", "ETF"]),
    top: int = 50,
    db: Database = Depends(get_db),
) -> list[RadarHit]:
    """當日 signal_history 依策略 + 市場過濾 → composite 降序。
    `top=0` 視為「全部」（不截斷）。否則回傳 top 筆。"""
    ensure_fresh(db)
    hits_data = query_radar_hits(
        db, strategy=strategy, markets=set(market), limit=top if top > 0 else None,
    )
    return [RadarHit(**h) for h in hits_data]
