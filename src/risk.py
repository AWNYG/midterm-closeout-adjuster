"""风控：限额/金额裁剪、置信度否决、量取整到最小交易单位。"""

import math

CONFIDENCE_MIN = 0.5


def _lot_decimals(min_lot: float) -> int:
    """min_lot 的小数位数（如 0.001→3、0.1→1、10→0），用于清除取整结果的浮点尾数。"""
    s = f"{min_lot:.12f}".rstrip("0").rstrip(".")
    return len(s) - s.index(".") - 1 if "." in s else 0


def floor_lot(volume: float, min_lot: float) -> float:
    """向下取整到 min_lot 的整数倍；负量视为 0。

    带 1e-9（商量级）容差，抵消 L/1.15−Q_c 等浮点运算
    恰好低于整包边界（如 129.99999999999994）导致的整包丢失。
    结果按 min_lot 的小数位数圆整，清除 0.9000000000000001 类浮点尾数。
    """
    if min_lot <= 0:
        raise ValueError(f"min_lot 必须为正: {min_lot}")
    if volume <= 0:
        return 0.0
    lots = math.floor(volume / min_lot + 1e-9)
    return round(lots * min_lot, _lot_decimals(min_lot))


def check(volume: float, position: float, position_limit: float,
          max_amount: float | None, best_price: float,
          confidence: float, min_lot: float, confidence_min: float = CONFIDENCE_MIN,
          side: str = "buy") -> tuple[float, list]:
    """限额裁剪 + 置信度否决 + 取整。

    side: "buy" 受持仓限额上限约束（持仓不得超过限额）；
          "sell" 减仓不受持仓限额约束（超限持仓允许卖出减仓）。

    return: (volume, reasons)。volume=0 表示该时段应 hold。
    """
    reasons: list = []
    if volume <= 0:
        return 0.0, reasons
    if not (confidence >= confidence_min):   # 含 NaN：比较恒 False → 拒绝
        return 0.0, ["low_confidence"]
    if side == "buy":
        room = position_limit - position
        if room < volume:
            volume = room
            reasons.append("position_cap")
    if max_amount is not None and best_price > 0:
        cap = int(max_amount / best_price)
        if cap < volume:
            volume = cap
            reasons.append("amount_cap")
    volume = floor_lot(volume, min_lot)
    if volume < min_lot:
        return 0.0, reasons
    return volume, reasons
