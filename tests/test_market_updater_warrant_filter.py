"""鎖定 MarketUpdater 不會再把權證寫進 daily_price / institutional / margin / per_pbr。

對應 2026-04-30 P3+ fix：之前 TWSE/TPEX OpenAPI 的 ALLBUT0999 端點會把 5 碼權證一併
回傳，舊版 fetcher 無腦塞進交易資料表 → daily_price 82% / institutional 87% 是權證列。
新版 _filter_tradable() 在寫入前用 stock_info.is_tradable 過濾。
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from app.data.db import Database
from app.data.market_updater import MarketUpdater


def _mk_ohlcv(rows: list[tuple]) -> pd.DataFrame:
    """rows = [(stock_id, stock_name)] — 其餘欄位填假值。"""
    cols = ["stock_id", "stock_name", "date", "open", "high", "low", "close", "volume"]
    data = []
    for sid, name in rows:
        data.append({
            "stock_id": sid, "stock_name": name, "date": "2026-04-30",
            "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000,
        })
    return pd.DataFrame(data, columns=cols)


def _mk_chip(rows: list[str], col: str = "foreign_net") -> pd.DataFrame:
    """三大法人/融資/PER 等下游表的最小資料。"""
    return pd.DataFrame([
        {"stock_id": sid, "date": "2026-04-30", col: 1000}
        for sid in rows
    ])


class TestWarrantFilter:
    def test_warrants_in_ohlcv_get_dropped(self, tmp_path):
        """混入 5 碼權證 + 4 碼真股票 → 寫到 daily_price 的只剩真股票。"""
        db = Database(tmp_path / "x.db")
        updater = MarketUpdater(db, request_delay=0)

        # twse: 真股票 2330 + 權證 70001（5 碼）
        twse_ohlcv = _mk_ohlcv([("2330", "台積電"), ("70001", "某權證")])
        # tpex: 真股票 5483 + 權證 03241
        tpex_ohlcv = _mk_ohlcv([("5483", "中美晶"), ("03241", "另一權證")])

        # 三大法人混入同樣的權證代號
        twse_inst = _mk_chip(["2330", "70001"])
        tpex_inst = _mk_chip(["5483", "03241"])

        with patch.object(updater.twse, "daily_ohlcv_and_indices", return_value=(twse_ohlcv, pd.DataFrame())), \
             patch.object(updater.tpex, "daily_ohlcv", return_value=tpex_ohlcv), \
             patch.object(updater.twse, "institutional", return_value=twse_inst), \
             patch.object(updater.tpex, "institutional", return_value=tpex_inst), \
             patch.object(updater.twse, "margin", return_value=pd.DataFrame()), \
             patch.object(updater.tpex, "margin", return_value=pd.DataFrame()), \
             patch.object(updater.twse, "per_pbr", return_value=pd.DataFrame()), \
             patch.object(updater.tpex, "per_pbr", return_value=pd.DataFrame()):
            updater.fetch_one_date("20260430")

        with db.connect() as conn:
            # daily_price：只有 2330 + 5483，沒有 70001 / 03241
            price_ids = sorted(r[0] for r in conn.execute(
                "SELECT DISTINCT stock_id FROM daily_price"
            ).fetchall())
            assert price_ids == ["2330", "5483"]

            # institutional：同樣只剩真股票
            inst_ids = sorted(r[0] for r in conn.execute(
                "SELECT DISTINCT stock_id FROM institutional"
            ).fetchall())
            assert inst_ids == ["2330", "5483"]

            # stock_info 仍然會記錄 4 個（含權證），只是 is_tradable 標好
            tradable = dict(conn.execute(
                "SELECT stock_id, is_tradable FROM stock_info"
            ).fetchall())
            assert tradable == {"2330": 1, "70001": 0, "5483": 1, "03241": 0}

    def test_pure_warrant_input_writes_nothing_to_price(self, tmp_path):
        """全部都是權證 → daily_price 不寫任何列（不會跳出來爆 KeyError）。"""
        db = Database(tmp_path / "x.db")
        updater = MarketUpdater(db, request_delay=0)

        twse_ohlcv = _mk_ohlcv([("70001", "權證A"), ("70002", "權證B")])

        with patch.object(updater.twse, "daily_ohlcv_and_indices", return_value=(twse_ohlcv, pd.DataFrame())), \
             patch.object(updater.tpex, "daily_ohlcv", return_value=pd.DataFrame()), \
             patch.object(updater.twse, "institutional", return_value=pd.DataFrame()), \
             patch.object(updater.tpex, "institutional", return_value=pd.DataFrame()), \
             patch.object(updater.twse, "margin", return_value=pd.DataFrame()), \
             patch.object(updater.tpex, "margin", return_value=pd.DataFrame()), \
             patch.object(updater.twse, "per_pbr", return_value=pd.DataFrame()), \
             patch.object(updater.tpex, "per_pbr", return_value=pd.DataFrame()):
            updater.fetch_one_date("20260430")

        with db.connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
            assert n == 0
            # stock_info 仍會建檔（is_tradable=0）
            n_info = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
            assert n_info == 2

    def test_etf_codes_pass_through(self, tmp_path):
        """0050 / 00878 等 ETF 代號（4-5 碼，0 開頭）必須通過 — _STOCK_PATTERN 已涵蓋。"""
        db = Database(tmp_path / "x.db")
        updater = MarketUpdater(db, request_delay=0)

        twse_ohlcv = _mk_ohlcv([("0050", "元大台灣50"), ("00878", "國泰永續高股息")])

        with patch.object(updater.twse, "daily_ohlcv_and_indices", return_value=(twse_ohlcv, pd.DataFrame())), \
             patch.object(updater.tpex, "daily_ohlcv", return_value=pd.DataFrame()), \
             patch.object(updater.twse, "institutional", return_value=pd.DataFrame()), \
             patch.object(updater.tpex, "institutional", return_value=pd.DataFrame()), \
             patch.object(updater.twse, "margin", return_value=pd.DataFrame()), \
             patch.object(updater.tpex, "margin", return_value=pd.DataFrame()), \
             patch.object(updater.twse, "per_pbr", return_value=pd.DataFrame()), \
             patch.object(updater.tpex, "per_pbr", return_value=pd.DataFrame()):
            updater.fetch_one_date("20260430")

        with db.connect() as conn:
            ids = sorted(r[0] for r in conn.execute(
                "SELECT DISTINCT stock_id FROM daily_price"
            ).fetchall())
            assert ids == ["0050", "00878"]
