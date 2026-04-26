"""Pytest 共用 fixtures。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# 把專案根加入 sys.path，讓 `from app...` 能 import
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def synthetic_price_df() -> pd.DataFrame:
    """50 根合成 K 線，含明顯趨勢 + 可預測波動，供 ATR / 技術指標測試用。"""
    rng = np.random.default_rng(42)
    n = 50
    base = 100 + np.cumsum(rng.normal(0.2, 1.0, n))  # 緩升趨勢
    noise = rng.normal(0, 0.5, n)
    close = base + noise
    open_ = close - rng.uniform(-1, 1, n)
    high = np.maximum(open_, close) + rng.uniform(0.2, 1.5, n)
    low = np.minimum(open_, close) - rng.uniform(0.2, 1.5, n)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")  # business days
    return pd.DataFrame({
        "date": dates,
        "stock_id": "TEST",
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    })


@pytest.fixture
def flat_price_df() -> pd.DataFrame:
    """完全平盤的 20 根 K 線。測邊界條件。"""
    n = 20
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "stock_id": "FLAT",
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.0] * n,
        "volume": [1_000_000] * n,
    })
