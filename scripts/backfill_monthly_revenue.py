"""全市場歷史月營收回補（FinMind 逐檔 + 節流）。

MOPS OpenAPI 只給最新一個月，要補歷史就得用 FinMind 按股票查詢（每檔 1 request）。
FinMind 免費版 600 req/hr，跑全市場約 2,700 檔需 4.5~5 小時。

腳本特色：
- 可中斷可續跑：已在 DB 裡有「至少 N 個月資料」的股票會被跳過
- 自動節流到 6 秒/請求（1 小時 600 次上限安全值）
- 每批 100 檔輸出一次進度 + 剩餘 ETA

用法：
    python -m scripts.backfill_monthly_revenue            # 回補 2022 起
    python -m scripts.backfill_monthly_revenue --start 2020-01-01
    python -m scripts.backfill_monthly_revenue --min-months 24  # 已有 24 個月資料者跳過
    python -m scripts.backfill_monthly_revenue --limit 50       # 只跑前 50 檔（測試用）
    python -m scripts.backfill_monthly_revenue --resume-from 3000  # 從代號 >= 3000 開始
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.fetcher import FinMindError, FinMindFetcher  # noqa: E402
from app.notifier import notify  # noqa: E402


def list_tradable_stocks(db: Database) -> list[str]:
    """所有 4 碼代號（一般股票）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_id FROM daily_price "
            "WHERE LENGTH(stock_id) = 4 "
            "ORDER BY stock_id"
        ).fetchall()
    return [r["stock_id"] for r in rows]


def stocks_with_sufficient_history(db: Database, min_months: int) -> set[str]:
    """已經有 min_months 個月歷史資料的股票，回補時跳過。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT stock_id, COUNT(DISTINCT date) AS n "
            "FROM monthly_revenue GROUP BY stock_id"
        ).fetchall()
    return {r["stock_id"] for r in rows if r["n"] >= min_months}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2022-01-01", help="回補起始日")
    parser.add_argument("--min-months", type=int, default=12,
                        help="已有 N 個月資料的股票視為已回補完成，跳過（預設 12）")
    parser.add_argument("--delay", type=float, default=6.0,
                        help="每個 request 間隔秒數（預設 6.0，對應 ~600 req/hr 上限）")
    parser.add_argument("--limit", type=int, help="只跑前 N 檔（測試用）")
    parser.add_argument("--resume-from", help="從代號 >= 此值開始（可恢復中斷）")
    parser.add_argument("--push", action="store_true", help="完成或失敗時推播")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("backfill_monthly_revenue")

    cfg = Config.load()
    db = Database(cfg.database.path)
    fetcher = FinMindFetcher(cfg.finmind, request_delay=args.delay)

    all_stocks = list_tradable_stocks(db)
    done = stocks_with_sufficient_history(db, args.min_months)
    log.info("全市場 4 碼股票 %d 檔；已有 ≥%d 月資料者 %d 檔（跳過）",
             len(all_stocks), args.min_months, len(done))

    targets = [s for s in all_stocks if s not in done]
    if args.resume_from:
        targets = [s for s in targets if s >= args.resume_from]
    if args.limit:
        targets = targets[:args.limit]

    eta_sec = len(targets) * args.delay
    log.info("待處理 %d 檔，預估耗時 %.1f 小時（含 API 等待）",
             len(targets), eta_sec / 3600)

    if not targets:
        log.info("沒有需要回補的股票。")
        return 0

    t0 = time.time()
    success = 0
    failed = 0
    for i, sid in enumerate(targets, 1):
        try:
            df = fetcher.monthly_revenue(sid, args.start)
            if df.empty:
                log.info("[%d/%d] %s: 無資料", i, len(targets), sid)
                continue
            out = df[["date", "stock_id", "revenue", "revenue_month",
                      "revenue_year", "mom_pct", "yoy_pct"]]
            n = db.upsert_df(out, "monthly_revenue")
            success += 1
            # 每 20 檔進度
            if i % 20 == 0 or i == len(targets):
                elapsed = time.time() - t0
                remaining = elapsed / i * (len(targets) - i)
                log.info("[%d/%d] %s: +%d 筆（已完成 %d 檔、失敗 %d；剩餘 ~%.1f 分）",
                         i, len(targets), sid, n, success, failed, remaining / 60)
        except FinMindError as e:
            failed += 1
            log.warning("[%d/%d] %s: %s", i, len(targets), sid, e)
            # 碰到 rate limit 等級錯誤時延長等待
            if "429" in str(e) or "rate" in str(e).lower():
                log.warning("遇到限流，延長等待 120 秒")
                time.sleep(120)
        except Exception as e:
            failed += 1
            log.warning("[%d/%d] %s: %s", i, len(targets), sid, e)

    duration = time.time() - t0
    msg = (f"月營收回補完成：成功 {success} 檔、失敗 {failed} 檔、"
           f"共 {len(targets)} 檔，耗時 {duration/60:.1f} 分")
    log.info(msg)
    if args.push:
        notify(msg, title="📊 月營收回補結束")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
