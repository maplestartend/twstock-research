"""回補全市場歷史季財報（MOPS 公開觀測站綜合損益表 + 累計差分產生單季值）。

每季 ~2 秒（上市 + 上櫃 共 2 個 POST），預設回補 5 季：能算最新一季 TTM
（4 季加總）+ YoY（最新一季 vs 去年同季的累計值比較）。

用法：
    # 預設：自動推算最近 5 個有公告的季度
    python -m scripts.backfill_financials_history

    # 指定季數（最少 2 季才有差分意義）
    python -m scripts.backfill_financials_history --quarters 8

    # 指定範圍（西元年/季）
    python -m scripts.backfill_financials_history --from 2023/1 --to 2025/4

    # 跳過已有資料的季度（適合 cron 跑時當輕量更新）
    python -m scripts.backfill_financials_history --skip-existing
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


def _latest_completed_quarter(today: date) -> tuple[int, int]:
    """估算「最近一個有公告的季別」。台股財報公告期限：
    - Q1 報表：5/15 之前
    - Q2 報表：8/14 之前
    - Q3 報表：11/14 之前
    - Q4 (年報)：3/31 之前

    保守起見以實際公告月後第二週才視為「資料齊全」。
    """
    y, m = today.year, today.month
    if m >= 12:
        return y, 3   # 12 月時 Q3 應已公告
    if m >= 9:
        return y, 2
    if m >= 6:
        return y, 1
    if m >= 4:
        return y - 1, 4
    if m >= 1:
        return y - 1, 3
    return y - 1, 3


def _enumerate_quarters(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """生成從 start 到 end (含端點) 的 (year, quarter) 列表。"""
    out: list[tuple[int, int]] = []
    y, q = start
    while (y, q) <= end:
        out.append((y, q))
        q += 1
        if q > 4:
            q = 1
            y += 1
    return out


def _existing_quarters(db: Database) -> set[tuple[int, int]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT year, quarter FROM financials_cumulative"
        ).fetchall()
    return {(int(r["year"]), int(r["quarter"])) for r in rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quarters", type=int, default=5, help="回補最近 N 季（預設 5）")
    parser.add_argument("--from", dest="qf", help="起始季 YYYY/Q，例 2023/1")
    parser.add_argument("--to", dest="qt", help="結束季 YYYY/Q，例 2025/4")
    parser.add_argument("--delay", type=float, default=0.5, help="每個請求間隔（秒）")
    parser.add_argument("--skip-existing", action="store_true",
                        help="跳過 financials_cumulative 已有資料的季度")
    parser.add_argument("--no-derive", action="store_true",
                        help="只抓不算差分（除錯用）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("backfill_financials_history")

    cfg = Config.load()
    db = Database(cfg.database.path)

    # 1. 計算要抓哪些季
    if args.qf and args.qt:
        ys, qs = [int(x) for x in args.qf.split("/")]
        ye, qe = [int(x) for x in args.qt.split("/")]
        quarters = _enumerate_quarters((ys, qs), (ye, qe))
    else:
        end = _latest_completed_quarter(date.today())
        # 從 end 倒推 N-1 季
        y, q = end
        quarters = []
        for _ in range(args.quarters):
            quarters.append((y, q))
            q -= 1
            if q < 1:
                q = 4
                y -= 1
        quarters.reverse()

    log.info("計畫抓取 %d 季：%s", len(quarters),
             ", ".join(f"{y}Q{q}" for y, q in quarters))

    # 2. 過濾已有資料的季（如果開啟 --skip-existing）
    if args.skip_existing:
        existing = _existing_quarters(db)
        skipped = [q for q in quarters if q in existing]
        quarters = [q for q in quarters if q not in existing]
        log.info("--skip-existing：跳過 %d 季（已有資料）：%s",
                 len(skipped), ", ".join(f"{y}Q{q}" for y, q in skipped))

    if not quarters:
        log.info("無新季度需抓取")
        # 仍跑一次 derive 把既有累計差分（idempotent）
        if not args.no_derive:
            n = derive_quarterly_from_cumulative(db)
            log.info("derive_quarterly_from_cumulative 寫入 %d 筆", n)
        return 0

    # 3. 逐季抓取並 upsert
    total_rows = 0
    for i, (y, q) in enumerate(quarters, 1):
        df = fetch_history_income_statement(y, q, delay=args.delay)
        if df.empty:
            log.warning("[%d/%d] %dQ%d 無資料回傳", i, len(quarters), y, q)
            continue
        n = db.upsert_df(df, "financials_cumulative")
        total_rows += n
        log.info("[%d/%d] %dQ%d: 寫入 %d 筆 / %d 檔",
                 i, len(quarters), y, q, n, df["stock_id"].nunique())

    log.info("MOPS 歷史回補完成：累計寫入 %d 筆", total_rows)

    # 4. 累計值差分產生單季值
    if not args.no_derive:
        n = derive_quarterly_from_cumulative(db)
        log.info("financials_quarterly_derived 寫入 %d 筆（差分後）", n)

    return 0


if __name__ == "__main__":
    sys.exit(main())
