"""Product registry + pricing orchestration with MC engine."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

import numpy as np

from engine.greeks import fd_greeks
from engine.monte_carlo import simulate_paths
from products.base import BaseStructuredProduct, PayoffResult
from products.booster_airbag import BoosterAirbagProduct
from products.fcn import FcnProduct
from products.phoenix import PhoenixProduct
from products.snowball import SnowballProduct

PRODUCT_TYPES = ("snowball", "fcn", "phoenix", "booster", "airbag", "capped_booster")

_REGISTRY: Dict[str, Type[BaseStructuredProduct]] = {
    "snowball": SnowballProduct,
    "fcn": FcnProduct,
    "phoenix": PhoenixProduct,
    "booster": BoosterAirbagProduct,
    "airbag": BoosterAirbagProduct,
    "capped_booster": BoosterAirbagProduct,
}


def build_product(product_type: str, params: Dict[str, Any]) -> BaseStructuredProduct:
    pt = product_type.lower()
    if pt not in _REGISTRY:
        raise ValueError(f"Unknown product_type={product_type}")
    p = dict(params)
    if pt in ("booster", "airbag", "capped_booster"):
        p["structure"] = "capped_booster" if pt == "capped_booster" else ("airbag" if pt == "airbag" else "booster")
    return _REGISTRY[pt](**p)


def _discount_curve(r: float, tenor: float, n_steps: int) -> np.ndarray:
    t = np.linspace(0.0, tenor, n_steps + 1)
    return np.exp(-r * t)


def _resolve_smoothing(greeks_cfg: Dict[str, Any], product: Dict[str, Any]) -> float:
    """spread_smoothing_width; 0 → derive from bump_spot as Δ_max proxy."""
    w = float(
        greeks_cfg.get(
            "spread_smoothing_width",
            greeks_cfg.get("digital_width", product.get("spread_smoothing_width", 0.0)),
        )
        or 0.0
    )
    if w > 0:
        return w
    # dynamic default from bump when desk features requested
    shift = float(
        greeks_cfg.get("barrier_shift_bps", greeks_cfg.get("barrier_shift", 0.0)) or 0.0
    )
    if abs(shift) > 0 or greeks_cfg.get("return_profiles"):
        return float(greeks_cfg.get("bump_spot", 0.01)) * 2.0  # ~2% default soft width
    return 0.0


def _inject_desk_params(
    product: Dict[str, Any],
    greeks_cfg: Dict[str, Any],
    apply_shift: bool,
) -> Dict[str, Any]:
    p = dict(product)
    bps = float(
        greeks_cfg.get(
            "barrier_shift_bps",
            greeks_cfg.get("barrier_shift", p.get("barrier_shift_bps", 0.0)),
        )
        or 0.0
    )
    # legacy: barrier_shift < 1 treated as relative → convert to bps
    if "barrier_shift_bps" not in greeks_cfg and "barrier_shift" in greeks_cfg:
        legacy = float(greeks_cfg.get("barrier_shift") or 0.0)
        if 0 < abs(legacy) < 1:
            bps = abs(legacy) * 10000.0
        elif abs(legacy) >= 1:
            # absolute level bump in % points of spot-rel barrier ≈ treat as bps directly if large
            bps = abs(legacy) if abs(legacy) > 5 else abs(legacy) * 10000.0
    w = _resolve_smoothing(greeks_cfg, product)
    p["barrier_shift_bps"] = bps
    p["spread_smoothing_width"] = w if apply_shift else float(product.get("spread_smoothing_width", 0.0) or 0.0)
    p["apply_desk_shift"] = bool(apply_shift)
    if apply_shift and w <= 0:
        p["spread_smoothing_width"] = _resolve_smoothing({**greeks_cfg, "return_profiles": True}, product)
    return p


def _price_once(
    product_type: str,
    product: Dict[str, Any],
    market: Dict[str, Any],
    sim: Dict[str, Any],
    greeks_cfg: Dict[str, Any],
    want_greeks: bool = False,
) -> Dict[str, Any]:
    n_paths = int(sim.get("n_paths", 20000))
    n_steps = int(sim.get("n_steps", 252))
    seed = int(sim.get("seed", 42))
    bridge = bool(sim.get("bridge", True))
    bgk = bool(sim.get("bgk", False))

    spots = np.atleast_1d(np.asarray(market.get("spots", [100.0]), dtype=float))
    r = float(market.get("r", 0.02))
    q = np.atleast_1d(np.asarray(market.get("q", [0.0]), dtype=float))
    if q.size == 1 and spots.size > 1:
        q = np.full(spots.size, float(q[0]))
    corr = np.asarray(market.get("corr", np.eye(spots.size)), dtype=float)
    vols = np.atleast_1d(np.asarray(market.get("flat_vols", [0.2]), dtype=float))
    if vols.size == 1 and spots.size > 1:
        vols = np.full(spots.size, float(vols[0]))

    prod = build_product(product_type, product)
    tenor = prod.tenor
    dt = tenor / max(n_steps, 1)
    df = _discount_curve(r, tenor, n_steps)

    paths, hit = simulate_paths(
        spots=spots,
        r=r,
        q=q,
        vol_or_locvol=vols,
        corr=corr,
        n_paths=n_paths,
        n_steps=n_steps,
        dt=dt,
        seed=seed,
        bridge=bridge,
        barrier=None,
        bgk_shift=bgk,
    )
    result: PayoffResult = prod.evaluate_payoff(paths, df)

    out: Dict[str, Any] = {
        "price": result.mean_pv,
        "stderr": result.stderr,
        "price_ci_95": [
            result.mean_pv - 1.96 * result.stderr,
            result.mean_pv + 1.96 * result.stderr,
        ],
        "diagnostics": {
            "ko_prob": float(np.mean(result.ko_date >= 0)),
            "ki_prob": float(np.mean(result.ki_flag)),
            "elapsed_ms": 0,
            "barrier_shift_bps": float(product.get("barrier_shift_bps", 0.0) or 0.0),
            "spread_smoothing_width": float(product.get("spread_smoothing_width", 0.0) or 0.0),
            "apply_desk_shift": bool(product.get("apply_desk_shift", False)),
        },
        "meta": {"seed": seed, "n_paths": n_paths, "product_type": product_type},
        "greeks": {},
    }

    if want_greeks:

        def price_fn(spot=None, spots=None, vol=None, flat_vols=None, seed=seed, **kw):
            sp = spots if spots is not None else spot
            vv = flat_vols if flat_vols is not None else vol
            sp = np.atleast_1d(np.asarray(sp if sp is not None else market["spots"], dtype=float))
            vv = np.atleast_1d(np.asarray(vv if vv is not None else vols, dtype=float))
            if vv.size == 1 and sp.size > 1:
                vv = np.full(sp.size, float(vv[0]))
            pths, _ = simulate_paths(
                spots=sp,
                r=r,
                q=q[: sp.size] if q.size >= sp.size else np.full(sp.size, float(q[0])),
                vol_or_locvol=vv,
                corr=corr[: sp.size, : sp.size] if corr.shape[0] >= sp.size else np.eye(sp.size),
                n_paths=min(n_paths, 8000),
                n_steps=n_steps,
                dt=dt,
                seed=int(seed),
                bridge=False,
            )
            return float(prod.evaluate_payoff(pths, df).mean_pv)

        g = fd_greeks(
            price_fn,
            {"spots": spots.tolist(), "flat_vols": vols.tolist()},
            bump={
                "spot": float(greeks_cfg.get("bump_spot", 0.01)),
                "vol": float(greeks_cfg.get("bump_vol", 0.01)),
            },
            seed=seed,
        )
        out["greeks"] = {
            "delta": g["delta"],
            "gamma": g["gamma"],
            "vega": g["vega"],
            "vanna": g["vanna"],
            "volga": g["volga"],
        }
    return out


def _spot_profile(
    product_type: str,
    product: Dict[str, Any],
    market: Dict[str, Any],
    sim: Dict[str, Any],
    spot0: float,
    mults: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """PV / Delta / Gamma vs spot grid under CRN (same seed)."""
    mults = mults or [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20]
    sim_lite = dict(sim)
    sim_lite["n_paths"] = min(int(sim.get("n_paths", 20000)), 6000)
    sim_lite["bridge"] = False
    spots_row: List[float] = []
    pvs: List[float] = []
    for m in mults:
        mk = dict(market)
        sp = float(spot0) * float(m)
        mk["spots"] = [sp] + list(np.atleast_1d(market.get("spots", [spot0]))[1:])
        res = _price_once(product_type, product, mk, sim_lite, {}, want_greeks=False)
        spots_row.append(sp)
        pvs.append(float(res["price"]))

    deltas, gammas = [], []
    for i, sp in enumerate(spots_row):
        if i == 0 or i == len(spots_row) - 1:
            # one-sided
            if i == 0:
                dS = spots_row[1] - spots_row[0]
                deltas.append((pvs[1] - pvs[0]) / max(dS, 1e-12))
                gammas.append(0.0)
            else:
                dS = spots_row[-1] - spots_row[-2]
                deltas.append((pvs[-1] - pvs[-2]) / max(dS, 1e-12))
                gammas.append(0.0)
        else:
            dS = spots_row[i + 1] - spots_row[i - 1]
            deltas.append((pvs[i + 1] - pvs[i - 1]) / max(dS, 1e-12))
            dS2 = 0.5 * (spots_row[i + 1] - spots_row[i - 1])
            gammas.append((pvs[i + 1] - 2 * pvs[i] + pvs[i - 1]) / max(dS2 * dS2, 1e-12))

    return {
        "spot": spots_row,
        "spot_mult": list(mults),
        "pv": pvs,
        "delta": deltas,
        "gamma": gammas,
    }


def price_product(
    product_type: str,
    product: Dict[str, Any],
    market: Dict[str, Any],
    sim: Optional[Dict[str, Any]] = None,
    greeks_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sim = sim or {}
    greeks_cfg = dict(greeks_cfg or {})
    return_profiles = bool(greeks_cfg.get("return_profiles", False))
    bps = float(
        greeks_cfg.get("barrier_shift_bps", greeks_cfg.get("barrier_shift", 0.0)) or 0.0
    )
    # auto dual-profile when shift/smoothing requested
    if bps != 0 or float(greeks_cfg.get("spread_smoothing_width", 0) or 0) > 0:
        return_profiles = True

    raw_prod = _inject_desk_params(product, greeks_cfg, apply_shift=False)
    # raw: no desk shift; optional smoothing only if explicitly on product
    raw_prod["apply_desk_shift"] = False
    raw_prod["barrier_shift_bps"] = 0.0
    raw_prod["spread_smoothing_width"] = 0.0

    out = _price_once(
        product_type,
        raw_prod,
        market,
        sim,
        greeks_cfg,
        want_greeks=bool(greeks_cfg.get("enabled")),
    )

    if return_profiles:
        shifted_prod = _inject_desk_params(product, greeks_cfg, apply_shift=True)
        shifted = _price_once(
            product_type,
            shifted_prod,
            market,
            sim,
            greeks_cfg,
            want_greeks=False,
        )
        spot0 = float(np.atleast_1d(market.get("spots", [100.0]))[0])
        raw_profile = _spot_profile(product_type, raw_prod, market, sim, spot0)
        shifted_profile = _spot_profile(product_type, shifted_prod, market, sim, spot0)
        reserve = float(shifted["price"] - out["price"])
        tenor = float(build_product(product_type, raw_prod).tenor)
        # linear theta release of reserve over life
        n_theta = 12
        theta_path = [
            {"t": tenor * i / n_theta, "reserve_left": reserve * (1.0 - i / n_theta)}
            for i in range(n_theta + 1)
        ]
        out["raw_profile"] = raw_profile
        out["shifted_profile"] = shifted_profile
        out["reserve_metrics"] = {
            "pv_raw": out["price"],
            "pv_shifted": shifted["price"],
            "reserve": reserve,
            "theta_release": theta_path,
        }
        out["diagnostics"]["shifted_price"] = shifted["price"]
        out["diagnostics"]["ko_prob_shifted"] = shifted["diagnostics"].get("ko_prob")
        out["diagnostics"]["ki_prob_shifted"] = shifted["diagnostics"].get("ki_prob")

    return out
