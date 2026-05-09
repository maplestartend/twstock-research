"""ETF 還原資料修補（v5d Wave 1）— 用 yfinance 抓 0050/00631L/00878/00692 完整還原歷史。

問題背景：
- daily_price.close 對 0050 跨年資料還原不一致（見 docs/architecture.md v5d notes）：
  2022 raw=33（分割還原後）、2024 raw=133（分割前）、2026 raw=70（分割後）
- 三種 mode 混在一起，回測 ETF buy-and-hold 結果完全失真
- daily_price_adj 也只有部分日期有 close_adj、且最新缺值

修法：
- yfinance auto_adjust=True 自動處理配息 + 分割還原
- 灌進 daily_price_adj 表，覆蓋既有不一致 row
- 回測腳本只讀 daily_price_adj（不再 fallback 到 raw close）

涵蓋範圍：2022-01-01 ~ 2026-05-08（4 年完整 4 ETF）
"""
from __future__ import annotations

import sqlite3
import sys
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


def fetch_etf_yf(ticker: str, start: str = "2022-01-01", end: str = "2026-05-09") -> pd.DataFrame:
    """yfinance 抓 ETF 完整還原 OHLC。auto_adjust=True 自動處理配息 + 分割。"""
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if df.empty:
        return df
    # multiindex 處理
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
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # 確認 daily_price_adj schema
    cur.execute("PRAGMA table_info(daily_price_adj)")
    cols = {r[1] for r in cur.fetchall()}
    print(f"daily_price_adj cols: {cols}")

    for sid, ticker in ETFS:
        print(f"\n=== {sid} ({ticker}) ===")
        df = fetch_etf_yf(ticker)
        if df.empty:
            print(f"  yfinance returned empty")
            continue
        print(f"  yfinance: {len(df)} rows, range {df['date'].min()} ~ {df['date'].max()}")

        # 灌進 daily_price_adj — 用 INSERT OR REPLACE 覆蓋不一致資料
        # 注意 schema：daily_price_adj 的 close_adj 是還原價、其他欄通常 None
        # 我們把 yfinance close 寫入 close_adj
        n = 0
        for _, r in df.iterrows():
            cur.execute(
                """INSERT OR REPLACE INTO daily_price_adj
                   (stock_id, date, open_adj, high_adj, low_adj, close_adj)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sid, r["date"],
                 float(r["open"]), float(r["high"]),
                 float(r["low"]), float(r["close"])),
            )
            n += 1
        con.commit()
        print(f"  upserted {n} rows into daily_price_adj")

    # Verify: 重新檢查 0050 跨年趨勢（應該一致還原）
    print("\n=== Verify 0050 close_adj yearly snapshot ===")
    for y in (2022, 2023, 2024, 2025, 2026):
        cur.execute(
            "SELECT date, close_adj FROM daily_price_adj WHERE stock_id='0050' AND date BETWEEN ? AND ? ORDER BY date LIMIT 1",
            (f"{y}-01-15", f"{y}-01-25"),
        )
        r = cur.fetchone()
        if r:
            print(f"  {r[0]}: {r[1]:.2f}")

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
