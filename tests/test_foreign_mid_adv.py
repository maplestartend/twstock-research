"""score_foreign_mid / score_trust_mid 用 % of ADV 規模化。

對應 Critical Fix #6（金融分析師審查）：原本 absolute 張數閾值對 2330 億級流通量
與小型百萬流通量股一視同仁，造成大型股永遠拿低分。
"""
from __future__ import annotations

from app.scoring.rubric import score_foreign_mid, score_trust_mid


class TestForeignMidScale:
    def test_no_data_returns_none(self):
        assert score_foreign_mid({}) is None

    def test_falls_back_to_absolute_when_no_avg_volume(self):
        # 沒 avg_volume_20 → fallback 絕對閾值（保持向後相容）
        assert score_foreign_mid({"foreign_cum20": 11_000_000}) == 85.0
        assert score_foreign_mid({"foreign_cum20": -11_000_000}) == 15.0

    def test_uses_adv_ratio_when_available(self):
        """同樣 1M 張外資買超：對日均量 100M 的大型股 = 0.05% / 對日均量 200K 的小型股 = 25%。"""
        # 大型股：avg_vol 100M, cum20 = 1M → 1M / (100M * 20) = 0.05%
        big = score_foreign_mid({"foreign_cum20": 1_000_000, "avg_volume_20": 100_000_000})
        # 小型股：avg_vol 200K, cum20 = 1M → 1M / (200K * 20) = 25%
        small = score_foreign_mid({"foreign_cum20": 1_000_000, "avg_volume_20": 200_000})
        # 大型股低分（接近 55，外資買進但量太小），小型股高分（接近 75，外資吃下大量）
        assert big < small
        assert big <= 55.0
        assert small >= 75.0

    def test_scale_thresholds_monotonic(self):
        """ratio 越大、score 越高（同方向）。"""
        avg_vol = 1_000_000  # 20 日總量 = 20M
        scores = []
        for cum in [-15_000_000, -3_000_000, -500_000, 500_000, 3_000_000, 15_000_000]:
            scores.append(score_foreign_mid({"foreign_cum20": cum, "avg_volume_20": avg_vol}))
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1], f"score non-monotonic at index {i}: {scores}"


class TestTrustMidScale:
    def test_falls_back_to_absolute_when_no_avg_volume(self):
        assert score_trust_mid({"trust_cum20": 3_000_000}) == 80.0

    def test_uses_adv_ratio(self):
        """投信閾值取外資的一半：% of ADV > 25% 拿 80。"""
        # avg_vol = 100K → 20 日總量 = 2M，cum20 = 1M → 50% (>25%) → 80
        s = score_trust_mid({"trust_cum20": 1_000_000, "avg_volume_20": 100_000})
        assert s == 80.0
