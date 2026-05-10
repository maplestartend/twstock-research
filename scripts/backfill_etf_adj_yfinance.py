"""ETF 還原資料修補（v5d Wave 1）— 用 yfinance 抓 ETF 完整還原歷史。

問題背景：
- daily_price.close 對 0050 / 00631L 跨年資料還原模式不一致（早期已還原、後期未還原）
- FinMind 對槓桿 ETF（00631L）沒提供 split events → adj_event 表空 → adjuster.py 算不出還原價
- 三種還原模式混在一起，ETF buy-and-hold 回測完全失真

修法：
- yfinance auto_adjust=True 自動處理配息 + 分割還原
- 灌進 daily_price_adj 表，覆蓋既有不一致 row
- 回測 / scoring 只讀 daily_price_adj（不 fallback 到 raw close）

每日整合：daily-update.bat 在 market_update 之後跑一次此腳本，自動補當天還原價。
end 日期動態取「明天」（yfinance 的 end 是 exclusive），確保覆蓋到最新交易日。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "stock.db"

# ETF list（含 yfinance ticker 對應）
ETFS = [
    ("0050",   "0050.TW"),     # 元大台灣 50
    ("00631L", "00631L.TW"),   # 元大台灣 50 正 2
    ("00878",  "00878.TW"),    # 國泰永續高股息
    ("00692",  "00692.TW"),    # 富邦台灣 50
]


def fetch_etf_yf(ticker: str, start: str, end: str) -> pd.DataFrame:
    """yfinance 抓 ETF 完整還原 OHLC。auto_adjust=True 自動處理配息 + 分割。"""
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close",
        "Volume": "volume",
    })
    return df[["date", "open", "high", "low", "close", "volume"]]


def main():
    ap = argparse.ArgumentParser(description="Backfill ETF adjusted prices via yfinance.")
    ap.add_argument("--start", default="2014-01-01", help="Backfill start date (YYYY-MM-DD)")
    ap.add_argument("--end", default=None,
                    help="Backfill end date (YYYY-MM-DD, exclusive); default = tomorrow")
    ap.add_argument("--quiet", action="store_true", help="Suppress per-ETF logs (still prints summary)")
    args = ap.parse_args()

    end_str = args.end or (date.today() + timedelta(days=1)).isoformat()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    summary: list[tuple[str, int, str, str]] = []
    for sid, ticker in ETFS:
        if not args.quiet:
            print(f"\n=== {sid} ({ticker}) ===")
        try:
            df = fetch_etf_yf(ticker, args.start, end_str)
        except Exception as e:
            print(f"  [ERROR] {sid} fetch failed: {e}")
            summary.append((sid, 0, "-", "fetch error"))
            continue
        if df.empty:
            print(f"  [WARN] {sid} yfinance returned empty")
            summary.append((sid, 0, "-", "empty"))
            continue
        if not args.quiet:
            print(f"  yfinance: {len(df)} rows, range {df['date'].min()} ~ {df['date'].max()}")

        rows = [
            (sid, r["date"], float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]))
            for _, r in df.iterrows()
        ]
        cur.executemany(
            """INSERT OR REPLACE INTO daily_price_adj
               (stock_id, date, open_adj, high_adj, low_adj, close_adj)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        con.commit()
        summary.append((sid, len(rows), df["date"].min(), df["date"].max()))

    print("\n=== Summary ===")
    for sid, n, dmin, dmax in summary:
        print(f"  {sid}: {n} rows ({dmin} ~ {dmax})")

    con.close()


if __name__ == "__main__":
    main()
