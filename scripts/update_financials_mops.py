"""從 TWSE / TPEX OpenAPI 抓全市場最新季度財報（無 token、無配額）。

用法：
    python -m scripts.update_financials_mops            # 抓最新季的綜損 + 資產負債表
    python -m scripts.update_financials_mops --income   # 只抓綜合損益表
    python -m scripts.update_financials_mops --balance  # 只抓資產負債表

資料寫入 `financials_cumulative` 表（與 FinMind 的單季 `financials` 分開保存，
因為 OpenAPI 給的是「當季累計」數字：Q2 = H1 合計、Q4 = 全年合計）。

全市場一次約 10 秒、涵蓋 ~1300 檔。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402
from app.data.mops_financials_fetcher import (  # noqa: E402
    fetch_latest_all,
    fetch_latest_balance_sheet,
    fetch_latest_income_statement,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--income", action="store_true", help="只抓綜合損益表")
    parser.add_argument("--balance", action="store_true", help="只抓資產負債表")
    parser.add_argument("--delay", type=float, default=0.3, help="端點間隔秒數（預設 0.3）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log = logging.getLogger("update_financials_mops")

    cfg = Config.load()
    db = Database(cfg.database.path)

    if args.income and not args.balance:
        df = fetch_latest_income_statement(delay=args.delay)
        label = "綜合損益表"
    elif args.balance and not args.income:
        df = fetch_latest_balance_sheet(delay=args.delay)
        label = "資產負債表"
    else:
        df = fetch_latest_all(delay=args.delay)
        label = "綜損 + 資產負債表"

    if df.empty:
        log.warning("無資料回傳")
        return 1

    n = db.upsert_df(df, "financials_cumulative")
    log.info(
        "MOPS 全市場%s：寫入 %d 筆 / %d 檔 / %s",
        label, n, df["stock_id"].nunique(), sorted(df["date"].unique()),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
