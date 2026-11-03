"""
Numba-accelerated multi-asset Monte Carlo with Brownian Bridge barrier correction.
"""
from __future__ import annotations

from typing import Optional, Tuple, Union

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


Array = np.ndarray


def correlate(Z: Array, Sigma: Array) -> Array:
    """Z_corr = L @ Z_iid with LL^T = Sigma. Z shape (n_paths, n_assets) or (..., n_assets)."""
    Sigma = np.asarray(Sigma, dtype=float)
    # ensure PSD
    eigval, eigvec = np.linalg.eigh(Sigma)
    eigval = np.maximum(eigval, 1e-12)
    L = eigvec @ np.diag(np.sqrt(eigval))
    return Z @ L.T


def cholesky_factor(Sigma: Array) -> Array:
    Sigma = np.asarray(Sigma, dtype=float)
    n = Sigma.shape[0]
    # ridge for numerical stability
    return np.linalg.cholesky(Sigma + 1e-12 * np.eye(n))


@njit(cache=True)
def brownian_bridge_hit_prob(S_k: float, S_kp1: float, H: float, sigma: float, dt: float) -> float:
    """
    P(hit H | S_k, S_{k+1}) = exp( -2 ln(S_k/H) ln(S_{k+1}/H) / (σ² Δt) )
    for continuous barrier between discrete steps (same side of H).
    """
    if H <= 0 or sigma <= 0 or dt <= 0 or S_k <= 0 or S_kp1 <= 0:
        return 0.0
    # already crossed in discrete sense
    if (S_k - H) * (S_kp1 - H) <= 0.0:
        return 1.0
    a = np.log(S_k / H)
    b = np.log(S_kp1 / H)
    return float(np.exp(-2.0 * a * b / (sigma * sigma * dt)))


@njit(cache=True)
def _bgk_shift(H: float, sigma: float, dt: float, up: bool) -> float:
    """Broadie–Glasserman–Kou discrete-extremum shift ≈ 0.5826 σ √Δt."""
    beta = 0.5826
    shift = beta * sigma * np.sqrt(dt)
    return H * np.exp(shift) if up else H * np.exp(-shift)


@njit(parallel=True, cache=True)
def _simulate_flat_vol(
    spots: Array,
    r: float,
    q: Array,
    vols: Array,
    L: Array,
    n_paths: int,
    n_steps: int,
    dt: float,
    seed: int,
) -> Array:
    n_assets = spots.shape[0]
    out = np.empty((n_paths, n_assets, n_steps + 1), dtype=np.float64)
    for p in prange(n_paths):
        # simple LCG per path for reproducibility with seed
        state = np.uint64(seed + p * 9973 + 1)
        S = spots.copy()
        for a in range(n_assets):
            out[p, a, 0] = S[a]
        for t in range(n_steps):
            Z = np.empty(n_assets, dtype=np.float64)
            for a in range(n_assets):
                # Box-Muller via two uniforms from LCG
                state = np.uint64(state * np.uint64(6364136223846793005) + np.uint64(1))
                u1 = (state >> np.uint64(11)) * (1.0 / 9007199254740992.0)
                state = np.uint64(state * np.uint64(6364136223846793005) + np.uint64(1))
                u2 = (state >> np.uint64(11)) * (1.0 / 9007199254740992.0)
                u1 = max(u1, 1e-12)
                Z[a] = np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * np.pi * u2)
            # correlate
            Zc = np.zeros(n_assets, dtype=np.float64)
            for i in range(n_assets):
                s = 0.0
                for j in range(n_assets):
                    s += L[i, j] * Z[j]
                Zc[i] = s
            for a in range(n_assets):
                vol = vols[a]
                drift = (r - q[a] - 0.5 * vol * vol) * dt
                S[a] = S[a] * np.exp(drift + vol * np.sqrt(dt) * Zc[a])
                out[p, a, t + 1] = S[a]
    return out


