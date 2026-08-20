import pytest

from src.risk import check, floor_lot

MIN_LOT = 10.0


class TestCheck:
    def test_limit_clip(self):
        vol, reasons = check(200, 4900, 5000, None, 400, 0.8, MIN_LOT)
        assert vol == 100

    def test_amount_cap(self):
        vol, reasons = check(200, 0, 5000, 10000, 400, 0.8, MIN_LOT)
        assert vol == 20  # int(10000/400)=25 → floor 到 20

    def test_low_confidence_reject(self):
        vol, reasons = check(200, 0, 5000, None, 400, 0.4, MIN_LOT)
        assert vol == 0
        assert "low_confidence" in reasons

    def test_zero_volume(self):
        vol, reasons = check(0, 0, 5000, None, 400, 0.8, MIN_LOT)
        assert vol == 0

    def test_floor_to_lot(self):
        vol, reasons = check(123, 0, 5000, None, 400, 0.8, MIN_LOT)
        assert vol == 120

    def test_volume_below_min_lot(self):
        vol, reasons = check(5, 0, 5000, None, 400, 0.8, MIN_LOT)
        assert vol == 0

    def test_negative_room(self):
        vol, reasons = check(200, 5100, 5000, None, 400, 0.8, MIN_LOT)
        assert vol == 0

    def test_sell_not_capped_by_position_limit(self):
        # 卖单减仓不受持仓限额约束：超限持仓允许卖出
        vol, reasons = check(500, 5100, 5000, None, 400, 0.8, MIN_LOT, side="sell")
        assert vol == 500

    def test_buy_position_cap_reason(self):
        vol, reasons = check(200, 4900, 5000, None, 400, 0.8, MIN_LOT, side="buy")
        assert vol == 100
        assert "position_cap" in reasons

    def test_sell_near_limit_not_clipped(self):
        # 卖 500 但持仓 4950（接近限额）→ 不减量
        vol, reasons = check(500, 4950, 5000, None, 400, 0.8, MIN_LOT, side="sell")
        assert vol == 500
        assert "position_cap" not in reasons

    def test_buy_position_cap_no_reason_when_unclipped(self):
        vol, reasons = check(200, 1000, 5000, None, 400, 0.8, MIN_LOT, side="buy")
        assert vol == 200
        assert reasons == []

    def test_amount_cap_reason(self):
        vol, reasons = check(200, 0, 5000, 10000, 400, 0.8, MIN_LOT)
        assert vol == 20
        assert "amount_cap" in reasons


class TestFloorLot:
    def test_multiples(self):
        assert floor_lot(10, MIN_LOT) == 10
        assert floor_lot(59, MIN_LOT) == 50
        assert floor_lot(0.5, MIN_LOT) == 0

    def test_no_float_artifacts(self):
        # 浮点尾数必须被清除：0.9000000000000001 → 0.9
        assert floor_lot(0.9000000000000001, 0.001) == 0.9
        assert floor_lot(0.30000000000000004, 0.1) == 0.3
        assert floor_lot(0.304, 0.001) == 0.304

    def test_fractional_lot_clean(self):
        assert floor_lot(3.3, 0.5) == 3.0
        assert floor_lot(7.500000000000001, 2.5) == 7.5
