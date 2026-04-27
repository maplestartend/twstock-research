"""即時報價 / what-if 重算路徑的整合測試。

涵蓋：
1. `score_stock(live_price=X)` — 短/中分數會跟著動，長期分數（ROE/EPS/股利）必須不變
2. `intraday._parse_msg` — '-' / 缺欄位 / 正常報價的解析
3. `/api/stocks/{id}/score?override_price=X` — 422/404 邊界 + 正常路徑
4. `/api/stocks/{id}/intraday` — 不打外網時應回 422，不能 hang
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from api.deps import get_db  # noqa: E402
from api.main import app  # noqa: E402
from app.data import intraday as intraday_mod  # noqa: E402
from app.data.intraday import IntradayQuote  # noqa: E402
from app.scoring.engine import score_stock  # noqa: E402

client = TestClient(app)


# 找一支「有足夠資料 + 個股（非 ETF）」的代號當測試基底
def _pick_test_stock() -> str:
    """挑一支至少 60 日資料的個股；測試本機 DB 沒裝 2330 也能跑。"""
    db = get_db()
    with db.connect() as c:
        row = c.execute(
            """
            SELECT p.stock_id, COUNT(*) AS n FROM daily_price p
            JOIN stock_info i ON i.stock_id = p.stock_id
            WHERE i.type = 'twse' AND length(p.stock_id) = 4 AND p.stock_id NOT LIKE '00%'
            GROUP BY p.stock_id HAVING n >= 200
            ORDER BY p.stock_id LIMIT 1
            """
        ).fetchone()
    if not row:
        pytest.skip("本機 DB 無 200 日以上的個股，跳過")
    return row["stock_id"]


@pytest.fixture(scope="module")
def stock_id() -> str:
    return _pick_test_stock()


# ----------------------------------------------------------------------
# 1) score_stock 用 live_price override
# ----------------------------------------------------------------------
def test_live_price_override_changes_short_not_long(stock_id: str):
    """live_price 應該影響短期分數（吃 close），但長期分數（吃 ROE/EPS/股利）必須不變。"""
    db = get_db()
    base = score_stock(db, stock_id, "")
    assert base is not None
    # -5%：吃技術面（RSI / MA / Bollinger）
    lo = score_stock(db, stock_id, "", live_price=base.close * 0.95)
    hi = score_stock(db, stock_id, "", live_price=base.close * 1.05)
    assert lo is not None and hi is not None

    # 長期維度只用財報資料，與 close 無關 → 三個版本完全相同
    assert base.long.total == lo.long.total == hi.long.total

    # close 欄位應反映覆寫價格
    assert abs(lo.close - base.close * 0.95) < 1e-6
    assert abs(hi.close - base.close * 1.05) < 1e-6

    # signals 標記
    assert lo.signals.get("live_price_used") is True
    assert lo.signals.get("live_price") == pytest.approx(base.close * 0.95)
    assert "live_price_used" not in base.signals  # baseline 不該有這個 flag


def test_override_with_zero_or_negative_is_ignored(stock_id: str):
    """live_price <= 0 應被忽略，等於走 baseline 路徑。"""
    db = get_db()
    base = score_stock(db, stock_id, "")
    assert base is not None
    s_zero = score_stock(db, stock_id, "", live_price=0)
    s_neg = score_stock(db, stock_id, "", live_price=-100)
    assert s_zero is not None and s_neg is not None
    assert s_zero.close == base.close
    assert s_neg.close == base.close
    assert s_zero.short.total == base.short.total
    assert "live_price_used" not in s_zero.signals
    assert "live_price_used" not in s_neg.signals


# ----------------------------------------------------------------------
# 2) intraday._parse_msg
# ----------------------------------------------------------------------
def test_parse_msg_uses_z_when_present():
    """z 有值 → 走 'match' 路徑、is_live=True。"""
    msg = {
        "c": "2330", "z": "1085.0", "pz": "1084.0", "y": "1075.0",
        "o": "1080.0", "h": "1090.0", "l": "1075.0",
        "a": "1086.0_1087.0_", "b": "1085.0_1084.0_",
        "v": "12345", "t": "13:30:00",
    }
    q = intraday_mod._parse_msg(msg)
    assert q is not None
    assert q.price == 1085.0
    assert q.prev_close == 1075.0
    assert q.is_live is True
    assert q.quote_source == "match"
    assert q.bid1 == 1085.0
    assert q.ask1 == 1086.0


def test_parse_msg_falls_back_to_pz_when_z_missing():
    """5 秒撮合間 z='-' 但 pz 還在 → 用 prev_match。"""
    msg = {"c": "2330", "z": "-", "pz": "1084.0", "y": "1075.0",
           "a": "1086.0_", "b": "1083.0_"}
    q = intraday_mod._parse_msg(msg)
    assert q is not None
    assert q.price == 1084.0
    assert q.is_live is True
    assert q.quote_source == "prev_match"


def test_parse_msg_falls_back_to_midpoint_when_z_pz_missing():
    """z 跟 pz 都空白（撮合間隙），但有委託簿 → (b1+a1)/2 中價，is_live=True。

    這是這次 bug 的核心修正：之前直接掉到昨收，使用者看到分數沒變還以為功能壞了。
    """
    msg = {"c": "2330", "z": "-", "pz": "-", "y": "1075.0",
           "a": "1086.0_1087.0_", "b": "1083.0_1082.0_"}
    q = intraday_mod._parse_msg(msg)
    assert q is not None
    assert q.price == pytest.approx((1086.0 + 1083.0) / 2)
    assert q.is_live is True
    assert q.quote_source == "midpoint"
    assert q.bid1 == 1083.0
    assert q.ask1 == 1086.0


def test_parse_msg_falls_back_to_prev_close_when_all_missing():
    """z / pz / 委託簿都空 → 退到昨收，is_live=False（盤後或休市）。"""
    msg = {"c": "2330", "z": "-", "pz": "-", "y": "1075.0",
           "a": "-_-_-_-_-_", "b": "-_-_-_-_-_"}
    q = intraday_mod._parse_msg(msg)
    assert q is not None
    assert q.price == 1075.0
    assert q.is_live is False
    assert q.quote_source == "prev_close"


def test_parse_msg_no_price_at_all_returns_none():
    """連 y 都沒值 → None。"""
    msg = {"c": "2330", "z": "-", "pz": "-", "y": "-"}
    assert intraday_mod._parse_msg(msg) is None


def test_parse_msg_limit_up_locked_uses_ceiling_price():
    """漲停鎖死真實 case（威剛 3260 2026-04-27 11:55）：
    z 偶爾空白、pz 空白、a='-'、b1='0'（市價單佔位），但 h==u 且有成交。
    需要直接用 u 當 price，不能掉到昨收（會誤顯示 -10%）。"""
    msg = {
        "c": "3260", "z": "-", "pz": "-", "y": "400.0",
        "o": "416.5", "h": "440.0", "l": "411.0",
        "u": "440.0", "w": "360.0",     # 漲跌停價
        "a": "-",                         # ask 全空
        "b": "0.0000_440.0000_439.5000_", # bid 第一檔是 0 占位
        "v": "41226", "t": "11:55:00",
    }
    q = intraday_mod._parse_msg(msg)
    assert q is not None
    assert q.price == 440.0
    assert q.is_live is True
    assert q.quote_source == "limit_up"
    # bid1 應被過濾掉（_first_level 把 0 當缺失），不污染 UI 顯示
    assert q.bid1 is None
    assert q.ask1 is None


def test_parse_msg_limit_down_locked_uses_floor_price():
    """跌停鎖死對稱 case：l==w 且有成交 → 用 w。"""
    msg = {
        "c": "1234", "z": "-", "pz": "-", "y": "100.0",
        "o": "98.0", "h": "98.0", "l": "90.0",
        "u": "110.0", "w": "90.0",
        "a": "0.0000_90.0000_90.5000_",  # ask 第一檔占位
        "b": "-",
        "v": "5000",
    }
    q = intraday_mod._parse_msg(msg)
    assert q is not None
    assert q.price == 90.0
    assert q.quote_source == "limit_down"
    assert q.is_live is True


def test_parse_msg_limit_locked_without_volume_falls_through():
    """h==u 但 v=0（盤前撮合試算），不是真正的鎖死，不用 u。"""
    msg = {
        "c": "9999", "z": "-", "pz": "-", "y": "100.0",
        "h": "110.0", "u": "110.0", "l": "100.0", "w": "90.0",
        "a": "111.0_", "b": "109.0_",
        "v": "0",
    }
    q = intraday_mod._parse_msg(msg)
    # v=0 → 不走 limit_up；應走 midpoint
    assert q is not None
    assert q.quote_source == "midpoint"


def test_parse_msg_one_sided_quote_uses_existing_side():
    """急跌：bid 全空、ask 還有；用 ask 當當下價（>= 你買進去要付的價）。"""
    msg = {
        "c": "5678", "z": "-", "pz": "-", "y": "50.0",
        "h": "52.0", "l": "47.0", "u": "55.0", "w": "45.0",
        "a": "47.5_48.0_", "b": "-",
    }
    q = intraday_mod._parse_msg(msg)
    assert q is not None
    assert q.price == 47.5
    assert q.quote_source == "ask_only"
    assert q.is_live is True


def test_first_level_treats_zero_as_missing():
    """市價單 mis 編碼成 0 — 不能當有效 bid/ask。"""
    assert intraday_mod._first_level("0.0000_440.0000_") is None
    assert intraday_mod._first_level("440.0000_") == 440.0
    assert intraday_mod._first_level("-") is None
    assert intraday_mod._first_level(None) is None


# ----------------------------------------------------------------------
# 3) Router /score?override_price=X
# ----------------------------------------------------------------------
def test_score_endpoint_override_price(stock_id: str):
    base = client.get(f"/api/stocks/{stock_id}/score").json()
    base_close = base["close"]
    new_price = round(base_close * 0.95, 2)
    r = client.get(
        f"/api/stocks/{stock_id}/score",
        params={"override_price": new_price},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["livePriceUsed"] is True
    assert data["livePrice"] == pytest.approx(new_price, rel=1e-3)
    assert data["close"] == pytest.approx(new_price, rel=1e-3)
    # 長期分數不變
    assert data["long"]["total"] == base["long"]["total"]


def test_score_endpoint_no_override_baseline(stock_id: str):
    r = client.get(f"/api/stocks/{stock_id}/score")
    assert r.status_code == 200
    data = r.json()
    assert data["livePriceUsed"] is False
    assert data["livePrice"] is None


# ----------------------------------------------------------------------
# 4) Router /intraday — mock mis API（不能在 unit test 真的打外網）
# ----------------------------------------------------------------------
def test_intraday_endpoint_with_mock(stock_id: str):
    fake = IntradayQuote(
        stock_id=stock_id, price=100.0, prev_close=98.0,
        open=99.0, high=101.0, low=98.5,
        volume_lots=1234.0, quote_time="10:30:00", is_live=True,
    )
    with patch.object(intraday_mod, "fetch_quote", return_value=fake):
        r = client.get(f"/api/stocks/{stock_id}/intraday")
    assert r.status_code == 200
    data = r.json()
    assert data["price"] == 100.0
    assert data["isLive"] is True
    assert data["changePct"] == pytest.approx((100.0 - 98.0) / 98.0)


def test_intraday_endpoint_returns_422_when_unavailable(stock_id: str):
    """興櫃 / mis 失敗 → 422。前端可隱藏「即時」按鈕並 fallback。"""
    with patch.object(intraday_mod, "fetch_quote", return_value=None):
        r = client.get(f"/api/stocks/{stock_id}/intraday")
    assert r.status_code == 422


def test_score_endpoint_live_param_with_mock(stock_id: str):
    """?live=1 路徑：mock fetch_quote 回有效報價，分數應該被重算。"""
    db = get_db()
    base = score_stock(db, stock_id, "")
    assert base is not None
    fake_price = base.close * 1.03
    fake = IntradayQuote(
        stock_id=stock_id, price=fake_price, prev_close=base.close,
        open=None, high=None, low=None,
        volume_lots=None, quote_time="11:00:00", is_live=True,
    )
    with patch.object(intraday_mod, "fetch_quote", return_value=fake):
        r = client.get(f"/api/stocks/{stock_id}/score", params={"live": 1})
    assert r.status_code == 200
    data = r.json()
    assert data["livePriceUsed"] is True
    assert data["livePrice"] == pytest.approx(fake_price, rel=1e-3)
    assert data["close"] == pytest.approx(fake_price, rel=1e-3)


def test_score_endpoint_live_fallback_when_intraday_fails(stock_id: str):
    """?live=1 但 mis 抓不到 → 不報錯，回退收盤分數（livePriceUsed=False）。"""
    with patch.object(intraday_mod, "fetch_quote", return_value=None):
        r = client.get(f"/api/stocks/{stock_id}/score", params={"live": 1})
    assert r.status_code == 200
    data = r.json()
    assert data["livePriceUsed"] is False
    assert data["livePrice"] is None
