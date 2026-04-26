"""/api/dq/* — 資料品質檢查 dashboard 用。

聚合三類問題：
1. **價格異常**：漲跌 ≥9.9%（含跌停）、量爆 (≥5× 20 日均)、停滯（連續多日 close 不變）、跳空缺口（>15% 且無除權息）
2. **股票級缺值**：daily_price 等表近 N 日缺超過 1/3 的股票
3. **新鮮度**：直接讀 dashboard.freshness 那組，重複拉避免使用者切頁

只查 watchlist + holdings + 雷達 Top 100 涵蓋的股票，避免全市場掃太久。
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query

from api.common import make_placeholders
from api.deps import get_db
from api.schemas.common import CamelModel
from app import watchlist as wl_mod
from app.data.clock import taipei_today
from app.data.db import Database
from app.data.market_type import classify_market

router = APIRouter(prefix="/api/dq", tags=["dq"])


class PriceAnomaly(CamelModel):
    stock_id: str
    stock_name: str
    market: str | None = None
    kind: str  # limit_up | limit_down | volume_spike | stale | huge_gap
    severity: str  # info | warning | critical
    date: str
    value: float | None = None
    note: str


class StockGap(CamelModel):
    stock_id: str
    stock_name: str
    table: str
    missing_days: int
    expected: int


class DqSummary(CamelModel):
    as_of: str | None
    n_anomalies: int
    n_gaps: int
    anomalies: list[PriceAnomaly]
    gaps: list[StockGap]
    scope: str  # 描述掃描範圍 (e.g. "自選 + 持股 + 雷達 Top 100, 共 N 檔")


def _focus_stocks(db: Database) -> dict[str, str]:
    """掃描重點股：自選 + 持股 + 當日雷達 Top 100。回傳 {stock_id: stock_name}。"""
    out: dict[str, str] = {}
    out.update(wl_mod.load())
    with db.connect() as conn:
        # 持股
        for r in conn.execute("SELECT stock_id FROM holdings WHERE shares > 0").fetchall():
            sid = r["stock_id"]
            if sid not in out:
                out[sid] = sid
        # 雷達 Top 100
        latest = conn.execute("SELECT MAX(as_of) AS as_of FROM signal_history").fetchone()
        if latest and latest["as_of"]:
            for r in conn.execute(
                "SELECT stock_id, stock_name FROM signal_history "
                "WHERE as_of=? ORDER BY composite DESC LIMIT 100",
                (latest["as_of"],),
            ).fetchall():
                sid = r["stock_id"]
                if sid not in out:
                    out[sid] = r["stock_name"] or sid
        # 補名稱
        if out:
            placeholders = make_placeholders(len(out))
            for r in conn.execute(
                f"SELECT stock_id, stock_name FROM stock_info WHERE stock_id IN ({placeholders})",
                list(out.keys()),
            ).fetchall():
                if not out.get(r["stock_id"]) or out[r["stock_id"]] == r["stock_id"]:
                    out[r["stock_id"]] = r["stock_name"] or r["stock_id"]
    return out


@router.get("/summary", response_model=DqSummary)
def summary(
    days: int = Query(default=10, ge=3, le=60),
    db: Database = Depends(get_db),
) -> DqSummary:
    today = taipei_today()
    since = (today - timedelta(days=days)).isoformat()

    focus = _focus_stocks(db)
    if not focus:
        return DqSummary(as_of=None, n_anomalies=0, n_gaps=0, anomalies=[], gaps=[], scope="無焦點股")

    anomalies: list[PriceAnomaly] = []
    gaps: list[StockGap] = []

    placeholders = make_placeholders(len(focus))
    with db.connect() as conn:
        # 一次批次抓 type、價格序列、adj_event（避免 N+1）
        type_map: dict[str, str | None] = {
            r["stock_id"]: r["type"]
            for r in conn.execute(
                f"SELECT stock_id, type FROM stock_info WHERE stock_id IN ({placeholders})",
                list(focus.keys()),
            ).fetchall()
        }

        as_of_row = conn.execute("SELECT MAX(date) AS d FROM daily_price").fetchone()
        as_of = as_of_row["d"] if as_of_row and as_of_row["d"] else None

        expected_dates = conn.execute(
            "SELECT COUNT(DISTINCT date) AS n FROM daily_price WHERE date >= ? AND date <= ?",
            (since, as_of or today.isoformat()),
        ).fetchone()["n"] or 0

        # 一次抓所有 focus 股的價格 → 在記憶體裡分群（取代 per-stock 的 SELECT）
        price_rows = conn.execute(
            f"SELECT stock_id, date, close, volume FROM daily_price "
            f"WHERE stock_id IN ({placeholders}) AND date >= ? "
            f"ORDER BY stock_id, date ASC",
            (*focus.keys(), since),
        ).fetchall()
        prices_by_stock: dict[str, list] = {}
        for r in price_rows:
            prices_by_stock.setdefault(r["stock_id"], []).append(r)

        # 一次抓所有 focus 股的 adj_event 用於跳空判斷
        adj_rows = conn.execute(
            f"SELECT stock_id, date FROM adj_event WHERE stock_id IN ({placeholders})",
            list(focus.keys()),
        ).fetchall()
        adj_set: set[tuple[str, str]] = {(r["stock_id"], r["date"]) for r in adj_rows}

        for sid, name in focus.items():
            mkt = classify_market(sid, type_map.get(sid))
            rows = prices_by_stock.get(sid, [])

            # 1. 缺值
            n_dates = len({r["date"] for r in rows})
            missing = expected_dates - n_dates
            if expected_dates >= 5 and missing >= max(3, expected_dates // 3):
                gaps.append(StockGap(
                    stock_id=sid, stock_name=name, table="daily_price",
                    missing_days=missing, expected=expected_dates,
                ))

            if len(rows) < 3:
                continue

            closes = [float(r["close"]) for r in rows if r["close"] is not None]
            volumes = [float(r["volume"]) for r in rows if r["volume"] is not None]
            dates = [r["date"] for r in rows]

            if len(closes) < 3:
                continue

            # 漲跌停 (>=9.9%)
            for i in range(1, len(closes)):
                if closes[i - 1] in (None, 0):
                    continue
                pct = (closes[i] - closes[i - 1]) / closes[i - 1]
                if abs(pct) >= 0.099:
                    kind = "limit_up" if pct > 0 else "limit_down"
                    anomalies.append(PriceAnomaly(
                        stock_id=sid, stock_name=name, market=mkt,
                        kind=kind,
                        severity="warning" if pct > 0 else "critical",
                        date=dates[i],
                        value=pct,
                        note=f"單日 {pct * 100:+.1f}%（漲跌停或極端波動）",
                    ))

            # 量爆 (今日 >= 5x 20 日均，且至少 5 筆樣本)
            if len(volumes) >= 6:
                avg = sum(volumes[:-1]) / max(1, len(volumes) - 1)
                today_v = volumes[-1]
                if avg > 0 and today_v >= 5 * avg:
                    anomalies.append(PriceAnomaly(
                        stock_id=sid, stock_name=name, market=mkt,
                        kind="volume_spike", severity="info",
                        date=dates[-1],
                        value=today_v / avg,
                        note=f"成交量 {today_v / avg:.1f}× 近期均量",
                    ))

            # 停滯：連續 ≥3 天 close 完全不變
            stale_streak = 1
            for i in range(len(closes) - 1, 0, -1):
                if closes[i] == closes[i - 1]:
                    stale_streak += 1
                else:
                    break
            if stale_streak >= 3:
                anomalies.append(PriceAnomaly(
                    stock_id=sid, stock_name=name, market=mkt,
                    kind="stale", severity="warning",
                    date=dates[-1],
                    value=stale_streak,
                    note=f"近 {stale_streak} 日 close 完全不變（疑似停牌或抓資料異常）",
                ))

            # 跳空缺口：>15% 且查無同日除權息事件
            for i in range(1, len(closes)):
                if closes[i - 1] in (None, 0):
                    continue
                pct = (closes[i] - closes[i - 1]) / closes[i - 1]
                if abs(pct) > 0.15:
                    # 同日有 adj_event？跳過（用記憶體 set 取代逐筆 SELECT）
                    if (sid, dates[i]) in adj_set:
                        continue
                    anomalies.append(PriceAnomaly(
                        stock_id=sid, stock_name=name, market=mkt,
                        kind="huge_gap", severity="critical",
                        date=dates[i],
                        value=pct,
                        note=f"跳空 {pct * 100:+.1f}% 但無除權息事件，疑漏抓 adj_event",
                    ))

    # severity 排序：critical → warning → info；缺值依 missing 降序
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    anomalies.sort(key=lambda a: (sev_order.get(a.severity, 9), a.date), reverse=False)
    gaps.sort(key=lambda g: g.missing_days, reverse=True)

    return DqSummary(
        as_of=as_of,
        n_anomalies=len(anomalies),
        n_gaps=len(gaps),
        anomalies=anomalies[:80],
        gaps=gaps[:30],
        scope=f"自選 + 持股 + 雷達 Top 100，共 {len(focus)} 檔；近 {days} 日",
    )
