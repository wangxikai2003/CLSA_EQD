"""
Gatheral–Jacquier JW-SVI parametrisation, quasi-arbitrage-free calibration.
Analytic w'(k), w''(k) and density g(k); Dupire LV lives in local_vol.py.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from scipy.optimize import least_squares, minimize

from .schemas import CalibResult, JwParams, RawSviParams

ArrayLike = Union[float, np.ndarray]

# Butterfly penalty weight in calibration objective
_DEFAULT_LAMBDA = 25.0
_G_EPS = 1e-6


def raw_svi_w(k: ArrayLike, a: float, b: float, rho: float, m: float, sigma: float) -> np.ndarray:
    k = np.asarray(k, dtype=float)
    x = k - m
    return a + b * (rho * x + np.sqrt(x * x + sigma * sigma))


def total_variance(k: ArrayLike, raw: RawSviParams) -> np.ndarray:
    return raw_svi_w(k, raw.a, raw.b, raw.rho, raw.m, raw.sigma)


def svi_w_derivatives(k: ArrayLike, raw: RawSviParams) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Analytic raw-SVI total variance and first/second k-derivatives.
    w(k) = a + b (ρ x + √(x²+σ²)), x = k - m
    w' = b (ρ + x/R)
    w'' = b σ² / R³
    where R = √(x²+σ²)
    """
    k = np.asarray(k, dtype=float)
    a, b, rho, m, sigma = raw.a, raw.b, raw.rho, raw.m, raw.sigma
    x = k - m
    R = np.sqrt(x * x + sigma * sigma)
    R = np.maximum(R, 1e-14)
    w = a + b * (rho * x + R)
    wp = b * (rho + x / R)
    wpp = b * (sigma * sigma) / (R ** 3)
    return w, wp, wpp


def implied_vol(k: ArrayLike, t: float, raw: RawSviParams) -> np.ndarray:
    t = max(float(t), 1e-12)
    w = total_variance(k, raw)
    return np.sqrt(np.maximum(w, 1e-12) / t)


def raw_to_jw(raw: RawSviParams, t: float) -> JwParams:
    t = max(float(t), 1e-12)
    a, b, rho, m, sigma = raw.a, raw.b, raw.rho, raw.m, raw.sigma
    w0 = float(raw_svi_w(0.0, a, b, rho, m, sigma))
    w0 = max(w0, 1e-12)
    sqrt_term = math.sqrt(m * m + sigma * sigma)
    dw_dk0 = b * (rho - m / sqrt_term)
    v_t = w0 / t
    psi = dw_dk0 / (2.0 * math.sqrt(w0 * t))
    p = b * (1.0 - rho)
    c = b * (1.0 + rho)
    w_min = a + b * sigma * math.sqrt(max(1.0 - rho * rho, 0.0))
    vtild = max(w_min, 0.0) / t
    return JwParams(v=v_t, psi=psi, p=p, c=c, vtild=vtild)


def jw_to_raw(jw: JwParams, t: float) -> RawSviParams:
    """Invert JW → raw SVI (Gatheral–Jacquier)."""
    t = max(float(t), 1e-12)
    v, psi, p, c, vtild = jw.v, jw.psi, jw.p, jw.c, jw.vtild
    w = max(v * t, 1e-12)
    b = 0.5 * (p + c)
    if b <= 1e-12:
        b = 1e-4
    rho = (c - p) / (p + c) if (p + c) > 1e-12 else 0.0
    rho = float(np.clip(rho, -0.999, 0.999))
    dw = 2.0 * psi * math.sqrt(w * t)
    w_min = max(vtild * t, 1e-12)
    beta = rho - dw / b
    beta = float(np.clip(beta, -0.999, 0.999))
    disc = math.sqrt(max(1.0 - rho * rho, 1e-12))
    sigma = max(0.05, abs(dw) * 0.5 + 0.05)
    R = sigma / math.sqrt(max(1.0 - beta * beta, 1e-8))
    m = beta * R
    sigma = R * math.sqrt(max(1.0 - beta * beta, 1e-8))
    a = w_min - b * sigma * disc
    w_model = float(raw_svi_w(0.0, a, b, rho, m, sigma))
    a += w - w_model
    return RawSviParams(a=float(a), b=float(b), rho=float(rho), m=float(m), sigma=float(max(sigma, 1e-4)))


