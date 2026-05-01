"""SQL 小工具：放在 app 層讓 app/* 與 api/* 都能共用。

過去這些 helper 散在 api/common.py，但 app/backtest 與 app/report 也需要拼
`stock_id IN (?,?,?)`，從 app 層 import api/* 會破壞分層 → 改放這裡，api 層
透過 re-export 維持原本 API 不動。
"""
from __future__ import annotations


def make_placeholders(n: int) -> str:
    """產生 "?,?,?,..." 給 SQL `IN (...)`。n=0 回傳空字串。

    呼叫端應自己擋空 list（傳 0 進來會產出 `IN ()` 觸發 SQL 語法錯誤）。
    """
    return ",".join("?" * n)
