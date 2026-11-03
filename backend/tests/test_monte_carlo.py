import numpy as np

from engine.monte_carlo import brownian_bridge_hit_prob, correlate, simulate_paths


def test_correlate_identity():
    Z = np.random.default_rng(0).standard_normal((1000, 2))
    out = correlate(Z, np.eye(2))
    assert out.shape == Z.shape
    # corr ≈ I
    c = np.corrcoef(out.T)
    assert abs(c[0, 1]) < 0.1


def test_corr_one_collapses():
    spots = np.array([100.0, 100.0])
    paths, _ = simulate_paths(
        spots=spots,
        r=0.0,
        q=np.array([0.0, 0.0]),
        vol_or_locvol=np.array([0.2, 0.2]),
        corr=np.array([[1.0, 1.0], [1.0, 1.0]]),
        n_paths=2000,
        n_steps=1,
        dt=1.0,
        seed=7,
        bridge=False,
    )
    # terminal returns almost identical
    r1 = np.log(paths[:, 0, -1] / 100)
    r2 = np.log(paths[:, 1, -1] / 100)
    assert np.corrcoef(r1, r2)[0, 1] > 0.95


def test_bs_european_mc_error_band():
    """MC call ≈ BS for ATM."""
    from scipy.stats import norm

    S, K, r, q, vol, T = 100.0, 100.0, 0.02, 0.0, 0.2, 1.0
    paths, _ = simulate_paths(
        spots=np.array([S]),
        r=r,
        q=np.array([q]),
        vol_or_locvol=np.array([vol]),
        corr=np.array([[1.0]]),
        n_paths=40000,
        n_steps=1,
        dt=T,
        seed=123,
        bridge=False,
    )
    payoff = np.maximum(paths[:, 0, -1] - K, 0.0)
    mc = np.exp(-r * T) * payoff.mean()
    # BS
    F = S * np.exp((r - q) * T)
    d1 = (np.log(F / K) + 0.5 * vol**2 * T) / (vol * np.sqrt(T))
    d2 = d1 - vol * np.sqrt(T)
    bs = np.exp(-r * T) * (F * norm.cdf(d1) - K * norm.cdf(d2))
    assert abs(mc - bs) / bs < 0.05


def test_bridge_hit_known_cross():
    # discrete cross → 1
    assert brownian_bridge_hit_prob(90, 110, 100, 0.2, 1 / 252) == 1.0
    # same side → (0,1)
    p = brownian_bridge_hit_prob(110, 120, 100, 0.2, 1 / 252)
    assert 0.0 <= p < 1.0
