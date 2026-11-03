import numpy as np
import pytest

from models.jwsvi import (
    RawSviParams,
    calibrate_slice,
    check_butterfly,
    check_calendar,
    density_g,
    implied_vol,
    jw_to_raw,
    raw_to_jw,
    svi_w_derivatives,
    total_variance,
)
from models.local_vol import dupire_local_vol_grid, generate_local_vol_surface


def test_raw_jw_roundtrip_atm():
    raw = RawSviParams(a=0.04, b=0.2, rho=-0.4, m=0.0, sigma=0.2)
    t = 1.0
    jw = raw_to_jw(raw, t)
    raw2 = jw_to_raw(jw, t)
    w0 = float(total_variance(0.0, raw))
    w0b = float(total_variance(0.0, raw2))
    assert abs(w0 - w0b) < 0.02


def test_analytic_derivatives_match_fd():
    raw = RawSviParams(a=0.03, b=0.2, rho=-0.5, m=-0.05, sigma=0.15)
    k = np.linspace(-0.8, 0.8, 41)
    w, wp, wpp = svi_w_derivatives(k, raw)
    eps = 1e-6
    wp_fd = (total_variance(k + eps, raw) - total_variance(k - eps, raw)) / (2 * eps)
    wpp_fd = (
        total_variance(k + eps, raw) - 2 * total_variance(k, raw) + total_variance(k - eps, raw)
    ) / (eps * eps)
    assert np.max(np.abs(wp - wp_fd)) < 1e-5
    assert np.max(np.abs(wpp - wpp_fd)) < 1e-4


def test_calibrate_synthetic_smile():
    raw_true = RawSviParams(a=0.03, b=0.15, rho=-0.5, m=-0.05, sigma=0.15)
    t = 0.5
    k = np.linspace(-0.4, 0.4, 21)
    iv = implied_vol(k, t, raw_true)
    res = calibrate_slice(k, iv, t, method="SLSQP")
    assert res.rmse < 0.02
    assert res.mae < 0.02
    ok, g_min = check_butterfly(res.raw)
    assert isinstance(ok, bool)
    assert np.isfinite(g_min)
    assert "arbitrage_check" in res.to_dict()


def test_deep_otm_put_skew_no_butterfly():
    """Extreme put wing: calibrate must keep g(k) >= 0 on dense grid."""
    t = 0.75
    k = np.linspace(-1.2, 0.6, 35)
    # Steep negative skew / fat left wing (deep OTM puts high IV)
    base = 0.18
    iv = base + 0.55 * np.exp(-3.0 * (k + 0.9) ** 2) * (k < 0) + 0.08 * np.maximum(-k, 0)
    iv = np.clip(iv, 0.08, 1.2)
    res = calibrate_slice(k, iv, t, method="SLSQP", butterfly_lambda=80.0)
    span = 4.0 * float(np.median(iv)) * np.sqrt(t)
    k_grid = np.linspace(-span, span, 201)
    g = density_g(k_grid, res.raw)
    assert np.nanmin(g) >= -1e-5, f"g_min={np.nanmin(g)}"
    assert res.butterfly_ok or res.g_min >= -1e-4
    assert res.has_butterfly_arbitrage is False or res.g_min >= -1e-4


def test_calendar_check():
    raw = RawSviParams(a=0.02, b=0.1, rho=0.0, m=0.0, sigma=0.1)
    k = np.linspace(-0.5, 0.5, 11)
    w1 = total_variance(k, raw)
    w2 = w1 * 1.5
    assert check_calendar(w1, w2)
    assert not check_calendar(w2, w1)


def test_local_vol_grid_bounded():
    raw1 = RawSviParams(a=0.02, b=0.12, rho=-0.3, m=0.0, sigma=0.15)
    raw2 = RawSviParams(a=0.05, b=0.15, rho=-0.35, m=-0.02, sigma=0.18)
    slices = [
        {"ttm": 0.25, "raw": raw1.to_dict()},
        {"ttm": 1.0, "raw": raw2.to_dict()},
    ]
    spot = 100.0
    S = np.linspace(60, 140, 25)
    T = np.array([0.25, 0.5, 0.75, 1.0])
    grid = dupire_local_vol_grid(slices, S, T, spot)
    assert grid.shape == (25, 4)
    assert np.all(np.isfinite(grid))
    assert np.all(grid >= 0.03 - 1e-9)
    assert np.all(grid <= 1.50 + 1e-9)
    surf = generate_local_vol_surface(spot, S.tolist(), T.tolist(), slices)
    assert "sigma_loc" in surf
    assert len(surf["S_grid"]) == 25
