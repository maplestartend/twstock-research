"""/api/watchlist/* — 自選股 CRUD + 排行。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.common import make_placeholders
from api.deps import get_db
from api.schemas.common import CamelModel
from api.schemas.stock import WatchlistMover, WatchlistOverviewRow
from app import watchlist as wl_mod
from app.data.db import Database
from app.data.market_type import classify_market
from app.scoring.radar_queries import latest_as_of
from app.scoring.snapshot_freshness import ensure_fresh

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistEntry(CamelModel):
    """回應用 — CamelModel 會自動 serialize 成 camelCase (stockId / stockName)。"""
    stock_id: str
    stock_name: str
    tags: list[str] = []  # 自訂分組標籤；無 tag 的檔回 []


class WatchlistLookup(CamelModel):
    stock_id: str
    stock_name: str | None = None


class TagCount(CamelModel):
    """每個 tag + 對應的命中檔數，用於 filter chip 上的數字徽章。"""
    tag: str
    count: int


class TagsUpdateBody(BaseModel):
    """覆寫單檔的所有 tags。空 list = 清空。
    後端會做 trim + 去重（保序），UI 不必預先處理。"""
    tags: list[str]


@router.get("", response_model=list[WatchlistEntry])
def list_all() -> list[WatchlistEntry]:
    tags_map = wl_mod.load_tags()
    return [
        WatchlistEntry(stock_id=k, stock_name=v, tags=tags_map.get(k, []))
        for k, v in wl_mod.load().items()
    ]


@router.get("/tags", response_model=list[TagCount])
def list_tags() -> list[TagCount]:
    """所有出現過的 tag + 命中檔數，按命中數降序（同數字按字典序）。
    UI 用此 endpoint 渲染 filter chip 列。"""
    tags_map = wl_mod.load_tags()
    counts: dict[str, int] = {}
    for tags in tags_map.values():
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
    return [
        TagCount(tag=t, count=counts[t])
        for t in sorted(counts.keys(), key=lambda x: (-counts[x], x))
    ]


@router.put("/{stock_id}/tags", response_model=WatchlistEntry)
def update_tags(stock_id: str, body: TagsUpdateBody) -> WatchlistEntry:
    """覆寫單檔 tags。stock_id 不在自選清單 → 404。"""
    sid = stock_id.strip()
    ok = wl_mod.set_tags(sid, body.tags)
    if not ok:
        raise HTTPException(status_code=404, detail=f"「{sid}」不在自選清單，無法設定 tags")
    name = wl_mod.load().get(sid, sid)
    return WatchlistEntry(
        stock_id=sid, stock_name=name, tags=wl_mod.load_tags().get(sid, []),
    )


class AddBody(BaseModel):
    stock_id: str


class BulkAddBody(BaseModel):
    stock_ids: list[str]


class BulkRemoveBody(BaseModel):
    stock_ids: list[str]


@router.post("")
def add(body: AddBody, db: Database = Depends(get_db)) -> dict:
    """新增自選：名稱一律以 stock_info 為準。
    驗證：股票必須在 stock_info 或 daily_price 表中至少存在一筆資料才能加入，
    否則會寫入垃圾代號（如 9999）導致雷達 / 評分異常。"""
    sid = body.stock_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="代號為空")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT stock_name FROM stock_info WHERE stock_id=?", (sid,)
        ).fetchone()
        # 沒進過 stock_info 也容許，只要 daily_price 有資料（剛抓回來的新股）
        has_price = conn.execute(
            "SELECT 1 FROM daily_price WHERE stock_id=? LIMIT 1", (sid,)
        ).fetchone()
    if not row and not has_price:
        raise HTTPException(
            status_code=404,
            detail=f"找不到股票代號「{sid}」。請確認是否為合法的台股代號（4~6 碼），或先跑一次 market_update 抓資料。",
        )
    name = row["stock_name"] if row and row["stock_name"] else sid
    ok = wl_mod.add(sid, name)
    if not ok:
        raise HTTPException(status_code=409, detail=f"「{sid}」已在自選清單")
    return {"ok": True, "stockName": name}


@router.delete("/{stock_id}")
def remove(stock_id: str) -> dict:
    ok = wl_mod.remove(stock_id)
    if not ok:
        raise HTTPException(status_code=404, detail="不在自選清單")
    return {"ok": True}


@router.post("/bulk-add")
def bulk_add(body: BulkAddBody, db: Database = Depends(get_db)) -> dict:
    """批次新增：若輸入只含代號，會自動從 stock_info 帶出名稱。"""
    to_add: dict[str, str] = {}
    current = wl_mod.load()
    with db.connect() as conn:
        for sid in body.stock_ids:
            sid = sid.strip()
            if not sid or sid in current or sid in to_add:
                continue
            row = conn.execute(
                "SELECT stock_name FROM stock_info WHERE stock_id=?", (sid,)
            ).fetchone()
            to_add[sid] = row["stock_name"] if row and row["stock_name"] else sid
    added = wl_mod.add_many(to_add) if to_add else 0
    return {"added": added, "skipped": len(body.stock_ids) - added}


@router.post("/bulk-remove")
def bulk_remove(body: BulkRemoveBody) -> dict:
    removed = wl_mod.remove_many(body.stock_ids)
    return {"removed": removed}


@router.get("/lookup/{stock_id}", response_model=WatchlistLookup)
def lookup(stock_id: str, db: Database = Depends(get_db)) -> WatchlistLookup:
    """代號 → 名稱（供新增表單 auto-fill）。回傳 camelCase (stockName)。"""
    with db.connect() as conn:
        r = conn.execute(
            "SELECT stock_name FROM stock_info WHERE stock_id=?", (stock_id,)
        ).fetchone()
    return WatchlistLookup(
        stock_id=stock_id,
        stock_name=r["stock_name"] if r else None,
    )


@router.get("/movers", response_model=list[WatchlistMover])
def movers(
    top: int = 5,
    direction: str = "up",
    db: Database = Depends(get_db),
) -> list[WatchlistMover]:
    """自選股當日漲/跌幅排行。direction=up|down。"""
    stocks = wl_mod.load()
    if not stocks:
        return []
    ensure_fresh(db)

    placeholders = make_placeholders(len(stocks))
    sids = list(stocks.keys())
    out: list[WatchlistMover] = []
    with db.connect() as conn:
        # 批次抓每檔最新 2 日價格 → 記憶體分群。
        # 用 ROW_NUMBER 只取每檔 top-2（語意同「全歷史排序後取前兩列」），
        # 避免把每檔上千列歷史全撈回 Python 端只用 rows[0]/rows[1]。
        price_rows = conn.execute(
            f"WITH ranked AS ("
            f"  SELECT stock_id, date, close, "
            f"         ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn "
            f"  FROM daily_price WHERE stock_id IN ({placeholders})"
            f") SELECT stock_id, date, close FROM ranked WHERE rn <= 2 "
            f"ORDER BY stock_id, date DESC",
            sids,
        ).fetchall()
        prices_by_stock: dict[str, list] = {}
        for r in price_rows:
            prices_by_stock.setdefault(r["stock_id"], []).append(r)

        # 用 latest_as_of() 取一次當日 signal_history 快照日，避免跑相依子查詢。
        as_of = latest_as_of(db)
        if as_of:
            score_rows = conn.execute(
                f"SELECT stock_id, composite FROM signal_history "
                f"WHERE stock_id IN ({placeholders}) AND as_of=?",
                (*sids, as_of),
            ).fetchall()
            score_by_stock = {r["stock_id"]: r["composite"] for r in score_rows}
        else:
            score_by_stock = {}

        type_rows = conn.execute(
            f"SELECT stock_id, type FROM stock_info WHERE stock_id IN ({placeholders})",
            sids,
        ).fetchall()
        type_by_stock = {r["stock_id"]: r["type"] for r in type_rows}

        for sid, name in stocks.items():
            rows = prices_by_stock.get(sid, [])
            if len(rows) < 2 or rows[1]["close"] in (None, 0):
                continue
            close = float(rows[0]["close"])
            prev = float(rows[1]["close"])
            pct = (close - prev) / prev
            comp = score_by_stock.get(sid)
            out.append(WatchlistMover(
                stock_id=sid,
                stock_name=name,
                close=close,
                change_pct=pct,
                composite_score=float(comp) if comp is not None else None,
                market=classify_market(sid, type_by_stock.get(sid)),
            ))

    reverse = direction == "up"
    # None 永遠墊底（不論 up/down 方向），避免無資料股票冒到榜首
    sentinel = float("-inf") if reverse else float("inf")
    out.sort(key=lambda m: m.change_pct if m.change_pct is not None else sentinel, reverse=reverse)
    return out[:top]


@router.get("/overview", response_model=list[WatchlistOverviewRow])
def overview(db: Database = Depends(get_db)) -> list[WatchlistOverviewRow]:
    """自選股總覽：每檔最新分數 + 當日漲跌，依綜合分數降序。
    讀 signal_history 最新一筆；snapshot 比 daily_price 舊時自動補跑後再讀。"""
    stocks = wl_mod.load()
    if not stocks:
        return []
    ensure_fresh(db)
    tags_map = wl_mod.load_tags()
    placeholders = make_placeholders(len(stocks))
    sids = list(stocks.keys())
    out: list[WatchlistOverviewRow] = []
    with db.connect() as conn:
        # 批次抓每檔最新 2 日價格（ROW_NUMBER 只取 top-2/檔，避免撈全歷史）
        price_rows = conn.execute(
            f"WITH ranked AS ("
            f"  SELECT stock_id, date, close, "
            f"         ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn "
            f"  FROM daily_price WHERE stock_id IN ({placeholders})"
            f") SELECT stock_id, date, close FROM ranked WHERE rn <= 2 "
            f"ORDER BY stock_id, date DESC",
            sids,
        ).fetchall()
        prices_by_stock: dict[str, list] = {}
        for r in price_rows:
            prices_by_stock.setdefault(r["stock_id"], []).append(r)

        # 用 latest_as_of() 綁定常數 as_of，比 ROW_NUMBER OVER PARTITION 省很多。
        # signal_history 是每日批次寫入的 snapshot，同一批 as_of 相同。
        as_of = latest_as_of(db)
        if as_of:
            sig_rows = conn.execute(
                f"SELECT stock_id, as_of, short, mid, long, composite, recommendation "
                f"FROM signal_history "
                f"WHERE stock_id IN ({placeholders}) AND as_of=?",
                (*sids, as_of),
            ).fetchall()
            sig_by_stock = {r["stock_id"]: r for r in sig_rows}
        else:
            sig_by_stock = {}

        type_rows = conn.execute(
            f"SELECT stock_id, type FROM stock_info WHERE stock_id IN ({placeholders})",
            sids,
        ).fetchall()
        type_by_stock = {r["stock_id"]: r["type"] for r in type_rows}

        for sid, name in stocks.items():
            prices = prices_by_stock.get(sid, [])
            close = float(prices[0]["close"]) if prices else None
            change_pct = None
            if len(prices) >= 2 and prices[1]["close"] not in (None, 0):
                change_pct = (float(prices[0]["close"]) - float(prices[1]["close"])) / float(prices[1]["close"])

            sig = sig_by_stock.get(sid)
            out.append(WatchlistOverviewRow(
                stock_id=sid,
                stock_name=name,
                close=close,
                change_pct=change_pct,
                short=float(sig["short"]) if sig and sig["short"] is not None else None,
                mid=float(sig["mid"]) if sig and sig["mid"] is not None else None,
                long=float(sig["long"]) if sig and sig["long"] is not None else None,
                composite=float(sig["composite"]) if sig and sig["composite"] is not None else None,
                recommendation=sig["recommendation"] if sig else None,
                as_of=sig["as_of"] if sig else None,
                market=classify_market(sid, type_by_stock.get(sid)),
                tags=tags_map.get(sid, []),
            ))

    # None 永遠墊底（避免新股或 ETF 沒分數時冒到榜首）
    out.sort(key=lambda r: r.composite if r.composite is not None else float("-inf"), reverse=True)
    return out
