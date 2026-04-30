"""把全市場個股 daily OHLC 用 yfinance 補回 2022-01（TWSE OpenAPI 只給近 2.7 年）。

為什麼需要：
- TWSE / TPEX OpenAPI 只回近 ~2.7 年資料 → daily_price 在 2024-01 之前只有自選股 ~100 檔
- IC 分析（diagnostics）跨 980 天時，前 700 天只用 100 檔算 cross-sectional → 樣本太小、
  且自選股本來就有 survivorship bias
- yfinance 對台股有完整覆蓋（^TWII / 2330.TW / 5483.TWO 都能拉到 1997+），同 close
  與 TWSE 官方一致

只補沒有的：跑前先看每檔在 daily_price 的 MIN(date)，只抓「比現有更早」的範圍。
完成後 score_all 對 2022-04 ~ 2023-12 那段就會看到 ~2000 檔全市場，IC 才算數。

⚠️ 不要在 backfill_signal_history / FastAPI 跑的時候動，會搶 SQLite 寫鎖。

用法：
    # dry-run：印出計畫（要抓哪些股票、各幾天）
    python -m scripts.backfill_daily_price_yfinance

    # 實際跑（預設 2022-01-01 起）
    python -m scripts.backfill_daily_price_yfinance --apply

    # 只補特定範圍
    python -m scripts.backfill_daily_price_yfinance --apply --from 2022-01-01 --to 2023-12-31

    # 只補某幾檔（debug 用）
    python -m scripts.backfill_daily_price_yfinance --apply --stocks 2330,2454,5483
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402

logger = logging.getLogger("backfill_daily_price_yfinance")


def _yf_ticker(stock_id: str, market: str | None) -> str:
    """yfinance 命名：上市 .TW、上櫃 .TWO；market 缺失時兩個都試（先 TW 後 TWO）。"""
    if market == "tpex":
        return f"{stock_id}.TWO"
    return f"{stock_id}.TW"


def _list_universe(db: Database, only: set[str] | None = None) -> list[tuple[str, str | None]]:
    """回 [(stock_id, market_type), ...] — 只挑 is_tradable=1 的真股票。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT stock_id, type FROM stock_info "
            "WHERE is_tradable = 1 ORDER BY stock_id"
        ).fetchall()
    out = [(r["stock_id"], r["type"]) for r in rows]
    if only is not None:
        out = [(sid, mkt) for sid, mkt in out if sid in only]
    return out


