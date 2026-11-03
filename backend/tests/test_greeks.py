import numpy as np

from engine.greeks import barrier_shift, call_spread_smooth, fd_greeks


def test_call_spread_smooth_bounds():
    S = np.array([90.0, 100.0, 110.0])
    y = call_spread_smooth(S, barrier=100.0, width=5.0, above=True)
    assert y[0] == 0.0
    assert y[-1] == 1.0
    assert 0.0 < y[1] < 1.0


def test_barrier_shift_relative():
    s = barrier_shift(100.0, 0.01)
    assert abs(s["H_up"] - 101.0) < 1e-9
    assert abs(s["H_down"] - 99.0) < 1e-9


def test_crn_reduces_noise():
    """CRN fixed seed → deterministic; bump delta stable."""

    def price_fn(spot=100.0, vol=0.2, seed=0, **kw):
        rng = np.random.default_rng(seed)
        # toy: E[max(S_T-K,0)] e^{-rT} with 1-step GBM
        S0 = float(np.atleast_1d(spot)[0])
        v = float(np.atleast_1d(vol)[0])
        z = rng.standard_normal(8000)
        ST = S0 * np.exp((-0.5 * v * v) + v * z)
        return float(np.maximum(ST - 100.0, 0.0).mean())

    g1 = fd_greeks(price_fn, {"spot": 100.0, "vol": 0.2}, seed=42)
    g2 = fd_greeks(price_fn, {"spot": 100.0, "vol": 0.2}, seed=42)
    assert abs(g1["delta"][0] - g2["delta"][0]) < 1e-12

    # different seeds without CRN would differ more — with CRN same seed identical
    g3 = fd_greeks(price_fn, {"spot": 100.0, "vol": 0.2}, seed=99)
    assert abs(g3["delta"][0] - g1["delta"][0]) > 1e-6 or abs(g3["price"] - g1["price"]) > 1e-6
