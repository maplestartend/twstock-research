"""market_update 等腳本的執行記錄。寫入 run_log 表。

用法：
    with run_context(db, "market_update") as rec:
        rec.note = "..."
        # do work
        rec.rows_written = 12345
    # on exit 會自動更新 ended_at / duration / status
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from app.data.db import Database

logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    run_id: int | None = None
    script: str = ""
    started_at: str = ""
    ended_at: str | None = None
    duration_sec: float | None = None
    status: str = "running"
    n_warnings: int = 0
    rows_written: int | None = None
    note: str | None = None


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start_run(db: Database, script: str, note: str | None = None) -> RunRecord:
    rec = RunRecord(script=script, started_at=_now_iso(), note=note)
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO run_log (script, started_at, status, note) VALUES (?, ?, 'running', ?)",
            (rec.script, rec.started_at, rec.note),
        )
        conn.commit()
        rec.run_id = cur.lastrowid
    return rec


def finish_run(db: Database, rec: RunRecord, status: str = "ok") -> None:
    rec.status = status
    rec.ended_at = _now_iso()
    start = datetime.fromisoformat(rec.started_at)
    end = datetime.fromisoformat(rec.ended_at)
    rec.duration_sec = round((end - start).total_seconds(), 2)
    with db.connect() as conn:
        conn.execute(
            "UPDATE run_log SET ended_at=?, duration_sec=?, status=?, "
            "n_warnings=?, rows_written=?, note=? WHERE run_id=?",
            (rec.ended_at, rec.duration_sec, rec.status,
             rec.n_warnings, rec.rows_written, rec.note, rec.run_id),
        )
        conn.commit()


@contextmanager
def run_context(db: Database, script: str, note: str | None = None) -> Iterator[RunRecord]:
    rec = start_run(db, script, note)
    try:
        yield rec
    except Exception as e:
        rec.status = "error"
        rec.note = (rec.note + " | " if rec.note else "") + f"EXCEPTION: {type(e).__name__}: {str(e)[:200]}"
        finish_run(db, rec, status="error")
        raise
    else:
        # 若呼叫端沒改 status，根據 n_warnings 決定
        if rec.status == "running":
            rec.status = "warn" if rec.n_warnings > 0 else "ok"
        finish_run(db, rec, status=rec.status)
