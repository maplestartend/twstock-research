"""/api/dashboard/* — 今日戰情室聚合資料。"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from api.common import make_placeholders
from api.deps import get_db
from api.schemas.common import CamelModel, StockRef
from api.schemas.stock import DataFreshness, ExDividendEvent, RadarHit
from app import watchlist as wl_mod
from app.data.clock import taipei_today
from app.data.db import Database
from app.scoring.radar_queries import query_radar_hits

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/radar-hits", response_model=list[RadarHit])
def radar_hits(
    limit: int = 10,
    market: list[str] = Query(default=["上市", "上櫃"]),
    db: Database = Depends(get_db),
) -> list[RadarHit]:
    """戰情室雷達命中。預設只回個股（上市/上櫃），ETF 評分機制不同所以默認排除。"""
    hits_data = query_radar_hits(db, markets=set(market), limit=limit)
    return [RadarHit(**h) for h in hits_data]


@router.get("/ex-dividend", response_model=list[ExDividendEvent])
def ex_dividend(days_ahead: int = 7, db: Database = Depends(get_db)) -> list[ExDividendEvent]:
    """近 N 日內的除權息事件（自選 + 持股）。
    資料源：adj_event 表（除權息實際發生時的還原因子記錄）。
    `dividend` 表為舊管道，目前資料量為 0；此處不再使用以避免誤導。
    cash_dividend 由 (before_price - after_price) 推估。"""
    today = taipei_today().isoformat()
    end = (taipei_today() + timedelta(days=days_ahead)).isoformat()
    watch_ids = set(wl_mod.load().keys())
    with db.connect() as conn:
        hold_rows = conn.execute("SELECT stock_id FROM holdings WHERE shares > 0").fetchall()
    focus_ids = watch_ids | {r["stock_id"] for r in hold_rows}
    if not focus_ids:
        return []

    placeholders = make_placeholders(len(focus_ids))
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT a.date AS ex_date, a.stock_id, "
            f"       COALESCE(s.stock_name, a.stock_id) AS stock_name, "
            f"       (a.before_price - a.after_price) AS cash_dividend, "
            f"       a.event_type "
            f"FROM adj_event a "
            f"LEFT JOIN stock_info s ON s.stock_id = a.stock_id "
            f"WHERE a.date >= ? AND a.date <= ? "
            f"  AND a.stock_id IN ({placeholders}) "
            f"ORDER BY a.date",
            [today, end, *focus_ids],
        ).fetchall()
    return [
        ExDividendEvent(
            ex_date=r["ex_date"],
            stock_id=r["stock_id"],
            stock_name=r["stock_name"],
            cash_dividend=r["cash_dividend"] if r["event_type"] == "dividend" else None,
            stock_dividend=None,  # adj_event 不分現金/股票股利，僅有 factor
        )
        for r in rows
    ]


_TABLE_LABELS = {
    "daily_price": "日線價量",
    "institutional": "三大法人",
    "margin": "融資融券",
    "per_pbr": "本益比",
    "monthly_revenue": "月營收",
    "financials_quarterly_derived": "季財報",
    "signal_history": "訊號快照",
}


def _expected_lag(table: str, today: date) -> tuple[int, int]:
    """各資料表的「正常 / 警告」延遲門檻（天）。
    - 日表：扣掉週末（週六最新資料是週五，今天週一最新仍是週五）
    - 月營收：每月 1~10 號公告上月，所以資料最舊可達 ~40 天仍正常
    - 季財報：上市每季最晚公告日 Q1=5/15、Q2=8/14、Q3=11/14、Q4(年報)=次年 3/31。
      最壞情況：剛過 4/1 還在等 Q4 年報、距 Q3 公告日已 ~140 天 → 用 145 / 180 天
    - 訊號快照：跟日表同步（盤後跑）
    """
    if table == "monthly_revenue":
        # 4/25 看到 3/xx 的資料是正常 → 期望 lag <= ~40 天；> 70 天才是真的舊
        return (45, 70)
    if table == "financials_quarterly_derived":
        return (145, 180)
    # 一般日表：扣掉今天/昨天可能是週末
    weekday = today.weekday()  # Mon=0 ... Sun=6
    extra = 0
    if weekday == 5:    # Sat
        extra = 1
    elif weekday == 6:  # Sun
        extra = 2
    elif weekday == 0:  # Mon (上週五最新)
        extra = 2
    return (1 + extra, 3 + extra)


class HitChange(StockRef):
    composite: float | None = None
    strategies: list[str] = []   # 新進命中的策略名 / 跌出命中的舊策略名


class ScoreMover(StockRef):
    prev_composite: float | None = None
    latest_composite: float | None = None
    delta: float | None = None


class SnapshotDelta(CamelModel):
    """signal_history 最新一天 vs 上一天的差異，用於戰情室「今日 vs 昨日」面板。"""
    latest_as_of: str | None = None
    prev_as_of: str | None = None
    new_hits: list[HitChange] = []         # 上次無命中、這次有命中（任一策略）
    dropped_hits: list[HitChange] = []     # 上次有命中、這次無
    big_movers: list[ScoreMover] = []      # 綜合分數變化 ≥ 5


class ScoreChange(StockRef):
    """單檔股票在 7 日窗口內的分數變化。給戰情室「我的關注本週分數變化」widget 用。"""
    in_watchlist: bool = False
    in_holdings: bool = False
    latest_score: float | None = None
    prev_score: float | None = None
    delta: float | None = None
    as_of_latest: str | None = None
    as_of_prev: str | None = None


@router.get("/my-score-changes", response_model=list[ScoreChange])
def my_score_changes(
    days: int = Query(default=7, ge=1, le=30, description="回看天數"),
    db: Database = Depends(get_db),
) -> list[ScoreChange]:
    """自選股 + 持股近 N 日綜合分數變化，依絕對值排序。

    為什麼不直接給「全市場 mover top 10」（snapshot-delta 已有）：
    - 使用者真正在意的只有自己關注的標的；全市場 mover 多半是不持有的
    - 結合 watchlist.yaml + holdings 來過濾，UI 可分流標示
    """
    watchlist_ids = set(wl_mod.load().keys())
    with db.connect() as conn:
        holdings = {
            r["stock_id"]
            for r in conn.execute("SELECT stock_id FROM holdings WHERE shares > 0").fetchall()
        }
    targets = sorted(watchlist_ids | holdings)
    if not targets:
        return []

    with db.connect() as conn:
        # 取目前最新 as_of
        latest_row = conn.execute("SELECT MAX(as_of) AS m FROM signal_history").fetchone()
        if not latest_row or not latest_row["m"]:
            return []
        latest = latest_row["m"]
        # 找回看 N 日前的 as_of：嚴格 ≤ (latest - N 天) 中最新的一筆
        cutoff = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()
        prev_row = conn.execute(
            "SELECT MAX(as_of) AS m FROM signal_history WHERE as_of <= ?",
            (cutoff,),
        ).fetchone()
        if not prev_row or not prev_row["m"]:
            return []
        prev = prev_row["m"]

        ph = make_placeholders(len(targets))
        latest_rows = conn.execute(
            f"SELECT stock_id, stock_name, composite FROM signal_history "
            f"WHERE as_of=? AND stock_id IN ({ph})",
            (latest, *targets),
        ).fetchall()
        prev_rows = conn.execute(
            f"SELECT stock_id, composite FROM signal_history "
            f"WHERE as_of=? AND stock_id IN ({ph})",
            (prev, *targets),
        ).fetchall()

    prev_score_by_sid = {r["stock_id"]: r["composite"] for r in prev_rows}
    out: list[ScoreChange] = []
    for r in latest_rows:
        sid = r["stock_id"]
        latest_c = r["composite"]
        prev_c = prev_score_by_sid.get(sid)
        if latest_c is None or prev_c is None:
            continue
        delta = float(latest_c) - float(prev_c)
        out.append(
            ScoreChange(
                stock_id=sid,
                stock_name=r["stock_name"] or sid,
                in_watchlist=sid in watchlist_ids,
                in_holdings=sid in holdings,
                latest_score=float(latest_c),
                prev_score=float(prev_c),
                delta=round(delta, 2),
                as_of_latest=latest,
                as_of_prev=prev,
            )
        )
    out.sort(key=lambda c: abs(c.delta or 0), reverse=True)
    return out


@router.get("/snapshot-delta", response_model=SnapshotDelta)
def snapshot_delta(top: int = 10, db: Database = Depends(get_db)) -> SnapshotDelta:
    """戰情室「今日 vs 昨日」delta（PM 審查 P0-6）。

    每日 loop 在乎的是「變化」而非「絕對值」——這是現有 dashboard 缺的角度。
    回傳：新進命中 / 跌出命中 / 綜合分數大幅變化（|Δ|≥5），各取 top N。
    若 signal_history 只有一天 → prev_as_of=null、各 list 為空。
    """
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT as_of FROM signal_history ORDER BY as_of DESC LIMIT 2"
        ).fetchall()
        if not rows:
            return SnapshotDelta()
        latest = rows[0]["as_of"]
        prev = rows[1]["as_of"] if len(rows) > 1 else None
        if prev is None:
            return SnapshotDelta(latest_as_of=latest)

        latest_rows = conn.execute(
            "SELECT stock_id, stock_name, composite, strategies FROM signal_history WHERE as_of=?",
            (latest,),
        ).fetchall()
        prev_rows = conn.execute(
            "SELECT stock_id, stock_name, composite, strategies FROM signal_history WHERE as_of=?",
            (prev,),
        ).fetchall()

    def _hits(strategies: str | None) -> set[str]:
        if not strategies:
            return set()
        return {s.strip() for s in strategies.split(",") if s.strip()}

    latest_map = {r["stock_id"]: r for r in latest_rows}
    prev_map = {r["stock_id"]: r for r in prev_rows}

    new_hits: list[HitChange] = []
    dropped_hits: list[HitChange] = []
    big_movers: list[ScoreMover] = []

    for sid, r in latest_map.items():
        latest_strats = _hits(r["strategies"])
        prev_r = prev_map.get(sid)
        prev_strats = _hits(prev_r["strategies"]) if prev_r else set()

        added = latest_strats - prev_strats
        if added and not prev_strats:
            # 整檔從「無命中」變「有命中」
            new_hits.append(HitChange(
                stock_id=sid, stock_name=r["stock_name"],
                composite=r["composite"], strategies=sorted(added),
            ))

        # 大幅變化
        prev_c = prev_r["composite"] if prev_r else None
        latest_c = r["composite"]
        if prev_c is not None and latest_c is not None:
            delta = float(latest_c) - float(prev_c)
            if abs(delta) >= 5:
                big_movers.append(ScoreMover(
                    stock_id=sid, stock_name=r["stock_name"],
                    prev_composite=float(prev_c), latest_composite=float(latest_c),
                    delta=round(delta, 2),
                ))

    for sid, prev_r in prev_map.items():
        prev_strats = _hits(prev_r["strategies"])
        if not prev_strats:
            continue
        latest_r = latest_map.get(sid)
        latest_strats = _hits(latest_r["strategies"]) if latest_r else set()
        if latest_strats:
            continue
        # 整檔從「有命中」變「無命中」
        dropped_hits.append(HitChange(
            stock_id=sid, stock_name=prev_r["stock_name"],
            composite=latest_r["composite"] if latest_r else None,
            strategies=sorted(prev_strats),
        ))

    # 排序 + 取 top
    new_hits.sort(key=lambda h: (h.composite or 0), reverse=True)
    dropped_hits.sort(key=lambda h: (h.composite or 0), reverse=True)
    big_movers.sort(key=lambda m: abs(m.delta or 0), reverse=True)

    return SnapshotDelta(
        latest_as_of=latest,
        prev_as_of=prev,
        new_hits=new_hits[:top],
        dropped_hits=dropped_hits[:top],
        big_movers=big_movers[:top],
    )


@router.get("/data-freshness", response_model=list[DataFreshness])
def data_freshness(db: Database = Depends(get_db)) -> list[DataFreshness]:
    """每張表最新一筆日期。舊版對 7 張表各跑一次 `MAX()` query → 7 次 round-trip
    （SQLite 是同連線所以快但仍非最佳）。改成單一 SELECT ... UNION ALL ...，一次拿齊。
    """
    today = taipei_today()
    union_parts = [
        f"SELECT '{table}' AS t, MAX({'as_of' if table == 'signal_history' else 'date'}) AS mx FROM {table}"
        for table in _TABLE_LABELS
    ]
    sql = " UNION ALL ".join(union_parts)
    mx_by_table: dict[str, str | None] = {t: None for t in _TABLE_LABELS}
    try:
        with db.connect() as conn:
            for r in conn.execute(sql).fetchall():
                mx_by_table[r["t"]] = r["mx"]
    except Exception:
        pass

    from datetime import datetime
    out: list[DataFreshness] = []
    for table, label in _TABLE_LABELS.items():
        mx = mx_by_table.get(table)
        lag = None
        tone = "error"
        if mx:
            try:
                d = datetime.fromisoformat(mx[:10]).date()
                lag = (today - d).days
                ok_thr, warn_thr = _expected_lag(table, today)
                if lag <= ok_thr:
                    tone = "ok"
                elif lag <= warn_thr:
                    tone = "warning"
                else:
                    tone = "error"
            except Exception:
                pass
        out.append(DataFreshness(
            table=table,
            label=label,
            latest_date=mx[:10] if mx else None,
            lag_days=lag,
            tone=tone,
        ))
    return out
