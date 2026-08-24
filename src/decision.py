"""单时段决策：方向判定、报价区间、目标量（挂单为主，不追价）。

main.py 对 24 个时段各调用一次 decide_period。
报价区间规则（围绕预测平均成交价 A = avg_trade_price_yuan_mwh[t]）：
  买：mv0 − θ > A       卖：A − θ > mv0       否则 hold
  报价区间（买卖对称）：[A − θ/2, A + θ/2]，初始挂单价 = A（区间中点）
目标量：买 = floor_lot(min(带内买入余量, 持仓限额余量))
        卖 = floor_lot(min(带内卖出余量, Q_c))
"""

from dataclasses import dataclass, field

from .risk import floor_lot
from .settlement import DEFAULT_BAND_LOW, DEFAULT_BAND_HIGH, DEFAULT_RECOVER, marginal_value
from .value import band_bounds


@dataclass
class Decision:
    period: int
    action: str                # buy / sell / hold
    price_range: list | None   # [low, high]；hold 时为 None
    volume_mwh: float          # 目标成交量（min_lot 的整数倍）
    orders: list               # 算法建议的初始挂单 [{side, price, volume}]
    mv: float                  # 该时段边际价值 MV_t(0)
    expected_pnl_cny: float    # 按初始挂单价成交的期望收益（保守口径）
    reasons: list = field(default_factory=list)


def band_room(Q_c: float, L: float, S: float, P_avg: float,
              band_low: float = DEFAULT_BAND_LOW,
              band_high: float = DEFAULT_BAND_HIGH,
              shrink: float = 0.0) -> tuple[float, float]:
    """带内回拉余量（按目标位置口径，3.6）：

    S < P_avg（现货便宜）→ 少持合约，目标 = L/1.15（下沿）
    S > P_avg（合约便宜）→ 多持合约，目标 = L/0.85（上沿）
    返回 (room_buy, room_sell)，均 ≥ 0。shrink 为带边界向中心收缩比例。
    """
    lo, hi = band_bounds(L, band_low, band_high, shrink)
    # S == P_avg 归入"合约便宜"分支（目标=带上沿 L/0.85）——文档 3.6 仅定义严格不等，
    # 边界等值按多持合约处理，已在测试中锁定该行为。
    target = lo if S < P_avg else hi
    room_buy = max(0.0, target - Q_c)
    room_sell = max(0.0, Q_c - target)
    return room_buy, room_sell


def _make_order(side: str, price: float, volume: float) -> dict:
    return {"side": side, "price": round(price, 2), "volume": volume}


def _initial_price(p_low: float, p_high: float, policy: str) -> float:
    """初始挂单价：当前仅实现 mid=区间中间价（策略扩展点）。"""
    return (p_low + p_high) / 2.0


def decide_period(avg_trade_price: float, Q_c: float, L: float, S: float, P_avg: float,
                  position_limit: float, min_lot: float, theta: float,
                  confidence: float = 1.0,
                  period: int = 0,
                  band_low: float = DEFAULT_BAND_LOW,
                  band_high: float = DEFAULT_BAND_HIGH,
                  recover: float = DEFAULT_RECOVER,
                  shrink: float = 0.0,
                  initial_order_policy: str = "mid") -> Decision:
    """单时段决策。avg_trade_price 为该时段预测盘口平均成交价 A（元/MWh）。

    单时段金额上限不在本层应用，由调用方经 risk.check 裁剪。

    return: Decision
    """
    mv0 = marginal_value(Q_c, L, S, P_avg, band_low, band_high, recover, shrink)
    room_buy, room_sell = band_room(Q_c, L, S, P_avg, band_low, band_high, shrink)

    if mv0 - theta > avg_trade_price:             # 买方向
        p_low = min(avg_trade_price - theta / 2.0, avg_trade_price + theta / 2.0)
        p_high = max(avg_trade_price - theta / 2.0, avg_trade_price + theta / 2.0)
        room_limit = position_limit - Q_c
        q = min(room_buy, room_limit)
        reasons = ["spread>theta"]
        if q >= room_buy - 1e-9:
            reasons.append("band_edge")
        if room_limit <= room_buy + 1e-9:
            reasons.append("position_cap")
        if room_limit <= 0:
            return Decision(period, "hold", None, 0, [], mv0, 0.0, reasons)
        q = floor_lot(q, min_lot)
        if q < min_lot:
            return Decision(period, "hold", None, 0, [], mv0, 0.0,
                            reasons + ["below_min_lot"])
        price = _initial_price(p_low, p_high, initial_order_policy)
        pnl = (mv0 - price) * q * confidence
        return Decision(period, "buy", [p_low, p_high], q,
                        [_make_order("buy", price, q)], mv0, pnl, reasons)

    if avg_trade_price - theta > mv0:             # 卖方向（对称）
        p_low = min(avg_trade_price - theta / 2.0, avg_trade_price + theta / 2.0)
        p_high = max(avg_trade_price - theta / 2.0, avg_trade_price + theta / 2.0)
        q = min(room_sell, Q_c)
        reasons = ["spread>theta"]
        if q >= room_sell - 1e-9:
            reasons.append("band_edge")
        q = floor_lot(q, min_lot)
        if q < min_lot:
            return Decision(period, "hold", None, 0, [], mv0, 0.0,
                            reasons + ["below_min_lot"])
        price = _initial_price(p_low, p_high, initial_order_policy)
        pnl = (price - mv0) * q * confidence
        return Decision(period, "sell", [p_low, p_high], q,
                        [_make_order("sell", price, q)], mv0, pnl, reasons)

    return Decision(period, "hold", None, 0, [], mv0, 0.0, ["hold_no_spread"])
