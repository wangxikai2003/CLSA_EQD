"""
Dupire local volatility projection from JW-SVI total-variance slices.
Uses analytic w_k / w_kk and PCHIP monotone spline for ∂w/∂T.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
from scipy.interpolate import PchipInterpolator

from .jwsvi import RawSviParams, svi_w_derivatives, total_variance

_EPS_DEN = 1e-4
_SIG_LO = 0.03
_SIG_HI = 1.50


def _as_raw(raw: Union[RawSviParams, Dict[str, float]]) -> RawSviParams:
    if isinstance(raw, RawSviParams):
        return raw
    return RawSviParams(**raw)


def _sorted_slices(slices: List[Dict]) -> List[Dict]:
    return sorted(slices, key=lambda s: float(s["ttm"]))


def _w_at_slice(k: float, sl: Dict) -> float:
    return float(total_variance(k, _as_raw(sl["raw"])))


def _build_pchip_w_T(k: float, slices: List[Dict]) -> PchipInterpolator:
    """Monotone PCHIP interpolator of w(k, T) vs T for fixed k."""
    ts = np.array([float(s["ttm"]) for s in slices], dtype=float)
    ws = np.array([_w_at_slice(k, s) for s in slices], dtype=float)
    # Enforce weak calendar monotonicity before fitting
    for i in range(1, len(ws)):
        if ws[i] < ws[i - 1]:
            ws[i] = ws[i - 1]
    if len(ts) == 1:
        # Degenerate: flat in T
        ts = np.array([ts[0] * 0.5, ts[0], ts[0] * 1.5 + 1e-6])
        ws = np.array([ws[0] * 0.5, ws[0], ws[0] * 1.5])
    return PchipInterpolator(ts, ws, extrapolate=True)


def total_variance_surface(k: float, t: float, slices: List[Dict]) -> float:
    slices = _sorted_slices(slices)
    pch = _build_pchip_w_T(k, slices)
    return float(max(pch(t), 1e-12))


def dw_dT(k: float, t: float, slices: List[Dict]) -> float:
    """∂w/∂T via PCHIP derivative (no oscillatory cubic)."""
    slices = _sorted_slices(slices)
    pch = _build_pchip_w_T(k, slices)
    d = float(pch.derivative()(t))
    return max(d, 0.0)  # calendar: non-decreasing total var


def dens_denominator(k: float, w: float, wp: float, wpp: float, eps: float = _EPS_DEN) -> float:
    """
    Dupire / Gatheral denominator:
    1 - (k/w) w_k + 1/4 (-1/4 - 1/w + k²/w²) (w_k)² + 1/2 w_kk
    Clamped away from zero when <= eps.
    """
    w = max(w, 1e-12)
    den = (
        1.0
        - (k / w) * wp
        + 0.25 * (-0.25 - 1.0 / w + (k * k) / (w * w)) * (wp * wp)
        + 0.5 * wpp
    )
    if den <= eps:
        return eps
    return float(den)


def local_vol_at(
    S: float,
    t: float,
    slices: List[Dict],
    spot: float,
    eps_den: float = _EPS_DEN,
    sig_lo: float = _SIG_LO,
    sig_hi: float = _SIG_HI,
) -> float:
    """Instantaneous Dupire local vol σ_loc(K=S, T=t) using analytic k-derivatives."""
    slices = _sorted_slices(slices)
    t = max(float(t), 1e-8)
    F = max(float(spot), 1e-12)
    k = math.log(max(S, 1e-12) / F)

    # Pick nearest slice for analytic w_k, w_kk; blend T via PCHIP for w and w_T
    ts = np.array([float(s["ttm"]) for s in slices])
    # Interpolate raw params lightly: use bracketing slices' derivatives blended
    if t <= ts[0]:
        raw = _as_raw(slices[0]["raw"])
        w, wp, wpp = svi_w_derivatives(k, raw)
        w = float(np.asarray(w))
        wp = float(np.asarray(wp))
        wpp = float(np.asarray(wpp))
    elif t >= ts[-1]:
        raw = _as_raw(slices[-1]["raw"])
        w, wp, wpp = svi_w_derivatives(k, raw)
        w = float(np.asarray(w))
        wp = float(np.asarray(wp))
        wpp = float(np.asarray(wpp))
    else:
        i = int(np.searchsorted(ts, t) - 1)
        i = max(0, min(i, len(slices) - 2))
        a = (t - ts[i]) / max(ts[i + 1] - ts[i], 1e-12)
        r1 = _as_raw(slices[i]["raw"])
        r2 = _as_raw(slices[i + 1]["raw"])
        w1, wp1, wpp1 = svi_w_derivatives(k, r1)
        w2, wp2, wpp2 = svi_w_derivatives(k, r2)
        w = float((1 - a) * w1 + a * w2)
        wp = float((1 - a) * wp1 + a * wp2)
        wpp = float((1 - a) * wpp1 + a * wpp2)

    # Prefer PCHIP w(T) for level + time derivative
    pch = _build_pchip_w_T(k, slices)
    w_pch = float(max(pch(t), 1e-12))
    w_t = max(float(pch.derivative()(t)), 0.0)
    w = w_pch

    den = dens_denominator(k, w, wp, wpp, eps=eps_den)
    loc_var = max(w_t / den, 1e-12)
    sig = math.sqrt(loc_var)
    return float(np.clip(sig, sig_lo, sig_hi))


def dupire_local_vol_grid(
    slices: List[Dict],
    s_grid: np.ndarray,
    t_grid: np.ndarray,
    spot: float,
    eps_den: float = _EPS_DEN,
    sig_lo: float = _SIG_LO,
    sig_hi: float = _SIG_HI,
) -> np.ndarray:
    """
    Build local vol σ_loc(S,t) grid.
    slices: [{ttm, raw: {a,b,rho,m,sigma}}, ...]
    Returns shape (len(s_grid), len(t_grid)).
    """
    slices = _sorted_slices(slices)
    s_grid = np.asarray(s_grid, dtype=float)
    t_grid = np.asarray(t_grid, dtype=float)
    out = np.zeros((len(s_grid), len(t_grid)))
    for j, t in enumerate(t_grid):
        for i, S in enumerate(s_grid):
            out[i, j] = local_vol_at(S, t, slices, spot, eps_den, sig_lo, sig_hi)
    return out


def generate_local_vol_surface(
    spot: float,
    strikes: Sequence[float],
    tenors: Sequence[float],
    slices: List[Dict],
    eps_den: float = _EPS_DEN,
    sig_lo: float = _SIG_LO,
    sig_hi: float = _SIG_HI,
) -> Dict[str, Any]:
    """
    Regular 2-D grid for MC bilinear lookup.
    Returns {spot, S_grid, T_grid, k_grid, sigma_loc}.
    """
    S_grid = np.asarray(strikes, dtype=float)
    T_grid = np.asarray(tenors, dtype=float)
    F = max(float(spot), 1e-12)
    k_grid = np.log(np.maximum(S_grid, 1e-12) / F)
    sigma_loc = dupire_local_vol_grid(slices, S_grid, T_grid, spot, eps_den, sig_lo, sig_hi)
    return {
        "spot": float(spot),
        "S_grid": S_grid.tolist(),
        "T_grid": T_grid.tolist(),
        "k_grid": k_grid.tolist(),
        "sigma_loc": sigma_loc.tolist(),
    }
