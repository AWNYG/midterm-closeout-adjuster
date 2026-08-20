import pytest

from src.decision import band_room, decide_period
from src.risk import floor_lot

THETA = 2.0
MIN_LOT = 10.0
POS_LIMIT = 5000.0


def book(bid1, ask1, bid_vols=(100, 80, 60, 40, 20), ask_vols=(100, 80, 60, 40, 20),
         bid_step=1.0, ask_step=1.0):
    return {
        "bid": [{"px": bid1 - bid_step * i, "vol": v} for i, v in enumerate(bid_vols)],
        "ask": [{"px": ask1 + ask_step * i, "vol": v} for i, v in enumerate(ask_vols)],
    }


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
        # 欠配：Q_c=800, L=1000 → mv0 = 410 > ask1+θ
        b = book(bid1=388.0, ask1=393.0)
        d = decide_period(b, 800, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "buy"
        lo, hi = d.price_range
        assert lo == pytest.approx(388.0)           # P_low = bid1
        assert hi == pytest.approx(min(393.0, 410 - THETA))  # P_high = min(ask1, mv−θ) = 393
        assert hi <= 410 - THETA + 1e-9             # 恒不等式
        assert lo >= 388.0 - 1e-9
        assert d.volume_mwh % MIN_LOT == 0
        assert d.orders[0]["side"] == "buy"
        assert lo <= d.orders[0]["price"] <= hi
        assert d.mv == pytest.approx(410)
        assert "spread>theta" in d.reasons

    def test_depth_cap(self):
        # P_high = min(ask1=393, mv−θ=408) = 393 → 只有第 1 档 px=393 在区间内 → 深度 10
        b = book(bid1=388.0, ask1=393.0, ask_vols=(10, 10, 10, 10, 10))
        d = decide_period(b, 800, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.volume_mwh == 10
        assert "depth_cap" in d.reasons

    def test_room_cap_beats_depth(self):
        # S=500 > P_avg=400 → 目标 L/0.85；L=875.5 → room_buy = 30，深度 200 → 量 = 30
        b = book(bid1=470.0, ask1=480.0, ask_vols=(200, 200, 200, 200, 200))
        d = decide_period(b, 1000, 875.5, 500, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.volume_mwh == 30
        assert "band_edge" in d.reasons

    def test_limit_cap(self):
        # 限额余量 < 带余量
        b = book(bid1=388.0, ask1=393.0, ask_vols=(500, 500, 500, 500, 500))
        d = decide_period(b, 800, 1000, 300, 400,
                          position_limit=830, min_lot=MIN_LOT, theta=THETA, period=0)
        assert d.volume_mwh == 30  # 限额余量 830-800=30

    def test_expected_below_min_lot_hold(self):
        # 深度仅 5 MWh → 期望量 0（< 10）→ hold，保留信号原因
        b = book(bid1=388.0, ask1=393.0, ask_vols=(5, 0, 0, 0, 0))
        d = decide_period(b, 800, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "hold"
        assert d.volume_mwh == 0
        assert "below_min_lot" in d.reasons
        assert "spread>theta" in d.reasons

    def test_buy_position_cap_hold_reason(self):
        # 已到/超过持仓限额 → 买单被限额否决，保留 position_cap 留痕
        b = book(bid1=388.0, ask1=393.0, ask_vols=(500, 500, 500, 500, 500))
        d = decide_period(b, 800, 1000, 300, 400,
                          position_limit=780, min_lot=MIN_LOT, theta=THETA, period=0)
        assert d.action == "hold"
        assert "position_cap" in d.reasons
        assert "spread>theta" in d.reasons

    def test_buy_partial_position_cap_reason(self):
        # 限额余量是三方最小值 → 量被限额裁剪，留痕 position_cap
        # Q_c=800, 深度 500, 带余量 869.6-800=69.6，限额 820 → 余量 20
        b = book(bid1=388.0, ask1=393.0, ask_vols=(500, 500, 500, 500, 500))
        d = decide_period(b, 800, 1000, 300, 400,
                          position_limit=820, min_lot=MIN_LOT, theta=THETA, period=0)
        assert d.action == "buy"
        assert d.volume_mwh == 20
        assert "position_cap" in d.reasons

    def test_buy_position_cap_not_flagged_when_unclipped(self):
        # 限额余量充足 → 不出现 position_cap
        b = book(bid1=388.0, ask1=393.0, ask_vols=(40, 0, 0, 0, 0))
        d = decide_period(b, 800, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.volume_mwh == 40
        assert "position_cap" not in d.reasons

    def test_spot_equals_avg_price_targets_high_edge(self):
        # S == P_avg 归入"合约便宜"分支 → 目标 L/0.85（行为已锁定）
        buy, sell = band_room(1000, 1000, 300, 300)
        assert buy == pytest.approx(1000 / 0.85 - 1000)
        assert sell == 0

    def test_floor_lot_epsilon_no_lot_loss(self):
        # 带内余量浮点运算落到整包边界下方 1e-14 时不得丢一整包
        from src.risk import floor_lot
        assert floor_lot(299.9999999999999, 10) == 300
        assert floor_lot(129.99999999999994, 10) == 130
        assert floor_lot(299.99995, 10) == 290  # 真实低于边界仍向下取整


class TestDecideSell:
    def test_basic_sell_range_and_invariants(self):
        # 超配：Q_c=1200, L=1000, S=500 → mv0 = 390；bid1 - θ > mv0
        b = book(bid1=398.0, ask1=402.0)
        d = decide_period(b, 1200, 1000, 500, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=1)
        assert d.action == "sell"
        lo, hi = d.price_range
        assert lo == pytest.approx(max(398.0, 390 + THETA))  # 392 < 398 → 398
        assert hi == pytest.approx(402.0)                    # P_high = ask1
        assert lo >= 390 + THETA - 1e-9
        assert hi <= 402.0 + 1e-9
        assert d.volume_mwh % MIN_LOT == 0
        assert d.orders[0]["side"] == "sell"
        assert lo <= d.orders[0]["price"] <= hi

    def test_sell_low_is_bid1_above_floor(self):
        # 卖触发要求 bid1 > mv0+θ，故 P_low = max(bid1, mv0+θ) = bid1（下界恒 ≥ mv0+θ）
        b = book(bid1=393.0, ask1=402.0, bid_vols=(500, 0, 0, 0, 0))
        d = decide_period(b, 1200, 1000, 500, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=1)
        assert d.action == "sell"
        assert d.price_range[0] == pytest.approx(393.0)
        assert d.price_range[0] >= 390 + THETA - 1e-9

    def test_bid1_equal_mv_plus_theta_hold(self):
        # bid1 == mv0+θ 时不满足严格触发 → hold
        b = book(bid1=392.0, ask1=402.0, bid_vols=(500, 0, 0, 0, 0))
        d = decide_period(b, 1200, 1000, 500, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=1)
        assert d.action == "hold"

    def test_sell_room_limited(self):
        # 超配但只允许卖回目标位 L/0.85=1176.5 → room_sell = 23.5 → 20
        b = book(bid1=398.0, ask1=402.0, bid_vols=(500, 500, 500, 500, 500))
        d = decide_period(b, 1200, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=1)
        # S=300 < P_avg=400 → 目标 L/1.15=869.6 → room_sell = 1200-869.6 = 330.4，深度 500 → 330
        assert d.volume_mwh == 330


class TestDecideHold:
    def test_no_spread_hold(self):
        # 带内 mv0 = S = 300，盘口 301/302 与 mv 价差 ≤ θ → hold
        b = book(bid1=301.0, ask1=302.0)
        d = decide_period(b, 1000, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "hold"
        assert d.price_range is None
        assert d.expected_pnl_cny == 0

    def test_mv_mid_in_band(self):
        # 带内 mv0 = S = 300，价差超过 θ 但方向不明（ask1=302, bid1=298 → 均不触发）
        b = book(bid1=298.0, ask1=302.0)
        d = decide_period(b, 1000, 1000, 300, 400,
                          POS_LIMIT, MIN_LOT, THETA, period=0)
        assert d.action == "hold"

    def test_expected_pnl_uses_confidence(self):
        b = book(bid1=388.0, ask1=393.0, ask_vols=(100, 100, 100, 100, 100))
        d0 = decide_period(b, 800, 1000, 300, 400,
                           POS_LIMIT, MIN_LOT, THETA, period=0, confidence=1.0)
        d1 = decide_period(b, 800, 1000, 300, 400,
                           POS_LIMIT, MIN_LOT, THETA, period=0, confidence=0.5)
        assert d0.expected_pnl_cny == pytest.approx(2 * d1.expected_pnl_cny)


class TestFloorLot:
    def test_floor_to_10(self):
        assert floor_lot(123.0, 10) == 120
        assert floor_lot(9.9, 10) == 0
        assert floor_lot(0, 10) == 0
        assert floor_lot(-5, 10) == 0
