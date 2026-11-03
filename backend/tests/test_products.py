import numpy as np

from products.registry import price_product


def test_snowball_prices():
    out = price_product(
        "snowball",
        {"tenor": 1.0, "ko_barrier": 1.03, "ki_barrier": 0.75, "coupon": 0.2, "obs_freq": "monthly"},
        {"spots": [100.0], "r": 0.02, "q": [0.0], "flat_vols": [0.2], "corr": [[1.0]]},
        {"n_paths": 5000, "n_steps": 12, "seed": 1, "bridge": False},
        {"enabled": False},
    )
    assert 0.5 < out["price"] < 1.3
    assert "ko_prob" in out["diagnostics"]


def test_booster_and_fcn():
    b = price_product(
        "booster",
        {"tenor": 1.0, "strike": 1.0, "participation": 1.5, "structure": "booster"},
        {"spots": [100.0], "r": 0.0, "q": [0.0], "flat_vols": [0.2], "corr": [[1.0]]},
        {"n_paths": 3000, "n_steps": 1, "seed": 2},
    )
    assert b["price"] > 0
    f = price_product(
        "fcn",
        {"tenor": 1.0, "fixed_coupon_pa": 0.1, "ko_barrier": 1.05, "strike": 1.0, "obs_freq": "monthly"},
        {
            "spots": [100.0, 100.0],
            "r": 0.02,
            "q": [0.0, 0.0],
            "flat_vols": [0.2, 0.2],
            "corr": [[1.0, 0.5], [0.5, 1.0]],
        },
        {"n_paths": 3000, "n_steps": 12, "seed": 3},
    )
    assert f["price"] > 0
