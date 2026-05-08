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
        """同樣 5M 張外資買超：對日均量 100M 的大型股 = 0.25% / 對日均量 2M 的中型股 = 12.5%。

        注意：M4 修補後 avg_vol < 1M 視為散戶股 ratio=0，所以測試用 2M（不觸發 floor）做小型股對照。
        """
        # 大型股：avg_vol 100M, cum20 = 5M → 5M / (100M * 20) = 0.25%
        big = score_foreign_mid({"foreign_cum20": 5_000_000, "avg_volume_20": 100_000_000})
        # 中型股：avg_vol 2M, cum20 = 5M → 5M / (2M * 20) = 12.5%
        small = score_foreign_mid({"foreign_cum20": 5_000_000, "avg_volume_20": 2_000_000})
        # 大型股低分（外資買進但量太小占比），中型股高分（外資吃下大量）
        assert big < small
        assert big <= 55.0
        assert small >= 65.0

    def test_scale_thresholds_monotonic(self):
        """ratio 越大、score 越高（同方向）。avg_vol 用 2M（過 M4 floor）。"""
        avg_vol = 2_000_000  # 20 日總量 = 40M
        scores = []
        for cum in [-30_000_000, -6_000_000, -1_000_000, 1_000_000, 6_000_000, 30_000_000]:
            scores.append(score_foreign_mid({"foreign_cum20": cum, "avg_volume_20": avg_vol}))
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1], f"score non-monotonic at index {i}: {scores}"

    def test_low_adv_returns_neutral(self):
        """M4：ADV < 1M 散戶股，外資 ratio 視為 0 → 中性 50。"""
        # cum 大買超但 ADV 只 200K → 視為散戶股、不該給 75+
        s = score_foreign_mid({"foreign_cum20": 1_000_000, "avg_volume_20": 200_000})
        # ratio=0 對應「介於 0 與 0.10 之間」的閾值區段（45 分），總之不該給高分（< 60）
        assert s is not None and s < 60


class TestTrustMidScale:
    def test_falls_back_to_absolute_when_no_avg_volume(self):
        assert score_trust_mid({"trust_cum20": 3_000_000}) == 80.0

    def test_uses_adv_ratio(self):
        """投信閾值取外資的一半：% of ADV > 25% 拿 80。avg_vol 用 2M（過 M4 floor）。"""
        # avg_vol = 2M → 20 日總量 = 40M，cum20 = 12M → 30% (>25%) → 80
        s = score_trust_mid({"trust_cum20": 12_000_000, "avg_volume_20": 2_000_000})
        assert s == 80.0

    def test_low_adv_returns_neutral(self):
        """M4：ADV < 1M 投信 ratio 也視為 0 → 中性 45-55 區段。"""
        s = score_trust_mid({"trust_cum20": 1_000_000, "avg_volume_20": 100_000})
        assert s is not None and 40 < s < 60
