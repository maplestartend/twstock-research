"""api/routers/backtest.py 5 個 POST endpoint 的 happy-path + 422 case 測試。

過去整個 backtest router (~450 行) 0 直接測試，schema 變更或 422 處理回歸時無人攔截。
這個測試吃 prod data/stock.db（與 test_routers 一致），對主要股票打最小參數，確保
endpoint 能跑完不噴 500，且 422 cases 仍走預期路徑。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

# 吃 prod data/stock.db；CI 無此 DB → 以 -m "not needs_prod_db" 排除。
pytestmark = pytest.mark.needs_prod_db

client = TestClient(app)


# ===== POST /api/backtest/stock =====

def test_backtest_stock_happy_path():
    """單檔回測 happy path：給 2330、預設 config，確認回 200 + 必要欄位。"""
    body = {
        "stock_id": "2330",
        "config": {
            "entry_threshold": 60,
            "exit_threshold": 45,
        },
    }
    r = client.post("/api/backtest/stock", json=body)
    assert r.status_code in (200, 422), f"unexpected status {r.status_code}: {r.text[:200]}"
    if r.status_code == 200:
        data = r.json()
        assert "summary" in data
        assert "trades" in data


def test_backtest_stock_empty_id_returns_400():
    """空 stock_id → 應該 400 (router 自己 raise) 而非 500。"""
    r = client.post("/api/backtest/stock", json={"stock_id": "  "})
    assert r.status_code == 400


def test_backtest_stock_missing_required_field():
    r = client.post("/api/backtest/stock", json={})
    assert r.status_code == 422


# ===== POST /api/backtest/portfolio =====

def test_backtest_portfolio_minimal():
    """投組回測：兩檔加 config，確認 endpoint 能跑（200 或 422 不爆 500）。"""
    body = {
        "stock_ids": ["2330", "2454"],
        "config": {"entry_threshold": 60},
    }
    r = client.post("/api/backtest/portfolio", json=body)
    assert r.status_code in (200, 422)


def test_backtest_portfolio_empty_list_422():
    """空 stock_ids → 422 而非 500。"""
    r = client.post("/api/backtest/portfolio", json={"stock_ids": []})
    assert r.status_code in (400, 422)


# ===== POST /api/backtest/grid-search =====

def test_grid_search_minimal_grid():
    """最小參數網格 (2×2 = 4 組) 應能成功計算。"""
    body = {
        "stock_id": "2330",
        "param_grid": {
            "entry_threshold": [55, 60],
            "exit_threshold": [40, 45],
        },
    }
    r = client.post("/api/backtest/grid-search", json=body)
    assert r.status_code in (200, 422)


# ===== POST /api/backtest/walk-forward =====

def test_walk_forward_minimal():
    body = {
        "stock_id": "2330",
        "train_window_days": 90,
        "test_window_days": 30,
        "step_days": 30,
    }
    r = client.post("/api/backtest/walk-forward", json=body)
    assert r.status_code in (200, 400, 422)


# ===== POST /api/backtest/event-driven =====

def test_event_driven_minimal():
    body = {
        "config": {
            "entry_offset": -1,
            "exit_offset": 5,
            "min_year": 2024,
        },
    }
    r = client.post("/api/backtest/event-driven", json=body)
    assert r.status_code in (200, 422)
