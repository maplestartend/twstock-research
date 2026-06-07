"""snapshot_freshness — 確保 _SCORING_DATASETS 列出的表名與 schema 對齊。

回歸測試起源：曾經把 institutional / margin 寫成 daily_institutional / daily_margin，
ensure_fresh() 在 snapshot 落後時觸發 all_datasets_synced() 會 OperationalError。
這個 test 直接在乾淨 in-memory schema 上跑 latest_dataset_dates()，表名錯就立刻爆。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import app.scoring.snapshot_freshness as sf
from app.data.db import Database
from app.scoring.snapshot_freshness import (
    _SCORING_DATASETS,
    all_datasets_synced,
    ensure_fresh,
    freshness_status,
    is_stale,
    latest_dataset_dates,
)


@pytest.fixture
def empty_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _seed_datasets(db: Database, date: str) -> None:
    """填四張 scoring dataset 到同一天（讓 datasets_synced=True）。"""
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO daily_price (date, stock_id, close) VALUES (?,?,?)", (date, "2330", 800.0))
        conn.execute("INSERT OR REPLACE INTO institutional (date, stock_id, foreign_net, investment_trust_net, dealer_net) VALUES (?,?,?,?,?)", (date, "2330", 1.0, 1.0, 1.0))
        conn.execute("INSERT OR REPLACE INTO margin (date, stock_id, margin_balance, short_balance) VALUES (?,?,?,?)", (date, "2330", 1.0, 1.0))
        conn.execute("INSERT OR REPLACE INTO per_pbr (date, stock_id, per, pbr, dividend_yield) VALUES (?,?,?,?,?)", (date, "2330", 12.0, 1.5, 3.0))
        conn.commit()


def test_all_dataset_tables_exist_in_schema(empty_db: Database):
    """每個 _SCORING_DATASETS 列出的表都應該已被 schema 建好（init 在 Database() 建構時跑）。"""
    with empty_db.connect() as conn:
        existing = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    missing = [t for t in _SCORING_DATASETS if t not in existing]
    assert not missing, f"_SCORING_DATASETS 含 schema 沒有的表: {missing}"


def test_latest_dataset_dates_does_not_raise_on_empty_db(empty_db: Database):
    """空 DB 上每張表都應該回傳 None，不可拋 OperationalError。"""
    out = latest_dataset_dates(empty_db)
    assert set(out.keys()) == set(_SCORING_DATASETS)
    assert all(v is None for v in out.values())


def test_all_datasets_synced_false_on_empty_db(empty_db: Database):
    assert all_datasets_synced(empty_db) is False


def test_is_stale_false_when_no_price_data(empty_db: Database):
    """daily_price 為空 → 沒有「最新一日」可比，is_stale 應回 False（不觸發重算）。"""
    assert is_stale(empty_db) is False


def test_engine_version_mismatch_marks_stale(empty_db: Database, monkeypatch):
    """日期相同但 engine_version 不同時也要視為 stale，避免快照與即時計分分岔。"""
    with empty_db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO daily_price (date, stock_id, close) VALUES (?,?,?)",
            ("2026-05-01", "2330", 800.0),
        )
        conn.execute(
            "INSERT OR REPLACE INTO institutional (date, stock_id, foreign_net, investment_trust_net, dealer_net) VALUES (?,?,?,?,?)",
            ("2026-05-01", "2330", 1.0, 1.0, 1.0),
        )
        conn.execute(
            "INSERT OR REPLACE INTO margin (date, stock_id, margin_balance, short_balance) VALUES (?,?,?,?)",
            ("2026-05-01", "2330", 1.0, 1.0),
        )
        conn.execute(
            "INSERT OR REPLACE INTO per_pbr (date, stock_id, per, pbr, dividend_yield) VALUES (?,?,?,?,?)",
            ("2026-05-01", "2330", 12.0, 1.5, 3.0),
        )
        conn.execute(
            "INSERT OR REPLACE INTO signal_history (as_of, stock_id, stock_name, close, engine_version) VALUES (?,?,?,?,?)",
            ("2026-05-01", "2330", "台積電", 800.0, "oldver"),
        )
        conn.commit()

    monkeypatch.setattr("app.scoring.snapshot_freshness.current_engine_version", lambda: "newver")
    st = freshness_status(empty_db)
    assert st["stale_reason"] == "engine_version_mismatch"
    assert st["is_stale"] is True
    assert st["can_refresh"] is True


def test_ensure_fresh_backgrounds_when_snapshot_exists(empty_db: Database, monkeypatch):
    """有舊 snapshot 但落後 → ensure_fresh 不阻塞、立即回 False，重算丟到背景 thread。"""
    import threading

    _seed_datasets(empty_db, "2026-05-02")  # price/籌碼最新到 05-02
    with empty_db.connect() as conn:
        # 舊 snapshot 停在 05-01 → stale，但有舊資料可服務
        conn.execute(
            "INSERT OR REPLACE INTO signal_history (as_of, stock_id, stock_name, close, engine_version) VALUES (?,?,?,?,?)",
            ("2026-05-01", "2330", "台積電", 800.0, current_engine_for(empty_db)),
        )
        conn.commit()

    called = threading.Event()

    def fake_snapshot(db, *a, **k):
        called.set()
        return 1

    monkeypatch.setattr(sf, "_refresh_in_progress", threading.Event())  # 乾淨起點
    monkeypatch.setattr(sf, "snapshot_today", fake_snapshot)

    ret = ensure_fresh(empty_db)
    assert ret is False, "背景模式應立即回 False（不阻塞 request）"
    assert called.wait(timeout=5), "背景 thread 應在數秒內呼叫 snapshot_today"


def test_ensure_fresh_blocks_when_no_snapshot(empty_db: Database, monkeypatch):
    """完全沒有 snapshot（首次）→ 沒舊資料可服務，ensure_fresh 同步阻塞跑完才回 True。"""
    import threading

    _seed_datasets(empty_db, "2026-05-02")  # 有 price、無 signal_history

    calls: list[str] = []

    def fake_snapshot(db, *a, **k):
        calls.append("ran")
        return 1

    monkeypatch.setattr(sf, "_refresh_in_progress", threading.Event())
    monkeypatch.setattr(sf, "snapshot_today", fake_snapshot)

    ret = ensure_fresh(empty_db)
    assert ret is True, "首次無 snapshot 應同步完成（回 True）"
    assert calls == ["ran"], "snapshot_today 應在 ensure_fresh 返回前已同步執行一次"


def current_engine_for(db: Database) -> str:
    from app.scoring.version import current_engine_version
    return current_engine_version()
