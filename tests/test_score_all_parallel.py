"""score_all 多進程並行（波次三）。

per-stock 評分是 GIL-bound，執行緒反而更慢 → 用多進程切塊（每塊用 stock_ids=chunk
跑序列 score_all，靠既有不變式保證 per-stock 分數與全市場快照等價），rows 合併後一次
建 DataFrame。本檔：
- CI 可跑：切塊器 _even_chunks、worker 決策 _resolve_score_workers（含 nested 防護）。
- needs_prod_db：並行輸出與序列 bit-identical（含 dtype）。
"""
from __future__ import annotations

import multiprocessing as mp

import pandas as pd
import pytest

from app.scoring import radar


# ----------------------------------------------------------------------
# _even_chunks：保序、不留空塊、平均分配
# ----------------------------------------------------------------------
class TestEvenChunks:
    def test_preserves_order_and_covers_all(self):
        items = list(range(10))
        chunks = radar._even_chunks(items, 3)
        assert [x for ch in chunks for x in ch] == items  # 串接 == 原序
        assert len(chunks) == 3
        assert sorted(len(c) for c in chunks) == [3, 3, 4]  # 平均

    def test_n_larger_than_len_no_empty_chunks(self):
        chunks = radar._even_chunks([1, 2, 3], 8)
        assert all(len(c) > 0 for c in chunks)
        assert [x for ch in chunks for x in ch] == [1, 2, 3]

    def test_single_chunk(self):
        assert radar._even_chunks([1, 2, 3], 1) == [[1, 2, 3]]


# ----------------------------------------------------------------------
# _resolve_score_workers：何時並行、何時序列
# ----------------------------------------------------------------------
class TestResolveWorkers:
    def test_explicit_max_workers_wins(self):
        assert radar._resolve_score_workers(4, None, None, 9999) == 4
        assert radar._resolve_score_workers(1, None, None, 9999) == 1
        # 明確指定 0/負 → 夾到 1
        assert radar._resolve_score_workers(0, None, None, 9999) == 1

    def test_stock_ids_forces_sequential(self):
        # 盤中即時頁（stock_ids）即使檔數多也序列（並行開銷不划算）
        assert radar._resolve_score_workers(None, ["2330", "2317"], None, 9999) == 1

    def test_live_prices_forces_sequential(self):
        assert radar._resolve_score_workers(None, None, {"2330": 100.0}, 9999) == 1

    def test_small_batch_sequential(self):
        assert radar._resolve_score_workers(None, None, None, radar._PARALLEL_MIN_STOCKS - 1) == 1

    def test_large_full_market_parallelizes(self):
        w = radar._resolve_score_workers(None, None, None, radar._PARALLEL_MIN_STOCKS + 100)
        assert w > 1
        assert w <= radar._PARALLEL_MAX_WORKERS

    def test_nested_child_process_forces_sequential(self, monkeypatch):
        """已在 multiprocessing 子進程內（如 backfill day-worker）→ 不再 nested 開 pool。"""
        monkeypatch.setattr(mp, "parent_process", lambda: object())
        assert radar._resolve_score_workers(None, None, None, 9999) == 1


# ----------------------------------------------------------------------
# 並行輸出與序列 bit-identical（需 prod DB）
# ----------------------------------------------------------------------
@pytest.mark.needs_prod_db
def test_parallel_matches_sequential_bit_identical():
    from pathlib import Path
    from app.data.db import Database

    db_path = Path("data/stock.db")
    if not db_path.exists():
        pytest.skip("本機無 data/stock.db")
    db = Database(db_path)
    cands = radar.list_candidate_stocks(db, 60)
    # 取夠過門檻、但不必全市場的一段，控制測試時間
    n = max(radar._PARALLEL_MIN_STOCKS + 50, 0)
    cands = cands[: max(n, 2 * radar._PARALLEL_MIN_STOCKS)]
    if len(cands) < radar._PARALLEL_MIN_STOCKS:
        pytest.skip("候選股不足以觸發並行門檻")

    seq = radar.score_all(db, candidate_stocks=cands, include_fundamentals=True, max_workers=1)
    par = radar.score_all(db, candidate_stocks=cands, include_fundamentals=True, max_workers=2)
    # 列順序 + 值 + dtype 全一致
    pd.testing.assert_frame_equal(seq, par, check_dtype=True)
