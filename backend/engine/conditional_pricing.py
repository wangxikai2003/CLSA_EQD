"""
Forward conditional exotic pricing from intermediate time t.

price_exotic_conditional(S_t, t_current, history_ki, ...) prices the remaining
cashflows on [t_current, T] under Before-KI / After-KI regimes.
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

import numpy as np

from engine.vectorized_engine import (
    KiMonitoring,
    KoMonitoring,
    price_grid_at_t,
)

ProductKind = Literal["snowball", "fcn", "phoenix"]


def price_exotic_conditional(
    S_t: float,
    t_current: float,
    history_ki: bool,
    product_params: Dict[str, Any],
    mc_params: Optional[Dict[str, Any]] = None,
    product_type: str = "snowball",
) -> Dict[str, float]:
    """
    Conditional forward revaluation at (S_t, t_current).

    Parameters
    ----------
    S_t : spot at valuation time
    t_current : years from inception, in [0, T]
    history_ki : True => After KI (locked); False => Before KI
    product_params : tenor, ko_barrier, ki_barrier, strike, coupon / fixed_coupon_pa, ...
                     plus S0 (initial ref), r, vol, ki_monitoring, ko_monitoring
    """
    mc = dict(mc_params or {})
    T = float(product_params.get("tenor", 1.0))
    S0 = float(product_params.get("S0", product_params.get("spot_ref", S_t)))
    r = float(product_params.get("r", 0.02))
    vol = float(product_params.get("vol", 0.2))
    q = float(product_params.get("q", 0.0))

    ki_mon = str(product_params.get("ki_monitoring_type", product_params.get("ki_style", "DAILY"))).upper()
    if ki_mon in ("AMERICAN", "DAILY", "CONTINUOUS"):
        ki_mode = KiMonitoring.DAILY
    else:
        ki_mode = KiMonitoring.EUROPEAN

    ko_mon = str(product_params.get("ko_monitoring_type", "EUROPEAN")).upper()
    ko_mode = KoMonitoring.DAILY if ko_mon in ("DAILY", "CONTINUOUS", "CONTINUOUS_AMERICAN") else KoMonitoring.EUROPEAN

    # Physical constraint: American + S<=KI cannot be Before KI
    ki_abs = float(product_params.get("ki_barrier", 0.75)) * S0
    if (not history_ki) and ki_mode == KiMonitoring.DAILY and S_t <= ki_abs + 1e-12:
        return {"price": float("nan"), "stderr": float("nan"), "valid": 0.0}

    n_paths = int(mc.get("n_paths", 8000))
    seed = int(mc.get("seed", 42))
    steps_per_year = int(mc.get("steps_per_year", 252))

    ptype = product_type.lower()
    if ptype == "phoenix":
        ptype = "snowball"  # approximate with snowball kernel for term-structure

    grid = price_grid_at_t(
        product_type=ptype,
        S0=S0,
        spots=np.array([float(S_t)], dtype=float),
        t_current=float(t_current),
        T=T,
        r=r,
        q=q,
        vol=vol,
        ko_barrier=float(product_params.get("ko_barrier", 1.03)),
        ki_barrier=float(product_params.get("ki_barrier", 0.75)),
        strike=float(product_params.get("strike", 1.0 if ptype == "snowball" else 0.75)),
        coupon=float(
            product_params.get(
                "coupon",
                product_params.get("fixed_coupon_pa", product_params.get("snowball_coupon_pa", 0.15)),
            )
        ),
        bonus_coupon=float(product_params.get("bonus_coupon", 0.0)),
        step_down=float(product_params.get("step_down", 0.0)),
        obs_per_year=12 if str(product_params.get("obs_freq", "monthly")).lower().startswith("month") else (
            252 if str(product_params.get("obs_freq", "")).lower().startswith("day") else 1
        ),
        ki_monitoring=ki_mode,
        ko_monitoring=ko_mode,
        history_ki=bool(history_ki),
        n_paths=n_paths,
        steps_per_year=steps_per_year,
        seed=seed,
        barrier_shift_bps=float(product_params.get("barrier_shift_bps", 0.0)),
        spread_smoothing_width=float(product_params.get("spread_smoothing_width", 0.0)),
    )
    pv = float(grid["pv"][0])
    return {
        "price": pv,
        "stderr": float(grid.get("stderr", [0.0])[0]),
        "valid": 0.0 if not np.isfinite(pv) else 1.0,
    }
