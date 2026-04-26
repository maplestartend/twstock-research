"""SQLite 備份與保留策略。

用 `VACUUM INTO` 產生的備份是原子且一致的（WAL 模式下也安全），不需要鎖 DB。

設計：
- 每日一份：放 backup_dir，命名 `stock_YYYYMMDD.db`
- 保留 N 份日備份；更早的若是「每週一」保留、若是「每月 1 號」也保留
- 可指向 Google Drive 同步資料夾；資料夾不存在 fallback 到 `data/backup/`
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


DEFAULT_LOCAL_BACKUP = Path(__file__).resolve().parent.parent / "data" / "backup"


def _expand_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    # 展開 ~ 與 %USERPROFILE% 等
    p = os.path.expanduser(os.path.expandvars(raw))
    return Path(p)


def resolve_backup_dir(configured: str | None) -> tuple[Path, bool]:
    """回傳 (實際使用的資料夾, 是否為 fallback)。

    若 configured 有值且該資料夾存在就用它；否則 fallback 到本機 data/backup。
    """
    target = _expand_path(configured)
    if target and target.exists() and target.is_dir():
        return target, False
    if target:
        logger.warning("設定的備份資料夾不存在：%s → 改用本機 %s", target, DEFAULT_LOCAL_BACKUP)
    DEFAULT_LOCAL_BACKUP.mkdir(parents=True, exist_ok=True)
    return DEFAULT_LOCAL_BACKUP, True


def make_backup(db_path: Path, backup_dir: Path, today: date | None = None) -> Path | None:
    """用 VACUUM INTO 產生當日備份。已存在就覆蓋。

    產生後會立刻對備份檔跑 `PRAGMA integrity_check`，回傳 [("ok",)] 才視為成功；
    若有任何錯誤訊息（例如 page corruption）就刪除壞檔並回傳 None，避免讓
    apply_retention 把舊的好備份輪掉、留下一個壞備份當日用。
    """
    today = today or date.today()
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"stock_{today:%Y%m%d}.db"
    # VACUUM INTO 不能寫到已存在的檔，先刪
    if target.exists():
        target.unlink()
    # sqlite3 在同步層面就能做
    import sqlite3
    try:
        with sqlite3.connect(db_path) as conn:
            # 字串插值安全：我們控制 target
            conn.execute(f"VACUUM INTO '{target.as_posix()}'")
        # integrity_check：開新連線到 backup file 跑 PRAGMA
        # （Windows 上 with-block 不一定 close，明確 close 才能 unlink 壞檔）
        bconn = sqlite3.connect(target)
        try:
            integrity = bconn.execute("PRAGMA integrity_check").fetchall()
        finally:
            bconn.close()
        if integrity != [("ok",)]:
            logger.warning(
                "備份 integrity_check 失敗，刪除壞檔（不套用 retention）：%s → %r",
                target, integrity,
            )
            try:
                target.unlink()
            except OSError:
                pass
            return None
        logger.info("備份完成：%s (%.1f MB)", target, target.stat().st_size / 1e6)
        return target
    except Exception as e:
        logger.warning("備份失敗：%s", e)
        return None


def apply_retention(
    backup_dir: Path,
    *,
    keep_days: int = 14,
    keep_weeks: int = 8,
    keep_months: int = 12,
    today: date | None = None,
) -> int:
    """刪除不符合保留規則的舊備份。回傳刪除檔案數。

    規則（任一成立就保留）：
    - 檔名日期在最近 `keep_days` 天內
    - 檔名日期是週一，且在最近 `keep_weeks` 週內
    - 檔名日期是每月 1 號，且在最近 `keep_months` 個月內
    """
    today = today or date.today()
    cutoff_days = today - timedelta(days=keep_days)
    cutoff_weeks = today - timedelta(days=keep_weeks * 7)
    cutoff_months = today - timedelta(days=keep_months * 31)

    deleted = 0
    for f in backup_dir.glob("stock_*.db"):
        try:
            d = _parse_backup_date(f.name)
        except ValueError:
            continue
        keep = False
        if d >= cutoff_days:
            keep = True
        elif d.weekday() == 0 and d >= cutoff_weeks:  # Monday
            keep = True
        elif d.day == 1 and d >= cutoff_months:
            keep = True
        if not keep:
            try:
                f.unlink()
                deleted += 1
                logger.info("刪除舊備份：%s", f.name)
            except OSError:
                pass
    return deleted


def _parse_backup_date(filename: str) -> date:
    """stock_20260424.db → 2026-04-24"""
    stem = Path(filename).stem
    parts = stem.split("_")
    if len(parts) < 2:
        raise ValueError(filename)
    ymd = parts[1]
    if len(ymd) != 8 or not ymd.isdigit():
        raise ValueError(filename)
    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))


def run_daily_backup(
    db_path: Path,
    configured_dir: str | None,
    *,
    keep_days: int = 14,
    keep_weeks: int = 8,
    keep_months: int = 12,
) -> dict:
    """高階入口：執行備份 + 保留策略，回傳摘要 dict。"""
    backup_dir, is_fallback = resolve_backup_dir(configured_dir)
    target = make_backup(db_path, backup_dir)
    deleted = 0
    if target:
        deleted = apply_retention(
            backup_dir,
            keep_days=keep_days,
            keep_weeks=keep_weeks,
            keep_months=keep_months,
        )
    total_size = sum(f.stat().st_size for f in backup_dir.glob("stock_*.db"))
    return {
        "backup_dir": str(backup_dir),
        "is_fallback": is_fallback,
        "new_file": str(target) if target else None,
        "deleted": deleted,
        "total_size_mb": round(total_size / 1e6, 1),
        "retained_count": len(list(backup_dir.glob("stock_*.db"))),
    }