def density_g(k: ArrayLike, raw: RawSviParams) -> np.ndarray:
    """
    Gatheral butterfly density factor:
    g(k) = (1 - k w'/(2w))^2 - (w'^2)/4 * (1/w + 1/4) + w''/2
    Requires g(k) >= 0 for no butterfly arbitrage.
    """
    k = np.asarray(k, dtype=float)
    w, wp, wpp = svi_w_derivatives(k, raw)
    w_safe = np.maximum(w, 1e-12)
    term1 = (1.0 - 0.5 * k * wp / w_safe) ** 2
    term2 = 0.25 * wp**2 * (1.0 / w_safe + 0.25)
    return term1 - term2 + 0.5 * wpp


# Back-compat alias
_density_g = density_g


def check_butterfly(
    raw: RawSviParams,
    k_grid: Optional[np.ndarray] = None,
    atm_vol: Optional[float] = None,
    t: Optional[float] = None,
) -> Tuple[bool, float]:
    if k_grid is None:
        if atm_vol is not None and t is not None:
            span = 4.0 * float(atm_vol) * math.sqrt(max(float(t), 1e-12))
            span = max(span, 0.5)
            k_grid = np.linspace(-span, span, 201)
        else:
            k_grid = np.linspace(-1.5, 1.5, 301)
    g = density_g(k_grid, raw)
    g_min = float(np.nanmin(g))
    return g_min >= -_G_EPS, g_min


def check_calendar(w_t1: np.ndarray, w_t2: np.ndarray) -> bool:
    """Calendar: later maturity should have higher total variance."""
    return bool(np.all(np.asarray(w_t2) + 1e-8 >= np.asarray(w_t1)))


def check_calendar_slices(
    raw_t1: RawSviParams,
    raw_t2: RawSviParams,
    k_grid: Optional[np.ndarray] = None,
) -> Tuple[bool, float]:
    """Return (ok, min_gap) where gap = w(T2)-w(T1); ok iff gap>=0 everywhere."""
    if k_grid is None:
        k_grid = np.linspace(-1.5, 1.5, 201)
    w1 = total_variance(k_grid, raw_t1)
    w2 = total_variance(k_grid, raw_t2)
    gap = w2 - w1
    min_gap = float(np.nanmin(gap))
    return min_gap >= -1e-8, min_gap


def _arb_penalty(raw: RawSviParams, k_scan: np.ndarray, lam: float) -> float:
    w = total_variance(k_scan, raw)
    if np.any(w <= 0):
        return 1e3
    g = density_g(k_scan, raw)
    neg = np.maximum(0.0, -g)
    return float(lam * np.sum(neg**2))


def _fit_raw_ls(
    k: np.ndarray, w: np.ndarray, weights: Optional[np.ndarray] = None
) -> Optional[RawSviParams]:
    k = np.asarray(k, dtype=float)
    w = np.asarray(w, dtype=float)
    mask = np.isfinite(k) & np.isfinite(w) & (w > 0)
    k, w = k[mask], w[mask]
    if len(k) < 5:
        return None
    wt = np.ones_like(w) if weights is None else np.asarray(weights, dtype=float)[mask]
    i0 = int(np.argmin(np.abs(k)))
    a0 = max(float(w[i0]) * 0.5, 1e-6)

    def resid(p):
        a, b, rho, m, sigma = p
        if b < 0 or sigma <= 0 or abs(rho) >= 1:
            return np.ones_like(w) * 1e3
        model = raw_svi_w(k, a, b, rho, m, sigma)
        if np.any(model <= 0):
            return np.ones_like(w) * 1e3
        return (model - w) * wt

    try:
        res = least_squares(
            resid,
            x0=[a0, 0.1, -0.3, 0.0, 0.1],
            bounds=([-0.5, 1e-6, -0.999, -1.0, 1e-4], [2.0, 5.0, 0.999, 1.0, 2.0]),
            max_nfev=500,
        )
        a, b, rho, m, sigma = map(float, res.x)
        if b <= 0 or sigma <= 0 or abs(rho) >= 1:
            return None
        return RawSviParams(a, b, rho, m, sigma)
    except Exception:
        return None


