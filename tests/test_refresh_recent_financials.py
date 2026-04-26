"""refresh_recent_financials：自動判斷哪些季別在「公告期」內值得試抓。

只測純函式邏輯（不打 MOPS）。
"""
from __future__ import annotations

from datetime import date

from app.data.db import Database
from scripts.refresh_recent_financials import (
    _next_quarter,
    _quarter_deadline,
    _quarters_to_try,
)


class TestQuarterDeadline:
    def test_q1_deadline_is_may15(self):
        assert _quarter_deadline(2026, 1) == date(2026, 5, 15)

    def test_q4_deadline_is_next_year_march31(self):
        # 2025 Q4 (= 年報) deadline 在 2026/3/31
        assert _quarter_deadline(2025, 4) == date(2026, 3, 31)


class TestNextQuarter:
    def test_q1_to_q2(self):
        assert _next_quarter(2026, 1) == (2026, 2)

    def test_q4_rolls_year(self):
        assert _next_quarter(2025, 4) == (2026, 1)


class TestQuartersToTry:
    """測「現在這一天該不該試 backfill 哪些季」的邏輯。"""

    def _seed_db(self, tmp_path, latest: tuple[int, int] | None) -> Database:
        db = Database(tmp_path / "x.db")
        if latest is not None:
            y, q = latest
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO financials_cumulative (date, stock_id, type, value, year, quarter) VALUES (?,?,?,?,?,?)",
                    ("2025-12-31", "9999", "Revenue", 1000.0, y, q),
                )
                conn.commit()
        return db

    def test_4_26_with_q4_2025_in_db_targets_q1_2026(self, tmp_path):
        """4/26 時 DB 已有 2025 Q4 → 應嘗試 2026 Q1（deadline 5/15，距今 19 天 < 60）。"""
        db = self._seed_db(tmp_path, latest=(2025, 4))
        targets = _quarters_to_try(date(2026, 4, 26), db)
        assert (2026, 1) in targets

    def test_3_1_with_q3_2025_in_db_targets_q4_2025(self, tmp_path):
        """3/1 時 DB 還只有 2025 Q3 → 應嘗試 2025 Q4（年報，deadline 3/31，距今 30 天 < 60）。"""
        db = self._seed_db(tmp_path, latest=(2025, 3))
        targets = _quarters_to_try(date(2026, 3, 1), db)
        assert (2025, 4) in targets

    def test_jan_with_q3_2025_too_early_for_q4(self, tmp_path):
        """1/1 時 Q4 deadline 在 3/31（89 天後）→ 還太早不該試（window 是 60 天）。"""
        db = self._seed_db(tmp_path, latest=(2025, 3))
        targets = _quarters_to_try(date(2026, 1, 1), db)
        assert (2025, 4) not in targets

    def test_already_have_latest_but_still_in_publish_window(self, tmp_path):
        """DB 已有 2026 Q1，但 Q1 deadline 5/15 距 4/26 還 19 天 → 仍在公告期 → 應再試
        （公告中公司每天會多）。Q2 太早（110 天）跳過。"""
        db = self._seed_db(tmp_path, latest=(2026, 1))
        targets = _quarters_to_try(date(2026, 4, 26), db)
        # 期望 Q1 在裡面（再 try 撈新公告者）；Q2 不在
        assert (2026, 1) in targets
        assert (2026, 2) not in targets

    def test_already_have_latest_window_passed_no_target(self, tmp_path):
        """DB 已有 2026 Q1，6/30 已過 Q1 deadline (5/15) 超過 30 天 → Q1 定案不再試；
        Q2 deadline 8/14 距今 45 天 < 60 → 應試 Q2。"""
        db = self._seed_db(tmp_path, latest=(2026, 1))
        targets = _quarters_to_try(date(2026, 6, 30), db)
        assert (2026, 1) not in targets
        assert (2026, 2) in targets

    def test_after_deadline_still_attempts(self, tmp_path):
        """deadline 已過 25 天還沒抓到 → 仍應嘗試（補延遲公告）。"""
        # DB 只到 2025 Q3，今天 4/26 → Q4 deadline 3/31 已過 26 天 → 仍試
        db = self._seed_db(tmp_path, latest=(2025, 3))
        targets = _quarters_to_try(date(2026, 4, 26), db)
        assert (2025, 4) in targets

    def test_empty_db_targets_year_q1q4(self, tmp_path):
        """空 DB → 從今年 Q1 開始試 4 季。"""
        db = self._seed_db(tmp_path, latest=None)
        targets = _quarters_to_try(date(2026, 4, 26), db)
        assert len(targets) == 4
        assert targets[0] == (2026, 1)
