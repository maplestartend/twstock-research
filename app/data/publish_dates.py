"""財報公告日規則（single source of truth）。

台股季財報法定公告下限：
- Q1: 05-15
- Q2: 08-14
- Q3: 11-14
- Q4: 次年 03-31
"""
from __future__ import annotations

from datetime import date, datetime


def quarter_publish_date(year_ce: int, quarter: int) -> str | None:
    """西元年 + 季別 -> 法定公告下限（YYYY-MM-DD）。"""
    if quarter == 1:
        return f"{year_ce}-05-15"
    if quarter == 2:
        return f"{year_ce}-08-14"
    if quarter == 3:
        return f"{year_ce}-11-14"
    if quarter == 4:
        return f"{year_ce + 1}-03-31"
    return None


def quarter_end_to_publish_date(quarter_end: object) -> str | None:
    """季末日（Timestamp/date/字串）-> 法定公告下限（YYYY-MM-DD）。"""
    if quarter_end is None:
        return None

    if isinstance(quarter_end, datetime):
        s = quarter_end.date().isoformat()
    elif isinstance(quarter_end, date):
        s = quarter_end.isoformat()
    else:
        s = str(quarter_end)[:10]

    if len(s) < 10:
        return None
    year_s = s[:4]
    md = s[5:10]
    try:
        year_ce = int(year_s)
    except ValueError:
        return None

    if md == "03-31":
        return quarter_publish_date(year_ce, 1)
    if md == "06-30":
        return quarter_publish_date(year_ce, 2)
    if md == "09-30":
        return quarter_publish_date(year_ce, 3)
    if md == "12-31":
        return quarter_publish_date(year_ce, 4)
    return None
