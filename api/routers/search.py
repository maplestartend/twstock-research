"""/api/search/* — 跨頁面快搜（Cmd+K）。

只查 stock_info（含代號/名稱模糊比對），不觸發任何重算。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_db
from api.schemas.common import CamelModel
from app import watchlist as wl_mod
from app.data.db import Database
from app.data.market_type import classify_market

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchHit(CamelModel):
    stock_id: str
    stock_name: str
    market: str | None = None
    industry: str | None = None
    in_watchlist: bool = False


@router.get("/stocks", response_model=list[SearchHit])
def search_stocks(
    q: str = "",
    limit: int = 12,
    db: Database = Depends(get_db),
) -> list[SearchHit]:
    """模糊搜尋股票代號/名稱。
    規則：
    - 空字串 → 回自選股前 N 檔（最近瀏覽的常用入口）
    - 純數字（1~6 碼）→ 代號前綴比對優先，其次代號子字串
    - 含中文/英文 → 名稱模糊比對
    - 同時含數字+文字 → 代號 OR 名稱
    自選股置頂（in_watchlist=True 排第一），其次是代號嚴格匹配。
    """
    q = (q or "").strip()
    limit = max(1, min(50, limit))
    wl_set = set(wl_mod.load().keys())

    if not q:
        # 空字串：回自選股代號/名稱（推薦入口）
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT stock_id, stock_name, industry_category, type FROM stock_info "
                "WHERE stock_id IN (" + ",".join("?" * len(wl_set)) + ") LIMIT ?"
                if wl_set else
                "SELECT stock_id, stock_name, industry_category, type FROM stock_info "
                "WHERE stock_id IN (SELECT stock_id FROM signal_history "
                "                  WHERE as_of=(SELECT MAX(as_of) FROM signal_history) "
                "                  ORDER BY composite DESC LIMIT ?) ",
                (*wl_set, limit) if wl_set else (limit,),
            ).fetchall()
        return [
            SearchHit(
                stock_id=r["stock_id"],
                stock_name=r["stock_name"] or r["stock_id"],
                market=classify_market(r["stock_id"], r["type"]),
                industry=r["industry_category"],
                in_watchlist=r["stock_id"] in wl_set,
            )
            for r in rows
        ]

    is_numeric = q.isdigit()
    with db.connect() as conn:
        if is_numeric:
            # 代號前綴 → 代號子字串 → 名稱（罕見但便利）
            rows = conn.execute(
                "SELECT stock_id, stock_name, industry_category, type, "
                "       CASE "
                "         WHEN stock_id = ? THEN 0 "
                "         WHEN stock_id LIKE ? THEN 1 "
                "         WHEN stock_id LIKE ? THEN 2 "
                "         ELSE 3 END AS rank "
                "FROM stock_info "
                "WHERE stock_id LIKE ? OR stock_name LIKE ? "
                "ORDER BY rank, stock_id LIMIT ?",
                (q, f"{q}%", f"%{q}%", f"%{q}%", f"%{q}%", limit * 2),
            ).fetchall()
        else:
            # 名稱模糊（前綴優先）
            rows = conn.execute(
                "SELECT stock_id, stock_name, industry_category, type, "
                "       CASE WHEN stock_name LIKE ? THEN 0 ELSE 1 END AS rank "
                "FROM stock_info "
                "WHERE stock_name LIKE ? OR stock_id LIKE ? "
                "ORDER BY rank, stock_id LIMIT ?",
                (f"{q}%", f"%{q}%", f"%{q}%", limit * 2),
            ).fetchall()

    out: list[SearchHit] = []
    for r in rows:
        out.append(SearchHit(
            stock_id=r["stock_id"],
            stock_name=r["stock_name"] or r["stock_id"],
            market=classify_market(r["stock_id"], r["type"]),
            industry=r["industry_category"],
            in_watchlist=r["stock_id"] in wl_set,
        ))
    # 自選股置頂
    out.sort(key=lambda h: (not h.in_watchlist, h.stock_id))
    return out[:limit]
