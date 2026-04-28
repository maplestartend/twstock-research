"""narrative_cache 表的 read/write helpers。

設計：
- 永久快取，不主動 invalidate。as_of 推進 → 自動是新一筆 row，舊的留作歷史。
- 同一個 (stock_id, as_of, kind) 重複呼叫 → 直接讀快取，不打 LLM。
- 寫入失敗（例：DB lock）不影響回傳結果，只 log warning。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional

from app.data.db import Database

logger = logging.getLogger(__name__)


@dataclass
class CachedNarrative:
    narrative: str
    model: str
    cached: bool                  # True = 從 DB 撈出來；False = 剛打完 LLM
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None


def get(db: Database, stock_id: str, as_of: str, kind: str) -> Optional[CachedNarrative]:
    """快取查詢；找不到 → None。"""
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT narrative, model, input_tokens, output_tokens, cache_read_tokens
              FROM narrative_cache
             WHERE stock_id=? AND as_of=? AND kind=?
            """,
            (stock_id, as_of, kind),
        ).fetchone()
    if not row:
        return None
    return CachedNarrative(
        narrative=row["narrative"],
        model=row["model"],
        cached=True,
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cache_read_tokens=row["cache_read_tokens"],
    )


def put(
    db: Database,
    stock_id: str,
    as_of: str,
    kind: str,
    narrative: str,
    model: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    cache_read_tokens: Optional[int] = None,
) -> None:
    """寫入快取。同 PK 重複寫 → REPLACE（覆蓋舊敘事，例：模型升級後 force regenerate）。"""
    try:
        with db.connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO narrative_cache
                        (stock_id, as_of, kind, narrative, model,
                         input_tokens, output_tokens, cache_read_tokens)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (stock_id, as_of, kind, narrative, model,
                     input_tokens, output_tokens, cache_read_tokens),
                )
    except sqlite3.Error as e:
        # 寫入失敗不能影響主流程（LLM 已經回應、使用者該看的內容已生成）
        logger.warning("narrative_cache put failed for %s/%s/%s: %s", stock_id, as_of, kind, e)
