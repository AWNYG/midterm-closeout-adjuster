import numpy as np
import pytest

from src.value import band_bounds, mv_curve

P_AVG = 400.0
S = 300.0
L = 1000.0


class TestBandBounds:
    def test_default(self):
        lo, hi = band_bounds(1000)
        assert lo == pytest.approx(1000 / 1.15)
        assert hi == pytest.approx(1000 / 0.85)
        assert lo < hi

    def test_shrink_narrower(self):
        lo0, hi0 = band_bounds(1000, shrink=0.0)
        lo1, hi1 = band_bounds(1000, shrink=0.5)
        assert (hi1 - lo1) < (hi0 - lo0)
        assert lo0 < lo1 < hi1 < hi0
        assert (lo1 + hi1) / 2 == pytest.approx((lo0 + hi0) / 2)

    def test_shrink_out_of_range(self):
        with pytest.raises(ValueError):
            band_bounds(1000, shrink=1.0)
        with pytest.raises(ValueError):
            band_bounds(1000, shrink=-0.1)


class TestMvCurve:
    def test_non_increasing(self):
        qs, mv = mv_curve(1000, L, S, P_AVG, max_q=500, step=1.0)
        assert np.all(np.diff(mv) <= 1e-12)

    def test_three_regions(self):
        # Q_c=900，欠配段：900+Q < 869.6 → Q < -30（不存在）
        # 带内：869.6 ≤ 900+Q ≤ 1176.5 → Q ∈ [0, 276]
        # 超配：900+Q > 1176.5 → Q > 276.5
        qs, mv = mv_curve(900, L, S, P_AVG, max_q=500, step=1.0)
        band_lo = 1000 / 1.15 - 900   # ≈ -30.4
        band_hi = 1000 / 0.85 - 900   # ≈ 276.5
        assert np.all(mv[qs <= band_hi + 1e-9] == pytest.approx(S))
        assert np.all(mv[qs > band_hi + 1e-9] == pytest.approx(min(S, 410)))

    def test_step_position_matches_band_edge(self):
        # 阶梯恰在持仓跨过 L/0.85 处：Q_c=1000 → 跨过点 Q = L/0.85 − 1000 ≈ 176.47
        qs, mv = mv_curve(1000, L, S, P_AVG, max_q=300, step=1.0)
        cross = L / 0.85 - 1000
        idx = int(np.floor(cross + 1e-9))
        assert mv[idx] == pytest.approx(S)
        assert mv[idx + 1] == pytest.approx(min(S, 410))

    def test_in_band_all_spot(self):
        # 持仓恰在带内，无阶梯（欠配/超配都不出现）
        qs, mv = mv_curve(1000, 1000, S, P_AVG, max_q=50, step=1.0)
        assert np.all(mv == pytest.approx(S))

    def test_mv0_equals_marginal_value(self):
        from src.settlement import marginal_value
        qs, mv = mv_curve(800, 1000, S, P_AVG, max_q=10, step=1.0)
        assert mv[0] == pytest.approx(marginal_value(800, 1000, S, P_AVG))

    def test_shrink_applied(self):
        qs1, mv1 = mv_curve(1000, L, S, P_AVG, max_q=300, shrink=0.5)
        qs0, mv0 = mv_curve(1000, L, S, P_AVG, max_q=300, shrink=0.0)
        # 收缩后阶梯提前（边界向中心收）
        first_drop1 = int(np.argmax(np.diff(mv1) < 0)) if np.any(np.diff(mv1) < 0) else len(mv1)
        first_drop0 = int(np.argmax(np.diff(mv0) < 0)) if np.any(np.diff(mv0) < 0) else len(mv0)
        assert first_drop1 <= first_drop0