def simulate_paths(
    spots: Array,
    r: float,
    q: Array,
    vol_or_locvol: Union[Array, Tuple[Array, Array, Array]],
    corr: Array,
    n_paths: int,
    n_steps: int,
    dt: float,
    seed: int = 42,
    bridge: bool = True,
    barrier: Optional[float] = None,
    barrier_up: bool = True,
    bgk_shift: bool = False,
) -> Tuple[Array, Optional[Array]]:
    """
    Simulate multi-asset paths.

    vol_or_locvol:
      - 1d array of flat vols per asset, or
      - tuple (s_grid, t_grid, sigma_loc[S,t]) for single-asset local vol (multi-asset LV uses flat fallback).

    Returns
    -------
    paths : (n_paths, n_assets, n_steps+1)
    hit_prob : optional (n_paths,) Brownian-bridge continuous hit probs vs `barrier`
               (1.0 if discrete cross, else product of step probs). None if barrier is None.
    """
    spots = np.asarray(spots, dtype=float).ravel()
    q = np.asarray(q, dtype=float).ravel()
    if q.size == 1 and spots.size > 1:
        q = np.full(spots.size, float(q[0]))
    n_assets = spots.size
    Sigma = np.asarray(corr, dtype=float)
    if Sigma.ndim == 0:
        Sigma = np.array([[1.0]])
    if Sigma.shape == (1,) or Sigma.size == 1:
        Sigma = np.eye(n_assets)
    L = cholesky_factor(Sigma)

    use_lv = isinstance(vol_or_locvol, tuple)
    if use_lv:
        # single-asset local vol path (asset 0); others flat 20% if multi
        s_grid, t_grid, sigma_loc = vol_or_locvol
        s_grid = np.asarray(s_grid, dtype=float)
        t_grid = np.asarray(t_grid, dtype=float)
        sigma_loc = np.asarray(sigma_loc, dtype=float)
        paths = np.empty((n_paths, n_assets, n_steps + 1), dtype=float)
        rng = np.random.default_rng(seed)
        Z_all = rng.standard_normal((n_paths, n_steps, n_assets))
        for p in range(n_paths):
            S = spots.copy()
            paths[p, :, 0] = S
            for t_i in range(n_steps):
                Zc = L @ Z_all[p, t_i]
                t_now = (t_i + 0.5) * dt
                # interpolate local vol for asset 0
                si = int(np.clip(np.searchsorted(s_grid, S[0]) - 1, 0, len(s_grid) - 2))
                ti = int(np.clip(np.searchsorted(t_grid, t_now) - 1, 0, len(t_grid) - 2))
                vol0 = float(sigma_loc[si, ti])
                vols = np.full(n_assets, 0.2)
                vols[0] = max(vol0, 1e-6)
                for a in range(n_assets):
                    vol = vols[a]
                    drift = (r - q[a] - 0.5 * vol * vol) * dt
                    S[a] = S[a] * np.exp(drift + vol * np.sqrt(dt) * Zc[a])
                paths[p, :, t_i + 1] = S
    else:
        vols = np.asarray(vol_or_locvol, dtype=float).ravel()
        if vols.size == 1 and n_assets > 1:
            vols = np.full(n_assets, float(vols[0]))
        if HAS_NUMBA:
            paths = _simulate_flat_vol(spots, float(r), q, vols, L, int(n_paths), int(n_steps), float(dt), int(seed))
        else:
            rng = np.random.default_rng(seed)
            paths = np.empty((n_paths, n_assets, n_steps + 1), dtype=float)
            for p in range(n_paths):
                S = spots.copy()
                paths[p, :, 0] = S
                Z = rng.standard_normal((n_steps, n_assets))
                for t_i in range(n_steps):
                    Zc = L @ Z[t_i]
                    for a in range(n_assets):
                        vol = vols[a]
                        drift = (r - q[a] - 0.5 * vol * vol) * dt
                        S[a] = S[a] * np.exp(drift + vol * np.sqrt(dt) * Zc[a])
                    paths[p, :, t_i + 1] = S
        # for bridge use average vol of asset 0
        vols_bridge = vols

    hit = None
    if barrier is not None and bridge:
        H = float(barrier)
        if bgk_shift:
            vol0 = float(np.asarray(vol_or_locvol).ravel()[0]) if not use_lv else 0.2
            H = float(_bgk_shift(H, vol0, dt, barrier_up))
        hit = np.ones(n_paths, dtype=float)
        vol0 = float(np.mean(np.asarray(vol_or_locvol).ravel())) if not use_lv else 0.2
        for p in range(n_paths):
            surv = 1.0
            for t_i in range(n_steps):
                sk = paths[p, 0, t_i]
                sk1 = paths[p, 0, t_i + 1]
                ph = brownian_bridge_hit_prob(sk, sk1, H, vol0, dt)
                surv *= 1.0 - ph
            hit[p] = 1.0 - surv
    return paths, hit
