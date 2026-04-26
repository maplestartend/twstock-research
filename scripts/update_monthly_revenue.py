"""從 FinMind 抓月營收，存進 monthly_revenue 表。

預設只處理 watchlist + holdings。
    python -m scripts.update_monthly_revenue
    python -m scripts.update_monthly_revenue --stock 2330
    python -m scripts.update_monthly_revenue --stocks 2330,2317,2454
    python -m scripts.update_monthly_revenue --all        # 全市場（FinMind 免費版不支援）
    python -m scripts.update_monthly_revenue --mops       # 全市場最新月（TWSE/TPEX OpenAPI，無 token）
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import watchlist as wl_mod  # noqa: E402
from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.fetcher import FinMindError, FinMindFetcher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", help="單一代號")
    parser.add_argument("--stocks", help="多個代號，逗號分隔")
    parser.add_argument("--all", action="store_true", help="全市場一次抓（FinMind 免費版不支援）")
    parser.add_argument("--mops", action="store_true",
                        help="用 OpenAPI 抓全市場最新月（無 token、無限流）；配 --from/--to 可回補歷史")
    parser.add_argument("--from", dest="ym_from", help="歷史回補起始年月，如 2024-01（僅 --mops）")
    parser.add_argument("--to", dest="ym_to", help="歷史回補結束年月，如 2026-03（僅 --mops）")
    parser.add_argument("--start", default="2022-01-01", help="起始日期（僅 FinMind 模式使用）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("update_monthly_revenue")

    cfg = Config.load()
    db = Database(cfg.database.path)

    if args.mops:
        # 歷史回補模式：--from 與 --to 同時提供
        if args.ym_from and args.ym_to:
            from app.data.mops_fetcher import fetch_monthly_revenue_range
            ys, ms = [int(x) for x in args.ym_from.split("-")]
            ye, me = [int(x) for x in args.ym_to.split("-")]
            log.info("MOPS 歷史回補：%s ~ %s …", args.ym_from, args.ym_to)
            df = fetch_monthly_revenue_range((ys, ms), (ye, me))
            if df.empty:
                log.warning("無資料回傳")
                return 1
            n = db.upsert_df(df, "monthly_revenue")
            log.info("MOPS 歷史月營收：寫入 %d 筆，涵蓋 %d 檔 × %d 個月",
                     n, df["stock_id"].nunique(),
                     df.groupby(["revenue_year", "revenue_month"]).ngroups)
            return 0
        # 預設：只抓最新月
        from app.data.mops_fetcher import fetch_latest_monthly_revenue
        log.info("從 TWSE/TPEX OpenAPI 抓取全市場最新月營收…")
        df = fetch_latest_monthly_revenue()
        if df.empty:
            log.warning("無資料回傳")
            return 1
        n = db.upsert_df(df, "monthly_revenue")
        ym = f"{int(df['revenue_year'].iloc[0])}-{int(df['revenue_month'].iloc[0]):02d}"
        log.info("MOPS 全市場月營收 (%s)：寫入 %d 筆，涵蓋 %d 檔", ym, n, df["stock_id"].nunique())
        return 0

    fetcher = FinMindFetcher(cfg.finmind, request_delay=cfg.fetch.request_delay)

    if args.all:
        log.info("全市場抓取中（單次請求）…")
        try:
            df = fetcher.monthly_revenue_all(args.start)
        except FinMindError as e:
            log.error("FinMind 失敗: %s", e)
            return 1
        if df.empty:
            log.warning("無資料回傳")
            return 0
        out = df[["date", "stock_id", "revenue", "revenue_month", "revenue_year", "mom_pct", "yoy_pct"]]
        n = db.upsert_df(out, "monthly_revenue")
        log.info("全市場月營收：寫入 %d 筆，涵蓋 %d 檔", n, df["stock_id"].nunique())
        return 0

    if args.stock:
        targets = [args.stock]
    elif args.stocks:
        targets = [s.strip() for s in args.stocks.split(",") if s.strip()]
    else:
        # watchlist + holdings
        targets = list(wl_mod.load().keys())
        with db.connect() as conn:
            rows = conn.execute("SELECT stock_id FROM holdings").fetchall()
        targets.extend([r["stock_id"] for r in rows])
        targets = sorted(set(targets))

    log.info("處理 %d 檔", len(targets))
    for i, sid in enumerate(targets, 1):
        try:
            df = fetcher.monthly_revenue(sid, args.start)
            if df.empty:
                log.info("[%d/%d] %s: 無資料", i, len(targets), sid)
                continue
            out = df[["date", "stock_id", "revenue", "revenue_month", "revenue_year", "mom_pct", "yoy_pct"]]
            n = db.upsert_df(out, "monthly_revenue")
            log.info("[%d/%d] %s: +%d 筆", i, len(targets), sid, n)
        except FinMindError as e:
            log.warning("[%d/%d] %s: %s", i, len(targets), sid, e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
