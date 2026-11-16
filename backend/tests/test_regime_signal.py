"""Unit tests for Fade / Momentum regime signal (v1)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from engine.regime_signal import (
    COOLDOWN_DAYS,
    RegimeState,
    decide_regime_signal,
    rolling_quantile_map,
)


def _bands():
    level = {
        "q01": -2.0,
        "q08": -1.2,
        "q10": -1.0,
        "q25": -0.5,
        "q30": -0.35,
        "q40": -0.2,
        "q60": 0.2,
        "q70": 0.35,
        "q75": 0.5,
        "q90": 1.0,
        "q92": 1.2,
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


def test_momentum_exhaust_mid_band():
    level, shock = _bands()
    st = RegimeState(position=-1, hold_days=1, regime_mode="momentum", entry_spread=0.8)
    dec = decide_regime_signal(st, S_t=0.0, z_t=0.0, level_q=level, shock_q=shock)
    assert dec.signal == 0 and dec.reason == "momentum_exhaust"


def test_fade_requires_dual_day_calm():
    level, shock = _bands()
    st = RegimeState()
    # prior day still hot (z_prev > q80) → blocked
    dec = decide_regime_signal(
        st, S_t=1.5, z_t=0.5, level_q=level, shock_q=shock, z_prev=1.0
    )
    assert dec.signal == 0
    # both calm (≤ q80) → fade short
    dec2 = decide_regime_signal(
        st, S_t=1.5, z_t=0.5, level_q=level, shock_q=shock, z_prev=0.5
    )
    assert dec2.signal == 1 and dec2.regime_mode == "fade"


def test_cooldown():
    level, shock = _bands()
    st = RegimeState(cooldown=2)
    dec = decide_regime_signal(st, S_t=0.8, z_t=1.5, level_q=level, shock_q=shock)
    assert dec.signal == 0 and dec.cooldown == 1
    assert dec.reason == "cooldown"
