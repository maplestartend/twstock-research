"""snapshot_today 流動性硬過濾：低成交額或限漲跌停股票不該獲得策略標籤。

雷達 hits 過濾依據 signal_history.strategies 是否非空，所以 gating 在策略寫入時做即可，
不需要動 query_radar_hits 或 score_all 的分數本身。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.data.db import Database
from app.scoring import history as hist_mod
from app.scoring import radar as radar_mod


def _make_score_df(rows: list[dict]) -> pd.DataFrame:
    """建立模擬 score_all 輸出。預設值都會命中『短線強勢』(short>=65, mid>=50)，
    其他策略需要的欄位給「不會命中」的預設（避免測試斷言被其他策略干擾）。"""
    base = {"as_of": "2026-04-29", "is_stale": 0}
    out = []
    for r in rows:
        full = {**base, **r}
        full.setdefault("short", 80.0)
        full.setdefault("mid", 60.0)
        full.setdefault("long", 50.0)  # < 65 → 不命中長期價值/三榜俱佳
        full.setdefault("composite", 70.0)
        full.setdefault("close", 100.0)
        full.setdefault("vr_macd", 30.0)  # < 60 → 不命中量能動能
        full.setdefault("data_completeness", 1.0)
        full.setdefault("recommendation", "")
        # 不會命中其他策略的中性值
        full.setdefault("foreign_streak_buy", 0)
        full.setdefault("foreign_streak_sell", 0)
        full.setdefault("foreign_cum20", 0)
        full.setdefault("rs20", 0.0)
        full.setdefault("rs60", 0.0)
        full.setdefault("rev_mom", 0.0)
        full.setdefault("rev_yoy_month", 0.0)
        full.setdefault("rev_yoy_streak_pos", 0)
        full.setdefault("rev_yoy_streak_hot", 0)
        out.append(full)
    return pd.DataFrame(out)


def _read_strategies(db: Database) -> dict[str, str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT stock_id, strategies FROM signal_history WHERE as_of=?",
            ("2026-04-29",),
        ).fetchall()
    return {r["stock_id"]: r["strategies"] or "" for r in rows}


def test_low_amount_stock_gets_no_strategy(tmp_path, monkeypatch):
    db = Database(tmp_path / "x.db")
    df = _make_score_df([
        {"stock_id": "AAA", "stock_name": "Liquid",   "amount_20d": 50_000_000.0, "pct_change_today": 0.02},
        {"stock_id": "BBB", "stock_name": "Illiquid", "amount_20d":     500_000.0, "pct_change_today": 0.01},
    ])
    monkeypatch.setattr(radar_mod, "score_all", lambda *a, **k: df)
    hist_mod.snapshot_today(db)
    strategies = _read_strategies(db)
    assert "短線強勢" in strategies["AAA"], "高成交額股票應該保留策略標籤"
    assert strategies["BBB"] == "", "20 日均額 < 100 萬應該被過濾掉，沒有策略標籤"


def test_limit_up_stock_gets_no_strategy(tmp_path, monkeypatch):
    db = Database(tmp_path / "x.db")
    df = _make_score_df([
        {"stock_id": "NRM", "stock_name": "Normal",   "amount_20d": 30_000_000.0, "pct_change_today":  0.03},
        {"stock_id": "LU",  "stock_name": "LimitUp",  "amount_20d": 30_000_000.0, "pct_change_today":  0.099},
        {"stock_id": "LD",  "stock_name": "LimitDn",  "amount_20d": 30_000_000.0, "pct_change_today": -0.097},
    ])
    monkeypatch.setattr(radar_mod, "score_all", lambda *a, **k: df)
    hist_mod.snapshot_today(db)
    strategies = _read_strategies(db)
    assert "短線強勢" in strategies["NRM"]
    assert strategies["LU"] == "", "當日 +9.9% 視為漲停鎖死，不該命中"
    assert strategies["LD"] == "", "當日 -9.7% 視為跌停鎖死，不該命中"


def test_filter_skipped_when_amount_data_unavailable(tmp_path, monkeypatch):
    """若 amount_20d 大量缺值（舊 DB / 早期資料），filter 應該跳過避免清空雷達。"""
    db = Database(tmp_path / "x.db")
    df = _make_score_df([
        {"stock_id": "X1", "stock_name": "S1", "amount_20d": None, "pct_change_today": None},
        {"stock_id": "X2", "stock_name": "S2", "amount_20d": None, "pct_change_today": None},
        {"stock_id": "X3", "stock_name": "S3", "amount_20d": None, "pct_change_today": None},
    ])
    monkeypatch.setattr(radar_mod, "score_all", lambda *a, **k: df)
    hist_mod.snapshot_today(db)
    strategies = _read_strategies(db)
    # 三檔都 short>=65 mid>=50 應該都命中短線強勢（filter 因為缺資料而 skip）
    assert all("短線強勢" in v for v in strategies.values()), (
        f"資料缺值時不該套 filter：{strategies}"
    )


def test_score_row_still_written_for_illiquid_stock(tmp_path, monkeypatch):
    """流動性 gating 只影響 strategies 欄；分數本身仍要寫進 signal_history。"""
    db = Database(tmp_path / "x.db")
    df = _make_score_df([
        {"stock_id": "BBB", "stock_name": "Illiquid", "amount_20d": 200_000.0, "pct_change_today": 0.0},
    ])
    monkeypatch.setattr(radar_mod, "score_all", lambda *a, **k: df)
    hist_mod.snapshot_today(db)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT stock_id, short, mid, composite, strategies FROM signal_history WHERE stock_id=?",
            ("BBB",),
        ).fetchone()
    assert row is not None, "個股歷史分數不應該因流動性過濾就消失"
    assert row["short"] == 80.0
    assert row["composite"] == 70.0
    assert (row["strategies"] or "") == ""
