"""
Single-parameter Solve-For engine for universal exotics (brentq + secant fallback).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

try:
    from scipy.optimize import brentq, minimize_scalar
except ImportError:  # pragma: no cover
    brentq = None  # type: ignore
    minimize_scalar = None  # type: ignore

from products.universal_exotic import UniversalExoticProduct, price_universal

SolveForField = str  # "cpn_rate" | "ko_rebate" | "ki_level" | "put_strike"

DEFAULT_BOUNDS: Dict[str, Tuple[float, float]] = {
    "cpn_rate": (0.0, 0.80),
    "ko_rebate": (0.0, 0.80),
    "ki_level": (0.40, 0.95),
    "put_strike": (0.50, 1.20),
}

SOFT_CLIP: Dict[str, Tuple[float, float]] = {
    "put_strike": (0.50, 1.0),
}


def _price_only(
    product: UniversalExoticProduct,
    market: Dict[str, Any],
    n_paths: int = 8000,
    seed: int = 42,
) -> float:
    out = price_universal(
        product,
        market,
        sim={"n_paths": n_paths, "n_steps": 126, "seed": seed, "bridge": True},
        greeks={"enabled": False, "return_profiles": False, "return_ki_profiles": False},
    )
    return float(out["price"])


def _secant(
    f: Callable[[float], float],
    a: float,
    b: float,
    tol: float = 1e-5,
    maxiter: int = 40,
) -> Tuple[float, Dict[str, Any]]:
    fa, fb = f(a), f(b)
    hist = [(a, fa), (b, fb)]
    x0, x1, f0, f1 = a, b, fa, fb
    for _ in range(maxiter):
        if abs(f1 - f0) < 1e-14:
            break
        # damped secant
        x2 = x1 - 0.7 * f1 * (x1 - x0) / (f1 - f0)
        x2 = min(max(x2, min(a, b)), max(a, b))
        f2 = f(x2)
        hist.append((x2, f2))
        if abs(f2) < tol or abs(x2 - x1) < tol:
            return x2, {"method": "secant", "iterations": hist, "converged": True}
        x0, f0, x1, f1 = x1, f1, x2, f2
    return x1, {"method": "secant", "iterations": hist, "converged": abs(f1) < tol * 10}


def solve_exotic_parameter(
    product_config: Dict[str, Any] | UniversalExoticProduct,
    market_env: Dict[str, Any],
    solve_for: SolveForField,
    target_price: float = 0.99,
    bounds: Optional[Tuple[float, float]] = None,
    n_paths: int = 8000,
    seed: int = 42,
    tol: float = 1e-4,
) -> Dict[str, Any]:
    """
    Solve product parameter so that PV ≈ target_price.

    Returns solution, residual, diagnostics, optional desk-style warning.
    """
    if isinstance(product_config, UniversalExoticProduct):
        base = product_config
    else:
        base = UniversalExoticProduct.model_validate(product_config)

    field = solve_for
    if field == "ko_rebate":
        # treat as accrued coupon solve
        pass
    if field not in DEFAULT_BOUNDS and field not in ("cpn_rate", "ko_rebate", "ki_level", "put_strike"):
        return {
            "ok": False,
            "solve_for": field,
            "solution": None,
            "detail": f"Unsupported solve_for field: {field}",
        }

    lo, hi = bounds if bounds else DEFAULT_BOUNDS.get(field, (0.01, 1.0))
    warnings: list[str] = []

    # soft clip advisory for put_strike
    if field == "put_strike" and (lo < 0.5 or hi > 1.0):
        warnings.append(
            f"put_strike bounds [{lo}, {hi}] extend outside desk band [0.5, 1.0]; "
            "result may be truncated for reporting."
        )

    def objective(x: float) -> float:
        prod = base.with_overrides(**{field: float(x)})
        # keep cpn_rate in sync when solving ko_rebate
        if field == "ko_rebate":
            prod = prod.with_overrides(cpn_rate=float(x), ko_rebate=float(x))
        if field == "cpn_rate":
            prod = prod.with_overrides(cpn_rate=float(x), ko_rebate=float(x))
        px = _price_only(prod, market_env, n_paths=n_paths, seed=seed)
        return px - float(target_price)

    fa = objective(lo)
    fb = objective(hi)
    diag: Dict[str, Any] = {
        "f_lo": fa,
        "f_hi": fb,
        "bounds": [lo, hi],
        "target_price": target_price,
    }

    if not (np.isfinite(fa) and np.isfinite(fb)):
        return {
            "ok": False,
            "solve_for": field,
            "solution": None,
            "residual": None,
            "diagnostics": diag,
            "detail": f"Objective not finite at bounds: f({lo})={fa}, f({hi})={fb}",
            "warnings": warnings,
        }

    same_sign = fa * fb > 0
    if same_sign:
        detail = (
            "No proper solution can be found. Original error: "
            f"f(a) and f(b) must have different signs, f({lo})={fa:.6g}, f({hi})={fb:.6g}"
        )
        # still attempt best-effort minimize |f|
        best_x = lo if abs(fa) < abs(fb) else hi
        method = "none"
        if minimize_scalar is not None:
            try:
                res = minimize_scalar(lambda x: abs(objective(x)), bounds=(lo, hi), method="bounded")
                best_x = float(res.x)
                method = "minimize_scalar_abs"
            except Exception:
                pass
        # soft truncate put_strike into [0.5,1]
        reported = best_x
        if field in SOFT_CLIP:
            c_lo, c_hi = SOFT_CLIP[field]
            if reported < c_lo or reported > c_hi:
                warnings.append(
                    f"{field}={reported:.6g} outside [{c_lo}, {c_hi}]; reporting clipped value."
                )
                reported = float(np.clip(reported, c_lo, c_hi))
        return {
            "ok": False,
            "solve_for": field,
            "solution": reported,
            "residual": float(objective(best_x)),
            "diagnostics": {**diag, "method": method, "fallback": True},
            "detail": detail,
            "warnings": warnings,
        }

    sol = None
    method = "brentq"
    iterations: list = []
    if brentq is not None:
        try:
            sol = float(
                brentq(objective, lo, hi, xtol=tol, rtol=tol, maxiter=64)
            )
        except ValueError as e:
            sol, meta = _secant(objective, lo, hi, tol=tol)
            method = meta["method"]
            iterations = meta.get("iterations", [])
            diag["brentq_error"] = str(e)
    else:
        sol, meta = _secant(objective, lo, hi, tol=tol)
        method = meta["method"]
        iterations = meta.get("iterations", [])

    residual = float(objective(sol))
    reported = sol
    if field in SOFT_CLIP:
        c_lo, c_hi = SOFT_CLIP[field]
        if sol < c_lo or sol > c_hi:
            warnings.append(
                f"{field}={sol:.6g} outside desk band [{c_lo}, {c_hi}]; clipped for display."
            )
            reported = float(np.clip(sol, c_lo, c_hi))

    return {
        "ok": abs(residual) < max(tol * 10, 1e-3),
        "solve_for": field,
        "solution": reported,
        "raw_solution": sol,
        "residual": residual,
        "price_at_solution": float(target_price + residual),
        "diagnostics": {
            **diag,
            "method": method,
            "iterations": iterations,
            "tol": tol,
        },
        "detail": None,
        "warnings": warnings,
    }
