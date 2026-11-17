"""Unit tests for Fade / Momentum regime signal."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.regime_signal import (
    RegimeState,
    decide_regime_signal,
    rolling_quantile_map,
)


def _bands():
    level = {
        "q01": -2.0,
        "q10": -1.0,
        "q25": -0.5,
        "q50": 0.0,
        "q75": 0.5,
        "q90": 1.0,
        "q99": 2.0,
    }
    shock = {
        "q10": -1.2,
        "q20": -0.8,
        "q80": 0.8,
        "q90": 1.2,
    }
    return level, shock


def test_rolling_quantile_map_no_lookahead():
    rng = np.random.default_rng(3)
    s = pd.Series(rng.normal(0, 0.01, size=400))
    b = rolling_quantile_map(s, window=252, qs=(0.1, 0.9))
    t = 300
    hi_before = float(b["q90"].iloc[t])
    s2 = s.copy()
    s2.iloc[t:] = 99.0
    b2 = rolling_quantile_map(s2, window=252, qs=(0.1, 0.9))
    assert abs(float(b2["q90"].iloc[t]) - hi_before) < 1e-12


def test_momentum_accel_zone():
    level, shock = _bands()
    st = RegimeState()
    dec = decide_regime_signal(st, S_t=0.8, z_t=1.5, level_q=level, shock_q=shock)
    assert dec.signal == -1 and dec.regime_mode == "momentum"


def test_momentum_exhaust_at_q50():
    """Mom exhausts once S crosses q50 after min hold."""
    level, shock = _bands()
    st_early = RegimeState(position=-1, hold_days=1, regime_mode="momentum", entry_spread=0.8)
    dec0 = decide_regime_signal(st_early, S_t=0.1, z_t=0.0, level_q=level, shock_q=shock)
    assert dec0.signal == -1 and dec0.reason == "momentum_hold"
    st = RegimeState(position=-1, hold_days=2, regime_mode="momentum", entry_spread=0.8)
    # Short Mom: S >= q50 → exhaust
    dec = decide_regime_signal(st, S_t=0.0, z_t=0.0, level_q=level, shock_q=shock)
    assert dec.signal == 0 and dec.reason == "momentum_exhaust"
    # Still above mean but not crossed for long: S=0.3 < q75, > q50 hold for short? S=0.3 >= 0 → exhaust
    dec_hold = decide_regime_signal(st, S_t=-0.2, z_t=0.0, level_q=level, shock_q=shock)
    assert dec_hold.signal == -1 and dec_hold.reason == "momentum_hold"
    # Keep S below q50 so exhaust does not fire; extreme adverse z stops.
    dec4 = decide_regime_signal(st, S_t=-0.2, z_t=-1.5, level_q=level, shock_q=shock)
    assert dec4.signal == 0 and dec4.reason == "momentum_adverse_shock"


def test_fade_requires_dual_day_calm():
    level, shock = _bands()
    st = RegimeState()
    dec = decide_regime_signal(
        st, S_t=1.5, z_t=0.5, level_q=level, shock_q=shock, z_prev=1.0
    )
    assert dec.signal == 0
    dec2 = decide_regime_signal(
        st, S_t=1.5, z_t=0.5, level_q=level, shock_q=shock, z_prev=0.5
    )
    assert dec2.signal == 1 and dec2.regime_mode == "fade"


def test_fade_exit_at_q50():
    level, shock = _bands()
    st = RegimeState(position=1, hold_days=3, regime_mode="fade", entry_spread=1.2)
    # Long fade: exit when S <= q50
    dec = decide_regime_signal(st, S_t=0.0, z_t=0.0, level_q=level, shock_q=shock)
    assert dec.signal == 0 and dec.reason == "fade_mean_revert_exit"
    dec_hold = decide_regime_signal(st, S_t=0.3, z_t=0.0, level_q=level, shock_q=shock)
    assert dec_hold.signal == 1 and dec_hold.reason == "fade_hold"


def test_cooldown():
    level, shock = _bands()
    st = RegimeState(cooldown=2)
    dec = decide_regime_signal(st, S_t=0.8, z_t=1.5, level_q=level, shock_q=shock)
    assert dec.signal == 0 and dec.cooldown == 1
    assert dec.reason == "cooldown"


def test_book_momentum_only_blocks_fade_entry():
    level, shock = _bands()
    st = RegimeState()
    dec = decide_regime_signal(
        st, S_t=1.5, z_t=0.5, level_q=level, shock_q=shock, z_prev=0.5, book="momentum"
    )
    assert dec.signal == 0 and dec.reason == "flat"
    dec2 = decide_regime_signal(
        st, S_t=0.8, z_t=1.5, level_q=level, shock_q=shock, book="momentum"
    )
    assert dec2.signal == -1 and dec2.regime_mode == "momentum"


def test_book_fade_only_blocks_momentum_entry():
    level, shock = _bands()
    st = RegimeState()
    dec = decide_regime_signal(
        st, S_t=0.8, z_t=1.5, level_q=level, shock_q=shock, book="fade"
    )
    assert dec.signal == 0 and dec.reason == "flat"
    dec2 = decide_regime_signal(
        st, S_t=1.5, z_t=0.5, level_q=level, shock_q=shock, z_prev=0.5, book="fade"
    )
    assert dec2.signal == 1 and dec2.regime_mode == "fade"
