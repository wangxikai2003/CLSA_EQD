"""
Conditional forward revaluation + multi-slice Before/After KI Greeks
and desk-level Exotic Risk Summary (cross-gamma, corr-rho, vanna, …).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from engine.conditional_pricing import price_exotic_conditional
from engine.vectorized_engine import compute_greeks_term_structure as _compute_gts
from products.universal_exotic import UniversalExoticProduct, price_universal


def price_exotic_conditional_universal(
    S_t: float,
    t_current: float,
    history_ki: bool,
    product: UniversalExoticProduct | Dict[str, Any],
    market_env: Dict[str, Any],
    mc_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    if isinstance(product, dict):
        prod = UniversalExoticProduct.model_validate(product)
    else:
        prod = product
    params = prod.to_legacy_product_dict()
    params["S0"] = float(np.atleast_1d(market_env.get("spots", [100.0]))[0])
    params["r"] = float(market_env.get("r", 0.02)) + prod.funding.funding_spread * prod.funding.funding_scale
    params["vol"] = float(np.atleast_1d(market_env.get("flat_vols", [0.2]))[0])
    params["q"] = float(np.atleast_1d(market_env.get("q", [0.0]))[0])
    params["ki_monitoring_type"] = prod.ki.ki_monitoring_type
    params["ko_monitoring_type"] = (
        "DAILY" if prod.ko.ko_monitoring_type == "AMERICAN" else "EUROPEAN"
    )
    return price_exotic_conditional(
        S_t=S_t,
        t_current=t_current,
        history_ki=history_ki,
        product_params=params,
        mc_params=mc_params,
        product_type=prod.inferred_catalog_type(),
    )


def _fd_cross_gamma(
    product: UniversalExoticProduct,
    market: Dict[str, Any],
    bump: float = 0.01,
    n_paths: int = 6000,
    seed: int = 42,
) -> List[List[float]]:
    n = product.n_assets()
    spots = list(np.atleast_1d(market.get("spots", [100.0] * n)).astype(float))
    while len(spots) < n:
        spots.append(spots[-1])
    spots = spots[:n]
    mat = [[0.0] * n for _ in range(n)]

    def px(sp: List[float]) -> float:
        m = dict(market)
        m["spots"] = sp
        out = price_universal(
            product,
            m,
            sim={"n_paths": n_paths, "n_steps": 84, "seed": seed, "bridge": True},
            greeks={"enabled": False},
        )
        return float(out["price"])

    p0 = px(spots)
    for i in range(n):
        for j in range(i, n):
            sp_ij = spots.copy()
            sp_i = spots.copy()
            sp_j = spots.copy()
            sp_ij[i] *= 1.0 + bump
            sp_ij[j] *= 1.0 + bump
            sp_i[i] *= 1.0 + bump
            sp_j[j] *= 1.0 + bump
            p_ij = px(sp_ij)
            p_i = px(sp_i)
            p_j = px(sp_j)
            dSi = spots[i] * bump
            dSj = spots[j] * bump
            g = (p_ij - p_i - p_j + p0) / max(dSi * dSj, 1e-12)
            mat[i][j] = g
            mat[j][i] = g
    return mat


def _corr_rho(
    product: UniversalExoticProduct,
    market: Dict[str, Any],
    bump: float = 0.01,
    n_paths: int = 6000,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    n = product.n_assets()
    if n < 2:
        return []
    corr = np.array(product.effective_corr(), dtype=float)
    out = []

    def px(c: np.ndarray) -> float:
        m = dict(market)
        m["corr"] = c.tolist()
        # temporarily override product corr
        p2 = product.model_copy(deep=True)
        p2.asset.correlation_matrix = c.tolist()
        return float(
            price_universal(
                p2,
                m,
                sim={"n_paths": n_paths, "n_steps": 84, "seed": seed, "bridge": True},
                greeks={"enabled": False},
            )["price"]
        )

    for i in range(n):
        for j in range(i + 1, n):
            cp = corr.copy()
            cm = corr.copy()
            cp[i, j] = cp[j, i] = min(corr[i, j] + bump, 0.999)
            cm[i, j] = cm[j, i] = max(corr[i, j] - bump, -0.999)
            sens = (px(cp) - px(cm)) / (2.0 * bump)
            out.append({"i": i, "j": j, "rho": float(corr[i, j]), "corr_rho": float(sens)})
    return out


def _vanna_spot0(
    product: UniversalExoticProduct,
    market: Dict[str, Any],
    bump_s: float = 0.01,
    bump_v: float = 0.01,
    n_paths: int = 5000,
    seed: int = 42,
) -> float:
    spots = list(np.atleast_1d(market.get("spots", [100.0])).astype(float))
    vols = list(np.atleast_1d(market.get("flat_vols", [0.2])).astype(float))

    def px(sp, vol):
        m = dict(market)
        m["spots"] = sp
        m["flat_vols"] = vol
        return float(
            price_universal(
                product,
                m,
                sim={"n_paths": n_paths, "n_steps": 84, "seed": seed, "bridge": True},
                greeks={"enabled": False},
            )["price"]
        )

    sp_p = [s * (1 + bump_s) for s in spots]
    sp_m = [s * (1 - bump_s) for s in spots]
    vol_p = [v + bump_v for v in vols]
    vol_m = [max(v - bump_v, 1e-4) for v in vols]
    # mixed derivative
    return float(
        (px(sp_p, vol_p) - px(sp_p, vol_m) - px(sp_m, vol_p) + px(sp_m, vol_m))
        / (4.0 * spots[0] * bump_s * bump_v)
    )


def compute_exotic_risk_summary(
    product: UniversalExoticProduct | Dict[str, Any],
    market: Dict[str, Any],
    n_paths: int = 12000,
    seed: int = 42,
) -> Dict[str, Any]:
    if isinstance(product, dict):
        prod = UniversalExoticProduct.model_validate(product)
    else:
        prod = product

    priced = price_universal(
        prod,
        market,
        sim={"n_paths": n_paths, "n_steps": 252, "seed": seed, "bridge": True},
        greeks={"enabled": True, "return_ki_profiles": False},
    )
    tags = (priced.get("diagnostics") or {})
    # Expected duration proxy from KO prob and tenor
    T = prod.tenor
    ko_prob = float(tags.get("ko_prob", 0.0) or 0.0)
    ki_prob = float(tags.get("ki_prob", 0.0) or 0.0)
    # crude: if uniform KO intensity λ with P(KO)=1-e^{-λT}
    if ko_prob >= 1.0 - 1e-9:
        exp_dur = T * 0.35
    elif ko_prob <= 1e-9:
        exp_dur = T
    else:
        lam = -np.log(max(1.0 - ko_prob, 1e-12)) / max(T, 1e-9)
        exp_dur = float((1.0 - np.exp(-lam * T)) / max(lam, 1e-12))

    # Marginal KO schedule (equal weight proxy over obs)
    opy = 12 if prod.obs_freq_token() == "monthly" else (252 if prod.obs_freq_token() == "daily" else 1)
    n_obs = max(int(round(T * opy)), 1)
    # geometric survival → marginals
    surv = 1.0
    marginal = []
    if ko_prob > 0:
        p_surv_T = 1.0 - ko_prob
        per = p_surv_T ** (1.0 / n_obs) if n_obs > 0 else 1.0
        for k in range(n_obs):
            m = surv * (1.0 - per)
            marginal.append({"obs": k + 1, "t": (k + 1) / opy, "marginal_ko": float(m)})
            surv *= per
    else:
        marginal = [{"obs": k + 1, "t": (k + 1) / opy, "marginal_ko": 0.0} for k in range(n_obs)]

    cum = []
    c = 0.0
    for row in marginal:
        c += row["marginal_ko"]
        cum.append({"obs": row["obs"], "t": row["t"], "cumulative_ko": min(c, 1.0)})

    # Bond floor
    r = float(market.get("r", 0.02)) + prod.funding.funding_spread * prod.funding.funding_scale
    bond_floor = float(np.exp(-r * T))
    # implied yield of note price vs par
    note_px = float(priced["price"])
    if note_px > 1e-8:
        ytm = float(-np.log(note_px) / max(T, 1e-9))
    else:
        ytm = float("nan")

    # Gap risk: overnight 1-vol move through KI
    S0 = float(np.atleast_1d(market.get("spots", [100.0]))[0])
    vol = float(np.atleast_1d(market.get("flat_vols", [0.2]))[0])
    ki = prod.ki.ki_level * S0
    gap_move = vol / np.sqrt(252.0) * S0
    # approx unprotected gamma near barrier × gap^2 / 2
    greeks = priced.get("greeks") or {}
    gamma0 = float(np.atleast_1d(greeks.get("gamma", [0.0]))[0]) if greeks else 0.0
    gap_risk = 0.5 * abs(gamma0) * (gap_move ** 2)

    cross_g = _fd_cross_gamma(prod, market, n_paths=max(n_paths // 2, 3000), seed=seed)
    corr_rhos = _corr_rho(prod, market, n_paths=max(n_paths // 2, 3000), seed=seed)
    vanna = _vanna_spot0(prod, market, n_paths=max(n_paths // 2, 3000), seed=seed)

    return {
        "price": note_px,
        "stderr": priced.get("stderr"),
        "greeks": greeks,
        "cross_gamma": cross_g,
        "correlation_rho": corr_rhos,
        "vanna": vanna,
        "expected_duration": exp_dur,
        "event_probabilities": {
            "ko_prob": ko_prob,
            "ki_prob": ki_prob,
            "marginal_ko": marginal,
            "cumulative_ko": cum,
            "final_ki_rate": ki_prob,
        },
        "gap_risk": float(gap_risk),
        "zero_coupon_bond_floor": {
            "pv": bond_floor,
            "implied_yield": ytm,
            "funding_r": r,
        },
        "meta": {
            "product_name": prod.name,
            "catalog_type": prod.inferred_catalog_type(),
            "n_assets": prod.n_assets(),
            "tenor": T,
            "ki_level": prod.ki.ki_level,
            "ko_level": prod.ko_level0(),
        },
        "diagnostics": tags,
    }


def compute_greeks_term_structure_universal(
    product: UniversalExoticProduct | Dict[str, Any],
    market: Dict[str, Any],
    time_slices: Optional[Sequence[float]] = None,
    spot_range: Optional[Tuple[float, float]] = None,
    spot_steps: int = 41,
    include_greeks: Optional[Sequence[str]] = None,
    n_paths: int = 5000,
    seed: int = 42,
    barrier_shift_bps: float = 0.0,
    spread_smoothing_width: float = 0.0,
    include_risk_summary: bool = False,
) -> Dict[str, Any]:
    if isinstance(product, dict):
        prod = UniversalExoticProduct.model_validate(product)
    else:
        prod = product

    legacy = prod.to_legacy_product_dict()
    ki_mon = prod.ki.ki_monitoring_type
    ko_mon = "DAILY" if prod.ko.ko_monitoring_type == "AMERICAN" else "EUROPEAN"
    include = list(include_greeks or ["pv", "delta", "gamma", "vanna"])

    gts = _compute_gts(
        product_type=prod.inferred_catalog_type(),
        product=legacy,
        market=market,
        time_slices=time_slices,
        spot_range=spot_range,
        spot_steps=spot_steps,
        ki_monitoring_type=ki_mon,
        ko_monitoring_type=ko_mon,
        include_greeks=[g for g in include if g != "vanna"],
        n_paths=n_paths,
        seed=seed,
        barrier_shift_bps=barrier_shift_bps,
        spread_smoothing_width=spread_smoothing_width,
    )

    # Attach crude vanna per slice via Δδ/Δσ on sparse subsample if requested
    if "vanna" in include:
        S0 = float(gts["S0"])
        vol0 = float(np.atleast_1d(market.get("flat_vols", [0.2]))[0])
        for sl in gts["slices"]:
            for regime in ("before_ki", "after_ki"):
                curve = sl[regime]
                spots = np.array(curve.get("valid_spots") or [], dtype=float)
                deltas = np.array(curve.get("delta") or [], dtype=float)
                if len(spots) < 3 or len(deltas) != len(spots):
                    curve["vanna"] = []
                    continue
                # finite-diff proxy: bump vol in a second cheap pass is expensive;
                # use delta curvature vs moneyness × vol as desk heuristic placeholder
                # replaced by scaled gamma * 0 for stability — fill zeros then overlay mid
                vannas = np.zeros_like(deltas)
                # central: reuse gamma * (vol sensitivity heuristic)
                gammas = np.array(curve.get("gamma") or np.zeros_like(deltas), dtype=float)
                vannas = gammas * (-0.5 * vol0)  # rough Black-ish link
                curve["vanna"] = vannas.tolist()

    # Optional raw vs shifted pair
    if barrier_shift_bps > 0:
        raw = _compute_gts(
            product_type=prod.inferred_catalog_type(),
            product=legacy,
            market=market,
            time_slices=time_slices,
            spot_range=spot_range,
            spot_steps=min(spot_steps, 21),
            ki_monitoring_type=ki_mon,
            ko_monitoring_type=ko_mon,
            include_greeks=["pv", "delta"],
            n_paths=max(n_paths // 2, 2000),
            seed=seed,
            barrier_shift_bps=0.0,
            spread_smoothing_width=spread_smoothing_width,
        )
        gts["raw_compare"] = raw

    if include_risk_summary:
        gts["risk_summary"] = compute_exotic_risk_summary(
            prod, market, n_paths=max(n_paths, 8000), seed=seed
        )

    gts["product_name"] = prod.name
    gts["catalog_type"] = prod.inferred_catalog_type()
    return gts
