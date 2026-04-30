"""新策略「量能動能」與 vr_macd 欄位 end-to-end 測試。

涵蓋：
1. score_all 輸出帶 vr_macd 欄位（並非全 NaN）。
2. _strat_vr_macd filter：>=60 留、<60 丟、NaN 丟、is_stale=1 丟。
3. STRATEGIES 與 sort/queries 對映正確。
4. signal_history schema 含 vr_macd 欄位。
5. snapshot_today → query_radar_hits("量能動能") 排序與門檻正確。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.data.db import Database
from app.scoring import radar
from app.scoring.history import snapshot_today
from app.scoring.radar import STRATEGIES, _strat_vr_macd
from app.scoring.radar_queries import (
    _ALLOWED_SORT_COLUMNS,
    _STRATEGY_SORT_COLUMN,
    query_radar_hits,
)


def _seed_minimal_price(db: Database, stock_id: str = "2330") -> None:
    """種一支股票 ~150 個交易日的合成價量。沿用 test_score_consistency.py 的模式，
    確保 daily_price 結尾在「今天前一天」(避免 score_all as_of=today 過濾掉)。"""
    rng = np.random.default_rng(7)
    n = 150
    dates = pd.date_range(
        end=(pd.Timestamp.now(tz="Asia/Taipei").normalize() - pd.Timedelta(days=1)).tz_localize(None),
        periods=n,
        freq="B",
    )
    base = 580 + np.cumsum(rng.normal(0.5, 4.0, n))
    close = base + rng.normal(0, 1.5, n)
    open_ = close - rng.uniform(-3, 3, n)
    high = np.maximum(open_, close) + rng.uniform(0.5, 4.0, n)
    low = np.minimum(open_, close) - rng.uniform(0.5, 4.0, n)
    volume = rng.integers(8_000_000, 25_000_000, n).astype(float)

    with db.connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO stock_info (stock_id, stock_name, industry_category, type, is_tradable) "
            "VALUES (?, ?, ?, ?, ?)",
            [(stock_id, "台積電", "半導體業", "twse", 1)],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO daily_price (stock_id, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            [
                (stock_id, d.strftime("%Y-%m-%d"), float(o), float(h), float(l), float(c), float(v))
                for d, o, h, l, c, v in zip(dates, open_, high, low, close, volume)
            ],
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 1. score_all 帶 vr_macd 欄位
# ---------------------------------------------------------------------------
class TestScoreAllExposesVrMacd:
    def test_score_all_has_vr_macd_column(self, tmp_path):
        db = Database(tmp_path / "x.db")
        _seed_minimal_price(db)
        df = radar.score_all(db, include_fundamentals=False)
        assert not df.empty, "score_all 在有資料的情況下應該回非空 DataFrame"
        assert "vr_macd" in df.columns, "rows.append 必須塞 vr_macd 欄位"
        # 至少一檔應該有有效 vr_macd 值（合成資料有 26+ 天 → score_vr_macd 有得算）
        assert df["vr_macd"].notna().any(), "至少一檔股票應該算得出 vr_macd"


# ---------------------------------------------------------------------------
# 2. _strat_vr_macd filter
# ---------------------------------------------------------------------------
class TestStratVrMacdFilter:
    def test_filter_fallback_when_raw_indicators_missing(self):
        """沒有 vr26/macd_hist 欄位（例如純 signal_history seed）→ 退回基本篩選 (vr_macd>=60, fresh)。"""
        df = pd.DataFrame([
            {"stock_id": "A", "vr_macd": 70.0, "is_stale": 0},  # keep
            {"stock_id": "B", "vr_macd": 50.0, "is_stale": 0},  # drop (< 60)
            {"stock_id": "C", "vr_macd": np.nan, "is_stale": 0},  # drop (NaN)
            {"stock_id": "D", "vr_macd": 80.0, "is_stale": 1},  # drop (stale)
            {"stock_id": "E", "vr_macd": 60.0, "is_stale": 0},  # keep (boundary)
        ])
        out = _strat_vr_macd(df)
        assert set(out["stock_id"]) == {"A", "E"}

    def test_filter_with_full_indicators_applies_vr150_gate(self):
        """純 VR 版本：使用者硬條件僅保留 vr26 > 150。"""
        df = pd.DataFrame([
            # 全部 vr_macd>=60 且 fresh，差別只在 vr26
            {"stock_id": "PASS",       "vr_macd": 72.0, "is_stale": 0, "vr26": 200.0},
            {"stock_id": "VR_LOW",     "vr_macd": 88.0, "is_stale": 0, "vr26": 100.0},
            {"stock_id": "VR_BOUNDARY","vr_macd": 70.0, "is_stale": 0, "vr26": 150.0},  # ==150 不算 > 150
            {"stock_id": "VR_HIGH",    "vr_macd": 62.0, "is_stale": 0, "vr26": 480.0},
            {"stock_id": "VR_NAN",     "vr_macd": 80.0, "is_stale": 0, "vr26": np.nan},
        ])
        out = _strat_vr_macd(df)
        assert set(out["stock_id"]) == {"PASS", "VR_HIGH"}, (
            f"VR>150 + vr_macd>=60 + fresh 才能進；實際: {set(out['stock_id'])}"
        )

    def test_filter_returns_empty_if_no_vr_macd_column(self):
        df = pd.DataFrame([{"stock_id": "X", "is_stale": 0}])
        out = _strat_vr_macd(df)
        assert out.empty


# ---------------------------------------------------------------------------
# 3. STRATEGIES 註冊 + radar_queries mapping
# ---------------------------------------------------------------------------
class TestStrategyRegistration:
    def test_strategy_exists_with_correct_metadata(self):
        s = STRATEGIES.get("量能動能")
        assert s is not None, "STRATEGIES 缺少 量能動能"
        assert s.sort_by == "vr_macd"
        assert s.stocks_only is False
        assert s.ascending is False
        assert "VR" in s.description

    def test_radar_queries_mapping(self):
        assert _STRATEGY_SORT_COLUMN.get("量能動能") == "vr_macd"
        assert "vr_macd" in _ALLOWED_SORT_COLUMNS


# ---------------------------------------------------------------------------
# 4. signal_history schema 含 vr_macd
# ---------------------------------------------------------------------------
class TestSignalHistorySchema:
    def test_fresh_db_has_vr_macd_column(self, tmp_path):
        db = Database(tmp_path / "fresh.db")
        with db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_history)").fetchall()}
        assert "vr_macd" in cols, f"signal_history 缺 vr_macd 欄位: {cols}"


# ---------------------------------------------------------------------------
# 5. End-to-end: snapshot → query_radar_hits（量能動能）
# ---------------------------------------------------------------------------
class TestQueryRadarHitsVrMacd:
    @pytest.fixture
    def seeded_db(self, tmp_path):
        """直接 seed signal_history（不跑 score_all），三檔故意給不同 vr_macd 值。

        參考 test_radar_sort_by_horizon.py 的做法：略過 score_all 的計算成本，
        直接寫進 signal_history 驗 query 路徑。"""
        db = Database(tmp_path / "y.db")
        rows = [
            # stock_id, name, short, mid, long, composite, vr_macd, is_stale
            ("V001", "Hot",      55.0, 55.0, 55.0, 55.0, 88.0, 0),  # 最高 vr_macd
            ("V002", "Mid",      60.0, 60.0, 60.0, 60.0, 72.0, 0),  # 中等
            ("V003", "Low",      90.0, 90.0, 90.0, 90.0, 50.0, 0),  # vr_macd<60 → 不該命中
            ("V004", "NoVr",     90.0, 90.0, 90.0, 90.0, None, 0),  # NULL → 不該命中
            ("V005", "Stale",    50.0, 50.0, 50.0, 50.0, 95.0, 1),  # stale → 不該命中
        ]
        sh = pd.DataFrame(rows, columns=[
            "stock_id", "stock_name", "short", "mid", "long", "composite", "vr_macd", "is_stale",
        ])
        sh["as_of"] = "2026-04-27"
        sh["close"] = 100.0
        sh["recommendation"] = "🟢 偏多"
        # 只給「snapshot_today 真實情況下會掛 tag」的 row 設 strategies。
        # _strat_vr_macd 會過濾掉 vr_macd<60 / NaN / is_stale=1，那些情況下 snapshot_today
        # 不會把 "量能動能" 寫入 strategies；query_radar_hits 只是用 LIKE 子字串對映過濾。
        sh["strategies"] = [
            "量能動能",   # V001: 88, fresh → 真會命中
            "量能動能",   # V002: 72, fresh → 真會命中
            "短線強勢",   # V003: vr_macd=50 → 真實情況 _strat_vr_macd 不會掛 tag
            "短線強勢",   # V004: vr_macd=NULL → 真實情況不會掛 tag
            "短線強勢",   # V005: stale=1 → 真實情況不會掛 tag
        ]
        db.upsert_df(sh, "signal_history")
        info = pd.DataFrame(
            [(r[0], r[1], "twse") for r in rows],
            columns=["stock_id", "stock_name", "type"],
        )
        db.upsert_df(info, "stock_info")
        return db

    def test_returns_only_vr_macd_strategy_hits_sorted_desc(self, seeded_db):
        out = query_radar_hits(seeded_db, strategy="量能動能", markets={"上市", "上櫃"})
        ids = [r["stock_id"] for r in out]
        # 只有 V001 / V002 在 strategies tag 裡含「量能動能」（真實 snapshot_today
        # 對 vr_macd<60 / NULL / stale=1 的 row 也不會寫入這個 tag）→ V003/V004/V005 都被 LIKE 排除
        assert ids == ["V001", "V002"], f"應只回 V001+V002 並依 vr_macd DESC 排序: {ids}"

    def test_radar_hit_dict_includes_vr_macd_field(self, seeded_db):
        out = query_radar_hits(seeded_db, strategy="量能動能", markets={"上市", "上櫃"})
        assert out, "至少應該有命中"
        assert "vr_macd" in out[0]
        assert out[0]["vr_macd"] == pytest.approx(88.0)


# ---------------------------------------------------------------------------
# 6. snapshot_today 把 vr_macd 寫進 DB
# ---------------------------------------------------------------------------
class TestSnapshotPropagatesVrMacd:
    def test_snapshot_today_writes_vr_macd(self, tmp_path):
        db = Database(tmp_path / "snap.db")
        _seed_minimal_price(db)
        n = snapshot_today(db, include_fundamentals=False)
        assert n >= 1
        with db.connect() as conn:
            row = conn.execute(
                "SELECT vr_macd FROM signal_history WHERE stock_id = '2330' "
                "ORDER BY as_of DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        # vr_macd 可能 None（若資料剛好不夠）也可能是有效分數；只要欄位存在且查得到就算通過
        # （score_all 那邊已經驗過 .notna().any()）
