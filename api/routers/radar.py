"""/api/radar/* — 雷達掃描。讀 signal_history 當天快照；snapshot 比 daily_price 舊時自動補跑。"""
from __future__ import annotations

import csv
import io
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response

from api.deps import get_db
from api.schemas.stock import RadarHit, RadarHitsPage, RadarStrategy
from app.data.db import Database
from app.export import excel as excel_export
from app.scoring.radar import STRATEGIES
from app.scoring.radar_queries import latest_as_of, query_radar_hits, query_radar_hits_page
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


@router.get("/hits", response_model=RadarHitsPage)
def hits(
    strategy: str | None = None,
    market: list[str] = Query(default=["上市", "上櫃", "ETF"]),
    top: int = 0,
    page: int = 1,
    page_size: int = 50,
    db: Database = Depends(get_db),
) -> RadarHitsPage:
    """當日 signal_history 依策略 + 市場過濾 → composite 降序，分頁回傳。

    - `top`：使用者「顯示前 N 名」上限（0 = 全部）；分頁在此上限內進行。
    - `page` / `page_size`：1-based 頁碼、每頁筆數。
    - 回傳 `{rows, total}`：rows = 當前頁、total = 過濾後總筆數（給「共 N 檔」與分頁器）。
      只傳當前頁，省掉「撈全部回前端再 client slice」的傳輸成本。
    """
    ensure_fresh(db)
    rows, total = query_radar_hits_page(
        db, strategy=strategy, markets=set(market),
        top=top, page=max(1, page), page_size=max(1, page_size),
    )
    return RadarHitsPage(rows=[RadarHit(**h) for h in rows], total=total)


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


@router.get("/export.csv")
def export_csv(
    strategy: str | None = None,
    market: list[str] = Query(default=["上市", "上櫃", "ETF"]),
    top: int = 0,  # 預設不截斷，匯出時通常想拿全部
    db: Database = Depends(get_db),
) -> Response:
    """匯出當前過濾條件下的雷達命中為 .csv（含 BOM，Excel 開繁中不亂碼）。

    與 /export.xlsx 同欄位、同預設（top=0 → 全部），只差純文字格式。改成後端 href 下載後，
    前端命中表不必再把全部列內嵌進 client props（原本 client 端組 CSV 會把整包 hits 序列化進
    RSC payload）。
    """
    ensure_fresh(db)
    hits_data = query_radar_hits(
        db, strategy=strategy, markets=set(market), limit=top if top > 0 else None,
    )
    headers = ["代號", "名稱", "市場", "收盤", "短期", "中期", "長期", "綜合", "建議", "VR-MACD", "命中策略"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for h in hits_data:
        writer.writerow([
            h.get("stock_id"), h.get("stock_name"), h.get("market"),
            h.get("close"), h.get("short"), h.get("mid"), h.get("long"), h.get("composite"),
            h.get("recommendation"), h.get("vr_macd"), h.get("strategies"),
        ])
    # BOM 讓 Excel 認 UTF-8；csv 模組預設行尾 \r\n（對齊原本前端產出的格式）
    payload = ("﻿" + buf.getvalue()).encode("utf-8")

    today = date.today().isoformat().replace("-", "")
    filename_full = f"radar_{strategy or 'all'}_{today}.csv"
    filename_ascii = f"radar_{today}.csv"
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename_ascii}"; '
                f"filename*=UTF-8''{quote(filename_full)}"
            ),
        },
    )
