"""current_state + check_alerts(push=False) 測試。

涵蓋：
- price_below / price_above 未觸發時 actualValue 仍應該回最新收盤
- score_drop 未觸發時 actualValue 仍應該回 7 日 short delta
- check_alerts(push=False) 不應該寫 last_triggered_at（避免佔掉真實 daily push 的 24h cooldown）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import alerts as alerts_mod
from app.alerts import current_state
from app.data.db import Database


@pytest.fixture
def seeded_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "alerts.db")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO stock_info (stock_id, stock_name, type, is_tradable) VALUES (?, ?, ?, ?)",
            ("2330", "台積電", "twse", 1),
        )
        conn.executemany(
            "INSERT INTO daily_price (stock_id, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            [
                ("2330", "2026-04-25", 940, 950, 935, 945, 30_000_000),
                ("2330", "2026-04-28", 945, 955, 942, 950, 32_000_000),
            ],
        )
        # 7 天份的 signal_history，short 從 80 → 60（drop 20 分）
        rows = [
            ("2026-04-22", 80.0),
            ("2026-04-23", 76.0),
            ("2026-04-24", 72.0),
            ("2026-04-25", 70.0),
            ("2026-04-26", 68.0),
            ("2026-04-27", 64.0),
            ("2026-04-28", 60.0),
        ]
        for d, s in rows:
            conn.execute(
                "INSERT INTO signal_history (as_of, stock_id, stock_name, close, short, mid, long, composite, recommendation, is_stale, strategies) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d, "2330", "台積電", 950.0, s, 50.0, 50.0, s, "", 0, ""),
            )
        conn.commit()
    return db


def test_price_below_returns_actual_when_not_triggered(seeded_db: Database):
    rid = alerts_mod.create_rule(seeded_db, "2330", "price_below", threshold=800.0)
    rule = next(r for r in alerts_mod.list_rules(seeded_db) if r["id"] == rid)
    actual, triggered = current_state(seeded_db, rule)
    assert actual == 950.0, "未觸發時 actual 應該是最新收盤 950"
    assert triggered is False


def test_price_below_triggered_when_close_below_threshold(seeded_db: Database):
    rid = alerts_mod.create_rule(seeded_db, "2330", "price_below", threshold=1000.0)
    rule = next(r for r in alerts_mod.list_rules(seeded_db) if r["id"] == rid)
    actual, triggered = current_state(seeded_db, rule)
    assert actual == 950.0
    assert triggered is True, "950 ≤ 1000 應該觸發"


def test_score_drop_returns_delta_when_not_triggered(seeded_db: Database):
    # 設定 threshold=50（要跌 50 分才觸發）→ 實際只跌 20 → 不觸發但仍回 delta
    rid = alerts_mod.create_rule(seeded_db, "2330", "score_drop", threshold=50.0)
    rule = next(r for r in alerts_mod.list_rules(seeded_db) if r["id"] == rid)
    actual, triggered = current_state(seeded_db, rule)
    assert actual is not None
    assert actual == pytest.approx(-20.0, abs=0.1), f"7 日 short delta 應該是 -20，實際 {actual}"
    assert triggered is False


def test_check_alerts_push_false_does_not_set_last_triggered(seeded_db: Database):
    """UI 預覽不該佔掉真實 daily push 的 24h cooldown。"""
    rid = alerts_mod.create_rule(seeded_db, "2330", "price_below", threshold=1000.0)
    hits = alerts_mod.check_alerts(seeded_db, push=False)
    assert len(hits) == 1, "950 ≤ 1000 應該命中"
    rule = next(r for r in alerts_mod.list_rules(seeded_db) if r["id"] == rid)
    assert rule["last_triggered_at"] is None, "push=False 不該寫 last_triggered_at"


def test_check_alerts_push_true_sets_last_triggered(seeded_db: Database, monkeypatch):
    """真實 push 流程應該寫 last_triggered_at（24h cooldown 才能生效）。"""
    monkeypatch.setattr("app.alerts.notify", lambda *a, **k: None)
    rid = alerts_mod.create_rule(seeded_db, "2330", "price_below", threshold=1000.0)
    alerts_mod.check_alerts(seeded_db, push=True)
    rule = next(r for r in alerts_mod.list_rules(seeded_db) if r["id"] == rid)
    assert rule["last_triggered_at"] is not None, "push=True 必須寫 last_triggered_at"
