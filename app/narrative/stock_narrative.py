"""個股綜合敘事：把 score_stock 的三維分數 + 籌碼 + 基本面翻成中文段落。

呼叫流程：
1. 看 narrative_cache 有無同 (stock_id, as_of, kind=stock_overview)
2. 命中 → 直接回，cached=True
3. 沒命中 → 組 prompt → 打 Anthropic API → 寫快取 → 回，cached=False

不在這層做任何 score 計算 — caller 必須先呼叫 score_stock 並把 dict 餵進來。
這樣保證 narrative 看到的數字跟 UI 看到的完全一致（不會有 score 版本飄移問題）。
"""
from __future__ import annotations

import logging
from typing import Any

from app.data.db import Database
from app.narrative import cache as narrative_cache
from app.narrative.client import (
    NARRATIVE_MAX_TOKENS,
    NARRATIVE_MODEL,
    NarrativeNotAvailable,
    get_client,
)
from app.narrative.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

KIND_STOCK_OVERVIEW = "stock_overview"


def generate_stock_narrative(
    db: Database,
    score_view: dict[str, Any],
    chip_snap: dict,
    fund_snap: dict,
    *,
    force_refresh: bool = False,
) -> narrative_cache.CachedNarrative:
    """產生個股綜合敘事。

    Args:
        score_view: 已序列化的 StockScoreView（即 stocks router /score endpoint 的回傳結構）。
        chip_snap: signals['chip_snapshot']
        fund_snap: signals['fundamental_snapshot']
        force_refresh: 跳過快取直接重打 LLM。debug 用。

    Raises:
        NarrativeNotAvailable: ANTHROPIC_API_KEY 未設或 anthropic 套件未安裝。
        anthropic.APIError 子類: 網路 / rate limit / auth 錯誤。caller (router) 該轉成 5xx。
    """
    stock_id = score_view["stock_id"]
    as_of = score_view["as_of"]

    if not force_refresh:
        cached = narrative_cache.get(db, stock_id, as_of, KIND_STOCK_OVERVIEW)
        if cached is not None:
            return cached

    client = get_client()  # 可能 raise NarrativeNotAvailable

    user_prompt = build_user_prompt(score_view, chip_snap, fund_snap)

    # cache_control 放 system 上：未來如果 SYSTEM_PROMPT 加長到 4096+ tokens，Haiku 會自動快取；
    # 現在 ~1K tokens 不會 cache 但加了也無害（向前相容）。
    response = client.messages.create(
        model=NARRATIVE_MODEL,
        max_tokens=NARRATIVE_MAX_TOKENS,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )

    # 抽出 text content。Haiku 沒 thinking 也沒 tool_use，正常情況就是單一 text block。
    narrative_text = ""
    for block in response.content:
        if block.type == "text":
            narrative_text += block.text
    narrative_text = narrative_text.strip()

    if not narrative_text:
        # stop_reason == "refusal" 或 max_tokens 切到 0 都可能。讓 caller 處理。
        raise RuntimeError(f"narrative generation produced empty text (stop_reason={response.stop_reason})")

    usage = response.usage
    narrative_cache.put(
        db,
        stock_id=stock_id,
        as_of=as_of,
        kind=KIND_STOCK_OVERVIEW,
        narrative=narrative_text,
        model=NARRATIVE_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
    )
    logger.info(
        "narrative generated stock=%s as_of=%s in_tok=%d out_tok=%d cache_read=%s",
        stock_id, as_of, usage.input_tokens, usage.output_tokens,
        getattr(usage, "cache_read_input_tokens", None),
    )

    return narrative_cache.CachedNarrative(
        narrative=narrative_text,
        model=NARRATIVE_MODEL,
        cached=False,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
    )


__all__ = ["generate_stock_narrative", "KIND_STOCK_OVERVIEW", "NarrativeNotAvailable"]
