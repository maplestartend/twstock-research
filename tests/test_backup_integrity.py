"""backup.make_backup 在跑 PRAGMA integrity_check 失敗時應刪除壞檔並回 None。

對應 P1 DB audit Fix #4。
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app import backup as backup_mod
from app.backup import make_backup, run_daily_backup


def _make_seed_db(p: Path) -> None:
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


class TestIntegrityCheck:
    def test_good_backup_returns_path(self, tmp_path: Path):
        src = tmp_path / "src.db"
        _make_seed_db(src)
        out = make_backup(src, tmp_path / "bk", today=date(2026, 4, 26))
        assert out is not None
        assert out.exists()
        # 好的備份要能正常開啟
        c = sqlite3.connect(out)
        assert c.execute("SELECT x FROM t").fetchone()[0] == 1
        c.close()

    def test_failed_integrity_deletes_and_returns_none(self, tmp_path: Path):
        """模擬 integrity_check 回非 ok：用 monkey-patch 把 PRAGMA 結果換掉。"""
        src = tmp_path / "src.db"
        _make_seed_db(src)
        backup_dir = tmp_path / "bk"

        # 真實 sqlite3.connect 仍然要做事；只攔截「PRAGMA integrity_check」那一條 SQL
        real_connect = sqlite3.connect
        backup_paths: list[Path] = []

        class FakeConn:
            def __init__(self, real, path):
                self._real = real
                self._path = path

            def __enter__(self):
                self._real.__enter__()
                return self

            def __exit__(self, *a):
                return self._real.__exit__(*a)

            def execute(self, sql, *args):
                if "integrity_check" in sql.lower():
                    class _C:
                        def fetchall(self_inner):
                            return [("*** in database main ***",),
                                    ("page 5: btreeInitPage failed",)]
                    return _C()
                return self._real.execute(sql, *args)

            def close(self):
                return self._real.close()

            def __getattr__(self, name):
                return getattr(self._real, name)

        def conn_factory(path, *args, **kwargs):
            real = real_connect(path, *args, **kwargs)
            # 只攔截「打開 backup file」那次（path 在 backup_dir 下）
            if str(path).startswith(str(backup_dir)):
                backup_paths.append(Path(path))
                return FakeConn(real, path)
            return real

        # backup.py 用 `import sqlite3` 在函式內部 → patch sqlite3 module 直接
        with patch.object(sqlite3, "connect", side_effect=conn_factory):
            out = make_backup(src, backup_dir, today=date(2026, 4, 26))
        assert out is None, "integrity_check 失敗應回 None"
        assert backup_paths, "fake connect 沒被觸發代表測試本身壞了"
        # 壞檔應該被刪
        assert not (backup_dir / "stock_20260426.db").exists()

    def test_run_daily_backup_skips_retention_when_integrity_fails(self, tmp_path: Path):
        """integrity_check 失敗 → make_backup 回 None → run_daily_backup 不應跑 retention。"""
        src = tmp_path / "src.db"
        _make_seed_db(src)
        bk_dir = tmp_path / "bk"
        bk_dir.mkdir()
        # 預先丟一個老備份，若 retention 跑就會被刪
        old = bk_dir / "stock_20200101.db"
        old.write_bytes(b"x")

        with patch.object(backup_mod, "make_backup", return_value=None):
            summary = run_daily_backup(src, str(bk_dir))
        assert summary["new_file"] is None
        assert summary["deleted"] == 0
        assert old.exists()  # retention 沒跑，老檔還在
