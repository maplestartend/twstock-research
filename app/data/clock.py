"""台北時區的「今天」工具。

server 可能跑在 UTC（雲端）或 Asia/Taipei（本機）；台股交易/資料以台北時間為準，
若用 server local time 在 UTC 凌晨 0~8 時可能誤判日期。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


# 台北時區固定 UTC+8（台灣不實施夏令時間）
TAIPEI_TZ = timezone(timedelta(hours=8))


def taipei_today() -> date:
    """以台北時區計算「今天」的日期。"""
    return datetime.now(TAIPEI_TZ).date()


def taipei_now() -> datetime:
    """以台北時區的當前時間。"""
    return datetime.now(TAIPEI_TZ)
