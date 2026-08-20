import pytest

from src.settlement import (
    marginal_value,
    settle_price,
    update_blended_avg,
)

P_AVG = 400.0


class TestSettlePrice:
    def test_in_band_returns_spot(self):
        # 带内：L/Q = 1.0
        assert settle_price(1000, 1000, 300, P_AVG) == 300

    def test_band_low_boundary(self):
        # L/Q = 0.85 精确覆盖（带内）
        assert settle_price(10000, 8500, 300, P_AVG) == 300

    def test_band_high_boundary(self):
        # L/Q = 1.15 精确覆盖（带内）
        assert settle_price(10000, 11500, 300, P_AVG) == 300

    def test_slightly_out_band_low(self):
        # L/Q = 0.8499 → 少用，min(S, 1.1*400-0.1*300=410) = 300
        assert settle_price(10000, 8499, 300, P_AVG) == 300

    def test_slightly_out_band_high(self):
        # L/Q = 1.1501 → 多用，max(S, 410) = 410
        assert settle_price(10000, 11501, 300, P_AVG) == 410

    def test_overuse_spot_below_avg_recovers(self):
        # 多用 且 S < P_avg → max(S, k) = k = 1.1*400-0.1*300 = 410
        assert settle_price(1000, 3000, 300, P_AVG) == 410

    def test_overuse_spot_above_avg(self):
        # 多用 且 S > P_avg → k = 1.1*400-0.1*500 = 390 < S → 结算价 = S
        assert settle_price(1000, 3000, 500, P_AVG) == 500

    def test_underuse_spot_above_avg_recovers(self):
        # 少用 且 S > P_avg → min(S, k) = k = 390
        assert settle_price(1000, 500, 500, P_AVG) == 390

    def test_underuse_spot_below_avg(self):
        # 少用 且 S < P_avg → min(S, k) = S = 300
        assert settle_price(1000, 500, 300, P_AVG) == 300


class TestMarginalValue:
    def test_under_allocated_ge_spot(self):
        # 欠配：Q_c=800 < 1000/1.15 → max(S, k)
        mv = marginal_value(800, 1000, 300, P_AVG)
        assert mv == 410

    def test_in_band_equals_spot(self):
        assert marginal_value(1000, 1000, 300, P_AVG) == 300

    def test_over_allocated_le_spot(self):
        # 超配：Q_c=1200 > 1000/0.85 → min(S, k)
        assert marginal_value(1200, 1000, 300, P_AVG) == 300

    def test_over_allocated_spot_high(self):
        # 超配 且 S 高 → min(S, k) = k
        assert marginal_value(1200, 1000, 500, P_AVG) == 390

    def test_boundary_upper_in_band(self):
        # Q_c == L/1.15 → 带内
        assert marginal_value(1000 / 1.15, 1000, 300, P_AVG) == 300

    def test_boundary_lower_in_band(self):
        # Q_c == L/0.85 → 带内
        assert marginal_value(1000 / 0.85, 1000, 300, P_AVG) == 300

    def test_zero_position_raises(self):
        with pytest.raises(ValueError):
            marginal_value(0, 1000, 300, P_AVG)

    def test_shrink_keeps_under_allocated(self):
        # 欠配（Q_c=800 << L/1.15=869.6），收缩后仍欠配
        assert marginal_value(800, 1000, 300, P_AVG, shrink=0.5) == 410

    def test_shrink_pulls_boundary_in_band(self):
        # 无收缩时 Q_c=L/1.15 为带内上边界（返回 S）；
        # 收缩 0.5 后该点落在带外欠配侧 → 返回 max(S,k)=410
        q_at_edge = 1000 / 1.15
        assert marginal_value(q_at_edge, 1000, 300, P_AVG, shrink=0.0) == 300
        assert marginal_value(q_at_edge, 1000, 300, P_AVG, shrink=0.5) == 410


class TestUpdateBlendedAvg:
    def test_delta_zero_returns_original(self):
        assert update_blended_avg(1000, 400.0, 0, 350.0) == 400.0

    def test_basic_blend(self):
        # (1000*400 + 100*360) / 1100 = 396.36...
        assert update_blended_avg(1000, 400.0, 100, 360.0) == pytest.approx(396.3636, rel=1e-4)

    def test_small_trade_large_position(self):
        # 大持仓下微量成交影响微小
        new = update_blended_avg(100000, 400.0, 10, 300.0)
        assert abs(new - 400.0) < 0.02

    def test_negative_delta_q(self):
        # 卖出减仓 ΔQ<0，公式同样适用
        assert update_blended_avg(1000, 400.0, -100, 450.0) == pytest.approx(394.444, rel=1e-3)
