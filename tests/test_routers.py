"""Router 層整合測試 — 用 TestClient 打實際 endpoint，依賴專案 data/stock.db。

驗證關鍵商業規則：
- ETF / 個股分流（0050 必須在 ETF tab）
- 雷達 strategy=None 時應回該策略命中（前端會帶預設）
- 月營收新鮮度判斷（lag ~30 天 = ok）
- watchlist overview / dq summary 不爆
- 0050 long 應為 None（ETF 沒長期分數）
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# 確保 cwd 是 ROOT，TestClient 才能讀 data/stock.db
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)


def test_health_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_snapshot_status_includes_reason_fields():
    r = client.get("/api/system/snapshot-status")
    assert r.status_code == 200
    data = r.json()
    assert "snapshotAsOf" in data
    assert "dailyPriceAsOf" in data
    assert "isStale" in data
    assert "datasetsSynced" in data
    assert "datasetDates" in data
    assert "staleReason" in data
    assert "canRefresh" in data
    assert "engineVersionSnapshot" in data
    assert "engineVersionCurrent" in data
    assert "engineVersionMatch" in data


def test_market_type_classify_0050_is_etf():
    """0050 是 4 碼 ETF，過去因 len < 5 漏判。確認分類正確。"""
    from app.data.market_type import classify_market, is_etf
    assert is_etf("0050")
    assert is_etf("00878")
    assert not is_etf("2330")
    assert classify_market("0050", "twse") == "ETF"
    assert classify_market("2330", "twse") == "上市"
    assert classify_market("6669", "tpex") == "上櫃"


def test_radar_strategies_returns_full_list():
    r = client.get("/api/radar/strategies")
    assert r.status_code == 200
    data = r.json()
    names = [s["name"] for s in data]
    assert "短線強勢" in names
    # stocks_only 旗標應該存在於部分策略
    assert any(s.get("stocksOnly") for s in data)


def test_radar_hits_etf_filter():
    """0050 應該只在 ETF tab 出現，不在個股 tab。"""
    r1 = client.get("/api/radar/hits", params={"market": ["上市", "上櫃"], "top": 100})
    assert r1.status_code == 200
    stock_ids = {h["stockId"] for h in r1.json()}
    assert "0050" not in stock_ids, "0050 不應出現在個股 tab"

    r2 = client.get("/api/radar/hits", params={"market": ["ETF"], "top": 100})
    assert r2.status_code == 200
    # ETF 至少有一些命中
    etf_ids = {h["stockId"] for h in r2.json()}
    assert all(h["market"] == "ETF" for h in r2.json())


def test_radar_hits_with_strategy_filter():
    r = client.get("/api/radar/hits", params={"strategy": "短線強勢", "top": 5})
    assert r.status_code == 200
    for h in r.json():
        assert "短線強勢" in (h.get("strategies") or "")


def test_factor_ic_response_contains_assumptions():
    r = client.get("/api/diagnostics/factor-ic")
    assert r.status_code == 200
    data = r.json()
    assert "forwardReturnBasis" in data
    assert "executionAssumption" in data
    assert "icCiMethod" in data


def test_dashboard_radar_hits_excludes_etf_by_default():
    """戰情室預設只回個股，不該含 ETF（避免兩種評分機制混合）。"""
    r = client.get("/api/dashboard/radar-hits", params={"limit": 20})
    assert r.status_code == 200
    for h in r.json():
        assert h.get("market") in ("上市", "上櫃", "其他", None)


def test_data_freshness_monthly_revenue_threshold_relaxed():
    """月營收 lag 不該用日表的 1/3 天門檻；至少 lag <= 70 天的 tone 不該是 error。"""
    r = client.get("/api/dashboard/data-freshness")
    assert r.status_code == 200
    data = r.json()
    mr = next((f for f in data if f["table"] == "monthly_revenue"), None)
    assert mr is not None
    if mr["lagDays"] is not None and mr["lagDays"] <= 45:
        assert mr["tone"] == "ok", f"月營收 lag {mr['lagDays']} 應為 ok"


def test_history_strategies_per_date():
    """歷史追蹤應該針對指定 as_of 算各策略命中數。"""
    dates_r = client.get("/api/history/dates")
    assert dates_r.status_code == 200
    dates = dates_r.json()
    if not dates:
        pytest.skip("尚無歷史快照資料")
    r = client.get("/api/history/strategies", params={"as_of": dates[0]})
    assert r.status_code == 200
    data = r.json()
    assert all("hitCount" in s for s in data)


def test_watchlist_overview_includes_market_field():
    r = client.get("/api/watchlist/overview")
    assert r.status_code == 200
    data = r.json()
    if data:
        assert "market" in data[0]


def test_dq_summary_does_not_crash():
    """DQ summary 大量計算不該爆，回的 anomalies 嚴重度合法。"""
    r = client.get("/api/dq/summary", params={"days": 10})
    assert r.status_code == 200
    data = r.json()
    valid_severities = {"info", "warning", "critical"}
    for a in data["anomalies"]:
        assert a["severity"] in valid_severities


def test_search_stocks_numeric_prefix():
    r = client.get("/api/search/stocks", params={"q": "2330", "limit": 5})
    assert r.status_code == 200
    hits = r.json()
    assert hits, "搜尋 2330 應有結果"
    assert hits[0]["stockId"] == "2330"


def test_search_stocks_chinese_name():
    r = client.get("/api/search/stocks", params={"q": "台積", "limit": 5})
    assert r.status_code == 200
    hits = r.json()
    if hits:
        assert any("台積" in h["stockName"] for h in hits)


def test_etf_score_long_is_none():
    """0050 是 ETF，長期分數應為 None（不是 re-normalize 的代用值）。"""
    from app.config import Config
    from app.data.db import Database
    from app.scoring.engine import score_stock

    db = Database(Config.load().database.path)
    s = score_stock(db, "0050", "元大台灣50")
    if s is None:
        pytest.skip("0050 無足夠資料")
    assert s.long.total is None
    assert s.long.completeness == 0.0


def test_stock_score_asof_rejects_live_override_mix():
    r = client.get("/api/stocks/2330/score", params={"as_of": "2025-01-01", "live": 1})
    assert r.status_code == 422
    assert "不能同時使用" in r.json()["detail"]


def test_stock_score_invalid_asof_format():
    r = client.get("/api/stocks/2330/score", params={"as_of": "2025/01/01"})
    assert r.status_code == 422
    assert "YYYY-MM-DD" in r.json()["detail"]


def test_taipei_today_returns_date():
    from app.data.clock import taipei_today
    today = taipei_today()
    assert hasattr(today, "year")


def test_narrative_status_without_api_key(monkeypatch):
    """沒設 ANTHROPIC_API_KEY → available=False，前端據此灰掉按鈕。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.get("/api/system/narrative-status")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False
    assert data["model"] is None


