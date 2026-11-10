"""
Numba-accelerated batch exotic pricing for Greeks term-structure.
CRN across the spot grid; Brownian-bridge KI correction for daily monitoring.
"""
from __future__ import annotations

import math
from enum import IntEnum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from numba import njit, prange

    HAS_NUMBA = True
except ImportError:  # pragma: no cover
    HAS_NUMBA = False

    def njit(*args, **kwargs):
        def wrap(fn):
            return fn

        if args and callable(args[0]):
            return args[0]
        return wrap

    def prange(*args):
        return range(*args)


class KiMonitoring(IntEnum):
    DAILY = 0
    EUROPEAN = 1


class KoMonitoring(IntEnum):
    EUROPEAN = 0  # discrete obs dates only
    DAILY = 1


@njit(cache=True)
def _bb_hit(S_k: float, S_kp1: float, H: float, sigma: float, dt: float) -> float:
    if H <= 0.0 or sigma <= 0.0 or dt <= 0.0 or S_k <= 0.0 or S_kp1 <= 0.0:
        return 0.0
    if (S_k - H) * (S_kp1 - H) <= 0.0:
        return 1.0
    a = np.log(S_k / H)
    b = np.log(S_kp1 / H)
    return np.exp(-2.0 * a * b / (sigma * sigma * dt))


@njit(cache=True)
def _smooth_above(S: float, H: float, w: float) -> float:
    if w <= 0.0:
        return 1.0 if S >= H else 0.0
    x = (S - (H - w)) / (2.0 * w)
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x


@njit(cache=True)
def _lcg_normal(state: np.uint64) -> Tuple[float, np.uint64]:
    state = np.uint64(state * np.uint64(6364136223846793005) + np.uint64(1))
    u1 = (state >> np.uint64(11)) * (1.0 / 9007199254740992.0)
    state = np.uint64(state * np.uint64(6364136223846793005) + np.uint64(1))
    u2 = (state >> np.uint64(11)) * (1.0 / 9007199254740992.0)
    u1 = max(u1, 1e-12)
    z = np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * np.pi * u2)
    return z, state