def calibrate_slice(
    k: Sequence[float],
    iv: Sequence[float],
    t: float,
    method: str = "SLSQP",
    forward: Optional[float] = None,
    prev_raw: Optional[RawSviParams] = None,
    butterfly_lambda: float = _DEFAULT_LAMBDA,
) -> CalibResult:
    """
    Calibrate JW-SVI with Gatheral quasi-AF butterfly penalty.
    Loss = IV-RMSE + λ Σ max(0, -g(k))² on dense k-grid.
    If prev_raw given (earlier tenor), calendar gaps enter the report.
    """
    k_arr = np.asarray(k, dtype=float)
    iv_arr = np.asarray(iv, dtype=float)
    t = max(float(t), 1e-8)
    if forward is not None and np.all(k_arr > 0) and np.nanmedian(k_arr) > 1.0:
        k_arr = np.log(k_arr / float(forward))
    w = (iv_arr**2) * t
    weights = np.exp(-2.0 * k_arr**2)

    raw = _fit_raw_ls(k_arr, w, weights)
    if raw is None:
        v = float(np.nanmedian(iv_arr) ** 2)
        raw = RawSviParams(a=v * t, b=1e-4, rho=0.0, m=0.0, sigma=0.1)

    atm_iv = float(np.nanmedian(iv_arr))
    span = max(4.0 * atm_iv * math.sqrt(t), 0.5)
    k_scan = np.linspace(-span, span, 121)

    def iv_rmse(r: RawSviParams) -> float:
        model_iv = implied_vol(k_arr, t, r)
        return float(np.sqrt(np.mean((model_iv - iv_arr) ** 2)))

    def objective_raw(p: np.ndarray) -> float:
        a, b, rho, m, sigma = map(float, p)
        if b <= 0 or sigma <= 0 or abs(rho) >= 1:
            return 1e3
        r = RawSviParams(a, b, rho, m, sigma)
        if np.any(total_variance(k_scan, r) <= 0):
            return 1e3
        return iv_rmse(r) + _arb_penalty(r, k_scan, butterfly_lambda)

    # Always refine with butterfly penalty (SLSQP or Nelder-Mead style via minimize)
    method_u = method.upper()
    x0 = np.array([raw.a, raw.b, raw.rho, raw.m, raw.sigma], dtype=float)
    bounds = [(-0.5, 2.0), (1e-6, 5.0), (-0.999, 0.999), (-1.5, 1.5), (1e-4, 2.0)]

    if method_u in ("SLSQP", "BFGS", "PENALTY", "LS"):
        try:
            # Stage-2: JW space with penalty mapped through jw_to_raw
            jw0 = raw_to_jw(raw, t)

            def obj_jw(x):
                jw = JwParams(*map(float, x))
                r = jw_to_raw(jw, t)
                if np.any(total_variance(k_scan, r) <= 0):
                    return 1e3
                return iv_rmse(r) + _arb_penalty(r, k_scan, butterfly_lambda)

            jw_bounds = [
                (1e-6, 4.0),
                (-2.0, 2.0),
                (1e-6, 10.0),
                (1e-6, 10.0),
                (1e-6, 4.0),
            ]
            res = minimize(
                obj_jw,
                x0=[jw0.v, jw0.psi, max(jw0.p, 1e-4), max(jw0.c, 1e-4), max(jw0.vtild, 1e-6)],
                method="SLSQP",
                bounds=jw_bounds,
                options={"maxiter": 250, "ftol": 1e-12},
            )
            if res.success or res.fun < objective_raw(x0):
                raw = jw_to_raw(JwParams(*map(float, res.x)), t)
        except Exception:
            pass

        # Stage-3: raw-space SLSQP polish with explicit g>=0 soft penalty
        try:
            res2 = minimize(
                objective_raw,
                x0=[raw.a, raw.b, raw.rho, raw.m, raw.sigma],
                method="SLSQP",
                bounds=bounds,
                options={"maxiter": 200},
            )
            if res2.success or float(res2.fun) < objective_raw(
                np.array([raw.a, raw.b, raw.rho, raw.m, raw.sigma])
            ):
                a, b, rho, m, sigma = map(float, res2.x)
                raw = RawSviParams(a, b, rho, m, sigma)
        except Exception:
            pass

    # Post-process: shrink wing slope b until g>=0 on scan grid (preserve ATM level)
    ok, g_min = check_butterfly(raw, k_scan, atm_vol=atm_iv, t=t)
    if not ok:
        w_atm = float(total_variance(0.0, raw))
        for _ in range(40):
            b_new = raw.b * 0.92
            if b_new < 1e-5:
                break
            a_new = raw.a + (raw.b - b_new) * (
                raw.rho * (-raw.m) + math.sqrt(raw.m * raw.m + raw.sigma * raw.sigma)
            )
            # Keep ATM total variance
            cand = RawSviParams(a=a_new, b=b_new, rho=raw.rho, m=raw.m, sigma=raw.sigma)
            w_model = float(total_variance(0.0, cand))
            cand = RawSviParams(
                a=cand.a + (w_atm - w_model),
                b=cand.b,
                rho=cand.rho,
                m=cand.m,
                sigma=cand.sigma,
            )
            ok2, g2 = check_butterfly(cand, k_scan, atm_vol=atm_iv, t=t)
            raw = cand
            ok, g_min = ok2, g2
            if ok:
                break

    jw = raw_to_jw(raw, t)
    model_iv = implied_vol(k_arr, t, raw)
    err = model_iv - iv_arr
    rmse = float(np.sqrt(np.mean(err**2)))
    mae = float(np.mean(np.abs(err)))
    max_residual = float(np.nanmax(np.abs(err))) if len(err) else 0.0
    ok, g_min = check_butterfly(raw, k_scan, atm_vol=atm_iv, t=t)

    cal_ok = True
    cal_gap = 0.0
    if prev_raw is not None:
        cal_ok, cal_gap = check_calendar_slices(prev_raw, raw, k_scan)

    return CalibResult(
        jw=jw,
        raw=raw,
        rmse=rmse,
        mae=mae,
        butterfly_ok=ok,
        g_min=g_min,
        max_residual=max_residual,
        has_butterfly_arbitrage=not ok,
        has_calendar_arbitrage=not cal_ok,
        calendar_min_gap=cal_gap,
    )


def calibrate_surface(
    slices: List[Dict],
    method: str = "SLSQP",
) -> List[CalibResult]:
    """
    Calibrate multiple tenors in increasing T order with calendar reporting.
    Each slice dict: {ttm, strikes|k, ivs, forward?}
    """
    ordered = sorted(slices, key=lambda s: float(s["ttm"]))
    out: List[CalibResult] = []
    prev: Optional[RawSviParams] = None
    for s in ordered:
        t = float(s["ttm"])
        k = s.get("k", s.get("strikes"))
        iv = s.get("ivs", s.get("iv"))
        fwd = s.get("forward")
        res = calibrate_slice(k, iv, t, method=method, forward=fwd, prev_raw=prev)
        out.append(res)
        prev = res.raw
    return out


# Re-export for backwards compatibility — implementation moved to local_vol
def dupire_local_vol_grid(slices, s_grid, t_grid, spot):
    from .local_vol import dupire_local_vol_grid as _impl

    return _impl(slices, s_grid, t_grid, spot)
