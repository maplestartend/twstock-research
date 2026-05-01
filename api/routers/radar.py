"""/api/radar/* — 雷達掃描。讀 signal_history 當天快照；snapshot 比 daily_price 舊時自動補跑。"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response

from api.deps import get_db
from api.schemas.stock import RadarHit, RadarStrategy
from app.data.db import Database
from app.export import excel as excel_export
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


@router.get("/export.xlsx")
def export_xlsx(
    strategy: str | None = None,
    market: list[str] = Query(default=["上市", "上櫃", "ETF"]),
    top: int = 0,  # 預設不截斷，匯出時通常想拿全部
    db: Database = Depends(get_db),
) -> Response:
    """匯出當前過濾條件下的雷達命中為 .xlsx。

    與 /hits 共用 query_radar_hits，所以排序 / 過濾邏輯一致；不同處只在輸出格式
    與「預設不截斷」（top=0 → 全部，避免使用者匯出時還要手動加參數）。
    """
    ensure_fresh(db)
    hits_data = query_radar_hits(
        db, strategy=strategy, markets=set(market), limit=top if top > 0 else None,
    )
    # camelCase 化以對齊 RadarHit response model（excel 模組讀 stockId/stockName/...）
    rows = [RadarHit(**h).model_dump(by_alias=True) for h in hits_data]
    payload = excel_export.radar_hits_workbook(
        rows,
        strategy=strategy or "全部",
        market_label="／".join(market),
        as_of=latest_as_of(db),
    )

    today = date.today().isoformat().replace("-", "")
    # 中文策略名 → 走 RFC 5987 filename*=UTF-8''… 編碼，瀏覽器（Chrome/Edge/Safari）都支援；
    # 同時保留 ASCII fallback filename 給舊 client 不要直接炸（latin-1 嚴格 encode）。
    filename_full = f"radar_{strategy or 'all'}_{today}.xlsx"
    filename_ascii = f"radar_{today}.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename_ascii}"; '
                f"filename*=UTF-8''{quote(filename_full)}"
            ),
        },
    )
