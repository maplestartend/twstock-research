"""walk_forward 記憶化等價性測試（效能優化波次一 #1）。

walk_forward 預建 `{sid: score series}` + 0050 benchmark 快取，取代「每組 param × 每個
split × train/test 都重 load + 重算評分」（n_splits=3、K=6 時同一檔原本算 ~21 次）。

本測試的目的不是測「快不快」，而是釘住「**快取路徑與舊的即時計算路徑 bit-identical**」這個
正確性不變量——只要兩條路徑結果相等，記憶化就是安全的純優化。吃 prod data/stock.db。
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

from app.backtest.engine import (  # noqa: E402
    StrategyConfig,
    _backtest_on_slice,
    _full_score_series,
    walk_forward,
)
from app.config import Config  # noqa: E402
from app.data.db import Database  # noqa: E402

# 吃 prod data/stock.db；CI 無此 DB → 以 -m "not needs_prod_db" 排除。
pytestmark = pytest.mark.needs_prod_db

_SIDS = ["2330", "2317", "2454"]
_START, _END = "2023-01-01", "2025-06-01"


def _db() -> Database:
    return Database(Config.load().database.path)


def _live_sids(db: Database) -> list[str]:
    """只留 prod DB 真的算得出 score series 的代號（避免環境差異 flaky）。"""
    return [s for s in _SIDS if _full_score_series(db, s) is not None]


def test_slice_cache_matches_uncached():
    """同一 cfg + 區間：series_cache/bm_cache 路徑 == 即時計算路徑（彙總 dict 完全相等）。

    跑多組 cfg，順帶證明 bm_cache 跨 cfg 重用後不會把上一組的結果污染進下一組。
    """
    db = _db()
    sids = _live_sids(db)
    if not sids:
        pytest.skip("prod DB 無這些代號的可用資料")

    cache = {s: _full_score_series(db, s) for s in sids}
    cache = {s: v for s, v in cache.items() if v is not None}
    bm_cache: dict = {}

    for cfg in [
        StrategyConfig(entry_threshold=60, exit_threshold=40, stop_loss_pct=0.08, take_profit_pct=0.20),
        StrategyConfig(entry_threshold=70, exit_threshold=35, stop_loss_pct=0.10, take_profit_pct=0.15),
    ]:
        uncached = _backtest_on_slice(db, sids, cfg, _START, _END)
        cached = _backtest_on_slice(
            db, sids, cfg, _START, _END, series_cache=cache, bm_cache=bm_cache
        )
        assert cached == uncached, f"cfg={cfg} 快取結果與即時計算不一致：{cached} != {uncached}"


def test_cache_object_not_mutated():
    """快取的 series 物件在切片回測後不被就地改寫（跨 cfg 共用 / 跨 split 共用安全）。"""
    db = _db()
    sids = _live_sids(db)
    if not sids:
        pytest.skip("prod DB 無這些代號的可用資料")
    sid = sids[0]
    series = _full_score_series(db, sid)
    snapshot = series.copy(deep=True)
    cache = {sid: series}
    cfg = StrategyConfig(entry_threshold=60, exit_threshold=40)
    _backtest_on_slice(db, [sid], cfg, _START, _END, series_cache=cache, bm_cache={})
    pd.testing.assert_frame_equal(cache[sid], snapshot)


def test_walk_forward_deterministic_and_runs():
    """walk_forward（內部已用快取）end-to-end 跑得動，且重跑結果一致（純函式、無隨機性）。"""
    db = _db()
    sids = _live_sids(db)
    if not sids:
        pytest.skip("prod DB 無這些代號的可用資料")
    grid = [
        StrategyConfig(entry_threshold=60, exit_threshold=40, stop_loss_pct=0.08, take_profit_pct=0.20),
        StrategyConfig(entry_threshold=65, exit_threshold=35, stop_loss_pct=0.10, take_profit_pct=0.15),
    ]
    wf1 = walk_forward(db, sids, grid, n_splits=3, train_ratio=0.7)
    wf2 = walk_forward(db, sids, grid, n_splits=3, train_ratio=0.7)
    pd.testing.assert_frame_equal(wf1, wf2)
