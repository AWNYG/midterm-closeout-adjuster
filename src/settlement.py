"""时段结算模型：带内外偏差结算、边际价值 MV_t(0)、成交后混合均价更新。

纯函数模块，可独立单测。参数来自 config/params.yaml 或默认值。
"""

DEFAULT_BAND_LOW = 0.85
DEFAULT_BAND_HIGH = 1.15
DEFAULT_RECOVER = 1.1


def settle_price(Q_c: float, L: float, S: float, P_avg: float,
                 band_low: float = DEFAULT_BAND_LOW,
                 band_high: float = DEFAULT_BAND_HIGH,
                 recover: float = DEFAULT_RECOVER) -> float:
    """返回该时段偏差电量的结算价（元/MWh）。

    带内（0.85 ≤ L/Q ≤ 1.15，边界精确覆盖）按实时现货价 S 结算；
    带外多用（L > 1.15·Q）结算 max(S, 1.1·P_avg − 0.1·S)；
    带外少用（L < 0.85·Q）结算 min(S, 1.1·P_avg − 0.1·S)。

    注：文档 2.1 中偏差率式 r=(L−Q)/Q 与"多用 L>1.15Q"表述矛盾，
    此处采用与 3.1/MV 阶梯一致的 L/Q 带（边界 L/1.15、L/0.85）。
    """
    if Q_c <= 0:
        raise ValueError("Q_c must be positive")
    k = recover * P_avg - (recover - 1) * S
    if band_low * Q_c <= L <= band_high * Q_c:
        return S
    if L > Q_c * band_high:          # 带外多用
        return max(S, k)
    return min(S, k)                 # 带外少用


def marginal_value(Q_c: float, L: float, S: float, P_avg: float,
                   band_low: float = DEFAULT_BAND_LOW,
                   band_high: float = DEFAULT_BAND_HIGH,
                   recover: float = DEFAULT_RECOVER,
                   shrink: float = 0.0) -> float:
    """单时段第 1 个单位合约的期望价值 MV_t(0)（3.1 中 Q=0 的情形）。

    超配（Q_c > L/0.85）→ min(S, k)；欠配（Q_c < L/1.15）→ max(S, k)；带内 → S。
    边界值 Q_c == L/1.15 或 Q_c == L/0.85 视为带内。
    shrink 为带边界向中心收缩比例（0~1，σ 不可用时的保守参数）。
    """
    if Q_c <= 0:
        raise ValueError("Q_c must be positive")
    k = recover * P_avg - (recover - 1) * S
    if shrink > 0:
        lo = (L / band_high + L / band_low) / 2.0
        half = (L / band_low - L / band_high) / 2.0 * (1.0 - shrink)
        lo, hi = lo - half, lo + half
    else:
        lo, hi = L / band_high, L / band_low
    if Q_c > hi:
        return min(S, k)
    if Q_c < lo:
        return max(S, k)
    return S


def update_blended_avg(Q_c: float, P_avg: float, delta_q: float, p_trade: float) -> float:
    """成交后混合均价：P_avg_new = (Q_c·P_avg + ΔQ·P_trade) / (Q_c + ΔQ)。

    ΔQ 为 0 时返回原值（不成交不动均价）。
    ΔQ ≤ −Q_c（卖出全部持仓）时剩余电量为 0，混合均价无意义，抛 ValueError。
    """
    if delta_q == 0:
        return P_avg
    if Q_c + delta_q <= 0:
        raise ValueError(f"Q_c+ΔQ 必须为正: {Q_c} + {delta_q} = {Q_c + delta_q}")
    return (Q_c * P_avg + delta_q * p_trade) / (Q_c + delta_q)
