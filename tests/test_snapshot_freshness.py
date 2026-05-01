"""snapshot_freshness — 確保 _SCORING_DATASETS 列出的表名與 schema 對齊。

回歸測試起源：曾經把 institutional / margin 寫成 daily_institutional / daily_margin，
ensure_fresh() 在 snapshot 落後時觸發 all_datasets_synced() 會 OperationalError。
這個 test 直接在乾淨 in-memory schema 上跑 latest_dataset_dates()，表名錯就立刻爆。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.data.db import Database
from app.scoring.snapshot_freshness import (
    _SCORING_DATASETS,
    all_datasets_synced,
    freshness_status,
    is_stale,
    latest_dataset_dates,
)


@pytest.fixture
def empty_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


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
