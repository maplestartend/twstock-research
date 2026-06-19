"""ETF 還原資料修補（v5d Wave 1）— 用 yfinance 抓 ETF 還原歷史。

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

**增量 vs full（2026-06-20 Wave 1 #2）**：
- 預設**增量**：start 取各 ETF 已存最後日期往回 OVERLAP_DAYS 天，只抓近期，省下每天重抓 12 年。
- 但 auto_adjust 在配息/分割日會**回溯重算整段歷史** → 天真增量會在新舊還原基準交界製造斷層。
  故增量模式會比對 overlap 區的 close_adj：偵測到基準變動（配息/分割）就**自動升級成 full 重抓**，
  保證 daily_price_adj 永遠是單一一致的還原基準（見 _basis_changed）。
- `--full` 強制從 FULL_START 全抓（一次性重建用）；`--start` 指定明確起點（不做升級判斷）。
- 任一 ETF **硬失敗**（fetch 例外 / full 模式抓到空）→ 回非零 exit code，讓 daily-update.bat 的
  RC4 告警真的觸發（原本永遠 exit 0 → 告警形同虛設）。
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

# 完整還原歷史的起點（首次 backfill / 偵測到基準變動時的 full 重抓起點）。
FULL_START = "2014-01-01"
# 增量模式回溯天數：抓「已存最後日期 − OVERLAP_DAYS」起，用 overlap 區偵測還原基準是否變動。
OVERLAP_DAYS = 10
# overlap 區 close_adj 相對誤差 > 此值 → 視為配息/分割造成的回溯重算（這些 ETF 的配息殖利率
# 多 ≥1%、分割更大；0.5% 容差可避開 yfinance 取數浮動誤判，又能抓到所有有意義的基準變動）。
_BASIS_TOL = 0.005

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


def _truncate_bad_jumps(df: pd.DataFrame, sid: str) -> pd.DataFrame:
    """截掉「跨日 close 跳變 >5× / <0.2×」前的資料（yfinance 早期拼接不同 source 的污染）。

    例：00631L 在 2014-12-31→2015-01-05 有 22× gap（已知 bug）。保留最後一個 big-jump 之後的乾淨段。
    純函式、不打網路，供單元測試。
    """
    if df.empty:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    ratio = df["close"] / df["close"].shift(1)
    bad = ((ratio > 5.0) | (ratio < 0.2)).fillna(False)
    if bad.any():
        cut_idx = int(bad[bad].index.max())  # 最後一個 bad transition 的位置
        dropped = df.iloc[:cut_idx]
        df = df.iloc[cut_idx:].reset_index(drop=True)
        print(f"  [SANITY] {sid} 偵測到 {int(bad.sum())} 個 close 跳變，截掉 {len(dropped)} 列 "
              f"({dropped['date'].min()} ~ {dropped['date'].max()})")
    return df


def _basis_changed(df: pd.DataFrame, stored: dict[str, float], tol: float = _BASIS_TOL) -> bool:
    """overlap 區（fetched ∩ stored）的 close_adj 是否出現相對誤差 > tol 的偏移。

    True 代表 yfinance 對歷史回溯重算了（配息/分割）→ 呼叫端應升級成 full 重抓，
    避免只覆蓋近期 row 而在更早的舊基準資料間留下斷層。純函式，供單元測試。
    """
    if df.empty or not stored:
        return False
    for d, new_c in zip(df["date"], df["close"]):
        old = stored.get(d)
        if old is None:
            continue
        old = float(old)
        if old > 0 and abs(float(new_c) - old) / old > tol:
            return True
    return False


def _resolve_start(
    cur: sqlite3.Cursor, sid: str, *, full: bool, explicit_start: str | None,
) -> tuple[str, str, str | None]:
    """決定該 ETF 的抓取起點與模式。回 (start, mode, prior_max)。

    mode: 'full'（從 FULL_START 全抓）/ 'explicit'（用 --start）/ 'incremental'（從已存最後日回溯）。
    沒有任何既存 adj 資料 → 一律 full（首次 backfill）。
    """
    row = cur.execute("SELECT MAX(date) FROM daily_price_adj WHERE stock_id=?", (sid,)).fetchone()
    prior_max = row[0] if row else None
    if full:
        return FULL_START, "full", prior_max
    if explicit_start:
        return explicit_start, "explicit", prior_max
    if prior_max:
        start = (date.fromisoformat(prior_max) - timedelta(days=OVERLAP_DAYS)).isoformat()
        return start, "incremental", prior_max
    return FULL_START, "full", prior_max


def _upsert(cur: sqlite3.Cursor, sid: str, df: pd.DataFrame) -> int:
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
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill ETF adjusted prices via yfinance.")
    ap.add_argument("--start", default=None,
                    help="明確起點 (YYYY-MM-DD)，不做基準變動升級判斷；省略=增量")
    ap.add_argument("--full", action="store_true",
                    help=f"強制從 {FULL_START} 全抓（一次性重建）")
    ap.add_argument("--end", default=None,
                    help="Backfill end date (YYYY-MM-DD, exclusive); default = tomorrow")
    ap.add_argument("--quiet", action="store_true", help="Suppress per-ETF logs (still prints summary)")
    args = ap.parse_args()

    end_str = args.end or (date.today() + timedelta(days=1)).isoformat()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    summary: list[tuple[str, int, str, str]] = []
    hard_failures = 0
    for sid, ticker in ETFS:
        start, mode, prior_max = _resolve_start(
            cur, sid, full=args.full, explicit_start=args.start,
        )
        if not args.quiet:
            print(f"\n=== {sid} ({ticker}) [{mode}] start={start} ===")
        try:
            df = fetch_etf_yf(ticker, start, end_str)
        except Exception as e:
            print(f"  [ERROR] {sid} fetch failed: {e}")
            summary.append((sid, 0, "-", "fetch error"))
            hard_failures += 1
            continue
        if df.empty:
            if mode == "incremental":
                # 增量視窗沒有新交易日（週末 / 已是最新）→ 正常、非失敗
                print(f"  [OK] {sid} 無新資料（增量視窗自 {start}）")
                summary.append((sid, 0, prior_max or "-", "up-to-date"))
            else:
                print(f"  [ERROR] {sid} yfinance returned empty (mode={mode})")
                summary.append((sid, 0, "-", "empty"))
                hard_failures += 1
            continue

        # 增量一致性檢查：overlap 區基準變動 → 升級 full 重抓（見 module docstring）
        if mode == "incremental":
            stored = dict(cur.execute(
                "SELECT date, close_adj FROM daily_price_adj WHERE stock_id=? AND date>=?",
                (sid, start),
            ).fetchall())
            if _basis_changed(df, stored):
                print(f"  [READJUST] {sid} 偵測到還原基準變動（配息/分割）→ 重抓 full history")
                try:
                    df = fetch_etf_yf(ticker, FULL_START, end_str)
                except Exception as e:
                    print(f"  [ERROR] {sid} full re-fetch failed: {e}")
                    summary.append((sid, 0, "-", "refetch error"))
                    hard_failures += 1
                    continue
                if df.empty:
                    print(f"  [ERROR] {sid} full re-fetch returned empty")
                    summary.append((sid, 0, "-", "refetch empty"))
                    hard_failures += 1
                    continue

        if not args.quiet:
            print(f"  yfinance: {len(df)} rows, range {df['date'].min()} ~ {df['date'].max()}")

        df = _truncate_bad_jumps(df, sid)
        n = _upsert(cur, sid, df)
        con.commit()
        summary.append((sid, n, df["date"].min(), df["date"].max()))

    print("\n=== Summary ===")
    for sid, n, dmin, dmax in summary:
        print(f"  {sid}: {n} rows ({dmin} ~ {dmax})")
    if hard_failures:
        print(f"  [FAIL] {hard_failures}/{len(ETFS)} ETF 還原價更新失敗")

    con.close()
    return 1 if hard_failures else 0


if __name__ == "__main__":
    sys.exit(main())
