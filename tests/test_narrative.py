"""LLM narrative 測試。

不依賴真的 anthropic 套件 — 全程 monkey-patch get_client，避免測試環境要灌 SDK / 設 key。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.data.db import Database
from app.narrative import cache as narrative_cache
from app.narrative import client as narrative_client
from app.narrative.stock_narrative import KIND_STOCK_OVERVIEW, generate_stock_narrative


@pytest.fixture
def tmp_db() -> Database:
    """每個測試一個獨立 sqlite，避免互相污染。"""
    with tempfile.TemporaryDirectory() as d:
        yield Database(Path(d) / "test.db")


@pytest.fixture
def fake_score_view() -> dict:
    return {
        "stock_id": "2330",
        "stock_name": "台積電",
        "as_of": "2026-04-25",
        "close": 1100.0,
        "short": {"total": 72.0, "completeness": 1.0,
                  "parts": {"ma_alignment": 100.0, "kd": 60.0, "macd": 80.0}},
        "mid": {"total": 65.0, "completeness": 1.0,
                "parts": {"trend": 80.0, "eps_growth": 70.0}},
        "long": {"total": 78.0, "completeness": 1.0,
                 "parts": {"roe": 90.0, "valuation": 60.0}},
        "composite_score": 70.5,
        "is_stale": False,
        "is_pending": False,
        "recommendation": "買進",
        "entry": ["趨勢延續"],
        "warnings": [],
    }


def _build_fake_response(text: str = "短期動能偏強。\n\n中長期體質扎實。\n\n值得關注。"):
    """模擬 anthropic SDK 的 messages.create() 回傳結構。"""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=850,
            output_tokens=120,
            cache_read_input_tokens=0,
        ),
    )


def test_narrative_cache_miss_calls_llm_and_writes(tmp_db, fake_score_view, monkeypatch):
    """初次呼叫：沒快取 → 打 LLM → 寫快取 → cached=False。"""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _build_fake_response()
    # 必須 patch stock_narrative 那邊的 get_client（它 from import 進來，
    # 形成獨立的 binding，patch 原模組是無效的）
    import app.narrative.stock_narrative as sn_mod
    monkeypatch.setattr(sn_mod, "get_client", lambda: fake_client)

    result = generate_stock_narrative(
        tmp_db, fake_score_view, chip_snap={"foreign_streak": 3}, fund_snap={"roe_ttm": 0.25}
    )

    assert result.cached is False
    assert "短期動能" in result.narrative
    assert result.model == narrative_client.NARRATIVE_MODEL
    assert result.input_tokens == 850
    assert result.output_tokens == 120
    fake_client.messages.create.assert_called_once()

    # 寫進 DB
    cached = narrative_cache.get(tmp_db, "2330", "2026-04-25", KIND_STOCK_OVERVIEW)
    assert cached is not None
    assert cached.narrative == result.narrative


def test_narrative_cache_hit_skips_llm(tmp_db, fake_score_view, monkeypatch):
    """第二次呼叫同 (stock_id, as_of)：直接讀快取，不打 LLM。"""
    # 預先塞快取
    narrative_cache.put(
        tmp_db, "2330", "2026-04-25", KIND_STOCK_OVERVIEW,
        narrative="預存敘事內容",
        model="claude-haiku-4-5",
        input_tokens=100, output_tokens=50, cache_read_tokens=0,
    )

    fake_client = MagicMock()
    import app.narrative.stock_narrative as sn_mod
    monkeypatch.setattr(sn_mod, "get_client", lambda: fake_client)

    result = generate_stock_narrative(
        tmp_db, fake_score_view, chip_snap={}, fund_snap={}
    )

    assert result.cached is True
    assert result.narrative == "預存敘事內容"
    # 重點：絕對不能打 LLM
    fake_client.messages.create.assert_not_called()


def test_narrative_force_refresh_bypasses_cache(tmp_db, fake_score_view, monkeypatch):
    """force_refresh=True：即使快取存在，也重打 LLM 並覆蓋。"""
    narrative_cache.put(
        tmp_db, "2330", "2026-04-25", KIND_STOCK_OVERVIEW,
        narrative="舊敘事",
        model="claude-haiku-4-5",
    )

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _build_fake_response("新敘事內容")
    import app.narrative.stock_narrative as sn_mod
    monkeypatch.setattr(sn_mod, "get_client", lambda: fake_client)

    result = generate_stock_narrative(
        tmp_db, fake_score_view, chip_snap={}, fund_snap={}, force_refresh=True
    )

    assert result.cached is False
    assert result.narrative == "新敘事內容"
    fake_client.messages.create.assert_called_once()
    # 快取被覆蓋
    cached = narrative_cache.get(tmp_db, "2330", "2026-04-25", KIND_STOCK_OVERVIEW)
    assert cached.narrative == "新敘事內容"


def test_narrative_empty_response_raises(tmp_db, fake_score_view, monkeypatch):
    """LLM 回空字串（refusal / 切到 0）→ 拋例外讓 router 轉 5xx。"""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="")],
        stop_reason="refusal",
        usage=SimpleNamespace(input_tokens=10, output_tokens=0, cache_read_input_tokens=0),
    )
    import app.narrative.stock_narrative as sn_mod
    monkeypatch.setattr(sn_mod, "get_client", lambda: fake_client)

    with pytest.raises(RuntimeError, match="empty text"):
        generate_stock_narrative(
            tmp_db, fake_score_view, chip_snap={}, fund_snap={}
        )

    # 空回應不該被快取
    assert narrative_cache.get(tmp_db, "2330", "2026-04-25", KIND_STOCK_OVERVIEW) is None


def test_is_available_without_api_key(monkeypatch):
    """ANTHROPIC_API_KEY 沒設 → False。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.narrative.client import is_available
    assert is_available() is False


def test_is_available_with_api_key_and_sdk(monkeypatch):
    """有 key 且能 import anthropic → True。沒灌 SDK 的環境會 skip。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    pytest.importorskip("anthropic")
    from app.narrative.client import is_available
    assert is_available() is True


def test_build_user_prompt_handles_none_values(fake_score_view):
    """缺資料的欄位要顯示為 '—'，不能 KeyError。"""
    from app.narrative.prompts import build_user_prompt
    fake_score_view["long"]["total"] = None  # 模擬 ETF
    fake_score_view["long"]["parts"] = {"roe": None, "valuation": None}

    prompt = build_user_prompt(
        fake_score_view,
        chip_snap={"foreign_streak": None, "trust_streak": 5},
        fund_snap={"roe_ttm": None},
    )
    assert "long: —" in prompt
    assert "roe=—" in prompt
    assert "trust_streak=5" in prompt