def _existing_min_date(db: Database) -> dict[str, str]:
    """每檔在 daily_price 的最早日期（用來決定要補多遠）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT stock_id, MIN(date) AS d FROM daily_price GROUP BY stock_id"
        ).fetchall()
    return {r["stock_id"]: r["d"] for r in rows if r["d"]}


def _fetch_one(stock_id: str, market: str | None, start: str, end: str) -> pd.DataFrame:
    """yfinance 抓一檔，回 daily_price schema 對齊的 DataFrame。

    上市/上櫃 ticker 命名不同；如果 stock_info.type 沒明確標 → 兩個都試。
    """
    import yfinance as yf
    candidates = [_yf_ticker(stock_id, market)]
    if market is None or market == "":
        candidates = [f"{stock_id}.TW", f"{stock_id}.TWO"]
    for tick in candidates:
        try:
            df = yf.Ticker(tick).history(
                start=start, end=end, auto_adjust=False,
            )
        except Exception as e:
            logger.debug("%s yfinance 例外: %s", tick, e)
            continue
        if df is None or df.empty:
            continue
        df = df.reset_index()
        # 標準化欄名
        df["date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.strftime("%Y-%m-%d")
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low", "Close": "close",
            "Volume": "volume",
        })
        df["stock_id"] = stock_id
        # daily_price 額外欄位（amount/turnover/spread）yfinance 沒給 → 留 NaN，DB schema 接得住
        for col in ("amount", "turnover", "spread"):
            df[col] = None
        cols = ["date", "stock_id", "open", "high", "low", "close",
                "volume", "amount", "turnover", "spread"]
        df = df[cols].dropna(subset=["close"]).reset_index(drop=True)
        return df
    return pd.DataFrame(columns=["date", "stock_id", "open", "high", "low", "close",
                                 "volume", "amount", "turnover", "spread"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", default="2022-01-01",
                        help="起始日期（預設 2022-01-01）")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="結束日期（預設今天）")
    parser.add_argument("--apply", action="store_true",
                        help="實際抓（沒帶 --apply 只印計畫）")
    parser.add_argument("--stocks", default=None,
                        help="逗號分隔代號，只抓這幾檔（debug 用，不指定則跑全市場）")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="只補比現有 MIN(date) 更早的部分（預設開）")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false",
                        help="強制覆蓋 — 整段重抓（會跟 TWSE 既有資料 upsert 衝突）")
    parser.add_argument("--delay", type=float, default=0.2,
                        help="每檔之間 sleep 秒數，避免 yfinance rate limit (預設 0.2s)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db = Database(Config.load().database.path)

    only = None
    if args.stocks:
        only = {s.strip() for s in args.stocks.split(",") if s.strip()}

    universe = _list_universe(db, only=only)
    end_str = args.date_to or date.today().isoformat()
    end = datetime.strptime(end_str, "%Y-%m-%d").date()

    logger.info("=== yfinance 個股 daily_price 補洞 ===")
    logger.info("可交易股票池: %d 檔（is_tradable=1）", len(universe))
    logger.info("目標時段: %s ~ %s", args.date_from, end_str)

    existing_min = _existing_min_date(db) if args.skip_existing else {}

    # 計畫：每檔要抓 [args.date_from, min(現有 MIN(date)-1d, end)]
    plan: list[tuple[str, str | None, str, str]] = []  # (sid, market, start, end)
    target_start = datetime.strptime(args.date_from, "%Y-%m-%d").date()
    n_full_skipped = 0
    for sid, market in universe:
        # 沒任何 daily_price → 抓 [from, end]
        # 已有 MIN(date) ≤ from → 整段已蓋完，跳過
        # 已有 MIN(date) > from → 抓 [from, MIN-1d]
        existing = existing_min.get(sid)
        if existing:
            existing_d = datetime.strptime(existing, "%Y-%m-%d").date()
            if existing_d <= target_start:
                n_full_skipped += 1
                continue
            this_end = (existing_d - timedelta(days=1)).isoformat()
        else:
            this_end = end_str
        plan.append((sid, market, args.date_from, this_end))

    logger.info("已蓋滿（無需補）: %d 檔", n_full_skipped)
    logger.info("實際待抓: %d 檔", len(plan))

    if not args.apply:
        if plan[:5]:
            logger.info("前 5 個 plan 範例：")
            for sid, mkt, s, e in plan[:5]:
                logger.info("  %s (%s): %s ~ %s", sid, mkt or "?", s, e)
        logger.info("[dry-run] 加 --apply 真的跑。預估 %d 檔 × ~1s/檔 ≈ %.0f 分鐘",
                    len(plan), len(plan) * (1 + args.delay) / 60)
        return 0

    if not plan:
        logger.info("沒有需要抓的，結束")
        return 0

    t0 = time.time()
    n_ok = 0
    n_empty = 0
    n_err = 0
    total_rows = 0
    for i, (sid, market, s, e) in enumerate(plan, 1):
        try:
            df = _fetch_one(sid, market, s, e)
        except Exception as exc:
            logger.warning("[%d/%d] %s 失敗: %s", i, len(plan), sid, exc)
            n_err += 1
            time.sleep(args.delay)
            continue
        if df.empty:
            n_empty += 1
        else:
            n = db.upsert_df(df, "daily_price")
            total_rows += n
            n_ok += 1
            if n_ok % 50 == 0:
                elapsed = time.time() - t0
                eta = (elapsed / n_ok) * (len(plan) - i)
                logger.info("[%d/%d] 已寫 %d 列，ETA %.0f 分鐘", i, len(plan), total_rows, eta / 60)
        if args.delay > 0:
            time.sleep(args.delay)

    elapsed = time.time() - t0
    logger.info("=== 完成 ===")
    logger.info("耗時 %.1f 分鐘", elapsed / 60)
    logger.info("成功 %d 檔 / 空回 %d 檔 / 例外 %d 檔", n_ok, n_empty, n_err)
    logger.info("共寫入 daily_price %s 列", f"{total_rows:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
