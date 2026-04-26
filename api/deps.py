"""共用 FastAPI 依賴。"""
from __future__ import annotations

from functools import lru_cache

from app.config import Config
from app.data.db import Database


@lru_cache(maxsize=1)
def get_db() -> Database:
    """單例 Database；lru_cache 等同於 @st.cache_resource 的替身。"""
    return Database(Config.load().database.path)
