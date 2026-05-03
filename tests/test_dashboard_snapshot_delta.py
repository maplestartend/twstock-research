from __future__ import annotations

from api.routers.dashboard import snapshot_delta
from app.data.db import Database


def _seed_signal_history(db: Database) -> tuple[str, str]:
    prev = "2026-05-01"
    latest = "2026-05-02"
    rows = [
        (prev, "1111", "甲公司", 60.0, ""),
        (prev, "2222", "乙公司", 82.0, "突破月線, 籌碼"),
        (prev, "3333", "丙公司", 50.0, "續強"),
        (prev, "4444", "丁公司", 10.0, ""),
        (latest, "1111", "甲公司", 88.0, "短線強勢, 量能放大"),
        (latest, "2222", "乙公司", 70.0, ""),
        (latest, "3333", "丙公司", 55.0, "續強"),
        (latest, "4444", "丁公司", 25.0, ""),
    ]
    with db.connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO signal_history "
            "(as_of, stock_id, stock_name, composite, strategies) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    return latest, prev


def test_snapshot_delta_sql_path_filters_correctly(tmp_path):
    db = Database(tmp_path / "delta.db")
    latest, prev = _seed_signal_history(db)

    out = snapshot_delta(top=2, db=db)

    assert out.latest_as_of == latest
    assert out.prev_as_of == prev

    # 1111: 無命中 -> 有命中
    assert [h.stock_id for h in out.new_hits] == ["1111"]
    assert out.new_hits[0].strategies == ["短線強勢", "量能放大"]

    # 2222: 有命中 -> 無命中
    assert [h.stock_id for h in out.dropped_hits] == ["2222"]
    assert out.dropped_hits[0].strategies == ["突破月線", "籌碼"]
    assert out.dropped_hits[0].composite == 70.0

    # abs delta 排序前 2 名：1111(+28), 4444(+15)
    assert [m.stock_id for m in out.big_movers] == ["1111", "4444"]
    assert out.big_movers[0].delta == 28.0
    assert out.big_movers[1].delta == 15.0


def test_snapshot_delta_top_zero_returns_empty_lists(tmp_path):
    db = Database(tmp_path / "delta_zero.db")
    latest, prev = _seed_signal_history(db)

    out = snapshot_delta(top=0, db=db)

    assert out.latest_as_of == latest
    assert out.prev_as_of == prev
    assert out.new_hits == []
    assert out.dropped_hits == []
    assert out.big_movers == []
