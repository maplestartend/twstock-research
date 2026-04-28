"""LLM 敘事層：把 numeric score 結果翻成散戶看得懂的中文解讀。

設計守則（重要）：
- 純讀取層，不會回頭改任何 score / signal。違反 [CLAUDE.md #5] 的 snapshot 一致性會立刻爆。
- 缺資料就如實說「缺」，不准讓 LLM 編造數字。
- 環境變數 ANTHROPIC_API_KEY 沒設 → is_available()==False，前端按鈕直接灰掉。
- 永久快取在 narrative_cache 表（key=stock_id+as_of+kind），單人單機一個月 < NT$5。
"""
from app.narrative.client import NarrativeNotAvailable, is_available
from app.narrative.stock_narrative import generate_stock_narrative

__all__ = ["NarrativeNotAvailable", "is_available", "generate_stock_narrative"]
