import pytest

from src.decision import band_room, decide_period
from src.risk import floor_lot

THETA = 2.0
MIN_LOT = 10.0
POS_LIMIT = 5000.0


class TestBandRoom:
    def test_spot_cheap_target_low_edge(self):
        # S < P_avg → 目标 L/1.15；Q_c 高于目标 → 只允许卖
        buy, sell = band_room(1000, 1000, 300, 400)
        assert buy == 0
        assert sell == pytest.approx(1000 - 1000 / 1.15)

    def test_contract_cheap_target_high_edge(self):
        # S > P_avg → 目标 L/0.85；Q_c 低于目标 → 只允许买
        buy, sell = band_room(1000, 1000, 500, 400)
        assert sell == 0
        assert buy == pytest.approx(1000 / 0.85 - 1000)

    def test_above_high_edge_can_sell_only(self):
        buy, sell = band_room(1500, 1000, 300, 400)
        assert buy == 0
        assert sell == pytest.approx(1500 - 1000 / 1.15)

    def test_shrink_reduces_room(self):
        buy0, _ = band_room(1000, 1000, 500, 400, shrink=0.0)
        buy1, _ = band_room(1000, 1000, 500, 400, shrink=0.5)
        assert buy1 < buy0
        assert buy0 == pytest.approx(1000 / 0.85 - 1000)


