"""/api/system/* — 系統狀態與管理 endpoints。

- snapshot-status：snapshot 是否最新（給前端顯示「資料新鮮度」）
- refresh-snapshot：手動強制重跑 signal_history（管理用）
- run-log：market_update 排程歷史（觀察成功率/耗時）
- notify-test：驗證 Discord webhook 設定
- backup-now：手動觸發 DB 備份
- rebuild-holding：trade_log 修正後重建單檔 holdings
- report/daily：讀 reports/YYYY-MM-DD.md 內容
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.deps import get_db
from api.schemas.common import CamelModel
from app import portfolio as pf
from app.data.db import Database
from app.scoring import history as snap_hist
from app.scoring import snapshot_freshness as snap_fresh

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])


class SnapshotStatus(CamelModel):
    snapshot_as_of: str | None
    daily_price_as_of: str | None
    is_stale: bool


@router.get("/snapshot-status", response_model=SnapshotStatus)
def snapshot_status(db: Database = Depends(get_db)) -> SnapshotStatus:
    """signal_history 最新日 vs daily_price 最新日；is_stale=true 代表列表頁下次查詢時會自動補跑。"""
    return SnapshotStatus(
        snapshot_as_of=snap_fresh._latest_snapshot_date(db),
        daily_price_as_of=snap_fresh._latest_price_date(db),
        is_stale=snap_fresh.is_stale(db),
    )


class NarrativeStatus(CamelModel):
    available: bool
    model: str | None = None       # 啟用時是哪個模型；未啟用 → None


@router.get("/narrative-status", response_model=NarrativeStatus)
def narrative_status() -> NarrativeStatus:
    """LLM 敘事是否可用。前端用此控制「AI 解讀」按鈕灰掉與否。

    available=False 的情況：ANTHROPIC_API_KEY 未設、或 anthropic 套件未安裝。
    """
    from app.narrative import is_available
    from app.narrative.client import NARRATIVE_MODEL
    avail = is_available()
    return NarrativeStatus(available=avail, model=NARRATIVE_MODEL if avail else None)


class RefreshSnapshotResponse(CamelModel):
    rows_written: int
    triggered: bool


@router.post("/refresh-snapshot", response_model=RefreshSnapshotResponse)
def refresh_snapshot(db: Database = Depends(get_db)) -> RefreshSnapshotResponse:
    """強制重跑當日 signal_history（即使 not stale 也跑）。耗時 1-2 分鐘。"""
    try:
        n = snap_hist.snapshot_today(db)
    except Exception as e:
        logger.exception("refresh_snapshot 失敗")
        raise HTTPException(status_code=500, detail=str(e))
    return RefreshSnapshotResponse(rows_written=n, triggered=True)


class NotifyTestBody(BaseModel):
    message: str = "🔔 測試訊息（來自 /api/system/notify-test）"


class NotifyTestResponse(CamelModel):
    sent: bool
    channel: str | None
    detail: str | None = None


@router.post("/notify-test", response_model=NotifyTestResponse)
def notify_test(body: NotifyTestBody) -> NotifyTestResponse:
    """測試 Discord webhook（從 config.yaml / DISCORD_WEBHOOK_URL 讀）。"""
    try:
        from app.notifier import notify
        notify(body.message, title="通知測試")
    except Exception as e:
        logger.exception("notify_test 失敗")
        return NotifyTestResponse(sent=False, channel=None, detail=str(e))

    try:
        from app.config import Config
        cfg = Config.load()
        channel = getattr(cfg.notify, "channel", None) if hasattr(cfg, "notify") else None
    except Exception:
        channel = None
    return NotifyTestResponse(sent=True, channel=channel)


class RunLogRow(CamelModel):
    started_at: str
    finished_at: str | None
    status: str | None
    duration_sec: float | None
    n_warnings: int | None
    note: str | None


@router.get("/run-log", response_model=list[RunLogRow])
def run_log(limit: int = 30, db: Database = Depends(get_db)) -> list[RunLogRow]:
    """market_update 執行歷史（最近優先）。表 `run_log` 不存在 → 回 []，其餘錯誤拋出。"""
    # schema 真實欄位是 ended_at；對外 alias 成 finished_at 保留 API 名稱穩定
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT started_at, ended_at AS finished_at, status, duration_sec, n_warnings, note "
                "FROM run_log ORDER BY started_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []
        raise
    return [
        RunLogRow(
            started_at=str(r["started_at"]),
            finished_at=str(r["finished_at"]) if r["finished_at"] else None,
            status=r["status"],
            duration_sec=float(r["duration_sec"]) if r["duration_sec"] is not None else None,
            n_warnings=int(r["n_warnings"]) if r["n_warnings"] is not None else None,
            note=r["note"],
        )
        for r in rows
    ]


class BackupResponse(CamelModel):
    path: str | None
    bytes: int | None
    triggered: bool
    detail: str | None = None


@router.post("/backup-now", response_model=BackupResponse)
def backup_now() -> BackupResponse:
    """手動觸發 DB 備份（VACUUM INTO）。需在 config.yaml 啟用 backup.enabled=true。"""
    try:
        from app import backup as bkp
        result = bkp.run_daily_backup()
    except Exception as e:
        logger.exception("backup_now 失敗")
        raise HTTPException(status_code=500, detail=str(e))
    if result is None:
        return BackupResponse(path=None, bytes=None, triggered=False, detail="backup 未啟用（config.yaml 的 backup.enabled=false）")
    if isinstance(result, dict):
        return BackupResponse(
            path=str(result.get("path")) if result.get("path") else None,
            bytes=int(result.get("bytes")) if result.get("bytes") is not None else None,
            triggered=True,
        )
    return BackupResponse(path=str(result), bytes=None, triggered=True)


@router.post("/rebuild-holding/{stock_id}", status_code=status.HTTP_204_NO_CONTENT)
def rebuild_holding(stock_id: str, db: Database = Depends(get_db)) -> None:
    """以 trade_log 為真相重建該股 holdings 的 shares / avg_cost。
    使用情境：手動修改 trade_log 後，需要 holdings 同步。"""
    pf.rebuild_holding(db, stock_id)


_REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DailyReportResponse(CamelModel):
    as_of: str
    markdown: str


@router.get("/report/daily", response_model=DailyReportResponse)
def report_daily(as_of: str | None = None) -> DailyReportResponse:
    """讀現成的每日報告（reports/YYYY-MM-DD.md）。
    - `as_of` 留空 → 讀 reports/latest.md
    - 未產生 → 404
    - 報告由 `python -m scripts.market_update` 順帶產生
    """
    if as_of is None:
        path = _REPORTS_DIR / "latest.md"
        label = "latest"
    else:
        if not _DATE_RE.match(as_of):
            raise HTTPException(status_code=400, detail="as_of 格式必須為 YYYY-MM-DD")
        path = _REPORTS_DIR / f"{as_of}.md"
        label = as_of
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"找不到報告 {path.name}")
    return DailyReportResponse(as_of=label, markdown=path.read_text(encoding="utf-8"))
