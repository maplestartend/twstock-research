"""評分引擎版本指紋。

用途：
- 標記 signal_history 是由哪版 engine/rubric/fundamentals 產生
- 在日期不變但程式邏輯更新時，仍能偵測快照需要重算
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


_VERSION_FILES: tuple[str, ...] = (
    "app/scoring/engine.py",
    "app/scoring/rubric.py",
    "app/indicators/fundamentals.py",
)


@lru_cache(maxsize=1)
def current_engine_version() -> str:
    root = Path(__file__).resolve().parents[2]
    h = hashlib.sha1()
    for rel in _VERSION_FILES:
        p = root / rel
        try:
            data = p.read_bytes()
        except OSError:
            data = b""
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    # 12 碼足夠做可讀識別與碰撞風險控制
    return h.hexdigest()[:12]
