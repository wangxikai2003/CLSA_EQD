"""
CRN finite-difference Greeks, digital call-spread smoothing, barrier shift.
Term-structure Greeks delegated to vectorized_engine.compute_greeks_term_structure.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import numpy as np

# Re-export for callers that expect greeks.compute_greeks_term_structure
from engine.vectorized_engine import compute_greeks_term_structure  # noqa: F401


def call_spread_smooth(
    S: np.ndarray,
    barrier: float,
    width: float,
    above: bool = True,
) -> np.ndarray:
    """
    Replace digital 1_{S>H} with a call-spread ramp over [H-w, H+w] (or below).
    width = Δ_max in spot units (absolute).
    """
    S = np.asarray(S, dtype=float)
    w = max(float(width), 1e-12)
    H = float(barrier)
    if above:
        x = (S - (H - w)) / (2.0 * w)
    else:
        x = ((H + w) - S) / (2.0 * w)
    return np.clip(x, 0.0, 1.0)


def barrier_shift(H: float, dH: float) -> Dict[str, float]:
    """Return over/under hedge barriers H ± ΔH (relative if |dH|<1 else absolute)."""
    H = float(H)
    dH = float(dH)
    if abs(dH) < 1.0:
        # relative bump e.g. 0.01 = 1%
        return {"H_up": H * (1.0 + dH), "H_down": H * (1.0 - dH), "H": H, "dH": dH}
    return {"H_up": H + dH, "H_down": H - dH, "H": H, "dH": dH}


def fd_greeks(
    price_fn: Callable[..., float],
    base_state: Dict[str, Any],
    bump: Optional[Dict[str, float]] = None,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Common-Random-Numbers finite differences.

    price_fn(**state) must accept keys in base_state and honour `seed` for CRN.
    Expected state keys: spot (float or list), vol (float or list), ...
    """
    bump = bump or {"spot": 0.01, "vol": 0.01}
    ds = float(bump.get("spot", 0.01))
    dv = float(bump.get("vol", 0.01))

    def _call(overrides: Dict[str, Any]) -> float:
        st = dict(base_state)
        st.update(overrides)
        st["seed"] = seed
        return float(price_fn(**st))

    # spot vector handling
    spot0 = np.atleast_1d(np.asarray(base_state.get("spot", base_state.get("spots", 1.0)), dtype=float))
    vol0 = np.atleast_1d(np.asarray(base_state.get("vol", base_state.get("flat_vols", 0.2)), dtype=float))
    if vol0.size == 1 and spot0.size > 1:
        vol0 = np.full(spot0.size, float(vol0[0]))

    p0 = _call({})
    deltas, gammas, vegas = [], [], []
    vannas, volgas = [], []

    for i in range(spot0.size):
        sp = spot0.copy()
        sm = spot0.copy()
        sp[i] *= 1.0 + ds
        sm[i] *= 1.0 - ds
        key_spot = "spot" if "spot" in base_state else "spots"
        pp = _call({key_spot: sp.tolist() if sp.size > 1 else float(sp[0])})
        pm = _call({key_spot: sm.tolist() if sm.size > 1 else float(sm[0])})
        dS = spot0[i] * ds
        delta = (pp - pm) / (2.0 * dS)
        gamma = (pp - 2.0 * p0 + pm) / (dS * dS)
        deltas.append(float(delta))
        gammas.append(float(gamma))

    for i in range(vol0.size):
        vp = vol0.copy()
        vm = vol0.copy()
        vp[i] += dv
        vm[i] = max(vm[i] - dv, 1e-6)
        key_vol = "vol" if "vol" in base_state else "flat_vols"
        pp = _call({key_vol: vp.tolist() if vp.size > 1 else float(vp[0])})
        pm = _call({key_vol: vm.tolist() if vm.size > 1 else float(vm[0])})
        vega = (pp - pm) / (2.0 * dv)
        volga = (pp - 2.0 * p0 + pm) / (dv * dv)
        vegas.append(float(vega))
        volgas.append(float(volga))

        # Vanna: d²P / dS dσ for asset 0 pairing (spot i with vol i)
        if i < spot0.size:
            sp = spot0.copy()
            sp[i] *= 1.0 + ds
            key_spot = "spot" if "spot" in base_state else "spots"
            p_sp_vp = _call(
                {
                    key_spot: sp.tolist() if sp.size > 1 else float(sp[0]),
                    key_vol: vp.tolist() if vp.size > 1 else float(vp[0]),
                }
            )
            p_sp_vm = _call(
                {
                    key_spot: sp.tolist() if sp.size > 1 else float(sp[0]),
                    key_vol: vm.tolist() if vm.size > 1 else float(vm[0]),
                }
            )
            sm = spot0.copy()
            sm[i] *= 1.0 - ds
            p_sm_vp = _call(
                {
                    key_spot: sm.tolist() if sm.size > 1 else float(sm[0]),
                    key_vol: vp.tolist() if vp.size > 1 else float(vp[0]),
                }
            )
            p_sm_vm = _call(
                {
                    key_spot: sm.tolist() if sm.size > 1 else float(sm[0]),
                    key_vol: vm.tolist() if vm.size > 1 else float(vm[0]),
                }
            )
            dS = spot0[i] * ds
            vanna = (p_sp_vp - p_sp_vm - p_sm_vp + p_sm_vm) / (4.0 * dS * dv)
            vannas.append(float(vanna))
        else:
            vannas.append(0.0)

    return {
        "price": p0,
        "delta": deltas,
        "gamma": gammas,
        "vega": vegas,
        "vanna": vannas,
        "volga": volgas,
        "seed": seed,
    }


def barrier_shift_premium_compare(
    price_fn: Callable[..., float],
    base_state: Dict[str, Any],
    H: float,
    dH: float,
    seed: int = 42,
) -> Dict[str, float]:
    """Price at H, H+ΔH, H-ΔH for over-hedge buffer comparison."""
    shifts = barrier_shift(H, dH)
    st = dict(base_state)
    st["seed"] = seed

    def px(barrier: float) -> float:
        s = dict(st)
        s["barrier"] = barrier
        return float(price_fn(**s))

    p0 = px(shifts["H"])
    pup = px(shifts["H_up"])
    pdn = px(shifts["H_down"])
    return {
        "price_base": p0,
        "price_H_up": pup,
        "price_H_down": pdn,
        "premium_reserve_up": pup - p0,
        "premium_reserve_down": pdn - p0,
        **shifts,
    }
