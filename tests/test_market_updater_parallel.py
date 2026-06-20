"""證明 fetch_one_date 的 TWSE / TPEx 抓取真的並行（Phase 1 並行 I/O）。

用 threading.Barrier(2)：兩市場的第一個 fetcher 各 wait 同一個 barrier。
- 真並行 → 兩個 thread 都抵達 barrier、一起放行 → fetch_one_date 正常完成。
- 退化成序列 → 先跑的那個 wait 會 timeout 拋 BrokenBarrierError（不是 Twse/TpexError，
  _safe 不會吞）→ 從 fut.result() 冒出來 → fetch_one_date 拋例外 → 測試失敗。

比「比較 wall-time」穩定，不靠 sleep 時長。
"""
from __future__ import annotations

import threading
from unittest.mock import patch

import pandas as pd

from app.data.db import Database
from app.data.market_updater import MarketUpdater


def _ohlcv(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "stock_id": sid, "stock_name": name, "date": "2026-06-18",
            "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000,
        }
        for sid, name in rows
    ])


def test_twse_tpex_fetch_in_parallel(tmp_path):
    db = Database(tmp_path / "x.db")
    updater = MarketUpdater(db, request_delay=0)

    barrier = threading.Barrier(2, timeout=5)
    twse_df = _ohlcv([("2330", "台積電")])
    tpex_df = _ohlcv([("5483", "中美晶")])

    def twse_ohlcv_indices(_date):
        barrier.wait()  # 等 TPEx thread 也到 → 證明兩者同時在跑
        return twse_df, pd.DataFrame()

    def tpex_ohlcv(_date):
        barrier.wait()
        return tpex_df

    empty = pd.DataFrame()
    with patch.object(updater.twse, "daily_ohlcv_and_indices", side_effect=twse_ohlcv_indices), \
         patch.object(updater.tpex, "daily_ohlcv", side_effect=tpex_ohlcv), \
         patch.object(updater.twse, "institutional", return_value=empty), \
         patch.object(updater.tpex, "institutional", return_value=empty), \
         patch.object(updater.twse, "margin", return_value=empty), \
         patch.object(updater.tpex, "margin", return_value=empty), \
         patch.object(updater.twse, "per_pbr", return_value=empty), \
         patch.object(updater.tpex, "per_pbr", return_value=empty):
        # 若退化成序列，這裡會因 BrokenBarrierError 而拋例外
        results = updater.fetch_one_date("20260618")

    # 兩市場的 OHLCV 都進了 daily_price → 兩個 bundle 都跑完且正確合併
    with db.connect() as conn:
        ids = sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT stock_id FROM daily_price"
        ).fetchall())
    assert ids == ["2330", "5483"]
    assert results.get("daily_price") == 2
