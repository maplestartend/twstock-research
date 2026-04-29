"""讀 signal_history 的小型查詢層。

把 dashboard.radar_hits / radar.hits 兩個 router 共用的邏輯抽出來：
最近一次 as_of、依策略 + 市場過濾、按對應分數降序、可截斷 top N。

注意：本模組屬於 app/ 層，不依賴 api/schemas，回傳純 dict。
"""
from __future__ import annotations

from typing import Any

from app.data.db import Database
from app.data.market_type import classify_market

# 策略 → signal_history 欄位的排序對映。
# 短/中/長三條線型策略各依自己的維度排（看「短期最強的前幾名」比看「綜合分最高」更直觀），
# 其他（外資連買、回檔布局、營收爆發…）一律 composite — 那些指標在 signal_history 沒存欄位，
# 也沒有跨指標的「自然偏序」，composite 是最不會誤導的預設。
_STRATEGY_SORT_COLUMN: dict[str, str] = {
    "短線強勢": "short",
    "中期波段": "mid",
    "長期價值": "long",
    "量能動能": "vr_macd",
}
_ALLOWED_SORT_COLUMNS = {"short", "mid", "long", "composite", "vr_macd"}


def latest_as_of(db: Database) -> str | None:
    """signal_history 最新快照日；表為空 → None。"""
    with db.connect() as conn:
        row = conn.execute("SELECT MAX(as_of) AS m FROM signal_history").fetchone()
    return row["m"] if row and row["m"] else None


def query_radar_hits(
    db: Database,
    *,
    strategy: str | None = None,
    markets: set[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """查當日命中（有 strategies 標籤）的股票；依 composite 降序，依 markets 過濾。

    參數：
    - strategy：用 LIKE %name% 子字串比對 signal_history.strategies
    - markets：要保留的市場集合（'上市'/'上櫃'/'ETF'/'其他'）；None = 不過濾
    - limit：截斷筆數；None / <=0 = 不截斷

    回傳：list of dict，每個 dict 欄位對應 RadarHit schema（snake_case）。
    """
    as_of = latest_as_of(db)
    if not as_of:
        return []

    # 依策略選排序欄位：短/中/長期策略各排自己的分數，其他 fallback composite
    sort_col = _STRATEGY_SORT_COLUMN.get(strategy or "", "composite")
    if sort_col not in _ALLOWED_SORT_COLUMNS:
        # 防禦：未來新增 mapping 漏寫時不要崩，回退 composite
        sort_col = "composite"

    unlimited = limit is None or limit <= 0
    sql = (
        "SELECT s.stock_id, s.stock_name, s.close, s.short, s.mid, s.long, "
        "       s.composite, s.vr_macd, s.recommendation, s.strategies, i.type "
        "FROM signal_history s "
        "LEFT JOIN stock_info i ON i.stock_id = s.stock_id "
        "WHERE s.as_of = ? "
        "  AND s.strategies IS NOT NULL AND s.strategies != '' "
    )
    params: list[Any] = [as_of]
    if strategy:
        sql += "  AND s.strategies LIKE ? "
        params.append(f"%{strategy}%")
    # 主排序：對應分數降序、NULL 推到最後；
    # tie-breaker chain: short DESC（量能動能策略要求；對其他策略也是合理 fallback）
    # → composite DESC → stock_id ASC（穩定排序、避免相同分數時順序漂浮）
    sql += (
        f"ORDER BY s.{sort_col} IS NULL, s.{sort_col} DESC, "
        f"         s.short IS NULL, s.short DESC, "
        f"         s.composite IS NULL, s.composite DESC, "
        f"         s.stock_id"
    )
    if not unlimited:
        # 多抓一些後再依 market 過濾，避免邊界不足
        sql += " LIMIT ?"
        params.append(limit * 4)

    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        mkt = classify_market(r["stock_id"], r["type"])
        if markets is not None and mkt not in markets:
            continue
        out.append({
            "stock_id": r["stock_id"],
            "stock_name": r["stock_name"] or r["stock_id"],
            "close": r["close"],
            "short": r["short"],
            "mid": r["mid"],
            "long": r["long"],
            "composite": r["composite"],
            "vr_macd": r["vr_macd"],
            "recommendation": r["recommendation"],
            "strategies": r["strategies"],
            "market": mkt,
        })
        if not unlimited and len(out) >= limit:
            break
    return out
