"""共用 HTTP session：對 TWSE / TPEX / FinMind / MOPS 統一加 retry/backoff。

過去個別 fetcher 都用 `requests.Session()` 不掛 retry 配接器，TWSE 偶發 502 / 連線重置、
MOPS 被 IP rate-limit、FinMind quota 還沒到結帳期但短暫 timeout 都會在 daily-update.bat
靜默吞掉（_safe wrapper 收到 exception 就 return 空 df），fetch_log 反而不會更新最後抓
取日期 → 隔天 incremental 起點落後一天，越拉越多缺值。

設計：
- 對 5xx / 429 / connection-related 錯誤做 backoff 3 次（0.5s / 1s / 2s）
- 4xx (除 429) 不 retry，馬上 throw 給上層判斷（404 → 沒這檔 / 422 → 參數錯）
- GET / HEAD 是 idempotent，POST 視 endpoint 安全性決定（MOPS POST 也是查詢、可重試）
"""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def make_session(
    *,
    total_retries: int = 3,
    backoff_factor: float = 0.5,
    allowed_methods: tuple[str, ...] = ("HEAD", "GET", "OPTIONS", "POST"),
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
    headers: dict[str, str] | None = None,
) -> requests.Session:
    """建一個帶 retry/backoff 的 requests.Session。caller 想覆寫 headers 在這裡傳。"""
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=allowed_methods,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if headers:
        session.headers.update(headers)
    return session
