"""自動回補「正在公告期」內的最新一兩季財報。

時機：每天盤後跑（接在 market_update 後面）。判斷邏輯：
  - 抓 DB 裡最新已有的 (year, quarter)
  - 嘗試抓**下一個季**（next quarter）— 如果該季的公告期已啟動（deadline 在 60 天內或剛過 30 天）
  - 也嘗試抓**再下一個季** — 處理跨年度切換 / 排程跳過好幾天的情形

公告 deadline（台股）：
  Q1 → 5/15、Q2 → 8/14、Q3 → 11/14、Q4 (年報) → 次年 3/31

idempotent：MOPS 端回空就什麼都不寫；已有的 (stock_id, date, type) row 走 INSERT OR REPLACE。
跑一次 5~30 秒，視 MOPS 回傳速度。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.mops_financials_fetcher import (  # noqa: E402
    derive_quarterly_from_cumulative,
    fetch_history_income_statement,
)


# Q → (deadline_month, deadline_day)
_QUARTER_DEADLINE = {
    1: (5, 15),
    2: (8, 14),
    3: (11, 14),
    4: (3, 31),   # 次年
}


def _quarter_deadline(year: int, quarter: int) -> date:
    """回傳該 (year, quarter) 的法定公告 deadline。Q4 deadline 在次年 3/31。"""
    m, d = _QUARTER_DEADLINE[quarter]
    deadline_year = year + 1 if quarter == 4 else year
    return date(deadline_year, m, d)


def _next_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 4:
        return year + 1, 1
    return year, quarter + 1


def _latest_in_db(db: Database) -> tuple[int, int] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(year * 10 + quarter) AS yq FROM financials_cumulative"
        ).fetchone()
    if not row or row["yq"] is None:
        return None
    yq = int(row["yq"])
    return yq // 10, yq % 10


def _is_in_publish_window(today: date, year: int, quarter: int, *, days_before: int, days_after: int) -> bool:
    """該 (year, quarter) 是否在「公告期」內？

    公告期 = [deadline - days_before, deadline + days_after]。
    在公告期內：每天都該嘗試（公告中的公司每天可能變多）。
    超過 deadline + days_after：視為定案，後續排程不用再試。
    """
    deadline = _quarter_deadline(year, quarter)
    days_to_deadline = (deadline - today).days
    return -days_after <= days_to_deadline <= days_before


def _quarters_to_try(today: date, db: Database, *, days_before: int = 60, days_after: int = 30) -> list[tuple[int, int]]:
    """挑出「值得嘗試抓」的季別：

    規則：
    1. **公告期內的所有季都試**（即使 DB 已有部分 — 公告中每天會多新公司）
    2. 排程跳過的歷史季：DB 缺、deadline 已過超過 days_after 也補一次
    3. 還太早（deadline 在 days_before 之後）→ 不試

    days_before：deadline 前 N 天就開始嘗試（早期公告者已陸續上線）
    days_after：deadline 後 N 天仍嘗試（捕捉延遲公告 + 公告期當天）
    """
    latest = _latest_in_db(db)
    if latest is None:
        # 空 DB → 從今年 Q1 開始試 4 季
        y = today.year
        return [(y, q) for q in range(1, 5)]

    # 從 latest 的下一季開始往前試最多 4 季
    out: list[tuple[int, int]] = []
    cur = latest
    for _ in range(4):
        cur = _next_quarter(*cur)
        if _is_in_publish_window(today, *cur, days_before=days_before, days_after=days_after):
            out.append(cur)
        elif (today - _quarter_deadline(*cur)).days > days_after:
            # deadline 已過、超過 days_after → 視為遲補（排程斷過的舊季）
            out.append(cur)
        else:
            # 還太早 → 後續季更不用試
            break

    # 額外規則：DB latest 自己的 deadline 還在公告期內 → 也再試（接 DB 的最後一季）
    # 例：4/26 DB 已有 2026 Q1 16 檔 → next 是 Q2（still 太早），但 Q1 仍在公告期
    if _is_in_publish_window(today, *latest, days_before=days_before, days_after=days_after):
        if latest not in out:
            out.insert(0, latest)

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=str, default=None, help="覆寫『今天』日期 (YYYY-MM-DD)，測試用")
    parser.add_argument("--days-before", type=int, default=60, help="deadline 前幾天開始嘗試（預設 60）")
    parser.add_argument("--days-after", type=int, default=30, help="deadline 後幾天仍補抓（預設 30）")
    parser.add_argument("--quiet", action="store_true", help="僅 WARN 以上輸出")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(message)s",
    )
    log = logging.getLogger("refresh_recent_financials")

    today = date.fromisoformat(args.today) if args.today else date.today()

    cfg = Config.load()
    db = Database(cfg.database.path)

    targets = _quarters_to_try(today, db, days_before=args.days_before, days_after=args.days_after)
    if not targets:
        log.info("無需要回補的季別（最新已有資料尚未進入下一季公告期）")
        return 0

    log.info("[%s] 嘗試回補 %d 季：%s",
             today.isoformat(), len(targets),
             ", ".join(f"{y}Q{q}" for y, q in targets))

    total_rows = 0
    new_quarters: list[str] = []
    for y, q in targets:
        df = fetch_history_income_statement(y, q, delay=0.3)
        if df.empty:
            log.info("  %dQ%d: MOPS 回空（尚未公告或解析失敗）", y, q)
            continue
        n = db.upsert_df(df, "financials_cumulative")
        total_rows += n
        new_quarters.append(f"{y}Q{q}")
        log.info("  %dQ%d: 寫入 %d 筆 / %d 檔", y, q, n, df["stock_id"].nunique())

    if total_rows > 0:
        # 累計值有變動 → 重新差分產生 financials_quarterly_derived（給 fundamentals 用）
        n_derived = derive_quarterly_from_cumulative(db)
        log.info("financials_quarterly_derived 重生：%d 筆（含歷史）", n_derived)
        log.info("✓ 共寫入 %d 筆累計值，涵蓋季別：%s", total_rows, ", ".join(new_quarters))
    else:
        log.info("✓ MOPS 沒有新資料；DB 保持原狀")
    return 0


if __name__ == "__main__":
    sys.exit(main())
