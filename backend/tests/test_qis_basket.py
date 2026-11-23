"""Unit tests for QIS basket risk-parity combiner (synthetic NAV, no DB)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.qis_basket import (
    BASKET_SIGNAL_CHOICES,
    build_basket_returns,
    estimate_weights,
    member_id,
    normalize_method,
    parse_members,
    scale_to_target,
)


def _synth_rets(n=400, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Correlated but different vol assets
    z = rng.normal(size=(n, 3))
    a = 0.01 * z[:, 0]
    b = 0.015 * (0.6 * z[:, 0] + 0.8 * z[:, 1])
    c = 0.008 * (0.3 * z[:, 0] + 0.954 * z[:, 2])
    idx = pd.bdate_range("2019-01-02", periods=n)
    return pd.DataFrame(
        {
            "cal:300:quantile": a,
            "cal:300:garch_z": b,
            "cal:300:regime2": c,
        },
        index=idx.astype(str),
    )


def test_normalize_and_parse_members():
    assert normalize_method("cvar") == "cvar_rp"
    assert normalize_method("risk_parity") == "vol_rp"
    with pytest.raises(ValueError):
        normalize_method("foo")
    ms = parse_members("cal:300:quantile,cal:300:garch_z")
    assert ms == ["cal:300:quantile", "cal:300:garch_z"]
    assert member_id("300", "regime2") == "cal:300:regime2"
    with pytest.raises(ValueError):
        parse_members(["cal:300:quantile"])


def test_inverse_vol_weights_favor_low_vol():
    rets = _synth_rets()
    window = rets.iloc[:126]
    w = estimate_weights(window, "vol_rp", 0.05)
    assert abs(w.sum() - 1.0) < 1e-9
    # Asset c has lowest vol → largest weight
    assert w[2] > w[0] > 0
    assert w[2] > w[1]


def test_cvar_weights_sum_to_one():
    rets = _synth_rets()
    w = estimate_weights(rets.iloc[:126], "cvar_rp", 0.05)
    assert abs(w.sum() - 1.0) < 1e-9
    assert (w >= 0).all()


def test_scale_to_target_vol_moves_leverage():
    rets = _synth_rets()
    window = rets.iloc[:126]
    w = estimate_weights(window, "vol_rp", 0.05)
    w_hi, lev_hi, _ = scale_to_target(w, window, "vol_rp", 0.20, 0.05)
    w_lo, lev_lo, _ = scale_to_target(w, window, "vol_rp", 0.05, 0.05)
    assert lev_hi > lev_lo
    assert abs(w_hi.sum() / w_lo.sum() - lev_hi / lev_lo) < 1e-6


def test_build_basket_walk_forward_no_lookahead_shape():
    rets = _synth_rets()
    br, wh, meta = build_basket_returns(
        rets, method="vol_rp", target_vol=0.10, lookback=63, rebalance=21
    )
    assert len(br) == len(rets) - 63
    assert not br.isna().any()
    assert wh.shape[0] == len(br)
    assert len(meta) >= 1
    # NAV path finite
    nav = (1.0 + br).cumprod()
    assert math.isfinite(float(nav.iloc[-1]))
    assert float(nav.iloc[-1]) > 0


def test_signal_choices_cover_subindices():
    assert "quantile" in BASKET_SIGNAL_CHOICES
    assert "garch_z" in BASKET_SIGNAL_CHOICES
    assert "regime2" in BASKET_SIGNAL_CHOICES