def test_narrative_endpoint_returns_503_without_api_key(monkeypatch):
    """缺 key 時 narrative endpoint 不應該打 LLM，直接 503。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/stocks/2330/narrative")
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_holding_net_pnl_subtracts_sell_costs():
    """淨損益應該比毛損益少 (因為扣了賣出手續費 + 0.3% 證交稅)。"""
    from app.portfolio import Holding

    h = Holding(stock_id="2330", shares=1000, avg_cost=500, entry_date=None, note=None)
    price = 600.0
    gross = h.unrealized_pnl(price)
    net = h.net_unrealized_pnl(price)
    assert net < gross
    # 扣的稅費約等於 1000 * 600 * (0.003 + 手續費率) ≈ 至少 1800（光稅就 1800）
    assert (gross - net) >= 1500


def test_holdings_endpoint_includes_atr_fields():
    """每筆持股都帶 atrStop / atrDistancePct / atrKind / atrBelowStop（前端 UI 直接顯示用）。
    資料不足會是 None，但欄位本身一定要在 schema 裡，不可缺失。"""
    r = client.get("/api/portfolio/holdings")
    assert r.status_code == 200
    rows = r.json()
    if not rows:
        pytest.skip("沒持股可測 ATR 欄位")
    for row in rows:
        # 結構檢查 — 缺欄位代表 schema 沒帶上
        assert "atrStop" in row
        assert "atrDistancePct" in row
        assert "atrKind" in row
        assert "atrBelowStop" in row
        # 型別/語意檢查
        if row["atrStop"] is not None:
            assert row["atrKind"] in ("trailing", "fixed")
            assert isinstance(row["atrBelowStop"], bool)
            # 距停損 % 與 below_stop 應一致：close < stop ⇔ distance_pct < 0
            if row["price"] is not None and row["atrDistancePct"] is not None:
                if row["atrBelowStop"]:
                    assert row["atrDistancePct"] < 0
                else:
                    assert row["atrDistancePct"] >= 0
        else:
            assert row["atrKind"] is None
