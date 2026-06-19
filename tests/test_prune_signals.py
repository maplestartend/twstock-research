"""S2-4：scripts/prune_signals.prune() 邏輯驗證 — 用合成 signal_history 灌資料測。

（2026-06：signal_history_factor_parts 子因子歷史表已整張移除，本檔只剩 signal_history。）
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from app.data.db import Database
from scripts.prune_signals import prune, prune_all, vacuum

_REF_TODAY = date(2026, 4, 26)  # 固定參考日方便 monkeypatch


@pytest.fixture
def db_with_signals(tmp_path: Path) -> Database:
    """灌 200 天的合成 signal_history，每天 5 檔股票。"""
    db = Database(tmp_path / "test.db")
    today = _REF_TODAY
    rows = []
    for d in range(200):
        as_of = (today - timedelta(days=d)).isoformat()
        for sid in ("2330", "2317", "0050", "1101", "2454"):
            rows.append({
                "as_of": as_of, "stock_id": sid,
                "short": 50.0, "mid": 60.0, "long": 70.0, "composite": 60.0,
                "recommendation": "中性", "strategies": "",
            })
    df = pd.DataFrame(rows)
    db.upsert_df(df, "signal_history")
    return db


def _seed_ic_cache(db: Database, n: int = 3) -> None:
    """塞 n 列假 factor_ic_cache（只填 NOT NULL 欄）。"""
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO factor_ic_cache "
            "(scope, snapshot_max_as_of, lookback_days, horizon, factor, forward_horizon, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [("aggregate", "2026-04-26", 120, "20", f"f{i}", 20, "2026-04-26T00:00:00")
             for i in range(n)],
        )
        conn.commit()


def _count(db: Database, table: str) -> int:
    with db.connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _patched_today(today: date):
    """monkeypatch taipei_today 在 prune_signals 模組內部使用。"""
    return mock.patch("scripts.prune_signals.taipei_today", lambda: today)


class TestPruneRetention:
    def test_dry_run_does_not_modify(self, db_with_signals: Database):
        with _patched_today(date(2026, 4, 26)):
            r = prune(db_with_signals, keep_days=90, dry_run=True)
        assert r["dry_run"] is True
        # 確認沒實際刪
        with db_with_signals.connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM signal_history").fetchone()[0]
        assert after == r["before"]

    def test_keeps_recent_window_intact(self, db_with_signals: Database):
        with _patched_today(date(2026, 4, 26)):
            prune(db_with_signals, keep_days=90, dry_run=False)
        # 近 90 天的資料一筆都不該被刪
        cutoff_iso = (date(2026, 4, 26) - timedelta(days=90)).isoformat()
        with db_with_signals.connect() as conn:
            recent = conn.execute(
                "SELECT COUNT(*) FROM signal_history WHERE as_of >= ?",
                (cutoff_iso,),
            ).fetchone()[0]
        # 近 90 天 × 5 檔 = 至少 90×5 (可能含 cutoff 當天本身)
        assert recent >= 90 * 5

    def test_keeps_only_mondays_beyond_cutoff(self, db_with_signals: Database):
        with _patched_today(date(2026, 4, 26)):
            prune(db_with_signals, keep_days=90, dry_run=False)
        cutoff_iso = (date(2026, 4, 26) - timedelta(days=90)).isoformat()
        # cutoff 之前留下來的 as_of，每筆 weekday 必須 == 0 (週一)
        with db_with_signals.connect() as conn:
            old = conn.execute(
                "SELECT DISTINCT as_of FROM signal_history WHERE as_of < ?",
                (cutoff_iso,),
            ).fetchall()
        for row in old:
            d = date.fromisoformat(row["as_of"])
            assert d.weekday() == 0, f"舊資料殘留非週一: {row['as_of']} weekday={d.weekday()}"

    def test_compression_ratio(self, db_with_signals: Database):
        """壓縮比：cutoff 之前每週 7 天剩 1 天 → 約 7x。"""
        with db_with_signals.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM signal_history").fetchone()[0]
        with _patched_today(date(2026, 4, 26)):
            r = prune(db_with_signals, keep_days=90, dry_run=False)
        with db_with_signals.connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM signal_history").fetchone()[0]
        assert before == r["before"]
        assert after == r["after"]
        assert r["deleted"] > 0
        # 壓縮率 sanity check：刪掉的應該佔總量 30~70%（200 天 ~110 天舊資料 × 6/7）
        ratio = r["deleted"] / r["before"]
        assert 0.3 < ratio < 0.7, f"壓縮比 {ratio:.2%} 異常"

    def test_idempotent(self, db_with_signals: Database):
        """跑兩次 prune 第二次應該不再刪資料（已經乾淨了）。"""
        with _patched_today(date(2026, 4, 26)):
            prune(db_with_signals, keep_days=90, dry_run=False)
            second = prune(db_with_signals, keep_days=90, dry_run=False)
        assert second["deleted"] == 0

    def test_table_param_backward_compat(self, db_with_signals: Database):
        """不帶 table= 時預設清 signal_history（保護既有 caller / market_update 假設）。"""
        with _patched_today(_REF_TODAY):
            r = prune(db_with_signals, keep_days=90, dry_run=True)
        assert r["table"] == "signal_history"


class TestPruneAll:
    def test_prunes_signal_history(self, db_with_signals: Database):
        with _patched_today(_REF_TODAY):
            result = prune_all(db_with_signals, keep_days=90, dry_run=False)
        assert result["signal_history"]["deleted"] > 0
        assert result["signal_history"]["table"] == "signal_history"

    def test_clears_ic_cache_when_signals_deleted(self, db_with_signals: Database):
        _seed_ic_cache(db_with_signals, n=3)
        with _patched_today(_REF_TODAY):
            result = prune_all(db_with_signals, keep_days=90, dry_run=False)
        assert result["signal_history"]["deleted"] > 0
        assert result["ic_cache_cleared"] == 3
        assert _count(db_with_signals, "factor_ic_cache") == 0

    def test_keeps_ic_cache_when_nothing_deleted(self, db_with_signals: Database):
        """keep_days 大到沒有任何列被刪 → 不該動 cache（避免無謂失效）。"""
        _seed_ic_cache(db_with_signals, n=3)
        with _patched_today(_REF_TODAY):
            result = prune_all(db_with_signals, keep_days=9999, dry_run=False)
        assert result["signal_history"]["deleted"] == 0
        assert result["ic_cache_cleared"] == 0
        assert _count(db_with_signals, "factor_ic_cache") == 3

    def test_dry_run_does_not_clear_cache(self, db_with_signals: Database):
        _seed_ic_cache(db_with_signals, n=2)
        with _patched_today(_REF_TODAY):
            result = prune_all(db_with_signals, keep_days=90, dry_run=True)
        assert result["ic_cache_cleared"] == 0
        assert _count(db_with_signals, "factor_ic_cache") == 2


class TestVacuum:
    def test_restores_wal_mode(self, db_with_signals: Database):
        """VACUUM 後 journal_mode 必須切回 wal（守 prune_warrants 式 save/restore）。"""
        ok = vacuum(db_with_signals, best_effort=False)
        assert ok is True
        with db_with_signals.connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_shrinks_after_delete(self, db_with_signals: Database):
        """刪一大段 + VACUUM 後檔案應不大於刪除前。"""
        import os
        with _patched_today(_REF_TODAY):
            prune_all(db_with_signals, keep_days=30, dry_run=False)
        before = os.path.getsize(db_with_signals.path)
        vacuum(db_with_signals, best_effort=False)
        after = os.path.getsize(db_with_signals.path)
        assert after <= before
