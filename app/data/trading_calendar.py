"""台股交易日 / 休市日判斷。

設計取捨：
- TWSE 不公開穩定的「股市休市日 API」，個別 holiday lib（例如 holidays.TW）依公務員行事曆，
  與股市休市日有出入（最明顯的是 5/1 勞動節：公務員上班但證交所收盤）。
- 不引外部資料源最可靠的事實是「這檔資料庫自己看到的歷史交易日」— `daily_price` 表裡
  缺哪些工作日，那天就是休市。
- 但 DB 觀察只能涵蓋「過去」；今天 / 未來幾個月的休市日要靠 TWSE 公告的年度行事曆
  hardcode。本檔保留一份小型 inline 對照表（每年 Q4 TWSE 公告新一年行事曆時更新）。

兩階段查詢：
- 過去日期：`daily_price` 沒紀錄的工作日 = 休市
- 今天 / 未來：先查 `daily_price`（雖然今天可能還沒 ingest），再查 `INLINE_TWSE_HOLIDAYS`，
  最後 fallback 純 weekday 判斷
"""
from __future__ import annotations

import threading
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.data.db import Database

# ----------------------------------------------------------------------
# Inline future TWSE 休市日（年度由 TWSE 公告，有人類維護的負擔）
# 來源：https://www.twse.com.tw/zh/holidaySchedule/holidaySchedule
# 慣例：只放「今年起算未來 ~12 個月還沒被 daily_price 觀察到」的日期。
# 過去日期一律走 DB 觀察，不受這份表的 stale 影響。
# ----------------------------------------------------------------------
INLINE_TWSE_HOLIDAYS: dict[str, str] = {
    # 2026 下半年（4/30 之後尚未發生的休市日）
    "2026-05-01": "勞動節",
    "2026-06-19": "端午節（農曆 5/5）",
    "2026-09-25": "中秋節（農曆 8/15）",
    "2026-10-09": "國慶日補假（10/10 為週六）",
    # 2027 元旦先補一筆，避免 12 月底跨年時 fallback 失效
    "2027-01-01": "元旦",
}


# ----------------------------------------------------------------------
# DB 觀察：daily_price 缺的工作日 = 休市日（thread-safe TTL cache）
# ----------------------------------------------------------------------
_observed_lock = threading.Lock()
_observed_cache: dict[str, tuple[float, frozenset[date]]] = {}
_OBSERVED_TTL_SEC = 600.0  # 10 分鐘 — daily_update 完才會新增、查多次也廉價


def _observed_holidays(db: "Database", lookback_years: int = 2) -> frozenset[date]:
    """從 daily_price 撈過去 N 年內的休市日（工作日但無資料）。

    rendered as frozenset[date] for membership test。同 connection 多次呼叫走 TTL cache，
    避免每次 freshness query 都掃整張表。
    """
    import time
    key = f"{db.path}|{lookback_years}"
    now = time.time()
    with _observed_lock:
        entry = _observed_cache.get(key)
        if entry and now - entry[0] < _OBSERVED_TTL_SEC:
            return entry[1]

    cutoff = (date.today() - timedelta(days=lookback_years * 365)).isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_price WHERE date >= ?",
            (cutoff,),
        ).fetchall()
    trading_days = {date.fromisoformat(r[0]) for r in rows}
    if not trading_days:
        result = frozenset()
    else:
        # 在資料範圍內，所有工作日 - 實際交易日 = 休市日
        first = min(trading_days)
        last = max(trading_days)
        gaps: set[date] = set()
        d = first
        while d <= last:
            if d.weekday() < 5 and d not in trading_days:
                gaps.add(d)
            d += timedelta(days=1)
        result = frozenset(gaps)

    with _observed_lock:
        _observed_cache[key] = (now, result)
    return result


def _is_inline_holiday(d: date) -> bool:
    return d.isoformat() in INLINE_TWSE_HOLIDAYS


def is_trading_day(d: date, db: "Database") -> bool:
    """d 是否為股市交易日。

    優先序：
    1. 週末：False（最快路徑）
    2. INLINE_TWSE_HOLIDAYS 命中：False（TWSE 公告值得信任，過去 / 未來都用）
    3. 過去日期 + 在 DB 觀察範圍內 + 工作日卻無資料：False（用 daily_price 自身做「已實證休市」）
    4. 其他：True（保守預設）

    為什麼 inline 比 DB 觀察優先：5/1/2026 prev 跑 daily-update 抓不到資料時 daily_price
    沒寫入也沒觀察紀錄 — 此時光看 DB 會誤判「不知道是不是交易日」。INLINE 直接告訴答案。
    """
    if d.weekday() >= 5:
        return False
    if _is_inline_holiday(d):
        return False
    # 過去 + 今天：DB 觀察（_observed_holidays 只會把「在 daily_price 已觀察範圍內、
    # 卻缺資料的工作日」當休市；今天若還沒 ingest 也不會被誤判，因為它根本不在觀察範圍裡）
    today = date.today()
    if d <= today and d in _observed_holidays(db):
        return False
    return True


def previous_trading_day(d: date, db: "Database") -> date:
    """d 之前最近一個交易日（不含 d）。"""
    cur = d - timedelta(days=1)
    while not is_trading_day(cur, db):
        cur -= timedelta(days=1)
    return cur


def expected_latest_close_date(today: date, db: "Database") -> date:
    """資料庫「應該」最新到哪一天的收盤。

    - 今天是交易日：回今天（盤後 16:30 後資料才完整 — caller 要自行決定是否再扣一天）
    - 今天非交易日（週末 / 休市）：回最近一個過去交易日
    """
    if is_trading_day(today, db):
        return today
    return previous_trading_day(today, db)


def calendar_lag_for_expected(latest_date: date, today: date, db: "Database") -> int:
    """`latest_date` 落後 `expected_latest_close_date(today)` 多少 calendar 天。

    - 0：資料完全跟上預期最新交易日
    - 正值：N 個 calendar 天落後預期（注意是 calendar，含週末/休市）
    - 負值：latest_date 比預期還新（理論上不會發生，但 daily_update 在盤中跑就有可能）
    """
    expected = expected_latest_close_date(today, db)
    return (expected - latest_date).days


def clear_cache() -> None:
    """測試用：清掉 in-memory cache。"""
    with _observed_lock:
        _observed_cache.clear()