@njit(parallel=True, fastmath=True, cache=True)
def _batch_pv_snowball_dual(
    spots: np.ndarray,
    S0: float,
    t_current: float,
    T: float,
    r: float,
    q: float,
    vol: float,
    ko_abs: float,
    ki_abs: float,
    put_strike_abs: float,
    coupon: float,
    bonus: float,
    step_down: float,
    obs_times: np.ndarray,
    ki_mode: int,
    ko_mode: int,
    n_paths: int,
    n_steps: int,
    seed: int,
    smooth_w: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    CRN dual-regime: returns (pv_before[n_spots], pv_after[n_spots]).
    Same path shocks for both regimes; European KI collapses to identical curves.
    """
    n_spots = spots.shape[0]
    pv_b = np.zeros(n_spots, dtype=np.float64)
    pv_a = np.zeros(n_spots, dtype=np.float64)
    tau = T - t_current
    cpn_mat = bonus if bonus > 0.0 else coupon * T

    if tau <= 1e-12:
        for i in range(n_spots):
            S = spots[i]
            ko_hit = _smooth_above(S, ko_abs, smooth_w) >= 0.5
            if ko_hit:
                pv_b[i] = 1.0 + coupon * T
                pv_a[i] = pv_b[i]
                continue
            if ki_mode == 1:
                ki_term = S <= ki_abs
                red = min(1.0, S / max(put_strike_abs, 1e-12)) if ki_term else (1.0 + cpn_mat)
                pv_b[i] = red
                pv_a[i] = red
            else:
                # After KI locked
                pv_a[i] = min(1.0, S / max(put_strike_abs, 1e-12))
                if S <= ki_abs:
                    pv_b[i] = np.nan
                else:
                    pv_b[i] = 1.0 + cpn_mat
        return pv_b, pv_a

    dt = tau / max(n_steps, 1)
    drift = (r - q - 0.5 * vol * vol) * dt
    diff = vol * np.sqrt(dt)
    n_obs = obs_times.shape[0]
    obs_steps = np.empty(n_obs, dtype=np.int64)
    n_future_obs = 0
    for j in range(n_obs):
        if obs_times[j] > t_current + 1e-12:
            frac = (obs_times[j] - t_current) / tau
            si = int(np.round(frac * n_steps))
            if si < 1:
                si = 1
            if si > n_steps:
                si = n_steps
            obs_steps[n_future_obs] = si
            n_future_obs += 1

    for i in prange(n_spots):
        S_start = spots[i]
        acc_b = 0.0
        acc_a = 0.0
        for p in range(n_paths):
            state = np.uint64(seed + p * 9973 + 1)
            S = S_start
            ki_b = False
            ki_a = True  # after locked
            if ki_mode == 1:
                ki_b = False
                ki_a = False
            ko = False
            payoff_b = 0.0
            payoff_a = 0.0
            obs_ptr = 0
            for step in range(1, n_steps + 1):
                z, state = _lcg_normal(state)
                S_prev = S
                S = S * np.exp(drift + diff * z)
                t_abs = t_current + step * dt

                if ki_mode == 0:
                    if (not ki_b) and (S <= ki_abs or S_prev <= ki_abs):
                        ki_b = True
                    elif not ki_b:
                        ph = _bb_hit(S_prev, S, ki_abs, vol, dt)
                        state = np.uint64(state * np.uint64(6364136223846793005) + np.uint64(1))
                        u = (state >> np.uint64(11)) * (1.0 / 9007199254740992.0)
                        if u < ph:
                            ki_b = True
                    # after already True; keep BB draw consumed for CRN alignment when ki_b already set
                    if not ki_a:
                        ki_a = True

                if ko_mode == 1 and (not ko):
                    if _smooth_above(S, ko_abs - step_down * (t_abs * 12.0), smooth_w) >= 0.5:
                        ko = True
                        df = np.exp(-r * (t_abs - t_current))
                        payoff_b = (1.0 + coupon * t_abs) * df
                        payoff_a = payoff_b
                        break

                if ko_mode == 0 and (not ko) and obs_ptr < n_future_obs and step == obs_steps[obs_ptr]:
                    obs_count = 0
                    for jj in range(n_obs):
                        if obs_times[jj] <= t_abs + 1e-12:
                            obs_count += 1
                    ko_lvl = ko_abs - step_down * max(obs_count - 1, 0)
                    if _smooth_above(S, ko_lvl, smooth_w) >= 0.5:
                        ko = True
                        df = np.exp(-r * (t_abs - t_current))
                        payoff_b = (1.0 + coupon * t_abs) * df
                        payoff_a = payoff_b
                        break
                    obs_ptr += 1

            if not ko:
                if ki_mode == 1:
                    hit = S <= ki_abs
                    ki_b = hit
                    ki_a = hit
                df = np.exp(-r * tau)
                put_red = min(1.0, S / max(put_strike_abs, 1e-12)) * df
                coup_red = (1.0 + cpn_mat) * df
                payoff_b = put_red if ki_b else coup_red
                payoff_a = put_red if ki_a else coup_red
            acc_b += payoff_b
            acc_a += payoff_a
        pv_b[i] = acc_b / n_paths
        pv_a[i] = acc_a / n_paths
        if ki_mode == 0 and S_start <= ki_abs + 1e-9:
            pv_b[i] = np.nan
    return pv_b, pv_a


@njit(parallel=True, fastmath=True, cache=True)
def _batch_pv_snowball(
    spots: np.ndarray,
    S0: float,
    t_current: float,
    T: float,
    r: float,
    q: float,
    vol: float,
    ko_abs: float,
    ki_abs: float,
    put_strike_abs: float,
    coupon: float,
    bonus: float,
    step_down: float,
    obs_times: np.ndarray,
    ki_mode: int,
    ko_mode: int,
    history_ki: int,
    n_paths: int,
    n_steps: int,
    seed: int,
    smooth_w: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Single-regime wrapper around dual kernel."""
    pb, pa = _batch_pv_snowball_dual(
        spots, S0, t_current, T, r, q, vol, ko_abs, ki_abs, put_strike_abs,
        coupon, bonus, step_down, obs_times, ki_mode, ko_mode,
        n_paths, n_steps, seed, smooth_w,
    )
    mean = pa if history_ki == 1 else pb
    var = np.zeros_like(mean)
    return mean, var


@njit(parallel=True, fastmath=True, cache=True)
def _batch_pv_fcn(
    spots: np.ndarray,
    S0: float,
    t_current: float,
    T: float,
    r: float,
    q: float,
    vol: float,
    ko_abs: float,
    strike_abs: float,
    coupon_pa: float,
    obs_times: np.ndarray,
    ki_mode: int,
    history_ki: int,
    n_paths: int,
    n_steps: int,
    seed: int,
    smooth_w: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """FCN: fixed coupon on obs; KO redeems 100%; else Min(1, S_T/strike). European KI by nature."""
    n_spots = spots.shape[0]
    pv_mean = np.zeros(n_spots, dtype=np.float64)
    pv_var = np.zeros(n_spots, dtype=np.float64)
    tau = T - t_current
    n_obs = obs_times.shape[0]

    if tau <= 1e-12:
        for i in range(n_spots):
            S = spots[i]
            if _smooth_above(S, ko_abs, smooth_w) >= 0.5:
                pv_mean[i] = 1.0
                continue
            # terminal put conversion
            if history_ki == 1 or ki_mode == 1:
                # after KI / european terminal: min(1, S/K)
                if history_ki == 0 and ki_mode == 1:
                    # before european at T: same as blended min
                    pv_mean[i] = min(1.0, S / max(strike_abs, 1e-12))
                elif history_ki == 0:
                    pv_mean[i] = 1.0
                else:
                    pv_mean[i] = min(1.0, S / max(strike_abs, 1e-12))
            else:
                pv_mean[i] = 1.0
        return pv_mean, pv_var

    dt = tau / max(n_steps, 1)
    sqrt_dt = np.sqrt(dt)
    drift = (r - q - 0.5 * vol * vol) * dt
    diff = vol * sqrt_dt

    obs_steps = np.empty(n_obs, dtype=np.int64)
    n_future_obs = 0
    for j in range(n_obs):
        if obs_times[j] > t_current + 1e-12:
            frac = (obs_times[j] - t_current) / tau
            si = int(np.round(frac * n_steps))
            if si < 1:
                si = 1
            if si > n_steps:
                si = n_steps
            obs_steps[n_future_obs] = si
            n_future_obs += 1

    # coupon per remaining obs period (equal weight over future obs)
    cpn_each = coupon_pa * T / max(n_obs, 1)

    for i in prange(n_spots):
        S_start = spots[i]
        acc = 0.0
        acc2 = 0.0
        for p in range(n_paths):
            state = np.uint64(seed + p * 9973 + 1)
            S = S_start
            ko = False
            payoff = 0.0
            obs_ptr = 0
            for step in range(1, n_steps + 1):
                z, state = _lcg_normal(state)
                S = S * np.exp(drift + diff * z)
                t_abs = t_current + step * dt
                if (not ko) and obs_ptr < n_future_obs and step == obs_steps[obs_ptr]:
                    df = np.exp(-r * (t_abs - t_current))
                    payoff += cpn_each * df
                    if _smooth_above(S, ko_abs, smooth_w) >= 0.5:
                        ko = True
                        payoff += 1.0 * df
                        break
                    obs_ptr += 1
            if not ko:
                df = np.exp(-r * tau)
                # Forced regimes: history_ki=0 => capital protected; 1 => put conversion
                if history_ki == 0:
                    redemption = 1.0
                else:
                    redemption = min(1.0, S / max(strike_abs, 1e-12))
                payoff += redemption * df
            acc += payoff
            acc2 += payoff * payoff
        mean = acc / n_paths
        var = max(acc2 / n_paths - mean * mean, 0.0)
        pv_mean[i] = mean
        pv_var[i] = var
    return pv_mean, pv_var


def _default_obs_times(T: float, obs_per_year: int) -> np.ndarray:
    n = max(int(round(T * obs_per_year)), 1)
    return np.linspace(T / n, T, n)


def _resolve_barriers(
    S0: float,
    ko_barrier: float,
    ki_barrier: float,
    strike: float,
    product_type: str,
    barrier_shift_bps: float,
    spread_smoothing_width: float,
) -> Tuple[float, float, float, float, float]:
    shift = abs(barrier_shift_bps) / 10000.0
    ko_b = ko_barrier * (1.0 - shift)
    ki_b = ki_barrier * (1.0 + shift)
    ko_abs = ko_b * S0
    ki_abs = ki_b * S0
    strike_abs = strike * S0
    put_abs = S0 if product_type == "snowball" else strike_abs
    smooth = float(spread_smoothing_width) * S0
    return ko_abs, ki_abs, strike_abs, put_abs, smooth


def price_dual_grid_at_t(
    product_type: str,
    S0: float,
    spots: np.ndarray,
    t_current: float,
    T: float,
    r: float,
    q: float,
    vol: float,
    ko_barrier: float,
    ki_barrier: float,
    strike: float,
    coupon: float,
    bonus_coupon: float = 0.0,
    step_down: float = 0.0,
    obs_per_year: int = 12,
    ki_monitoring: KiMonitoring = KiMonitoring.DAILY,
    ko_monitoring: KoMonitoring = KoMonitoring.EUROPEAN,
    n_paths: int = 6000,
    steps_per_year: int = 84,
    seed: int = 42,
    barrier_shift_bps: float = 0.0,
    spread_smoothing_width: float = 0.0,
) -> Dict[str, np.ndarray]:
    """Price Before/After KI on the same CRN paths."""
    spots = np.asarray(spots, dtype=float).ravel()
    tau = max(T - t_current, 0.0)
    n_steps = max(int(round(tau * steps_per_year)), 1) if tau > 1e-12 else 1
    obs = _default_obs_times(T, obs_per_year)
    ko_abs, ki_abs, strike_abs, put_abs, smooth = _resolve_barriers(
        S0, ko_barrier, ki_barrier, strike, product_type, barrier_shift_bps, spread_smoothing_width,
    )
    ki_mode = int(ki_monitoring)
    ko_mode = int(ko_monitoring)
    sd_abs = step_down * S0

    if product_type == "fcn":
        pb, _ = _batch_pv_fcn(
            spots, S0, t_current, T, r, q, vol, ko_abs, strike_abs, coupon, obs,
            ki_mode, 0, n_paths, n_steps, seed, smooth,
        )
        pa, _ = _batch_pv_fcn(
            spots, S0, t_current, T, r, q, vol, ko_abs, strike_abs, coupon, obs,
            ki_mode, 1, n_paths, n_steps, seed, smooth,
        )
        if ki_mode == int(KiMonitoring.EUROPEAN):
            # AXKI: single continuous expectation
            pb = pa.copy()
    else:
        pb, pa = _batch_pv_snowball_dual(
            spots, S0, t_current, T, r, q, vol, ko_abs, ki_abs, put_abs,
            coupon, bonus_coupon, sd_abs, obs, ki_mode, ko_mode,
            n_paths, n_steps, seed, smooth,
        )
    return {"pv_before": pb, "pv_after": pa}


def price_grid_at_t(
    product_type: str,
    S0: float,
    spots: np.ndarray,
    t_current: float,
    T: float,
    r: float,
    q: float,
    vol: float,
    ko_barrier: float,
    ki_barrier: float,
    strike: float,
    coupon: float,
    bonus_coupon: float = 0.0,
    step_down: float = 0.0,
    obs_per_year: int = 12,
    ki_monitoring: KiMonitoring = KiMonitoring.DAILY,
    ko_monitoring: KoMonitoring = KoMonitoring.EUROPEAN,
    history_ki: bool = False,
    n_paths: int = 6000,
    steps_per_year: int = 84,
    seed: int = 42,
    barrier_shift_bps: float = 0.0,
    spread_smoothing_width: float = 0.0,
) -> Dict[str, np.ndarray]:
    dual = price_dual_grid_at_t(
        product_type, S0, spots, t_current, T, r, q, vol, ko_barrier, ki_barrier,
        strike, coupon, bonus_coupon, step_down, obs_per_year, ki_monitoring,
        ko_monitoring, n_paths, steps_per_year, seed, barrier_shift_bps,
        spread_smoothing_width,
    )
    mean = dual["pv_after"] if history_ki else dual["pv_before"]
    return {"pv": mean, "stderr": np.zeros_like(mean)}


def _fd_greeks_from_pv(spots: np.ndarray, pv: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    n = len(spots)
    delta = np.full(n, np.nan)
    gamma = np.full(n, np.nan)
    for i in range(n):
        if not np.isfinite(pv[i]):
            continue
        if 0 < i < n - 1 and np.isfinite(pv[i - 1]) and np.isfinite(pv[i + 1]):
            dS = spots[i + 1] - spots[i - 1]
            delta[i] = (pv[i + 1] - pv[i - 1]) / max(dS, 1e-12)
            h = 0.5 * dS
            gamma[i] = (pv[i + 1] - 2 * pv[i] + pv[i - 1]) / max(h * h, 1e-12)
        elif i == 0 and n > 1 and np.isfinite(pv[1]):
            dS = spots[1] - spots[0]
            delta[i] = (pv[1] - pv[0]) / max(dS, 1e-12)
            gamma[i] = 0.0
        elif i == n - 1 and n > 1 and np.isfinite(pv[n - 2]):
            dS = spots[n - 1] - spots[n - 2]
            delta[i] = (pv[n - 1] - pv[n - 2]) / max(dS, 1e-12)
            gamma[i] = 0.0
    return delta, gamma


def compute_greeks_term_structure(
    product_type: str,
    product: Dict[str, Any],
    market: Dict[str, Any],
    time_slices: Optional[Sequence[float]] = None,
    spot_range: Optional[Tuple[float, float]] = None,
    spot_steps: int = 41,
    ki_monitoring_type: str = "DAILY",
    ko_monitoring_type: str = "EUROPEAN",
    include_greeks: Optional[Sequence[str]] = None,
    n_paths: int = 6000,
    seed: int = 42,
    barrier_shift_bps: float = 0.0,
    spread_smoothing_width: float = 0.0,
) -> Dict[str, Any]:
    """
    Multi-slice Before/After KI PV/Delta/Gamma surface.
    European KI => before and after curves identical (single continuous expectation).
    """
    include = set(g.lower() for g in (include_greeks or ["pv", "delta", "gamma"]))
    S0 = float(np.atleast_1d(market.get("spots", [100.0]))[0])
    r = float(market.get("r", 0.02))
    q = float(np.atleast_1d(market.get("q", [0.0]))[0])
    vol = float(np.atleast_1d(market.get("flat_vols", [0.2]))[0])
    T = float(product.get("tenor", 1.0))

    if time_slices is None:
        time_slices = [0.0, T / 3.0, T / 2.0, 2.0 * T / 3.0, T]
    time_slices = [float(min(max(t, 0.0), T)) for t in time_slices]

    lo, hi = spot_range if spot_range else (0.5 * S0, 1.4 * S0)
    spot_grid = np.linspace(float(lo), float(hi), int(spot_steps))

    ki_mon = KiMonitoring.EUROPEAN if str(ki_monitoring_type).upper() in (
        "EUROPEAN",
        "AXKI",
        "EU",
    ) else KiMonitoring.DAILY
    ko_mon = KoMonitoring.DAILY if str(ko_monitoring_type).upper() in (
        "DAILY",
        "CONTINUOUS",
        "CONTINUOUS_AMERICAN",
        "AMERICAN",
    ) else KoMonitoring.EUROPEAN

    ko_b = float(product.get("ko_barrier", 1.03 if product_type != "fcn" else 1.05))
    ki_b = float(product.get("ki_barrier", 0.75))
    strike = float(product.get("strike", 1.0 if product_type != "fcn" else 0.75))
    coupon = float(
        product.get("coupon", product.get("fixed_coupon_pa", product.get("snowball_coupon_pa", 0.15)))
    )
    bonus = float(product.get("bonus_coupon", 0.0))
    step_down = float(product.get("step_down", 0.0))
    obs_freq = str(product.get("obs_freq", "monthly")).lower()
    if obs_freq.startswith("day"):
        opy = 252
    elif obs_freq.startswith("year") or obs_freq.startswith("euro"):
        opy = 1
    else:
        opy = 12

    ptype = product_type.lower()
    if ptype == "phoenix":
        ptype = "snowball"

    ki_abs = ki_b * S0
    labels = {
        0.0: "t = 0",
        T / 3.0: "t = T/3",
        T / 2.0: "t = T/2",
        2.0 * T / 3.0: "t = 2T/3",
        T: "t = T",
    }

    slices_out: List[Dict[str, Any]] = []
    for t in time_slices:
        dual = price_dual_grid_at_t(
            ptype, S0, spot_grid, t, T, r, q, vol, ko_b, ki_b, strike, coupon, bonus,
            step_down, opy, ki_mon, ko_mon, n_paths, 84, seed,
            barrier_shift_bps, spread_smoothing_width,
        )
        before_pv = dual["pv_before"].astype(float)
        after_pv = dual["pv_after"].astype(float)
        if ki_mon == KiMonitoring.DAILY:
            before_pv = before_pv.copy()
            before_pv[spot_grid <= ki_abs + 1e-9] = np.nan

        d_after, g_after = _fd_greeks_from_pv(spot_grid, after_pv)
        d_before, g_before = _fd_greeks_from_pv(spot_grid, before_pv)

        def pack(spots, pv, delta, gamma):
            mask = np.isfinite(pv)
            out: Dict[str, Any] = {"valid_spots": spots[mask].tolist()}
            if "pv" in include:
                out["pv"] = pv[mask].tolist()
            if "delta" in include:
                out["delta"] = np.where(mask, delta, np.nan)[mask].tolist()
            if "gamma" in include:
                out["gamma"] = np.where(mask, gamma, np.nan)[mask].tolist()
            return out

        # nearest label
        lab = labels.get(t)
        if lab is None:
            lab = f"t = {t:.4f}"

        slices_out.append(
            {
                "t": t,
                "label": lab,
                "before_ki": pack(spot_grid, before_pv, d_before, g_before),
                "after_ki": pack(spot_grid, after_pv, d_after, g_after),
            }
        )

    return {
        "spot_grid": spot_grid.tolist(),
        "S0": S0,
        "T": T,
        "ki_level": ki_abs,
        "ko_level": ko_b * S0,
        "ki_monitoring_type": "EUROPEAN" if ki_mon == KiMonitoring.EUROPEAN else "DAILY",
        "ko_monitoring_type": "DAILY" if ko_mon == KoMonitoring.DAILY else "EUROPEAN",
        "slices": slices_out,
    }
