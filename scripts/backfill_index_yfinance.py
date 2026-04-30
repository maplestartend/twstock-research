"""把 TWSE 加權指數歷史補到比 TWSE OpenAPI 更早（2022-01 起）。

⚠️ ONE-SHOT 性質：執行一次補完 2022-01 ~ 今天的 ^TWII 資料即可，往後新日期由
   `market_update.py` 抓 TWSE OpenAPI 增量更新。完成日期：2026-04-30（commit 95726ce 起）。
   只在「想往更早回補」（如改 --from 2020-01）時才需要再跑。



TWSE OpenAPI 的 daily_indices 只回近 ~2.7 年資料，但 yfinance 的 `^TWII` 從 ~1997 起
都有，且收盤價與 TWSE 官方數值對齊（已比對過：2023-08 起 502 個 overlap 日期完全一致）。

只補 mid factor 用的「發行量加權股價指數」。其他 60+ 個產業/主題指數 yfinance 沒有，
那些 mid 因子的 RS 計算也不依賴它們，所以不補。

用法：
    python -m scripts.backfill_index_yfinance              # 預設 2022-01-01 ~ 今天，--skip-existing
    python -m scripts.backfill_index_yfinance --from 2020-01-01
    python -m scripts.backfill_index_yfinance --no-skip    # 強制覆寫已有日期
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402

logger = logging.getLogger("backfill_index_yfinance")

TAIEX_NAME = "發行量加權股價指數"


def fetch_taiex(start: str, end: str) -> pd.DataFrame:
    """抓 ^TWII OHLC，回 [date, close, change, change_pct] 四欄（與 index_daily schema 對齊）。"""
    df = yf.Ticker("^TWII").history(start=start, end=end, auto_adjust=False)
    if df.empty:
        return pd.DataFrame(columns=["date", "index_name", "close", "change", "change_pct"])
    df = df.reset_index()
    # yfinance Date 是 tz-aware；轉純日期字串
    df["date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.strftime("%Y-%m-%d")
    df = df[["date", "Close"]].rename(columns={"Close": "close"})
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    # change / change_pct
    df["change"] = df["close"].diff()
    df["change_pct"] = df["close"].pct_change() * 100
    df["index_name"] = TAIEX_NAME
    return df[["date", "index_name", "close", "change", "change_pct"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="從 yfinance ^TWII 補 TWSE 加權指數歷史")
    parser.add_argument("--from", dest="date_from", default="2022-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", default=None, help="結束日期（預設今天）")
    parser.add_argument("--no-skip", action="store_true", help="不跳過 DB 已有日期（強制 upsert）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db = Database(Config.load().database.path)

    end = args.date_to or (date.today() + timedelta(days=1)).isoformat()  # yfinance end 不含當日，加 1
    logger.info("抓 ^TWII: %s ~ %s", args.date_from, end)
    df = fetch_taiex(args.date_from, end)
    if df.empty:
        logger.warning("yfinance 沒回任何資料")
        return 1
    logger.info("yfinance 回傳 %d 列（%s ~ %s）", len(df), df["date"].min(), df["date"].max())

    if not args.no_skip:
        with db.connect() as conn:
            existing = pd.read_sql_query(
                "SELECT DISTINCT date FROM index_daily WHERE index_name = ?",
                conn, params=[TAIEX_NAME],
            )["date"].tolist()
        before = len(df)
        df = df[~df["date"].isin(set(existing))].reset_index(drop=True)
        logger.info("--skip-existing: 跳過 %d 個 DB 已有日期，剩 %d 列待寫入", before - len(df), len(df))

    if df.empty:
        logger.info("沒有需要寫入的新資料")
        return 0

    n = db.upsert_df(df, "index_daily")
    logger.info("index_daily: 寫入 %d 列（index_name=%s，%s ~ %s）", n, TAIEX_NAME, df["date"].min(), df["date"].max())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
