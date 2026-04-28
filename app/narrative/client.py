"""Anthropic SDK client wrapper。

關鍵設計：
- ANTHROPIC_API_KEY 沒設 → is_available()==False，所有 endpoint 走 503 路徑。
- 模型固定 Haiku 4.5：narrative on structured data 用不到 Sonnet/Opus，省 5-15 倍成本。
- 單例 client，避免每次 request 重新 import / connect。
- 不在 import time 觸發任何網路或檔案 IO，import 失敗只能因為 anthropic 套件未安裝。
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 避免在沒裝 anthropic 套件時 import 階段就爆
    from anthropic import Anthropic

# Sonnet 4.6：比 Haiku 貴 3 倍但中文流暢度與規則跟隨度更好；prompt cache 門檻 2048 tokens，
# 我們的 system prompt ~3000 tokens 會自動觸發 cache（cached read 折 90%），實際成本約是
# uncached Sonnet 的 52%。$3/MTok input + $15/MTok output。
# 之前用 Haiku 4.5 時 cache 門檻 4096 tokens 卡在邊緣不一定觸發，所以 Sonnet 實際成本沒翻 3 倍。
NARRATIVE_MODEL = "claude-sonnet-4-6"

# 3 段、每段 80 字 ≈ 240 字 ≈ 400 tokens 中文。留 600 buffer 給變數展開。
NARRATIVE_MAX_TOKENS = 600


class NarrativeNotAvailable(Exception):
    """ANTHROPIC_API_KEY 未設定 / SDK 未安裝 → endpoint 應回 503。"""


def is_available() -> bool:
    """前端顯示按鈕前先呼叫 GET /api/system/narrative-status 檢查。"""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def get_client() -> "Anthropic":
    """單例 Anthropic client。lru_cache 避免每個 request 重建。

    Raises:
        NarrativeNotAvailable: ANTHROPIC_API_KEY 未設或 anthropic 套件未安裝。
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise NarrativeNotAvailable("ANTHROPIC_API_KEY 環境變數未設定")
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise NarrativeNotAvailable("anthropic 套件未安裝：pip install anthropic") from e
    return Anthropic()
