"""factor_diagnostics — IC 計算正確性 + 邊界 case。

直接 seed signal_history + daily_price，驗證：
1. 完美正相關因子 → IC 接近 +1
2. 完美負相關因子 → IC 接近 -1
3. 隨機因子 → IC 接近 0
4. 樣本不足（< MIN_DATES_PER_FACTOR 個 IC 點）→ 回 None
5. Quintile spread 方向正確（top - bot ≈ 因子和報酬的高低差）
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.data.db import Database
from app.scoring.factor_diagnostics import (
    FACTORS,
    MIN_DATES_PER_FACTOR,
    compute_factor_ic,
)


def _seed(db: Database, *, factor_to_return_correlation: float, n_dates: int = 60, n_stocks: int = 80) -> None:
    """產生 signal_history + daily_price，short 分數和 5 日 forward return 的 Spearman 相關設定為指定值。

    做法：先 sample 一個 base score（uniform），然後 forward_return = correlation × base_rank + noise。
    再把 base 寫進 signal_history.short，把對應未來 close 寫進 daily_price 形成 forward return。
    """
    rng = np.random.default_rng(42)
    end = datetime.now().date() - timedelta(days=1)
    # 連續 n_dates 個交易日（簡化：直接日曆日，忽略週末）+ 後面留 30 天當 forward window
    dates = [end - timedelta(days=i) for i in range(n_dates + 30 - 1, -1, -1)]
    stocks = [f"S{i:03d}" for i in range(n_stocks)]

    rows_snap = []
    rows_price = []
    for d_idx, d in enumerate(dates):
        d_str = d.isoformat()
        # 為每個 stock 在這一天 sample base score 0–100
        base = rng.uniform(20, 80, n_stocks)
        # 5 日後（如果還在 dates 範圍內）的 close = today_close × (1 + return)
        # return = correlation × normalized_base + iid noise
        normalized = (base - base.mean()) / (base.std() + 1e-9)
        future_return = factor_to_return_correlation * normalized * 0.05 + rng.normal(0, 0.02, n_stocks)

        if d_idx < n_dates:
            for s_idx, sid in enumerate(stocks):
                rows_snap.append((d_str, sid, sid, base[s_idx], 50.0, 50.0, base[s_idx], None, "", 0, ""))
                rows_price.append((sid, d_str, 100.0))
        # 對應 5 日後價格寫入：寫到對應 d_idx + 5 的日期
        target_idx = d_idx + 5
        if target_idx < len(dates):
            target_d = dates[target_idx].isoformat()
            for s_idx, sid in enumerate(stocks):
                rows_price.append((sid, target_d, 100.0 * (1 + future_return[s_idx])))

    with db.connect() as conn:
        for sid in stocks:
            conn.execute(
                "INSERT OR REPLACE INTO stock_info (stock_id, stock_name, type, is_tradable) VALUES (?, ?, ?, ?)",
                (sid, sid, "twse", 1),
            )
        conn.executemany(
            "INSERT OR REPLACE INTO signal_history (as_of, stock_id, stock_name, short, mid, long, composite, vr_macd, recommendation, is_stale, strategies) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows_snap,
        )
        # daily_price 可能同 (sid, date) 重複（forward write 與 base write 撞）→ 用 OR IGNORE 保留先到的
        conn.executemany(
            "INSERT OR IGNORE INTO daily_price (stock_id, date, close) VALUES (?, ?, ?)",
            rows_price,
        )
        conn.commit()


def test_perfect_positive_correlation_yields_high_ic(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    _seed(db, factor_to_return_correlation=1.0)
    res = compute_factor_ic(db, lookback_days=80, horizons=(5,))
    short_5d = next((r for r in res if r.factor == "short" and r.horizon == 5), None)
    assert short_5d is not None and short_5d.ic is not None
    assert short_5d.ic > 0.6, f"完美正相關下 IC 應該 > 0.6，實際 {short_5d.ic}"
    assert short_5d.top_quintile_return is not None
    assert short_5d.bot_quintile_return is not None
    assert short_5d.top_quintile_return > short_5d.bot_quintile_return, "top quintile 應該 > bot quintile"


def test_perfect_negative_correlation_yields_negative_ic(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    _seed(db, factor_to_return_correlation=-1.0)
    res = compute_factor_ic(db, lookback_days=80, horizons=(5,))
    short_5d = next((r for r in res if r.factor == "short" and r.horizon == 5), None)
    assert short_5d is not None and short_5d.ic is not None
    assert short_5d.ic < -0.6, f"完美負相關下 IC 應該 < -0.6，實際 {short_5d.ic}"


def test_no_correlation_yields_near_zero_ic(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    _seed(db, factor_to_return_correlation=0.0)
    res = compute_factor_ic(db, lookback_days=80, horizons=(5,))
    short_5d = next((r for r in res if r.factor == "short" and r.horizon == 5), None)
    assert short_5d is not None and short_5d.ic is not None
    assert abs(short_5d.ic) < 0.15, f"無相關下 IC 應該接近 0（容差 0.15），實際 {short_5d.ic}"


def test_insufficient_data_returns_none_ic(tmp_path: Path):
    """只 seed 4 個日期 → 少於 MIN_DATES_PER_FACTOR (5) → ic 應該是 None。"""
    db = Database(tmp_path / "x.db")
    # 自製極小資料：4 個日期
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO stock_info (stock_id, stock_name, type, is_tradable) VALUES (?, ?, ?, ?)",
            ("AAA", "AAA", "twse", 1),
        )
        for d_idx in range(4):
            d = (datetime.now().date() - timedelta(days=10 + d_idx)).isoformat()
            conn.execute(
                "INSERT INTO signal_history (as_of, stock_id, stock_name, short, mid, long, composite, vr_macd, recommendation, is_stale, strategies) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d, "AAA", "AAA", 50.0, 50.0, 50.0, 50.0, None, "", 0, ""),
            )
            conn.execute(
                "INSERT INTO daily_price (stock_id, date, close) VALUES (?, ?, ?)",
                ("AAA", d, 100.0),
            )
        conn.commit()
    res = compute_factor_ic(db, lookback_days=30, horizons=(5,))
    short_5d = next((r for r in res if r.factor == "short" and r.horizon == 5), None)
    assert short_5d is not None
    assert short_5d.ic is None, "資料不足時 ic 應該是 None"


def test_returns_one_row_per_factor_horizon(tmp_path: Path):
    """無論資料多寡，每個 (factor, horizon) 都應該有一筆紀錄（即使 ic=None）。"""
    db = Database(tmp_path / "x.db")
    _seed(db, factor_to_return_correlation=0.0, n_dates=20)
    res = compute_factor_ic(db, lookback_days=40, horizons=(5, 20, 60))
    expected = len(FACTORS) * 3
    assert len(res) == expected, f"應有 {expected} 列（{len(FACTORS)} factors × 3 horizons），實際 {len(res)}"
