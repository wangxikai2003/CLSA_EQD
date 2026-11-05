"""Black-76 pricing and Greeks for index futures options."""
from __future__ import annotations

import math
from typing import Dict


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black76_greeks(
    F: float,
    K: float,
    t: float,
    sigma: float,
    r: float,
    cp: str,
) -> Dict[str, float]:
    """Return price (pts) and Greeks.

    Delta / gamma are *undiscounted* forward greeks (for IF hedging).
    Vega is ∂V/∂σ in option points per 1.0 absolute vol (decimal).
    Vega_point is per 1 vol-point (1%).
    Theta is calendar-year ∂V/∂t in option points (use × Δt/365 in PnL).
    """
    t = max(float(t), 1e-8)
    sigma = max(float(sigma), 1e-8)
    F = max(float(F), 1e-12)
    K = max(float(K), 1e-12)
    r = float(r or 0.0)
    cp_u = (cp or "C").upper()[:1]

    vol_sqrt = sigma * math.sqrt(t)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * t) / vol_sqrt
    d2 = d1 - vol_sqrt
    df = math.exp(-r * t)
    nd1 = _norm_pdf(d1)
    Nd1 = _norm_cdf(d1)
    Nd2 = _norm_cdf(d2)

    if cp_u == "C":
        undisc = F * Nd1 - K * Nd2
        delta = Nd1
        # discounted theta (calendar)
        theta = (
            -df * F * nd1 * sigma / (2.0 * math.sqrt(t))
            + r * df * K * Nd2
            - r * df * undisc
        )
    else:
        undisc = K * _norm_cdf(-d2) - F * _norm_cdf(-d1)
        delta = Nd1 - 1.0
        theta = (
            -df * F * nd1 * sigma / (2.0 * math.sqrt(t))
            - r * df * K * _norm_cdf(-d2)
            - r * df * undisc
        )

    price = df * undisc
    gamma = nd1 / (F * vol_sqrt)  # undiscounted ∂²/∂F²
    vega = df * F * nd1 * math.sqrt(t)  # per 1.0 vol
    return {
        "price": float(price),
        "delta": float(delta),
        "gamma": float(gamma),
        "vega": float(vega),
        "vega_point": float(vega / 100.0),
        "theta": float(theta),
        "d1": float(d1),
        "d2": float(d2),
    }