class TestDecideBuy:
    def test_basic_buy_range_and_invariants(self):
        # 欠配：Q_c=800, L=1000 → mv0 = 410；A=405 < mv0−θ → 买
        A = 405.0
        d = decide_period(A, 800, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "buy"
        lo, hi = d.price_range
        assert lo == pytest.approx(A - THETA / 2)           # [A−θ/2, A+θ/2]
        assert hi == pytest.approx(A + THETA / 2)
        # 本例 mv0−A=5 > 3θ/2 → 上界恰好低于 mv−θ（并非恒成立，见 test_range_*_not_capped）
        assert hi <= 410 - THETA + 1e-9
        assert d.volume_mwh % MIN_LOT == 0
        assert d.orders[0]["side"] == "buy"
        assert d.orders[0]["price"] == pytest.approx(A)     # 初始挂单价 = A（区间中点）
        assert lo <= d.orders[0]["price"] <= hi
        assert d.mv == pytest.approx(410)
        assert "spread>theta" in d.reasons
        assert "position_cap" not in d.reasons

    def test_range_upper_not_capped_by_mv_minus_theta(self):
        # 触发只保证挂单价 A 的边际 ≥ θ；当 mv0 ∈ (A+θ, A+3θ/2) 时区间上界
        # A+θ/2 会超过 mv0−θ（区间不按内在价值线封顶，设计行为）
        A = 405.0
        d = decide_period(A, 800, 1000, 300, 437.5 / 1.1,   # mv0 = max(S,k) = 407.5
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "buy"
        assert d.mv == pytest.approx(407.5)
        assert d.price_range[1] == pytest.approx(A + THETA / 2)
        assert d.price_range[1] > d.mv - THETA              # 上界越过 mv0−θ
        assert d.orders[0]["price"] == pytest.approx(A)     # 挂单价仍 = A，边际 = 2.5 > θ
        assert d.expected_pnl_cny > 0

    def test_band_edge(self):
        # S=500 > P_avg=400 → 目标 L/0.85；L=875.5 → room_buy = 30 → 量 = 30
        A = 490.0
        d = decide_period(A, 1000, 875.5, 500, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "buy"
        assert d.volume_mwh == 30
        assert "band_edge" in d.reasons
        assert "position_cap" not in d.reasons

    def test_limit_cap(self):
        # 限额余量 < 带余量（Q_c=800, room_buy=69.6, 限额 830 → 余量 30）
        A = 405.0
        d = decide_period(A, 800, 1000, 300, 400,
                          position_limit=830, min_lot=MIN_LOT, theta=THETA, period=0)
        assert d.volume_mwh == 30
        assert "position_cap" in d.reasons

    def test_expected_below_min_lot_hold(self):
        # 带内余量 0.43 MWh（< 10）→ 期望量 0 → hold，保留信号原因
        A = 405.0
        d = decide_period(A, 1000, 1150.5, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "hold"
        assert d.volume_mwh == 0
        assert "below_min_lot" in d.reasons
        assert "spread>theta" in d.reasons

    def test_buy_position_cap_hold_reason(self):
        # 已到/超过持仓限额 → 买单被限额否决，保留 position_cap 留痕
        A = 405.0
        d = decide_period(A, 800, 1000, 300, 400,
                          position_limit=780, min_lot=MIN_LOT, theta=THETA, period=0)
        assert d.action == "hold"
        assert "position_cap" in d.reasons
        assert "spread>theta" in d.reasons

    def test_buy_partial_position_cap_reason(self):
        # 限额余量是两方最小值 → 量被限额裁剪，留痕 position_cap
        # Q_c=800, 带余量 869.6-800=69.6，限额 820 → 余量 20
        A = 405.0
        d = decide_period(A, 800, 1000, 300, 400,
                          position_limit=820, min_lot=MIN_LOT, theta=THETA, period=0)
        assert d.action == "buy"
        assert d.volume_mwh == 20
        assert "position_cap" in d.reasons

    def test_buy_position_cap_not_flagged_when_unclipped(self):
        # 限额余量充足 → 不出现 position_cap（量被带内余量封顶 → band_edge）
        A = 405.0
        d = decide_period(A, 800, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.volume_mwh == 60
        assert "position_cap" not in d.reasons
        assert "band_edge" in d.reasons

    def test_spot_equals_avg_price_targets_high_edge(self):
        # S == P_avg 归入"合约便宜"分支 → 目标 L/0.85（行为已锁定）
        buy, sell = band_room(1000, 1000, 300, 300)
        assert buy == pytest.approx(1000 / 0.85 - 1000)
        assert sell == 0

    def test_floor_lot_epsilon_no_lot_loss(self):
        from src.risk import floor_lot
        assert floor_lot(299.9999999999999, 10) == 300
        assert floor_lot(129.99999999999994, 10) == 130
        assert floor_lot(299.99995, 10) == 290  # 真实低于边界仍向下取整


class TestDecideSell:
    def test_basic_sell_range_and_invariants(self):
        # 超配：Q_c=1200, L=1000, S=500 → mv0 = 390；A − θ > mv0 → 卖
        A = 395.0
        d = decide_period(A, 1200, 1000, 500, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=1)
        assert d.action == "sell"
        lo, hi = d.price_range
        assert lo == pytest.approx(A - THETA / 2)
        assert hi == pytest.approx(A + THETA / 2)
        # 本例 A−mv0=5 > 3θ/2 → 下界恰好高于 mv+θ（并非恒成立，见 test_range_*_not_capped）
        assert lo >= 390 + THETA - 1e-9
        assert d.volume_mwh % MIN_LOT == 0
        assert d.orders[0]["side"] == "sell"
        assert d.orders[0]["price"] == pytest.approx(A)
        assert lo <= d.orders[0]["price"] <= hi
        assert d.mv == pytest.approx(390)

    def test_range_lower_not_capped_by_mv_plus_theta(self):
        # 卖触发只保证挂单价 A 的边际 ≥ θ；当 mv0 ∈ (A−θ, A−θ/2) 时区间下界
        # A−θ/2 会低于 mv0+θ（区间不按内在价值线封顶，设计行为）
        A = 395.0
        d = decide_period(A, 1200, 1000, 500, 442.5 / 1.1,   # mv0 = min(S,k) = 392.5
                          POS_LIMIT, MIN_LOT, THETA, period=1)
        assert d.action == "sell"
        assert d.mv == pytest.approx(392.5)
        assert d.price_range[0] == pytest.approx(A - THETA / 2)
        assert d.price_range[0] < d.mv + THETA              # 下界越过 mv0+θ
        assert d.orders[0]["price"] == pytest.approx(A)     # 挂单价仍 = A，边际 = 2.5 > θ
        assert d.expected_pnl_cny > 0

    def test_avg_equal_mv_plus_theta_hold(self):
        # A == mv0+θ 时不满足严格触发 → hold
        A = 392.0  # mv0 = 390
        d = decide_period(A, 1200, 1000, 500, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=1)
        assert d.action == "hold"

    def test_sell_room_limited(self):
        # 超配但只允许卖回目标位 L/1.15=869.6 → room_sell = 330.4 → 330
        A = 305.0
        d = decide_period(A, 1200, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=1)
        assert d.volume_mwh == 330
        assert "band_edge" in d.reasons

    def test_sell_not_position_capped(self):
        # 卖单减仓不受持仓限额约束（超限持仓允许卖出）
        A = 305.0
        d = decide_period(A, 1200, 1000, 300, 400,
                          position_limit=300, min_lot=MIN_LOT, theta=THETA, period=1)
        assert d.action == "sell"
        assert "position_cap" not in d.reasons


class TestDecideHold:
    def test_no_spread_hold(self):
        # 带内 mv0 = S = 300，A=301 与 mv 价差 ≤ θ → hold
        d = decide_period(301.0, 1000, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "hold"
        assert d.price_range is None
        assert d.expected_pnl_cny == 0

    def test_mv_mid_in_band(self):
        # 带内 mv0 = S = 300，A=300 两个方向均不触发 → hold
        d = decide_period(300.0, 1000, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "hold"

    def test_expected_pnl_uses_confidence(self):
        A = 405.0
        d0 = decide_period(A, 800, 1000, 300, 400,
                           POS_LIMIT, MIN_LOT, THETA, period=0, confidence=1.0)
        d1 = decide_period(A, 800, 1000, 300, 400,
                           POS_LIMIT, MIN_LOT, THETA, period=0, confidence=0.5)
        assert d0.expected_pnl_cny == pytest.approx(2 * d1.expected_pnl_cny)


class TestFloorLot:
    def test_floor_to_10(self):
        assert floor_lot(123.0, 10) == 120
        assert floor_lot(9.9, 10) == 0
        assert floor_lot(0, 10) == 0
        assert floor_lot(-5, 10) == 0
