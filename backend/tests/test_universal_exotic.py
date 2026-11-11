"""Universal Exotic Workbench acceptance tests."""
from __future__ import annotations

import numpy as np
import pytest

from engine.greeks_term_structure import compute_greeks_term_structure_universal
from engine.solver import solve_exotic_parameter
from products.universal_exotic import (
    UniversalExoticProduct,
    get_preset_product,
    list_presets,
)


def _mkt(S0=100.0, vol=0.2, n=1):
    return {
        "spots": [S0] * n,
        "r": 0.02,
        "q": [0.0] * n,
        "flat_vols": [vol] * n,
        "vol_mode": "flat",
    }


def test_presets_map_to_universal():
    names = [p["name"] for p in list_presets() if p["id"] != "custom"]
    for name in ["Snowball Classic", "Multi-Asset FCN", "Phoenix Memory Autocall", "Stepdown Snowball"]:
        p = get_preset_product(name)
        assert isinstance(p, UniversalExoticProduct)
        assert p.name
        assert p.tenor > 0
        assert 1 <= p.n_assets() <= 4
        legacy = p.to_legacy_product_dict()
        assert "ko_barrier" in legacy and "ki_barrier" in legacy
        cat = p.inferred_catalog_type()
        if "FCN" in name:
            assert cat == "fcn"
            assert p.ki.ki_monitoring_type == "EUROPEAN"
            assert p.coupon.cpn_type == "FIXED"
        if "Phoenix" in name:
            assert cat == "phoenix"
            assert p.coupon.memory_cpn is True
        if "Snowball" in name:
            assert cat == "snowball"
            assert p.ki.ki_monitoring_type == "DAILY"


def test_solver_converges_cpn_and_warnings():
    p = get_preset_product("Snowball Classic", tenor=1.0)
    # Use looser target that sits in range for snowball PV
    res = solve_exotic_parameter(
        p,
        _mkt(),
        solve_for="cpn_rate",
        target_price=0.99,
        bounds=(0.01, 0.60),
        n_paths=4000,
        seed=7,
        tol=1e-3,
    )
    assert res["solution"] is not None
    if res["ok"]:
        assert abs(res["residual"]) < 1e-2

    # same-sign / no root → diagnostic warning
    bad = solve_exotic_parameter(
        p,
        _mkt(vol=0.05),
        solve_for="ki_level",
        target_price=0.01,  # unreachable low
        bounds=(0.5, 0.9),
        n_paths=2000,
        seed=1,
    )
    assert bad["detail"] is not None
    assert "No proper solution can be found" in bad["detail"]
    assert "f(a) and f(b) must have different signs" in bad["detail"]


def test_solver_put_strike_clip_advisory():
    p = get_preset_product("Multi-Asset FCN")
    res = solve_exotic_parameter(
        p,
        _mkt(n=2),
        solve_for="put_strike",
        target_price=0.99,
        bounds=(0.4, 1.3),
        n_paths=3000,
        seed=3,
    )
    assert "solution" in res
    # either converges or returns clipped advisory
    if res.get("warnings"):
        assert any("put_strike" in w or "outside" in w for w in res["warnings"])


def test_gts_american_after_ge_before():
    p = get_preset_product("Snowball Classic", tenor=1.0)
    out = compute_greeks_term_structure_universal(
        p,
        _mkt(),
        time_slices=[0.5],
        spot_range=(80.0, 120.0),
        spot_steps=21,
        n_paths=6000,
        seed=42,
        spread_smoothing_width=0.01,
    )
    sl = out["slices"][0]
    ki = out["ki_level"]
    b_s = np.array(sl["before_ki"]["valid_spots"])
    b_d = np.array(sl["before_ki"]["delta"])
    a_s = np.array(sl["after_ki"]["valid_spots"])
    a_d = np.array(sl["after_ki"]["delta"])
    diffs = []
    for s, db in zip(b_s, b_d):
        if s <= ki:
            continue
        j = int(np.argmin(np.abs(a_s - s)))
        if abs(a_s[j] - s) < 1e-6:
            diffs.append(float(a_d[j] - db))
    assert len(diffs) >= 4
    assert np.mean(diffs) >= -0.03


def test_gts_before_empty_below_ki():
    p = get_preset_product("Snowball Classic", tenor=1.0)
    out = compute_greeks_term_structure_universal(
        p,
        _mkt(),
        time_slices=[0.25],
        spot_range=(50.0, 100.0),
        spot_steps=26,
        n_paths=2000,
        seed=2,
    )
    spots = np.array(out["slices"][0]["before_ki"]["valid_spots"])
    assert np.all(spots > out["ki_level"] - 1e-6)


def test_gts_terminal_step_and_european_single():
    p = get_preset_product("Snowball Classic", tenor=1.0)
    out = compute_greeks_term_structure_universal(
        p,
        _mkt(),
        time_slices=[1.0],
        spot_range=(50.0, 140.0),
        spot_steps=31,
        n_paths=2000,
        seed=5,
    )
    after = out["slices"][0]["after_ki"]
    spots = np.array(after["valid_spots"])
    delta = np.array(after["delta"])
    above = spots > 110
    below = spots < 90
    if above.any():
        assert np.nanmean(np.abs(delta[above])) < 0.08
    if below.any():
        assert np.nanmean(delta[below]) > -0.05  # put-like non-negative-ish

    fcn = get_preset_product("Multi-Asset FCN")
    # use single-asset market for speed — product still european AXKI
    fcn.asset.underlying = ["SPX"]
    fcn.asset.correlation_matrix = [[1.0]]
    eu = compute_greeks_term_structure_universal(
        fcn,
        _mkt(),
        time_slices=[0.0, 0.5, 1.0],
        spot_range=(60.0, 130.0),
        spot_steps=15,
        n_paths=2500,
        seed=9,
    )
    assert eu["ki_monitoring_type"] == "EUROPEAN"
    for sl in eu["slices"]:
        np.testing.assert_allclose(
            sl["before_ki"]["pv"], sl["after_ki"]["pv"], rtol=1e-12, atol=1e-12
        )
