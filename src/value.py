"""单时段边际价值 MV_t(Q) 阶梯曲线与带边界。

MV 关于累计买入量 Q 单调不增：
  Q_c+Q < L/1.15（欠配）→ max(S, k)
  带内（L/1.15 ≤ Q_c+Q ≤ L/0.85）→ S
  Q_c+Q > L/0.85（超配）→ min(S, k)
其中 k = recover·P_avg − (recover−1)·S。
"""

import numpy as np

from .settlement import DEFAULT_BAND_LOW, DEFAULT_BAND_HIGH, DEFAULT_RECOVER


def band_bounds(load: float,
                band_low: float = DEFAULT_BAND_LOW,
                band_high: float = DEFAULT_BAND_HIGH,
                shrink: float = 0.0) -> tuple[float, float]:
    """带边界 [L/1.15, L/0.85]，shrink 为向中心收缩比例（0~1，σ 不可用时的保守参数）。

    收缩后带宽 = (1 − shrink) × 原带宽，边界 = 中心 ± 半带宽。
    """
    lo = load / band_high
    hi = load / band_low
    if not 0.0 <= shrink < 1.0:
        raise ValueError("shrink must be in [0, 1)")
    if shrink > 0:
        center = (lo + hi) / 2.0
        half = (hi - lo) / 2.0 * (1.0 - shrink)
        lo, hi = center - half, center + half
    return lo, hi


def mv_curve(Q_c: float, L: float, S: float, P_avg: float,
             max_q: float, step: float = 1.0,
             band_low: float = DEFAULT_BAND_LOW,
             band_high: float = DEFAULT_BAND_HIGH,
             recover: float = DEFAULT_RECOVER,
             shrink: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """返回 (Q 序列, MV(Q) 序列)，Q 从 0 到 max_q（含），单调不增。

    带边界按 shrink 收缩（见 band_bounds）。边界值视为带内（MV = S）。
    """
    if Q_c <= 0:
        raise ValueError("Q_c must be positive")
    qs = np.arange(0.0, max_q + 1e-9, step)
    k = recover * P_avg - (recover - 1) * S
    lo, hi = band_bounds(L, band_low, band_high, shrink)
    pos = Q_c + qs
    mv = np.where(pos > hi, min(S, k),
                  np.where(pos < lo, max(S, k), S))
    return qs, mv
