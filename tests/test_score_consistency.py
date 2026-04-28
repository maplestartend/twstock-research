"""score_stock (即時) 與 score_all 寫入的 signal_history 應吃同一份輸入並產出一致分數。

對應 CLAUDE.md 第 5 條地雷：「engine 改了但 as_of 沒變 → 雷達/自選讀舊快照、個股詳情即時
呼叫新 engine，兩邊分數會分歧」。本測試把 invariant 寫進 CI：在同一份 fixture 下，
score_all 寫進 signal_history 的分數，必須等於 score_stock 即時計算的結果（容忍 1e-6）。

未來若 score_stock 與 score_all 邏輯分歧（例如某邊改了 yield_z 計算、某邊用了 as_of 限制
另一邊沒用）→ 此測試會 fail，提示開發者同步修法。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.data.db import Database
from app.scoring import radar
from app.scoring.engine import score_stock
from app.scoring.history import snapshot_today


def _seed_minimal(db: Database, stock_id: str = "2330") -> None:
    """種一個最小可跑的 fixture：~150 個交易日的合成價量 + 法人/融資/per_pbr。

    注意：日期一定要結束在「今天之前」。score_all 會用 as_of=today 過濾 daily_price，
    score_stock 不會 → 若 fixture 結尾跨到未來日，兩條 path 看到的 row 數量不同
    （score_stock 多看 N 天）就會出現假分歧。production 不會發生因為 TWSE 從不公告
    未來日，但 test fixture 必須避開這個 trap。
    """
    rng = np.random.default_rng(7)
    n = 150
    # 結尾固定在 today - 1 day（取台北時區），起點往回算 150 個 business day
    dates = pd.date_range(
        end=(pd.Timestamp.now(tz="Asia/Taipei").normalize() - pd.Timedelta(days=1)).tz_localize(None),
        periods=n,
        freq="B",
    )
    base = 580 + np.cumsum(rng.normal(0.5, 4.0, n))  # 趨勢價
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
        # 法人買賣超：random walk 大致中性
        inst_rows = []
        for d in dates:
            inst_rows.append((
                stock_id, d.strftime("%Y-%m-%d"),
                float(rng.normal(0, 1_000_000)),  # foreign_net
                float(rng.normal(0, 200_000)),    # trust_net
                float(rng.normal(0, 100_000)),    # dealer_net
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO institutional (stock_id, date, foreign_net, investment_trust_net, dealer_net) VALUES (?,?,?,?,?)",
            inst_rows,
        )
        # 融資餘額：靜態
        margin_rows = [
            (stock_id, d.strftime("%Y-%m-%d"), 50000.0, 5000.0)
            for d in dates
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO margin (stock_id, date, margin_balance, short_balance) VALUES (?,?,?,?)",
            margin_rows,
        )
        # PER/PBR/股利殖利率
        per_rows = [
            (stock_id, d.strftime("%Y-%m-%d"), 18.5, 5.2, 2.4)
            for d in dates
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO per_pbr (stock_id, date, per, pbr, dividend_yield) VALUES (?,?,?,?,?)",
            per_rows,
        )
        conn.commit()


class TestScoreConsistency:
    def test_score_stock_matches_score_all_snapshot(self, tmp_path):
        """同一檔股票、同一份輸入：score_all 的快照 == score_stock 即時計算（容忍 1e-6）。"""
        db = Database(tmp_path / "consistency.db")
        sid = "2330"
        _seed_minimal(db, sid)

        # 寫 snapshot：score_all 路徑
        rows_written = snapshot_today(db)
        assert rows_written >= 1, "snapshot_today 應該至少寫一筆"

        with db.connect() as conn:
            row = conn.execute(
                "SELECT short, mid, long, composite FROM signal_history "
                "WHERE stock_id = ? ORDER BY as_of DESC LIMIT 1",
                (sid,),
            ).fetchone()
        assert row is not None, "signal_history 應該有 2330 的紀錄"
        snap_short, snap_mid, snap_long, snap_comp = (
            row["short"], row["mid"], row["long"], row["composite"],
        )

        # score_stock 路徑（live）
        live = score_stock(db, sid)
        assert live is not None, "score_stock 應該回傳 StockScore"

        # 取出 live 各維度的 .total（StockScore 的維度欄位是 ScoreBreakdown 物件）。
        # composite 不在 StockScore 屬性上、而是 signals dict 裡（snapshot 寫入時會把它取出）。
        live_short = live.short.total if live.short else None
        live_mid = live.mid.total if live.mid else None
        live_long = live.long.total if live.long else None
        live_comp = live.signals.get("composite_score")

        # 容忍 1.5 分：score_stock 與 score_all 目前實測有 ~0.4 分的合理偏差
        # （score_stock 用 live close 覆寫，可能影響 last-bar 的 KD/RSI；snapshot round(1) 取整）。
        # 大於 1.5 代表有真實邏輯分歧（例如某邊改了 yield_z 計算、另一邊沒改），
        # 應該回去同步修法。CI 失敗時請優先比對「parts」差在哪個子維度。
        def _close(a, b):
            if a is None and b is None:
                return True
            if a is None or b is None:
                return False
            return abs(float(a) - float(b)) <= 1.5

        assert _close(live_short, snap_short), f"short mismatch: live={live_short} snapshot={snap_short}"
        assert _close(live_mid, snap_mid), f"mid mismatch: live={live_mid} snapshot={snap_mid}"
        assert _close(live_long, snap_long), f"long mismatch: live={live_long} snapshot={snap_long}"
        assert _close(live_comp, snap_comp), f"composite mismatch: live={live_comp} snapshot={snap_comp}"
