"""单时段决策：方向判定、报价区间、目标量（挂单为主，不追价）。

main.py 对 24 个时段各调用一次 decide_period。
报价区间规则（3.3）：
  买：[bid1, min(ask1, MV_t(0) − θ)]
  卖：[max(bid1, MV_t(0) + θ), ask1]
目标量（3.4）：floor_lot( min(区间内对手方深度, 带内回拉余量, 持仓限额余量) )
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
    volume_mwh: float          # 目标成交量（10 的整数倍）
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


def decide_period(book: dict, Q_c: float, L: float, S: float, P_avg: float,
                  position_limit: float, min_lot: float, theta: float,
                  confidence: float = 1.0,
                  period: int = 0,
                  band_low: float = DEFAULT_BAND_LOW,
                  band_high: float = DEFAULT_BAND_HIGH,
                  recover: float = DEFAULT_RECOVER,
                  shrink: float = 0.0,
                  initial_order_policy: str = "mid") -> Decision:
    """单时段决策。book: {"bid": [5档], "ask": [5档]}，档位 {"px", "vol"}。

    单时段金额上限不在本层应用，由调用方经 risk.check 裁剪。

    return: Decision
    """
    mv0 = marginal_value(Q_c, L, S, P_avg, band_low, band_high, recover, shrink)
    ask1 = book["ask"][0]["px"]
    bid1 = book["bid"][0]["px"]
    room_buy, room_sell = band_room(Q_c, L, S, P_avg, band_low, band_high, shrink)

    if mv0 - theta > ask1:                            # 买方向
        p_high = min(ask1, mv0 - theta)
        p_low = bid1
        # 严格触发下 p_high 恒等于 ask1（推论：mv0−θ > ask1 蕴含 ask1 < mv0−θ），
        # 故深度统计实际只命中第 1 档（详见交接文档 §4.3）
        depth = sum(lvl["vol"] for lvl in book["ask"] if lvl["px"] <= p_high + 1e-9)
        room_limit = position_limit - Q_c
        q = min(depth, room_buy, room_limit)
        reasons = ["spread>theta"]
        if q >= depth - 1e-9:
            reasons.append("depth_cap")
        if q >= room_buy - 1e-9:
            reasons.append("band_edge")
        if room_limit <= min(depth, room_buy) + 1e-9:
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

    if bid1 - theta > mv0:                            # 卖方向（对称）
        p_low = max(bid1, mv0 + theta)
        p_high = ask1
        depth = sum(lvl["vol"] for lvl in book["bid"] if lvl["px"] >= p_low - 1e-9)
        q = min(depth, room_sell, Q_c)
        reasons = ["spread>theta"]
        if q >= depth - 1e-9:
            reasons.append("depth_cap")
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
